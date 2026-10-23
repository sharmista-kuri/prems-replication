#!/usr/bin/env python3

import os, sys, csv, re, shutil, subprocess, argparse, xml.etree.ElementTree as ET
from pathlib import Path

WORKSPACE    = Path("/workspaces/RepairLLaMA-prms-experiment")
DATASET_DIR  = WORKSPACE / "apr-overfitting-benchmark/datasets/repairllama/context_size_3_filtered"
WORKDIR      = WORKSPACE / "jacoco_workdirs"
REPORTS_BASE = WORKSPACE / "jacoco_reports"
BUGGY_DIR    = WORKSPACE / "buggy_checkouts"
OUTPUT_CSV   = WORKSPACE / "repairllama_jacoco_results.csv"
JACOCO_LIBS  = WORKSPACE / "jacoco-libs"
JACOCO_AGENT = JACOCO_LIBS / "jacocoagent.jar"
JACOCO_CLI   = JACOCO_LIBS / "jacococli.jar"

ANT_PROJECTS = {"Chart", "Closure", "Time", "Mockito"}

FIELDNAMES = ["bug_id", "project", "label", "patch_file", "fqcn",
              "patched_lines", "scope_mode",
              "line_coverage", "branch_coverage", "patched_line_coverage",
              "status"]

BASE_ENV = dict(os.environ)
BASE_ENV["JAVA_TOOL_OPTIONS"] = "-Dfile.encoding=UTF-8"

def make_test_env(exec_path, fqcn_prefix):
    env = dict(BASE_ENV)
    env["JAVA_TOOL_OPTIONS"] = (
        f"-Dfile.encoding=UTF-8 "
        f"-javaagent:{JACOCO_AGENT}=destfile={exec_path},"
        f"append=false,includes={fqcn_prefix}.*"
    )
    return env

def run(cmd, cwd=None, timeout=600, env=None):
    if env is None:
        env = BASE_ENV
    r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, env=env,
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
    patched = {}
    current_file = None
    new_line = 0
    with open(diff_path, "r", errors="replace") as f:
        for line in f:
            if line.startswith("+++ "):
                p = re.match(r"\+\+\+ (.+)", line).group(1).strip()
                for prefix in ("b/", "a/"):
                    if p.startswith(prefix): p = p[len(prefix):]
                current_file = p.split("\t")[0]
                new_line = 0
            elif line.startswith("--- "):
                pass
            elif line.startswith("@@ "):
                m = re.search(r"\+(\d+)", line)
                if m: new_line = int(m.group(1)) - 1
            elif line.startswith("+") and not line.startswith("+++"):
                new_line += 1
                content = line[1:].strip()
                if content and not content.startswith("//") and not content.startswith("*"):
                    patched.setdefault(current_file, [])
                    if new_line not in patched[current_file]:
                        patched[current_file].append(new_line)
            elif not line.startswith("-") and not line.startswith("\\"):
                new_line += 1
    return patched

def get_fqcn_and_lines(diff_path, wdir):
    patched_map = extract_patched_lines(diff_path)
    if not patched_map:
        return None, None

    rel_path = list(patched_map.keys())[0]
    lines    = list(patched_map.values())[0]
    filename = Path(rel_path).name

    java_file = None
    for candidate in wdir.rglob(filename):
        java_file = candidate
        break
    if java_file is None:
        return None, lines

    fqcn = None
    try:
        text = java_file.read_text(errors="replace")
        m = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
        if m:
            fqcn = m.group(1) + "." + java_file.stem
    except:
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
        if (wdir / c).exists(): return str(wdir / c)
    return str(wdir)

def get_classes_dir(wdir, project):
    if project == "Closure":
        return str(wdir / "build/classes")
    elif project in ANT_PROJECTS:
        return str(wdir / "build")
    else:
        return str(wdir / "target/classes")

def compile_project(wdir, project):
    if project in ANT_PROJECTS:
        rc, out, err = run("defects4j compile", cwd=wdir, timeout=300)
        return rc == 0, out + err
    else:
        pom = wdir / "pom.xml"
        if pom.exists():
            run(f"sed -i 's/<maven.compile.source>[^<]*<\\/maven.compile.source>/<maven.compile.source>1.8<\\/maven.compile.source>/' {pom}")
            run(f"sed -i 's/<maven.compile.target>[^<]*<\\/maven.compile.target>/<maven.compile.target>1.8<\\/maven.compile.target>/' {pom}")
        rc, out, err = run(
            "mvn -q compile -Dmaven.compiler.source=1.8 -Dmaven.compiler.target=1.8 -Dmaven.test.skip=true",
            cwd=wdir, timeout=300)
        if rc != 0: return False, out + err
        rc2, out2, err2 = run(
            "mvn -q test-compile -Dmaven.compiler.source=1.8 -Dmaven.compiler.target=1.8",
            cwd=wdir, timeout=300)
        return rc2 == 0, out + err + out2 + err2

def run_tests_with_jacoco(wdir, project, fqcn, exec_path):
    parts = fqcn.split(".")
    fqcn_prefix = ".".join(parts[:4]) if len(parts) >= 4 else ".".join(parts[:-1])
    test_env = make_test_env(exec_path, fqcn_prefix)

    rc, out, err = run("defects4j test -r", cwd=wdir, timeout=600, env=test_env)
    return rc, out, err

def generate_jacoco_xml(wdir, project, exec_path, report_dir):
    os.makedirs(report_dir, exist_ok=True)
    xml_path = os.path.join(report_dir, "jacoco.xml")

    classes_dir = get_classes_dir(wdir, project)
    src_dir     = get_src_dir(wdir)

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

    parts = fqcn.split(".")
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

    line_covered = line_missed = 0
    branch_covered = branch_missed = 0
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

