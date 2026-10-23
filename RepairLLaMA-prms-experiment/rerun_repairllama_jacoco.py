#!/usr/bin/env python3

import os, csv, re, shutil, subprocess, argparse, xml.etree.ElementTree as ET
from pathlib import Path

WORKSPACE    = Path("/workspaces/RepairLLaMA-prms-experiment")
DATASET_DIR  = WORKSPACE / "apr-overfitting-benchmark/datasets/repairllama/context_size_3_filtered"
BUGGY_DIR    = WORKSPACE / "buggy_checkouts"
JACOCO_LIBS  = WORKSPACE / "jacoco-libs"
JACOCO_AGENT = JACOCO_LIBS / "jacocoagent.jar"
JACOCO_CLI   = JACOCO_LIBS / "jacococli.jar"
WORKDIR      = WORKSPACE / "jacoco_rerun_workdirs"
REPORTS_BASE = WORKSPACE / "jacoco_rerun_reports"
OUTPUT       = WORKSPACE / "repairllama_jacoco_rerun.csv"

ANT_PROJECTS = {"Chart", "Closure", "Time", "Mockito"}

FIELDNAMES = ["bug_id","project","label","patch_file","fqcn","patched_lines",
              "scope_mode","line_coverage","branch_coverage",
              "patched_line_coverage","status"]

BASE_ENV = dict(os.environ)
BASE_ENV["JAVA_TOOL_OPTIONS"] = "-Dfile.encoding=UTF-8"

RERUN_PATCHES = [
    "Chart_20_repairllama-ir4_patch_0.diff",
    "Chart_26_repairllama-ir4_patch_8.diff",
    "Chart_4_repairllama-ir4_patch_2.diff",
    "Closure_10_repairllama-ir4_patch_1.diff",
    "Closure_10_repairllama-ir4_patch_8.diff",
    "Closure_13_repairllama-ir4_patch_6.diff",
    "Closure_57_repairllama-ir4_patch_1.diff",
    "Closure_62_repairllama-ir4_patch_0.diff",
    "Closure_62_repairllama-ir4_patch_5.diff",
    "Closure_73_repairllama-ir4_patch_0.diff",
    "Closure_73_repairllama-ir4_patch_3.diff",
    "Closure_73_repairllama-ir4_patch_5.diff",
    "Closure_86_repairllama-ir4_patch_0.diff",
    "Closure_86_repairllama-ir4_patch_1.diff",
    "Closure_86_repairllama-ir4_patch_4.diff",
    "Closure_86_repairllama-ir4_patch_5.diff",
    "Closure_86_repairllama-ir4_patch_6.diff",
    "Closure_86_repairllama-ir4_patch_7.diff",
    "Closure_86_repairllama-ir4_patch_8.diff",
    "Closure_86_repairllama-ir4_patch_9.diff",
    "Math_11_repairllama-ir4_patch_0.diff",
    "Math_11_repairllama-ir4_patch_1.diff",
    "Math_11_repairllama-ir4_patch_5.diff",
    "Math_11_repairllama-ir4_patch_6.diff",
    "Math_11_repairllama-ir4_patch_9.diff",
    "Math_2_repairllama-ir4_patch_3.diff",
    "Math_28_repairllama-ir4_patch_2.diff",
    "Math_30_repairllama-ir4_patch_4.diff",
    "Math_30_repairllama-ir4_patch_5.diff",
    "Math_32_repairllama-ir4_patch_2.diff",
    "Math_32_repairllama-ir4_patch_5.diff",
    "Math_32_repairllama-ir4_patch_7.diff",
    "Math_32_repairllama-ir4_patch_9.diff",
    "Math_34_repairllama-ir4_patch_0.diff",
    "Math_34_repairllama-ir4_patch_1.diff",
    "Math_34_repairllama-ir4_patch_2.diff",
    "Math_34_repairllama-ir4_patch_3.diff",
    "Math_50_repairllama-ir4_patch_4.diff",
    "Math_8_repairllama-ir4_patch_6.diff",
    "Math_84_repairllama-ir4_patch_1.diff",
]

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
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
        return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"

def strip_crlf(path):
    p = Path(path)
    data = p.read_bytes()
    crlf = bytes([13, 10])
    if crlf in data:
        p.write_bytes(data.replace(crlf, bytes([10])))

