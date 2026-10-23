#!/usr/bin/env python3

import csv, difflib, json, os, re, shutil, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

ROOT      = Path(".").resolve()
DATASET   = ROOT / "SRepair/SRepair/dataset/defects4j-sf.json"
PIT_LIBS  = ROOT / "pitest-libs"
LABELED   = ROOT / "srepair_labeled.json"

STAGE1_CSV = ROOT / "srepair_prms_results_v1.csv"
STAGE2_CSV = ROOT / "srepair_prms_nomutants_rerun.csv"
FINAL_CSV  = ROOT / "srepair_prms_results.csv"

XML_DIR1  = ROOT / "pit_xmls"
XML_DIR2  = ROOT / "pit_xmls_nomutants"
WORK_BASE = Path("/tmp/srepair_prms")

PIT_JARS_LIST = [
    "pitest-1.9.0.jar",
    "pitest-command-line-1.9.0.jar",
    "pitest-entry-1.9.0.jar",
]

JUNIT3_JAR   = "/defects4j/framework/lib/build_systems/gradle/deps/junit/junit/3.8.1/junit-3.8.1.jar"
JUNIT4_JAR   = "/defects4j/framework/projects/lib/junit-4.12-hamcrest-1.3.jar"
JUNIT3_PROJECTS = {"Lang", "Cli"}
SKIP_PROJECTS   = {"Closure", "Mockito"}

TIMEOUT      = 1800
BOOTSTRAP_N  = 10000
RANDOM_SEED  = 42

CSV_FIELDS = ["bug_id", "project", "label", "fqcn", "scope_lines", "scope_mode",
              "killed", "survived", "nocov", "other", "total", "prms", "prmsc", "status"]

def pit_jars_cp():
    return ":".join(str(PIT_LIBS / j) for j in PIT_JARS_LIST)

def run(cmd, cwd=None, timeout=TIMEOUT):
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"

def d4j_prop(wdir, prop, default=""):
    rc, o, _ = run(["defects4j", "export", "-p", prop], cwd=wdir, timeout=120)
    val = [v.strip() for v in (o or "").splitlines()
           if v.strip() and not v.startswith("Running")]
    return val[-1] if val else default

def project_dirs(wdir):
    return (d4j_prop(wdir, "dir.src.classes", "src/main/java"),
            d4j_prop(wdir, "dir.bin.classes", "target/classes"),
            d4j_prop(wdir, "dir.bin.tests",   "target/test-classes"))

def full_test_cp(wdir):
    rc, o, _ = run(["defects4j", "export", "-p", "cp.test"], cwd=wdir, timeout=120)
    val = [v.strip() for v in (o or "").splitlines()
           if v.strip() and not v.startswith("Running")]
    cp_str = val[-1] if val else ""
    return [e for e in cp_str.split(":") if e]

def checkout(bug, wdir):
    proj, num = bug.split("-")
    if wdir.exists():
        shutil.rmtree(wdir, ignore_errors=True)
    rc, _, _ = run(["defects4j", "checkout", "-p", proj, "-v", f"{num}b", "-w", str(wdir)],
                   timeout=300)
    return wdir.is_dir()

def insert_function(wdir, loc, start, end, patch_code):
    full = wdir / loc
    if not full.exists():
        return False
    try:
        lines = full.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except Exception:
        return False
    out, patched = [], False
    for idx, line in enumerate(lines):
        if start - 1 <= idx <= end - 1:
            if not patched:
                out.append(patch_code.strip() + "\n")
                patched = True
        else:
            out.append(line)
    if not patched:
        return False
    full.write_text("".join(out), encoding="utf-8", errors="ignore")
    return True

def changed_lines(buggy_func, patch_func, start):
    b = buggy_func.strip().splitlines()
    p = patch_func.strip().splitlines()
    sm = difflib.SequenceMatcher(None, [x.strip() for x in b], [x.strip() for x in p])
    lines = set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            for j in range(j1, j2):
                lines.add(start + j)
    return lines

def fqcn_from_loc(loc):
    m = re.search(r"(?:source|src/main/java|src/java|src)/(.+)\.java$", loc)
    path = m.group(1) if m else loc.rsplit(".", 1)[0]
    return path.replace("/", ".")

