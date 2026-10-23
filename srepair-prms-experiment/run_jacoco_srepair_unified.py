#!/usr/bin/env python3

PRMS_CSV     = "srepair_prms_results.csv"
LABELED_JSON = "srepair_labeled.json"
OUTPUT_CSV   = "srepair_jacoco_results.csv"

import os, csv, json, re, shutil, subprocess, difflib, argparse, xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict

WORKSPACE    = Path("/workspaces/srepair-prms-experiment")
DATASET_JSON = WORKSPACE / "SRepair/SRepair/dataset/defects4j-sf.json"
JACOCO_LIBS  = WORKSPACE / "jacoco-libs"
JACOCO_AGENT = JACOCO_LIBS / "jacocoagent.jar"
JACOCO_CLI   = JACOCO_LIBS / "jacococli.jar"
WORKDIR      = WORKSPACE / "jacoco_workdirs"
REPORTS_BASE = WORKSPACE / "jacoco_reports"

ANT_PROJECTS = {"Chart", "Closure", "Time", "Mockito", "JacksonDatabind"}

FIELDNAMES = ["bug_id", "project", "label", "fqcn", "scope_lines", "scope_mode",
              "line_coverage", "branch_coverage", "patched_line_coverage", "status"]

BASE_ENV = dict(os.environ)
BASE_ENV["JAVA_TOOL_OPTIONS"] = "-Dfile.encoding=UTF-8"

def make_test_env(exec_path, fqcn_prefix):
    agent_arg = (f"-javaagent:{JACOCO_AGENT}=destfile={exec_path},"
                 f"append=false,includes={fqcn_prefix}.*")
    env = dict(BASE_ENV)
    env["JAVA_TOOL_OPTIONS"] = f"-Dfile.encoding=UTF-8 {agent_arg}"
    env["ANT_OPTS"]   = agent_arg
    env["MAVEN_OPTS"] = agent_arg
    return env

def run(cmd, cwd=None, timeout=600, env=None):
    if env is None:
        env = BASE_ENV
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
        return p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"

def insert_function(wdir, loc, start, end, patch_code):
    full = Path(wdir) / loc
    if not full.exists():
        return False
    try:
        lines = full.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except Exception:
        return False
    out = []
    patched = False
    for idx, line in enumerate(lines):
        if start - 1 <= idx <= end - 1:
            if not patched:
                out.append(patch_code.strip() + "\n")
                patched = True
        else:
            out.append(line)
    if not patched:
        return False
    full.write_text("".join(out), encoding="utf-8")
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

def d4j_prop(wdir, prop, default=""):
    rc, o, e = run(["defects4j", "export", "-p", prop], cwd=wdir, timeout=120)
    val = [v.strip() for v in (o or "").strip().splitlines()
           if v.strip() and not v.startswith("Running")]
    return val[-1].strip() if val else default

def project_dirs(wdir):
    src = d4j_prop(wdir, "dir.src.classes", "src/main/java")
    bcl = d4j_prop(wdir, "dir.bin.classes", "target/classes")
    bte = d4j_prop(wdir, "dir.bin.tests",   "target/test-classes")
    return src, bcl, bte

def get_src_dir(wdir, src_rel):
    full = Path(wdir) / src_rel
    if full.exists():
        return str(full)
    for c in ["src/main/java", "src/java", "source", "src"]:
        if (Path(wdir) / c).exists():
            return str(Path(wdir) / c)
    return str(Path(wdir))

def get_classes_dir(wdir, project, bin_classes):
    full = Path(wdir) / bin_classes
    if full.exists():
        return str(full)
    if project == "Closure":
        return str(Path(wdir) / "build/classes")
    elif project in ANT_PROJECTS:
        return str(Path(wdir) / "build")
    return str(Path(wdir) / "target/classes")

def run_tests_with_jacoco(wdir, fqcn, exec_path):
    parts = fqcn.split(".")
    if parts[0] in ("org", "com", "net", "io"):
        fqcn_prefix = ".".join(parts[:3]) if len(parts) >= 3 else ".".join(parts[:-1])
    else:
        fqcn_prefix = ".".join(parts[:4]) if len(parts) >= 4 else ".".join(parts[:-1])
    test_env = make_test_env(exec_path, fqcn_prefix)
    rc, out, err = run("defects4j test", cwd=wdir, timeout=600, env=test_env)
    return rc, out, err

