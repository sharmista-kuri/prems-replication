#!/usr/bin/env python3

import os, sys, csv, re, shutil, subprocess, argparse, xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter

try:
    import numpy as np
    from sklearn.metrics import roc_auc_score, matthews_corrcoef
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

WORKSPACE   = Path("/workspaces/RepairLLaMA-prms-experiment")
DATASET_DIR = WORKSPACE / "apr-overfitting-benchmark/datasets/repairllama/context_size_3_filtered"
WORKDIR     = WORKSPACE / "patch_workdirs"
OUTPUT_CSV       = WORKSPACE / "repairllama_results.csv"
OUTPUT_CSV_CLEAN = WORKSPACE / "repairllama_prms_results.csv"
PIT_LIB     = WORKSPACE / "pitest-libs"

ANT_PROJECTS          = {"Chart", "Closure", "Time", "Mockito"}
TRIGGER_ONLY_PROJECTS = {"Chart", "Closure", "Time", "Mockito"}

FIELDNAMES = ["bug_id","project","label","patch_file","fqcn",
              "patched_lines","killed","survived","nocov","total","prmsc","status"]

MISLABELED = {
    ("Math_57",  "Math_57_repairllama-ir4_patch_1.diff"),
    ("Math_57",  "Math_57_repairllama-ir4_patch_4.diff"),
    ("Math_57",  "Math_57_repairllama-ir4_patch_5.diff"),
    ("Chart_12", "Chart_12_repairllama-ir4_patch_1.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_2.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_3.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_5.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_6.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_7.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_8.diff"),
}