def get_tests(wdir, cap=40):
    rc, o, _ = run(["defects4j", "export", "-p", "tests.trigger"], cwd=wdir, timeout=120)
    trigger = set()
    for t in o.splitlines():
        t = t.strip()
        if "::" in t:  trigger.add(t.split("::")[0])
        elif t and not t.startswith("Running"):  trigger.add(t)

    rc, o, _ = run(["defects4j", "export", "-p", "tests.relevant"], cwd=wdir, timeout=120)
    relevant = [t.strip() for t in o.splitlines()
                if t.strip() and not t.startswith("Running")]

    seen = set(trigger)
    return list(trigger) + [t for t in relevant if t not in seen][:cap - len(trigger)]

def score_xml(xml_path, scope):
    killed = survived = nocov = other = 0
    if not xml_path or not Path(xml_path).exists():
        return killed, survived, nocov, other, 0, 0.0, 0.0
    try:
        for m in ET.parse(xml_path).getroot().findall("mutation"):
            ln = int(m.findtext("lineNumber", "0"))
            if scope and ln not in scope:
                continue
            st = m.get("status")
            if   st == "KILLED":      killed   += 1
            elif st == "SURVIVED":    survived  += 1
            elif st == "NO_COVERAGE": nocov     += 1
            else:                     other     += 1
    except Exception:
        pass
    total = killed + survived + nocov + other
    prms  = killed / total if total else 0.0
    prmsc = killed / (killed + survived) if (killed + survived) else 0.0
    return killed, survived, nocov, other, total, prms, prmsc

def append_row(csv_path, row):
    with open(csv_path, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)

def make_junit4_wrapper(test_class, bin_cp, wrapper_dir):
    simple   = test_class.split(".")[-1]
    wname    = f"{simple}J4Wrapper"
    src_path = Path(wrapper_dir) / f"{wname}.java"
    src_path.write_text(f"""import org.junit.runner.RunWith;
import org.junit.runners.AllTests;
import junit.framework.TestSuite;

@RunWith(AllTests.class)
public class {wname} {{
    public static junit.framework.Test suite() {{
        return new TestSuite({test_class}.class);
    }}
}}
""")
    javac_cp = bin_cp + ":" + JUNIT4_JAR + ":" + JUNIT3_JAR
    rc, _, e = run(["javac", "-cp", javac_cp, "-d", wrapper_dir, str(src_path)], timeout=30)
    if rc != 0:
        print(f"    [wrapper compile fail for {test_class}] {e[:200]}")
        return None
    return wname

def run_pit_stage1(wdir, fqcn, tests, src_dir, bin_classes, bin_tests):
    pit_cp  = pit_jars_cp()
    abs_src = wdir / src_dir
    report  = wdir / "pit_report"
    cmd = ["java", "-cp", pit_cp,
           "org.pitest.mutationtest.commandline.MutationCoverageReport",
           "--reportDir",     str(report),
           "--targetClasses", fqcn,
           "--targetTests",   ",".join(tests),
           "--sourceDirs",    str(abs_src),
           "--classPath",     str(wdir / bin_classes),
           "--classPath",     str(wdir / bin_tests),
           "--classPath",     JUNIT4_JAR,
           "--mutators", "ALL",
           "--timestampedReports=false",
           "--outputFormats", "XML"]
    run(cmd, cwd=wdir, timeout=TIMEOUT)
    xml = report / "mutations.xml"
    return str(xml) if xml.exists() else None