def collect_patches():
    patches = []
    for label_str, label in [("correct", 1), ("overfitting", 0)]:
        d = DATASET_DIR / label_str
        if not d.exists(): continue
        for f in sorted(d.glob("*.diff")):
            parts  = f.stem.split("_")
            bug_id = f"{parts[0]}_{parts[1]}"
            patches.append((bug_id, label, f))
    return patches

def process_patch(bug_id, label, diff_path):
    project = bug_id.split("_")[0]
    bug_num = bug_id.split("_")[1]
    wdir    = WORKDIR / f"{bug_id}_{diff_path.stem}"

    base = {k: "" for k in FIELDNAMES}
    base.update({"bug_id": bug_id, "project": project,
                 "label": label, "patch_file": diff_path.name})

    print(f"  project={project} bug={bug_num}")

    checkout_dir = BUGGY_DIR / f"{bug_id}_buggy"
    if not checkout_dir.exists():
        print(f"  Checking out {project}-{bug_num}b ...")
        rc, out, err = run(
            f"defects4j checkout -p {project} -v {bug_num}b -w {checkout_dir}",
            timeout=300)
        if not checkout_dir.exists():
            return {**base, "status": "CHECKOUT_FAILED"}

    if wdir.exists():
        shutil.rmtree(wdir)
    shutil.copytree(checkout_dir, wdir)

    if project in ANT_PROJECTS:
        for jf in wdir.rglob("*.java"):
            strip_crlf(jf)

    with open(diff_path, "rb") as fh:
        res = subprocess.run(["patch", "-p0", "--ignore-whitespace"],
                             stdin=fh, capture_output=True, cwd=wdir)
    if res.returncode != 0:
        with open(diff_path, "rb") as fh:
            res = subprocess.run(["patch", "-p1", "--ignore-whitespace"],
                                 stdin=fh, capture_output=True, cwd=wdir)
        if res.returncode != 0:
            return {**base, "status": "PATCH_FAILED"}

    fqcn, lines = get_fqcn_and_lines(diff_path, wdir)
    if not fqcn:
        return {**base, "status": "NO_FQCN"}
    if not lines:
        return {**base, "status": "NO_LINES"}

    base["fqcn"]          = fqcn
    base["patched_lines"] = ";".join(str(l) for l in sorted(lines))
    base["scope_mode"]    = "lines"
    print(f"  fqcn={fqcn} lines={sorted(lines)[:5]}{'...' if len(lines)>5 else ''}")

    ok, compile_log = compile_project(wdir, project)
    if not ok:
        return {**base, "status": "COMPILE_FAIL"}

    report_dir = str(REPORTS_BASE / f"{bug_id}_{diff_path.stem[:30]}")
    exec_path  = os.path.join(report_dir, "jacoco.exec")
    os.makedirs(report_dir, exist_ok=True)

    print(f"  running tests with JaCoCo...")
    try:
        rc, out, err = run_tests_with_jacoco(wdir, project, fqcn, exec_path)
    except subprocess.TimeoutExpired:
        return {**base, "status": "TIMEOUT"}

    if not os.path.exists(exec_path):
        return {**base, "status": "JACOCO_NO_EXEC"}

    xml_path, why = generate_jacoco_xml(wdir, project, exec_path, report_dir)
    if xml_path is None:
        return {**base, "status": why}

    line_cov, branch_cov, patched_cov, why = parse_jacoco_xml(
        xml_path, fqcn, set(lines))
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
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", default=str(OUTPUT_CSV))
    args = parser.parse_args()

    WORKDIR.mkdir(exist_ok=True)
    REPORTS_BASE.mkdir(exist_ok=True)
    BUGGY_DIR.mkdir(exist_ok=True)

    patches = collect_patches()
    print(f"Total patches: {len(patches)}")

    done = set()
    out_path = Path(args.output)
    if args.resume and out_path.exists():
        with open(out_path) as f:
            for row in csv.DictReader(f):
                done.add(row["patch_file"])
        print(f"Resuming: {len(done)} already done")

    write_header = not out_path.exists() or not args.resume
    csvfile = open(args.output, "a" if args.resume else "w", newline="")
    writer  = csv.DictWriter(csvfile, fieldnames=FIELDNAMES)
    if write_header:
        writer.writeheader()

    ok_count = fail_count = 0
    total = len(patches)

    for i, (bug_id, label, diff_path) in enumerate(patches, 1):
        if diff_path.name in done:
            continue
        print(f"\n[{i}/{total}] {diff_path.name}")
        try:
            result = process_patch(bug_id, label, diff_path)
        except Exception as e:
            result = {k: "" for k in FIELDNAMES}
            result.update({"bug_id": bug_id, "label": label,
                           "patch_file": diff_path.name,
                           "status": f"EXCEPTION:{str(e)[:100]}"})

        writer.writerow(result)
        csvfile.flush()

        if result["status"] == "OK":
            ok_count += 1
        else:
            fail_count += 1
            print(f"  FAILED: {result['status']}")

        wdir = WORKDIR / f"{bug_id}_{diff_path.stem}"
        if wdir.exists():
            shutil.rmtree(wdir, ignore_errors=True)

    csvfile.close()
    print(f"\n{'='*60}")
    print(f"SUMMARY: Total={total} OK={ok_count} Failed={fail_count}")
    print(f"Results: {args.output}")


if __name__ == "__main__":
    main() 