def generate_jacoco_xml(wdir, project, exec_path, report_dir, src_rel, bin_classes):
    os.makedirs(report_dir, exist_ok=True)
    xml_path = os.path.join(report_dir, "jacoco.xml")
    classes_dir = get_classes_dir(wdir, project, bin_classes)
    src_dir     = get_src_dir(wdir, src_rel)
    cmd = (f"java -jar {JACOCO_CLI} report {exec_path} "
           f"--classfiles {classes_dir} "
           f"--sourcefiles {src_dir} "
           f"--xml {xml_path}")
    rc, out, err = run(cmd, cwd=str(wdir), timeout=120)
    if rc != 0 or not os.path.exists(xml_path):
        return None, f"JACOCO_REPORT_FAILED: {err[-200:]}"
    return xml_path, "OK"

def parse_jacoco_xml(xml_path, fqcn, patched_lines):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    parts      = fqcn.split(".")
    class_name = parts[-1] + ".java"
    pkg_path   = "/".join(parts[:-1])

    sourcefile = None
    for pkg in root.iter("package"):
        if pkg.get("name", "") == pkg_path:
            for sf in pkg.findall("sourcefile"):
                if sf.get("name") == class_name:
                    sourcefile = sf
                    break
    if sourcefile is None:
        for sf in root.iter("sourcefile"):
            if sf.get("name") == class_name:
                sourcefile = sf
                break
    if sourcefile is None:
        return None, None, None, f"CLASS_NOT_FOUND: {fqcn}"

    line_data = {}
    for line_elem in sourcefile.findall("line"):
        nr = int(line_elem.get("nr", 0))
        ci = int(line_elem.get("ci", 0))
        line_data[nr] = ci

    line_covered = line_missed = branch_covered = branch_missed = 0
    for counter in sourcefile.findall("counter"):
        ctype   = counter.get("type")
        covered = int(counter.get("covered", 0))
        missed  = int(counter.get("missed", 0))
        if ctype == "LINE":
            line_covered, line_missed = covered, missed
        elif ctype == "BRANCH":
            branch_covered, branch_missed = covered, missed

    total_lines    = line_covered + line_missed
    line_cov       = line_covered / total_lines if total_lines > 0 else 0.0
    total_branches = branch_covered + branch_missed
    branch_cov     = branch_covered / total_branches if total_branches > 0 else 0.0

    if patched_lines:
        covered_patched  = sum(1 for ln in patched_lines if line_data.get(ln, 0) > 0)
        patched_line_cov = covered_patched / len(patched_lines)
    else:
        patched_line_cov = None

    return line_cov, branch_cov, patched_line_cov, "OK"