def extract_patched_lines(diff_path):
    patched = {}
    deleted = {}
    current_file = None
    new_line = 0
    old_line = 0

    with open(diff_path, "r", errors="replace") as f:
        for line in f:
            if line.startswith("+++ "):
                p = re.match(r"\+\+\+ (.+)", line).group(1).strip()
                for prefix in ("b/", "a/"):
                    if p.startswith(prefix): p = p[len(prefix):]
                current_file = p.split("\t")[0]
                new_line = 0
                old_line = 0
            elif line.startswith("--- "):
                pass
            elif line.startswith("@@ "):
                m_new = re.search(r"\+(\d+)", line)
                m_old = re.search(r"-(\d+)", line)
                if m_new: new_line = int(m_new.group(1)) - 1
                if m_old: old_line = int(m_old.group(1)) - 1
            elif line.startswith("+") and not line.startswith("+++"):
                new_line += 1
                content = line[1:].strip()
                if content and not content.startswith("//") and not content.startswith("*"):
                    patched.setdefault(current_file, [])
                    if new_line not in patched[current_file]:
                        patched[current_file].append(new_line)
            elif line.startswith("-") and not line.startswith("---"):
                old_line += 1
                content = line[1:].strip()
                if content and not content.startswith("//") and not content.startswith("*"):
                    deleted.setdefault(current_file, [])
                    pos = new_line + 1
                    if pos not in deleted[current_file]:
                        deleted[current_file].append(pos)
            elif not line.startswith("\\"):
                new_line += 1
                old_line += 1

    for f, lines in deleted.items():
        if f not in patched or not patched[f]:
            patched[f] = lines

    return patched

def fqcn_from_path(rel_path):
    p = Path(rel_path)
    parts = list(p.parts)
    src_roots = {"source", "src", "java", "main", "test", "tests"}
    while parts and parts[0] in src_roots:
        parts = parts[1:]
    if parts:
        return ".".join(parts).removesuffix(".java")
    return Path(rel_path).stem

def get_fqcn_and_lines(diff_path, wdir):
    patched_map = extract_patched_lines(diff_path)
    if not patched_map:
        return None, None

    rel_path = max(patched_map, key=lambda k: len(patched_map[k]))
    lines    = patched_map[rel_path]
    filename = Path(rel_path).name

    java_file = None
    for candidate in wdir.rglob(filename):
        java_file = candidate
        break

    print(f"    [dbg] rel_path={rel_path} filename={filename} java_file={java_file}")

    fqcn = None

    if java_file is not None:
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
        fqcn = fqcn_from_path(rel_path)

    if not fqcn:
        fqcn = Path(rel_path).stem

    return fqcn, lines

def get_classes_dir(wdir, project):
    if project == "Closure":
        return str(wdir / "build/classes")
    elif project in ANT_PROJECTS:
        return str(wdir / "build")
    else:
        return str(wdir / "target/classes")

def get_src_dir(wdir):
    for c in ["src/main/java", "src/java", "source", "src"]:
        if (wdir / c).exists(): return str(wdir / c)
    return str(wdir)

def compile_project(wdir, project):
    if project in ANT_PROJECTS:
        rc, out, err = run("defects4j compile", cwd=wdir, timeout=900)
        return rc == 0, out + err
    else:
        pom = wdir / "pom.xml"
        if pom.exists():
            run(f"sed -i 's/<maven.compile.source>[^<]*<\\/maven.compile.source>/<maven.compile.source>1.8<\\/maven.compile.source>/' {pom}")
            run(f"sed -i 's/<maven.compile.target>[^<]*<\\/maven.compile.target>/<maven.compile.target>1.8<\\/maven.compile.target>/' {pom}")
        rc, out, err = run(
            "mvn -q compile -Dmaven.compiler.source=1.8 -Dmaven.compiler.target=1.8 -Dmaven.test.skip=true",
            cwd=wdir, timeout=900)
        if rc != 0: return False, out + err
        rc2, out2, err2 = run(
            "mvn -q test-compile -Dmaven.compiler.source=1.8 -Dmaven.compiler.target=1.8",
            cwd=wdir, timeout=900)
        return rc2 == 0, out + err + out2 + err2