def stage1(labeled, dataset, single_bug=None):
    print("\n" + "="*55)
    print("Stage 1: Main run")
    print("="*55)

    XML_DIR1.mkdir(parents=True, exist_ok=True)
    WORK_BASE.mkdir(parents=True, exist_ok=True)

    with open(STAGE1_CSV, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

    items = [x for x in labeled if single_bug is None or x["bug_id"] == single_bug]
    total = len(items)

    for i, item in enumerate(items, 1):
        bug   = item["bug_id"]
        label = item["label"]
        patch = item["patch_code"]
        proj  = bug.split("-")[0]
        print(f"\n[{i}/{total}] {bug} label={label}")

        row = {k: "" for k in CSV_FIELDS}
        row.update({"bug_id": bug, "project": proj, "label": label, "status": "?"})

        meta = dataset.get(bug)
        if not meta:
            row["status"] = "NO_META"; append_row(STAGE1_CSV, row); continue

        loc, start, end = meta["loc"], int(meta["start"]), int(meta["end"])
        fqcn = fqcn_from_loc(loc); row["fqcn"] = fqcn
        wdir = WORK_BASE / f"{bug}_{i}"

        try:
            if not checkout(bug, wdir):
                row["status"] = "CHECKOUT_FAIL"; append_row(STAGE1_CSV, row); continue
            if not insert_function(wdir, loc, start, end, patch):
                row["status"] = "INSERT_FAIL"; append_row(STAGE1_CSV, row); continue

            scope = changed_lines(meta["buggy"], patch, start)
            row["scope_lines"] = ";".join(map(str, sorted(scope)))
            row["scope_mode"]  = "diff"

            run(["defects4j", "compile"], cwd=wdir, timeout=600)
            src_dir, bin_classes, bin_tests = project_dirs(wdir)

            if not (wdir / bin_classes).is_dir():
                row["status"] = "COMPILE_FAIL"; append_row(STAGE1_CSV, row); continue

            tests = get_tests(wdir)
            if not tests:
                row["status"] = "NO_TESTS"; append_row(STAGE1_CSV, row); continue

            xml = run_pit_stage1(wdir, fqcn, tests, Path(src_dir), bin_classes, bin_tests)
            k, s, n, o2, t, prms, prmsc = score_xml(xml, scope)
            if t == 0:
                k, s, n, o2, t, prms, prmsc = score_xml(xml, set(range(start, end + 1)))
                row["scope_mode"] = "function"

            row.update({"killed": k, "survived": s, "nocov": n, "other": o2, "total": t,
                        "prms": f"{prms:.4f}", "prmsc": f"{prmsc:.4f}",
                        "status": "OK" if t > 0 else "NO_MUTANTS"})
            print(f"  -> {row['status']} PRMSc={prmsc:.3f} (k={k} s={s} n={n})")

        except Exception as ex:
            row["status"] = f"EXC:{ex}"
        finally:
            xml_src = wdir / "pit_report" / "mutations.xml"
            if xml_src.exists():
                shutil.copy(xml_src, XML_DIR1 / f"{bug}_{label}_{i}.xml")
            shutil.rmtree(wdir, ignore_errors=True)

        append_row(STAGE1_CSV, row)

    rows = sum(1 for _ in open(STAGE1_CSV)) - 1
    print(f"\nStage 1 done. {rows} rows → {STAGE1_CSV.name}")

def run_pit_stage2(wdir, fqcn, tests, src_dir, cp_entries, proj):
    pit_cp  = pit_jars_cp()
    abs_src = wdir / src_dir

    if len(tests) > 40:
        pkg   = ".".join(fqcn.split(".")[:-1])
        tests = sorted(tests, key=lambda t: (0 if ".".join(t.split(".")[:-1]) == pkg else 1))[:40]

    report      = wdir / "pit_report"
    wrapper_dir = None

    if proj in JUNIT3_PROJECTS:
        wrapper_dir = tempfile.mkdtemp(prefix="pit_j3wrap_")
        bin_cp  = ":".join(cp_entries)
        wrapped = [w for t in tests
                   for w in [make_junit4_wrapper(t, bin_cp, wrapper_dir)] if w]
        if not wrapped:
            shutil.rmtree(wrapper_dir, ignore_errors=True)
            return None, False
        system_cp = ":".join([pit_cp, wrapper_dir] + cp_entries + [JUNIT4_JAR, JUNIT3_JAR])
        cmd = ["java", "-cp", system_cp,
               "org.pitest.mutationtest.commandline.MutationCoverageReport",
               "--reportDir",     str(report),
               "--targetClasses", fqcn,
               "--targetTests",   ",".join(wrapped),
               "--sourceDirs",    str(abs_src),
               "--mutators", "ALL",
               "--timestampedReports=false",
               "--outputFormats", "XML"]
    else:
        cmd = ["java", "-cp", pit_cp,
               "org.pitest.mutationtest.commandline.MutationCoverageReport",
               "--reportDir",     str(report),
               "--targetClasses", fqcn,
               "--targetTests",   ",".join(tests),
               "--sourceDirs",    str(abs_src),
               "--mutators", "ALL",
               "--timestampedReports=false",
               "--outputFormats", "XML"]
        for cp in cp_entries:
            cmd += ["--classPath", cp]

    rc, o, e = run(cmd, cwd=wdir, timeout=TIMEOUT)

    if wrapper_dir:
        shutil.rmtree(wrapper_dir, ignore_errors=True)

    combined      = (o or "") + (e or "")
    fails_trigger = ("did not pass without mutation" in combined or
                     "Tests failing without mutation" in combined)

    xml = report / "mutations.xml"
    return (str(xml) if xml.exists() else None), fails_trigger

def stage2(labeled, dataset):
    print("\n" + "="*55)
    print("Stage 2: Rerun NO_MUTANTS patches")
    print("="*55)

    if not STAGE1_CSV.exists():
        print(f"ERROR: {STAGE1_CSV} not found — run stage 1 first"); return

    old_rows   = list(csv.DictReader(open(STAGE1_CSV)))
    target_set = {(r["bug_id"], str(r["label"]))
                  for r in old_rows
                  if r["status"] == "NO_MUTANTS" and r["project"] not in SKIP_PROJECTS}

    print(f"Patches to rerun: {len(target_set)}")
    print(f"Skipping projects: {SKIP_PROJECTS}")
    print(f"JUnit3 wrapper projects: {JUNIT3_PROJECTS}")

    XML_DIR2.mkdir(parents=True, exist_ok=True)
    WORK_BASE.mkdir(parents=True, exist_ok=True)

    with open(STAGE2_CSV, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

    rerun_count = 0
    for i, item in enumerate(labeled, 1):
        bug   = item["bug_id"]
        label = str(item["label"])
        patch = item["patch_code"]

        if (bug, label) not in target_set:
            continue

        rerun_count += 1
        proj = bug.split("-")[0]
        print(f"\n[{rerun_count}] {bug} label={label}")

        row = {k: "" for k in CSV_FIELDS}
        row.update({"bug_id": bug, "project": proj, "label": label, "status": "?"})

        meta = dataset.get(bug)
        if not meta:
            row["status"] = "NO_META"; append_row(STAGE2_CSV, row); continue

        loc, start, end = meta["loc"], int(meta["start"]), int(meta["end"])
        fqcn = fqcn_from_loc(loc); row["fqcn"] = fqcn
        wdir = WORK_BASE / f"{bug}_{i}_rerun"

        try:
            if not checkout(bug, wdir):
                row["status"] = "CHECKOUT_FAIL"; append_row(STAGE2_CSV, row); continue
            if not insert_function(wdir, loc, start, end, patch):
                row["status"] = "INSERT_FAIL"; append_row(STAGE2_CSV, row); continue

            scope = changed_lines(meta["buggy"], patch, start)
            row["scope_lines"] = ";".join(map(str, sorted(scope)))
            row["scope_mode"]  = "diff"

            run(["defects4j", "compile"], cwd=wdir, timeout=600)
            src_dir, bin_classes, bin_tests = project_dirs(wdir)

            if not (wdir / bin_classes).is_dir():
                row["status"] = "COMPILE_FAIL"; append_row(STAGE2_CSV, row); continue

            cp_entries = full_test_cp(wdir)
            if not cp_entries:
                row["status"] = "NO_CP"; append_row(STAGE2_CSV, row); continue

            trig = get_tests(wdir)
            if not trig:
                row["status"] = "NO_TESTS"; append_row(STAGE2_CSV, row); continue

            xml, fails_trigger = run_pit_stage2(wdir, fqcn, trig, src_dir, cp_entries, proj)

            if fails_trigger:
                row["status"] = "FAILS_TRIGGER"
                print(f"  -> FAILS_TRIGGER (patch breaks test, overfitting confirmed)")
            else:
                k, s, n, o2, t, prms, prmsc = score_xml(xml, scope)
                if t == 0:
                    k, s, n, o2, t, prms, prmsc = score_xml(xml, set(range(start, end + 1)))
                    row["scope_mode"] = "function"
                row.update({"killed": k, "survived": s, "nocov": n, "other": o2, "total": t,
                            "prms": f"{prms:.4f}", "prmsc": f"{prmsc:.4f}",
                            "status": "OK" if (k + s + n + o2) > 0 else "NO_MUTANTS"})
                print(f"  -> {row['status']} PRMSc={prmsc:.3f} (k={k} s={s} n={n})")

        except Exception as ex:
            row["status"] = f"EXC:{ex}"
        finally:
            xml_src = wdir / "pit_report" / "mutations.xml"
            if xml_src.exists():
                shutil.copy(xml_src, XML_DIR2 / f"{bug}_{label}_{i}.xml")
            shutil.rmtree(wdir, ignore_errors=True)

        append_row(STAGE2_CSV, row)

    print(f"\nStage 2 done. Reran {rerun_count} patches → {STAGE2_CSV.name}")

def stage3():
    print("\n" + "="*55)
    print("Stage 3: Merge and compute AUC")
    print("="*55)

    def load_csv(path):
        try:
            return list(csv.DictReader(open(path)))
        except FileNotFoundError:
            print(f"  [WARN] {path} not found — skipping")
            return []

    final = load_csv(STAGE1_CSV)
    nomut = load_csv(STAGE2_CSV)

    if not final:
        print(f"ERROR: {STAGE1_CSV} required"); return

    rerun_queue = defaultdict(list)
    for r in nomut:
        k  = int(r.get("killed",  0) or 0)
        s  = int(r.get("survived", 0) or 0)
        st = r["status"]
        if st in ("OK", "FAILS_TRIGGER") and (k + s > 0 or st == "FAILS_TRIGGER"):
            rerun_queue[(r["bug_id"], r["label"])].append(r)

    used, merged = defaultdict(int), []
    for r in final:
        key = (r["bug_id"], r["label"])
        if r["status"] == "NO_MUTANTS" and used[key] < len(rerun_queue[key]):
            merged.append(rerun_queue[key][used[key]])
            used[key] += 1
        else:
            merged.append(r)

    fields = list(final[0].keys())
    with open(FINAL_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(merged)
    overrides = sum(used.values())
    print(f"Merged CSV → {FINAL_CSV.name}  ({len(merged)} rows, {overrides} overrides)")

    def assessable(r):
        if r["status"] == "FAILS_TRIGGER": return True
        if r["status"] != "OK": return False
        return float(r.get("killed", 0) or 0) + float(r.get("survived", 0) or 0) > 0

    def prmsc_val(r):
        if r["status"] == "FAILS_TRIGGER": return 0.0
        return float(r.get("prmsc", 0) or 0)

    asm = [r for r in merged if assessable(r)]
    n_correct = sum(1 for r in asm if r["label"] == "1")
    n_overfit = sum(1 for r in asm if r["label"] == "0")

    y = [1 if r["label"] == "0" else 0 for r in asm]
    s = [-prmsc_val(r) for r in asm]
    auc = roc_auc_score(y, s)

    np.random.seed(RANDOM_SEED)
    boot_aucs = []
    for _ in range(BOOTSTRAP_N):
        idx  = np.random.choice(len(y), len(y), replace=True)
        ys   = [y[i] for i in idx]
        ss   = [s[i] for i in idx]
        if len(set(ys)) > 1:
            boot_aucs.append(roc_auc_score(ys, ss))
    ci_lo, ci_hi = np.percentile(boot_aucs, [2.5, 97.5])

    print()
    print("=" * 50)
    print("SRepair PReMS Results")
    print("=" * 50)
    print(f"Assessable patches : {len(asm)}")
    print(f"  Correct  (label=1): {n_correct}")
    print(f"  Overfit  (label=0): {n_overfit}")
    print()
    print(f"AUC    = {auc:.3f}")
    print(f"95% CI = [{ci_lo:.3f}, {ci_hi:.3f}]")
    
    

def main():
    labeled = json.load(open(LABELED))
    dataset = json.load(open(DATASET))

    args = sys.argv[1:]

    if not args:
        stage1(labeled, dataset)
        stage2(labeled, dataset)
        stage3()
        return

    if args[0] == "--stage1":
        stage1(labeled, dataset); return
    if args[0] == "--stage2":
        stage2(labeled, dataset); return
    if args[0] == "--stage3":
        stage3(); return

    single = args[0]
    stage1(labeled, dataset, single_bug=single)

if __name__ == "__main__":
    main()