def run(cmd, cwd=None, timeout=1800):
    r = subprocess.run(cmd, shell=True, cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")


def strip_crlf(path):
    p = Path(path)
    data = p.read_bytes()
    crlf = bytes([13, 10])
    if crlf in data:
        p.write_bytes(data.replace(crlf, bytes([10])))


def extract_patched_lines(diff_path):
    patched     = {}
    hunk_ranges = {}
    current_file = None
    new_line = 0

    with open(diff_path, "r", errors="replace") as f:
        for line in f:
            if line.startswith("+++ "):
                p = re.match(r"\+\+\+ (.+)", line).group(1).strip()
                for prefix in ("b/", "a/"):
                    if p.startswith(prefix):
                        p = p[len(prefix):]
                current_file = p.split("\t")[0]
                new_line = 0
            elif line.startswith("--- "):
                pass
            elif line.startswith("@@ "):
                m = re.search(r"\+(\d+)(?:,(\d+))?", line)
                if m:
                    new_line = int(m.group(1)) - 1
                    count = int(m.group(2)) if m.group(2) else 1
                    hunk_ranges.setdefault(current_file, [])
                    for i in range(count):
                        hunk_ranges[current_file].append(int(m.group(1)) + i)
            elif line.startswith("+") and not line.startswith("+++"):
                new_line += 1
                text = line[1:].strip()
                if text and not text.startswith("//") and not text.startswith("*"):
                    patched.setdefault(current_file, [])
                    if new_line not in patched[current_file]:
                        patched[current_file].append(new_line)
            elif not line.startswith("-") and not line.startswith("\\"):
                new_line += 1

    if not patched and hunk_ranges:
        return hunk_ranges
    return patched


def get_fqcn_and_lines(diff_path, wdir):
    patched_map = extract_patched_lines(diff_path)
    if not patched_map:
        return None, None

    rel_path = list(patched_map.keys())[0]
    lines    = list(patched_map.values())[0]
    filename = Path(rel_path).name

    candidates = list(wdir.rglob(filename))
    if not candidates:
        parts = Path(rel_path).parts
        for depth in range(min(4, len(parts)), 0, -1):
            suffix = str(Path(*parts[-depth:]))
            candidates = [p for p in wdir.rglob("*.java")
                          if str(p).endswith(suffix.replace("/", os.sep))]
            if candidates:
                break
    if not candidates:
        return None, None

    java_file = candidates[0]
    fqcn = None
    try:
        text = java_file.read_text(errors="replace")
        m = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
        if m:
            fqcn = m.group(1) + "." + java_file.stem
    except Exception:
        pass
    if not fqcn:
        for src_root in ["src/main/java", "src/java", "source", "src"]:
            try:
                rel = java_file.relative_to(wdir / src_root)
                fqcn = str(rel).replace(os.sep, ".").removesuffix(".java")
                break
            except ValueError:
                continue
    if not fqcn:
        fqcn = java_file.stem

    return fqcn, lines


def get_classpath(wdir, project):
    _, cp_compile, _ = run("defects4j export -p cp.compile", cwd=wdir)
    _, cp_test, _    = run("defects4j export -p cp.test",    cwd=wdir)
    cp_compile = cp_compile.strip()
    cp_test    = cp_test.strip()
    if project in ANT_PROJECTS:
        if project == "Closure":
            build_main  = str(wdir / "build/classes")
            build_tests = str(wdir / "build/test")
        else:
            build_main  = str(wdir / "build")
            build_tests = str(wdir / "build-tests")
    else:
        build_main  = str(wdir / "target/classes")
        build_tests = str(wdir / "target/test-classes")
    return ":".join(filter(None, [build_main, build_tests, cp_compile, cp_test]))


def get_src_dir(wdir):
    for c in ["src/main/java", "src/java", "source", "src"]:
        if (wdir / c).exists():
            return str(wdir / c)
    return str(wdir)


def compile_project(wdir, project):
    if project in ANT_PROJECTS:
        rc, out, err = run("defects4j compile", cwd=wdir, timeout=600)
        return rc == 0, out + err
    else:
        pom = wdir / "pom.xml"
        if pom.exists():
            run(f"sed -i 's/<maven.compile.source>[^<]*<\\/maven.compile.source>/"
                f"<maven.compile.source>1.8<\\/maven.compile.source>/' {pom}")
            run(f"sed -i 's/<maven.compile.target>[^<]*<\\/maven.compile.target>/"
                f"<maven.compile.target>1.8<\\/maven.compile.target>/' {pom}")
        rc, out, err = run(
            "mvn -q compile -Dmaven.compiler.source=1.8 -Dmaven.compiler.target=1.8 "
            "-Dmaven.test.skip=true",
            cwd=wdir, timeout=600)
        if rc != 0:
            return False, out + err
        rc2, out2, err2 = run(
            "mvn -q test-compile -Dmaven.compiler.source=1.8 -Dmaven.compiler.target=1.8",
            cwd=wdir, timeout=600)
        return rc2 == 0, out + err + out2 + err2


def run_pit(wdir, project, fqcn, trigger_tests, all_tests, pit_dir):
    pit_jars = ":".join([
        str(PIT_LIB / "pitest-1.6.7.jar"),
        str(PIT_LIB / "pitest-entry-1.6.7.jar"),
        str(PIT_LIB / "pitest-command-line-1.6.7.jar"),
    ])
    cp      = get_classpath(wdir, project)
    src_dir = get_src_dir(wdir)

    if project in ("Chart", "Closure"):
        target_simple = fqcn.split(".")[-1].lower().replace("impl", "")
        relevant = [t for t in trigger_tests if target_simple in t.lower()]
        if not relevant:
            relevant = trigger_tests[:1]
        trigger_tests = relevant
        all_tests     = relevant

    ordered = trigger_tests + [t for t in all_tests if t not in trigger_tests]
    ordered_classes = list(dict.fromkeys(t.split("::")[0] for t in ordered))
    trigger_classes = list(dict.fromkeys(t.split("::")[0] for t in trigger_tests))

    if len(ordered_classes) > 50:
        ordered_classes = trigger_classes if trigger_classes else ordered_classes[:10]

    test_list = ",".join(ordered_classes)

    SLOW_CLASSES = {
        "org.jfree.chart.plot.XYPlot",
        "org.jfree.chart.JFreeChart",
        "org.apache.commons.math.util.FastMath",
        "com.google.javascript.jscomp.LightweightMessageFormatter",
        "org.jfree.chart.renderer.category.AbstractCategoryItemRenderer",
        "org.jfree.chart.axis.Axis",
        "org.jfree.data.time.TimeSeries"
    }   


    mutators = "DEFAULTS" if fqcn in SLOW_CLASSES else "ALL"

    cmd = (
        f"java -cp {pit_jars}:{cp} "
        f"org.pitest.mutationtest.commandline.MutationCoverageReport "
        f"--reportDir {pit_dir} "
        f"--targetClasses {fqcn} "
        f"--targetTests {test_list} "
        f"--sourceDirs {src_dir} "
        f"--outputFormats XML "
        f"--mutators {mutators} "
        f"--timestampedReports false "
        f"--timeoutConst 10000"
    )
    rc, out, err = run(cmd, cwd=wdir, timeout=1800)
    return rc == 0, out + err


def parse_xml(pit_dir, patched_lines):
    xml_files = list(Path(pit_dir).rglob("mutations.xml"))
    if not xml_files:
        return 0, 0, 0, 0, "NO_XML"

    killed = survived = nocov = 0
    for xf in xml_files:
        root = ET.parse(xf).getroot()
        for m in root.findall(".//mutation"):
            ln_el = m.find("lineNumber")
            st_el = m.find("status")
            ln = int(ln_el.text) if ln_el is not None else int(m.get("lineNumber", 0))
            st = (st_el.text if st_el is not None else "") or m.get("status", "")
            if ln not in patched_lines:
                continue
            if   st == "KILLED":                    killed  += 1
            elif st == "SURVIVED":                  survived += 1
            elif st in ("NO_COVERAGE","RUN_ERROR"): nocov   += 1

    total = killed + survived + nocov
    if total == 0:               return 0, 0, 0, 0, "NO_MUTANTS"
    if killed + survived == 0:   return killed, survived, nocov, total, "NO_MUTANTS"
    return killed, survived, nocov, total, "OK"


def collect_patches(bug_filter=None):
    patches = []
    for label_str, label in [("correct", 1), ("overfitting", 0)]:
        d = DATASET_DIR / label_str
        if not d.exists():
            continue
        for f in sorted(d.glob("*.diff")):
            parts  = f.stem.split("_")
            bug_id = f"{parts[0]}_{parts[1]}"
            if bug_filter and bug_id not in bug_filter:
                continue
            patches.append((bug_id, label, f))
    return patches


def load_existing(csv_path):
    done = {}
    if csv_path.exists():
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                done[(row["bug_id"], row["patch_file"])] = row["status"]
    return done


def process_patch(bug_id, label, diff_path, writer, csvfile):
    project = bug_id.split("_")[0]
    bug_num = bug_id.split("_")[1]
    wdir    = WORKDIR / f"{bug_id}_{diff_path.stem}"

    print(f"\n{'='*60}")
    print(f"  {bug_id} | {diff_path.name} | {'correct' if label==1 else 'overfitting'}")
    print(f"{'='*60}")

    def write_row(status, killed="", survived="", nocov="", total="",
                  prmsc="", fqcn="", lines=None):
        row = {
            "bug_id":       bug_id,
            "project":      project,
            "label":        label,
            "patch_file":   diff_path.name,
            "fqcn":         fqcn,
            "patched_lines": ";".join(map(str, lines)) if lines else "",
            "killed":       killed,
            "survived":     survived,
            "nocov":        nocov,
            "total":        total,
            "prmsc":        prmsc,
            "status":       status,
        }
        writer.writerow(row)
        csvfile.flush()
        print(f"  → {status} | killed={killed} survived={survived} PRMSc={prmsc}")

    if wdir.exists():
        print(f"  [REUSE] existing checkout")
    else:
        wdir.mkdir(parents=True)
        print(f"  Checking out {project} {bug_num}b ...")
        rc, _, err = run(f"defects4j checkout -p {project} -v {bug_num}b -w {wdir}")
        if rc != 0:
            write_row("CHECKOUT_FAIL")
            return

    tmp_diff = wdir / "patch.diff"
    shutil.copy(diff_path, tmp_diff)
    strip_crlf(tmp_diff)
    if project in ("Chart", "Closure"):
        for jf in wdir.rglob("*.java"):
            strip_crlf(jf)

    fqcn, lines = get_fqcn_and_lines(tmp_diff, wdir)
    if not fqcn or not lines:
        write_row("NO_LINES")
        return
    print(f"  FQCN: {fqcn}  lines: {lines}")

    print(f"  Applying patch ...")
    run(f"patch -p0 --ignore-whitespace -R --dry-run < {tmp_diff}", cwd=wdir)
    run(f"patch -p0 --ignore-whitespace -R < {tmp_diff}", cwd=wdir)
    rc, out, err = run(f"patch -p0 --ignore-whitespace < {tmp_diff}", cwd=wdir)
    if rc != 0:
        write_row("PATCH_FAIL", fqcn=fqcn, lines=lines)
        return

    print(f"  Compiling ...")
    ok, compile_out = compile_project(wdir, project)
    if not ok:
        write_row("COMPILE_FAIL", fqcn=fqcn, lines=lines)
        return

    _, trig_out, _ = run("defects4j export -p tests.trigger", cwd=wdir)
    _, all_out, _  = run("defects4j export -p tests.all",     cwd=wdir)
    trigger_tests = [t.strip() for t in trig_out.strip().splitlines() if t.strip()]
    all_tests     = [t.strip() for t in all_out.strip().splitlines()  if t.strip()]
    if not trigger_tests:
        write_row("NO_TRIGGER_TESTS", fqcn=fqcn, lines=lines)
        return
    if project in TRIGGER_ONLY_PROJECTS:
        all_tests = trigger_tests

    pit_dir = wdir / "pit-reports"
    pit_dir.mkdir(exist_ok=True)
    print(f"  Running PIT ...")
    pit_ok, pit_out = run_pit(wdir, project, fqcn, trigger_tests, all_tests, pit_dir)
    if not pit_ok:
        write_row("PIT_FAIL", fqcn=fqcn, lines=lines)
        return

    killed, survived, nocov, total, status = parse_xml(pit_dir, lines)
    prmsc = round(killed / (killed + survived), 4) if killed + survived > 0 else ""
    write_row(status, killed=killed, survived=survived, nocov=nocov,
              total=total, prmsc=prmsc, fqcn=fqcn, lines=lines)


def is_assessable(row):
    if row["status"] != "OK":
        return False
    try:
        k = float(row.get("killed") or 0)
        s = float(row.get("survived") or 0)
        return (k + s) > 0
    except Exception:
        return False


def bootstrap_auc_ci(y_true, scores, n=10000, seed=42):
    rng  = np.random.default_rng(seed)
    aucs = []
    for _ in range(n):
        idx = rng.integers(0, len(y_true), len(y_true))
        yt  = np.array(y_true)[idx]
        ys  = np.array(scores)[idx]
        if len(set(yt)) < 2:
            continue
        try:
            aucs.append(roc_auc_score(yt, ys))
        except Exception:
            pass
    lo, hi = np.percentile(aucs, [2.5, 97.5])
    return round(lo, 3), round(hi, 3)


def report_auc(label, rows):
    if not rows:
        print(f"  {label}: no assessable rows")
        return
    y = [1 if r["label"] == "0" else 0 for r in rows]
    s = [-float(r["prmsc"]) for r in rows]
    auc      = roc_auc_score(y, s)
    lo, hi   = bootstrap_auc_ci(y, s)
    preds    = [1 if float(r["prmsc"]) < 0.5 else 0 for r in rows]
    mcc      = matthews_corrcoef(y, preds)
    correct  = sum(1 for r in rows if r["label"] == "1")
    overfit  = sum(1 for r in rows if r["label"] == "0")
    print(f"  n={len(rows)} (correct={correct}, overfitting={overfit})")
    print(f"  AUC  = {auc:.3f}  95% CI [{lo}, {hi}]")
    print(f"  MCC  = {mcc:.3f}  (threshold=0.5)")


def stage2(remove_mislabeled=False):
    if not _HAS_SKLEARN:
        print("ERROR: numpy and scikit-learn are required for stage 2.")
        print("  pip3 install numpy scikit-learn")
        sys.exit(1)

    if not OUTPUT_CSV.exists():
        print(f"ERROR: {OUTPUT_CSV} not found. Run stage 1 first.")
        sys.exit(1)

    with open(OUTPUT_CSV) as f:
        rows = list(csv.DictReader(f))

    assessable = [r for r in rows if is_assessable(r)]

    print(f"{'='*55}")
    print(f"RepairLLaMA PRMS Results")
    print(f"{'='*55}")
    print(f"Total patches : {len(rows)}")
    print(f"Assessable    : {len(assessable)}")
    print()

    print("--- Raw results ---")
    report_auc("All", assessable)

    filtered = [r for r in rows
                if not (r["label"] == "0"
                        and (r["bug_id"], r["patch_file"]) in MISLABELED)]
    with open(OUTPUT_CSV_CLEAN, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(filtered)
    print(f"\nCleaned CSV written to: {OUTPUT_CSV_CLEAN}")

    filtered_assessable = [r for r in filtered if is_assessable(r)]
    removed = len(assessable) - len(filtered_assessable)
    print(f"\n--- After removing {removed} confirmed mislabeled patches ---")
    print(f"  (Math_57 ×3, Chart_12 ×1, Lang_33 ×6)")
    report_auc("Corrected", filtered_assessable)

    print(f"\n--- Per-project breakdown ---")
    projects = sorted(set(r["project"] for r in assessable))
    for proj in projects:
        proj_rows = [r for r in assessable if r["project"] == proj]
        if len(proj_rows) < 2:
            continue
        y = [1 if r["label"] == "0" else 0 for r in proj_rows]
        s = [-float(r["prmsc"]) for r in proj_rows]
        if len(set(y)) < 2:
            print(f"  {proj}: {len(proj_rows)} rows (single class, AUC N/A)")
            continue
        try:
            auc = roc_auc_score(y, s)
            print(f"  {proj}: n={len(proj_rows)}, AUC={auc:.3f}")
        except Exception:
            print(f"  {proj}: AUC error")

    print(f"\n--- Status breakdown ---")
    for s, c in sorted(Counter(r["status"] for r in rows).items()):
        print(f"  {s:25s}: {c}")


def main():
    parser = argparse.ArgumentParser(
        description="PRMS pipeline for RepairLLaMA dataset")
    parser.add_argument("--stage1", action="store_true",
                        help="Run mutation testing only (skip AUC)")
    parser.add_argument("--stage2", action="store_true",
                        help="Compute AUC only (skip mutation testing)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip already-completed patches (append to CSV)")
    parser.add_argument("--bugs", nargs="*", metavar="BUG_ID",
                        help="Only process these bug IDs (e.g. Math_57 Lang_33)")
    parser.add_argument("--remove-mislabeled", action="store_true",
                        help="In stage 2, also report AUC after removing confirmed "
                             "mislabeled overfitting patches")
    args = parser.parse_args()

    run_s1 = not args.stage2
    run_s2 = not args.stage1

    if run_s1:
        WORKDIR.mkdir(parents=True, exist_ok=True)
        bug_filter = set(args.bugs) if args.bugs else None
        patches    = collect_patches(bug_filter)
        print(f"Found {len(patches)} patches" +
              (f" for: {args.bugs}" if args.bugs else ""))

        done        = load_existing(OUTPUT_CSV) if args.resume else {}
        RETRY_STATUSES = {"PATCH_FAIL", "COMPILE_FAIL", "PIT_FAIL",
                          "NO_COVERAGE", "CHECKOUT_FAIL"}
        write_mode = "a" if (args.resume and OUTPUT_CSV.exists()) else "w"

        with open(OUTPUT_CSV, write_mode, newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=FIELDNAMES)
            if write_mode == "w":
                writer.writeheader()

            for bug_id, label, diff_path in patches:
                key = (bug_id, diff_path.name)
                if key in done:
                    status = done[key]
                    if status not in RETRY_STATUSES:
                        print(f"  [SKIP] {bug_id}/{diff_path.name} ({status})")
                        continue
                    else:
                        print(f"  [RETRY] {bug_id}/{diff_path.name} (was {status})")
                try:
                    process_patch(bug_id, label, diff_path, writer, csvfile)
                except Exception as e:
                    print(f"  ERROR processing {bug_id}/{diff_path.name}: {e}")

        print(f"\nStage 1 done. Results in: {OUTPUT_CSV}")

    if run_s2:
        print()
        stage2(remove_mislabeled=True)


if __name__ == "__main__":
    main()