def run_tests_with_jacoco(wdir, project, fqcn, exec_path):
    parts = fqcn.split(".")
    fqcn_prefix = ".".join(parts[:4]) if len(parts) >= 4 else ".".join(parts[:-1])
    test_env = make_test_env(exec_path, fqcn_prefix)
    rc, out, err = run("defects4j test -r", cwd=wdir, timeout=900, env=test_env)
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
    parts      = fqcn.split(".")
    class_name = parts[-1] + ".java"
    pkg_path   = "/".join(parts[:-1])
    sourcefile = None
    for pkg in root.iter("package"):
        if pkg.get("name", "") == pkg_path:
            for sf in pkg.findall("sourcefile"):
                if sf.get("name") == class_name:
                    sourcefile = sf; break
    if sourcefile is None:
        for sf in root.iter("sourcefile"):
            if sf.get("name") == class_name:
                sourcefile = sf; break
    if sourcefile is None:
        return None, None, None, f"CLASS_NOT_FOUND: {fqcn}"
    line_data = {int(l.get("nr", 0)): int(l.get("ci", 0)) for l in sourcefile.findall("line")}
    lc = lm = bc = bm = 0
    for c in sourcefile.findall("counter"):
        cv, ms = int(c.get("covered", 0)), int(c.get("missed", 0))
        if c.get("type") == "LINE":   lc, lm = cv, ms
        elif c.get("type") == "BRANCH": bc, bm = cv, ms
    tl = lc + lm; tb = bc + bm
    line_cov   = lc / tl if tl > 0 else 0.0
    branch_cov = bc / tb if tb > 0 else 0.0
    if patched_lines:
        patched_cov = sum(1 for ln in patched_lines if line_data.get(ln, 0) > 0) / len(patched_lines)
    else:
        patched_cov = None
    return line_cov, branch_cov, patched_cov, "OK"

def find_diff_path(patch_file, label):
    label_str = "correct" if int(label) == 1 else "overfitting"
    p = DATASET_DIR / label_str / patch_file
    if p.exists():
        return p
    for ls in ("correct", "overfitting"):
        p2 = DATASET_DIR / ls / patch_file
        if p2.exists():
            return p2
    return None