def process_patch(bug_id, label, patch_code, dataset, idx):
    proj = bug_id.split("-")[0]
    num  = bug_id.split("-")[1]

    base = {k: "" for k in FIELDNAMES}
    base.update({"bug_id": bug_id, "project": proj, "label": label})

    meta = dataset.get(bug_id)
    if not meta:
        return {**base, "status": "NO_META"}

    loc   = meta["loc"]
    start = int(meta["start"])
    end   = int(meta["end"])
    fqcn  = fqcn_from_loc(loc)
    base["fqcn"] = fqcn

    wdir = WORKDIR / f"{bug_id}_label{label}_{idx}"
    if wdir.exists():
        shutil.rmtree(wdir)

    rc, out, err = run(f"defects4j checkout -p {proj} -v {num}b -w {wdir}", timeout=900)
    if not wdir.exists():
        return {**base, "status": "CHECKOUT_FAIL"}

    if not insert_function(wdir, loc, start, end, patch_code):
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "INSERT_FAIL"}

    scope = changed_lines(meta["buggy"], patch_code, start)
    if not scope:
        scope = set(range(start, end + 1))
        scope_mode = "function"
    else:
        scope_mode = "diff"
    base["scope_lines"] = ";".join(map(str, sorted(scope)))
    base["scope_mode"]  = scope_mode
    print(f"  fqcn={fqcn} scope={sorted(scope)[:5]}{'...' if len(scope)>5 else ''}")

    rc, out, err = run(["defects4j", "compile"], cwd=wdir, timeout=600)
    if rc != 0:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "COMPILE_FAIL"}

    src_rel, bin_classes, bin_tests = project_dirs(wdir)
    report_dir = str(REPORTS_BASE / f"{bug_id}_{idx}")
    exec_path  = os.path.join(report_dir, "jacoco.exec")
    os.makedirs(report_dir, exist_ok=True)

    print(f"  running tests with JaCoCo...")
    rc, out, err = run_tests_with_jacoco(str(wdir), fqcn, exec_path)

    if not os.path.exists(exec_path):
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "JACOCO_NO_EXEC"}

    xml_path, why = generate_jacoco_xml(str(wdir), proj, exec_path, report_dir, src_rel, bin_classes)
    if xml_path is None:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": why}

    line_cov, branch_cov, patched_cov, why = parse_jacoco_xml(xml_path, fqcn, scope)
    shutil.rmtree(wdir, ignore_errors=True)

    if why != "OK":
        return {**base, "status": why}

    patched_str = f"{patched_cov:.4f}" if patched_cov is not None else ""
    print(f"  line={line_cov:.3f} branch={branch_cov:.3f} patched={patched_str}")

    return {**base,
            "line_coverage":         f"{line_cov:.4f}",
            "branch_coverage":       f"{branch_cov:.4f}",
            "patched_line_coverage": patched_str,
            "status": "OK"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="skip already-written rows")
    args = parser.parse_args()

    labeled_all = json.load(open(LABELED_JSON))
    patch_lookup = defaultdict(list)
    for item in labeled_all:
        patch_lookup[(item["bug_id"], str(item["label"]))].append(item["patch_code"])

    dataset = json.load(open(DATASET_JSON))

    to_run = []
    counts = defaultdict(int)
    with open(PRMS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            bug_id = row["bug_id"].strip()
            label  = str(row["label"]).strip()
            key    = (bug_id, label)
            c      = counts[key]
            codes  = patch_lookup[key]
            if codes:
                patch_code = codes[c] if c < len(codes) else codes[-1]
            else:
                patch_code = None
            to_run.append({"bug_id": bug_id, "label": label, "patch_code": patch_code})
            counts[key] += 1

    total = len(to_run)
    print(f"Patches to run (from CSV): {total}")

    WORKDIR.mkdir(exist_ok=True)
    REPORTS_BASE.mkdir(exist_ok=True)

    skip_rows = 0
    out_path  = Path(OUTPUT_CSV)
    if args.resume and out_path.exists():
        with open(out_path) as f:
            skip_rows = sum(1 for _ in csv.DictReader(f))
        print(f"Resuming: skipping first {skip_rows} patches")

    write_header = not out_path.exists() or not args.resume
    csvfile = open(OUTPUT_CSV, "a" if args.resume else "w", newline="")
    writer  = csv.DictWriter(csvfile, fieldnames=FIELDNAMES)
    if write_header:
        writer.writeheader()

    ok_count = fail_count = 0
    for i, item in enumerate(to_run, 1):
        if i <= skip_rows:
            continue
        print(f"\n[{i}/{total}] {item['bug_id']} label={item['label']}")

        if item["patch_code"] is None:
            result = {k: "" for k in FIELDNAMES}
            result.update({"bug_id": item["bug_id"], "label": item["label"],
                           "status": "NO_PATCH_CODE"})
        else:
            try:
                result = process_patch(item["bug_id"], item["label"],
                                       item["patch_code"], dataset, i)
            except Exception as e:
                result = {k: "" for k in FIELDNAMES}
                result.update({"bug_id": item["bug_id"], "label": item["label"],
                               "status": f"EXCEPTION:{str(e)[:100]}"})

        writer.writerow(result)
        csvfile.flush()

        if result["status"] == "OK":
            ok_count += 1
        else:
            fail_count += 1
            print(f"  FAILED: {result['status']}")

    csvfile.close()
    print(f"\n{'='*60}")
    print(f"SUMMARY: Total={total} OK={ok_count} Failed={fail_count}")
    print(f"Results: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()