def process_patch(patch_file, bug_id, project, label):
    base = {k: "" for k in FIELDNAMES}
    base.update({"bug_id": bug_id, "project": project, "label": label,
                 "patch_file": patch_file, "scope_mode": "lines"})

    diff_path = find_diff_path(patch_file, label)
    if diff_path is None:
        return {**base, "status": "DIFF_NOT_FOUND"}

    bug_num = bug_id.split("_")[1]

    wdir = WORKDIR / patch_file.replace(".diff", "")
    if wdir.exists():
        shutil.rmtree(wdir)

    print(f"  Checking out {project}-{bug_num}b ...")
    rc, out, err = run(
        f"defects4j checkout -p {project} -v {bug_num}b -w {wdir}",
        timeout=300)
    if not wdir.exists() or rc != 0:
        return {**base, "status": "CHECKOUT_FAILED"}

    if project in ANT_PROJECTS:
        for jf in wdir.rglob("*.java"):
            strip_crlf(jf)

    diff_bytes = diff_path.read_bytes()
    if b'\r\n' in diff_bytes:
        clean_diff = diff_bytes.replace(b'\r\n', b'\n')
    else:
        clean_diff = diff_bytes

    patch_ok = False
    for strip in [1, 0, 2]:
        res = subprocess.run(
            ["patch", f"-p{strip}", "--ignore-whitespace"],
            input=clean_diff, capture_output=True, cwd=str(wdir))
        if res.returncode == 0:
            patch_ok = True
            break
    if not patch_ok:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".diff", delete=False) as tf:
            tf.write(clean_diff)
            clean_diff_path = tf.name
        try:
            rc_g, _, _ = run(f"git apply --whitespace=fix {clean_diff_path}", cwd=str(wdir), timeout=60)
            if rc_g != 0:
                rc_g2, _, _ = run(f"git apply -p0 --whitespace=fix {clean_diff_path}", cwd=str(wdir), timeout=60)
                patch_ok = (rc_g2 == 0)
            else:
                patch_ok = True
        finally:
            os.unlink(clean_diff_path)
    if not patch_ok:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "PATCH_FAILED"}

    fqcn, lines = get_fqcn_and_lines(diff_path, wdir)
    if not fqcn:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "NO_FQCN"}
    if not lines:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "NO_LINES"}

    base["fqcn"]          = fqcn
    base["patched_lines"] = ";".join(str(l) for l in sorted(lines))
    print(f"  fqcn={fqcn} lines={sorted(lines)[:5]}{'...' if len(lines)>5 else ''}")

    ok, compile_log = compile_project(wdir, project)
    if not ok:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "COMPILE_FAILED"}

    report_dir = str(REPORTS_BASE / patch_file.replace(".diff", ""))
    exec_path  = os.path.join(report_dir, "jacoco.exec")
    os.makedirs(report_dir, exist_ok=True)

    print(f"  running tests with JaCoCo ...")
    rc, out, err = run_tests_with_jacoco(wdir, project, fqcn, exec_path)
    if err == "TIMEOUT" or rc == -1:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "TIMEOUT"}

    if not os.path.exists(exec_path):
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "JACOCO_NO_EXEC"}

    xml_path, why = generate_jacoco_xml(wdir, project, exec_path, report_dir)
    if xml_path is None:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": why}

    line_cov, branch_cov, patched_cov, why = parse_jacoco_xml(xml_path, fqcn, set(lines))
    shutil.rmtree(wdir, ignore_errors=True)
    if why != "OK":
        return {**base, "status": why}

    patched_str = f"{patched_cov:.4f}" if patched_cov is not None else ""
    print(f"  OK line={line_cov:.3f} branch={branch_cov:.3f} patched={patched_str}")
    return {**base,
            "line_coverage":         f"{line_cov:.4f}",
            "branch_coverage":       f"{branch_cov:.4f}",
            "patched_line_coverage": patched_str,
            "status": "OK"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    prems_rows = {}
    prems_csv = WORKSPACE / "repairllama_prms_results.csv"
    with open(prems_csv) as f:
        for row in csv.DictReader(f):
            prems_rows[row["patch_file"]] = row

    WORKDIR.mkdir(parents=True, exist_ok=True)
    REPORTS_BASE.mkdir(parents=True, exist_ok=True)
    BUGGY_DIR.mkdir(parents=True, exist_ok=True)

    done = set()
    if args.resume and OUTPUT.exists():
        with open(OUTPUT) as f:
            for row in csv.DictReader(f):
                done.add(row["patch_file"])
        print(f"Resuming: {len(done)} already done")

    write_header = not OUTPUT.exists() or not args.resume
    csvfile = open(OUTPUT, "a" if args.resume else "w", newline="")
    writer  = csv.DictWriter(csvfile, fieldnames=FIELDNAMES)
    if write_header:
        writer.writeheader()

    ok_count = fail_count = 0
    total = len(RERUN_PATCHES)

    for i, patch_file in enumerate(RERUN_PATCHES, 1):
        if patch_file in done:
            print(f"[{i}/{total}] SKIP {patch_file}")
            continue

        row = prems_rows.get(patch_file, {})
        bug_id  = row.get("bug_id", "")
        project = row.get("project", "")
        label   = row.get("label", "")
        if not bug_id:
            m = re.match(r"(\w+)_(\d+)_", patch_file)
            if m:
                bug_id  = f"{m.group(1)}_{m.group(2)}"
                project = m.group(1)

        print(f"\n[{i}/{total}] {patch_file}  (label={label})")
        try:
            result = process_patch(patch_file, bug_id, project, label)
        except Exception as e:
            result = {k: "" for k in FIELDNAMES}
            result.update({"patch_file": patch_file, "bug_id": bug_id,
                           "project": project, "label": label,
                           "status": f"EXCEPTION:{str(e)[:120]}"})

        writer.writerow(result)
        csvfile.flush()

        if result["status"] == "OK":
            ok_count += 1
        else:
            fail_count += 1
            print(f"  FAILED: {result['status']}")

    csvfile.close()
    print(f"\n{'='*60}")
    print(f"DONE: OK={ok_count}  Failed={fail_count}")
    print(f"Results: {OUTPUT}")

if __name__ == "__main__":
    main()