import csv, os, re, shutil, subprocess, ast, xml.etree.ElementTree as ET
from pathlib import Path

WORKSPACE    = Path("/workspaces/D4C-prms-experiment")
D4C_DIR      = WORKSPACE / "D4C"
BUGGY_DIR    = WORKSPACE / "d4c_buggy"
WORKDIR      = WORKSPACE / "jacoco_workdirs_fix"
REPORTS_BASE = WORKSPACE / "jacoco_reports"
JACOCO_AGENT = WORKSPACE / "jacoco-libs/jacocoagent.jar"
JACOCO_CLI   = WORKSPACE / "jacoco-libs/jacococli.jar"
OUTPUT       = WORKSPACE / "d4c_jacoco_results.csv"
D4C_FINAL    = WORKSPACE / "d4c_prms_results.csv"

BASE_ENV = dict(os.environ)
BASE_ENV["JAVA_TOOL_OPTIONS"] = "-Dfile.encoding=UTF-8"

TARGETS = {
    "Chart_1":  ("Chart", "1"),
    "Chart_6":  ("Chart", "6"),
    "Chart_13": ("Chart", "13"),
    "Chart_24": ("Chart", "24"),
    "Time_18":  ("Time",  "18"),
    "Math_58":  ("Math",  "58"),
    "Mockito_5":  ("Mockito",  "5"),
    "Math_58":  ("Math",  "58"),
    "Math_97":  ("Math",  "97"),
}

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

def fqcn_to_file(wdir, fqcn, project):
    parts = fqcn.split(".")
    rel = "/".join(parts) + ".java"
    for src in ["source", "src/main/java", "src/java", "src"]:
        f = Path(wdir) / src / rel
        if f.exists():
            return f
    return None

def apply_patch(java_file, bug_code, fix_code):
    content = java_file.read_text(errors="ignore")
    file_lines = content.splitlines()
    bug_lines = bug_code.strip().splitlines()
    n = len(bug_lines)

    if bug_code.strip() in content:
        java_file.write_text(content.replace(bug_code.strip(), fix_code.strip(), 1))
        return True

    bug_r = [l.rstrip() for l in bug_lines]
    for i in range(len(file_lines) - n + 1):
        if [l.rstrip() for l in file_lines[i:i+n]] == bug_r:
            new = "\n".join(file_lines[:i] + fix_code.strip().splitlines() + file_lines[i+n:]) + "\n"
            java_file.write_text(new); return True

    bug_s = [l.strip() for l in bug_lines]
    for i in range(len(file_lines) - n + 1):
        if [l.strip() for l in file_lines[i:i+n]] == bug_s:
            new = "\n".join(file_lines[:i] + fix_code.strip().splitlines() + file_lines[i+n:]) + "\n"
            java_file.write_text(new); return True

    sig = bug_lines[0].strip()
    for i in range(len(file_lines)):
        if sig in file_lines[i]:
            depth = 0
            end = i
            for j in range(i, len(file_lines)):
                depth += file_lines[j].count("{") - file_lines[j].count("}")
                if depth > 0 or j == i:
                    end = j
                if depth == 0 and j > i:
                    end = j; break
            new = "\n".join(file_lines[:i] + fix_code.strip().splitlines() + file_lines[end+1:]) + "\n"
            java_file.write_text(new); return True

    return False

def run_tests_with_jacoco(wdir, fqcn, exec_path):
    parts = fqcn.split(".")
    prefix = ".".join(parts[:3]) if len(parts) >= 3 else ".".join(parts[:-1])
    env = dict(BASE_ENV)
    env["JAVA_TOOL_OPTIONS"] = (
        f"-Dfile.encoding=UTF-8 "
        f"-javaagent:{JACOCO_AGENT}=destfile={exec_path},"
        f"append=false,includes={prefix}.*"
    )
    return run("defects4j test -r", cwd=str(wdir), timeout=600, env=env)

def generate_xml(wdir, project, exec_path, report_dir):
    os.makedirs(report_dir, exist_ok=True)
    xml_path = os.path.join(report_dir, "jacoco.xml")
    rc, src, _ = run("defects4j export -p dir.src.classes", cwd=str(wdir))
    src = src.strip() or ("source" if project == "Chart" else "src/main/java")
    rc, bc, _ = run("defects4j export -p dir.bin.classes", cwd=str(wdir))
    bc = bc.strip() or "target/classes"
    cmd = (f"java -jar {JACOCO_CLI} report {exec_path} "
           f"--classfiles {wdir}/{bc} --sourcefiles {wdir}/{src} --xml {xml_path}")
    rc, out, err = run(cmd, cwd=str(wdir), timeout=120)
    if rc != 0 or not os.path.exists(xml_path):
        return None
    return xml_path

def parse_xml(xml_path, fqcn, abs_lines):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    parts = fqcn.split(".")
    class_name = parts[-1] + ".java"
    pkg_path   = "/".join(parts[:-1])
    sf = None
    for pkg in root.iter("package"):
        if pkg.get("name","") == pkg_path:
            for s in pkg.findall("sourcefile"):
                if s.get("name") == class_name:
                    sf = s; break
    if sf is None:
        for s in root.iter("sourcefile"):
            if s.get("name") == class_name:
                sf = s; break
    if sf is None:
        return None, None, None
    line_data = {int(l.get("nr",0)): int(l.get("ci",0)) for l in sf.findall("line")}
    lc = lm = bc = bm = 0
    for c in sf.findall("counter"):
        cv, ms = int(c.get("covered",0)), int(c.get("missed",0))
        if c.get("type")=="LINE": lc,lm = cv,ms
        elif c.get("type")=="BRANCH": bc,bm = cv,ms
    tl, tb = lc+lm, bc+bm
    line_cov   = lc/tl if tl>0 else 0.0
    branch_cov = bc/tb if tb>0 else 0.0
    patched_cov = sum(1 for ln in abs_lines if line_data.get(ln,0)>0)/len(abs_lines) if abs_lines else None
    return line_cov, branch_cov, patched_cov

def main():
    with open(D4C_DIR/"archive/defects4j/pred_d4c_gpt_archived.csv") as f:
        patches = {r["slug"]: r for r in csv.DictReader(f)}
    with open(D4C_DIR/"archive/defects4j/correct_id.txt") as f:
        correct_ids = set(l.strip() for l in f if l.strip())

    with open(D4C_FINAL) as f:
        final_rows = {r["slug"]: r for r in csv.DictReader(f)}

    WORKDIR.mkdir(exist_ok=True)
    BUGGY_DIR.mkdir(exist_ok=True)

    fieldnames = ["slug","project","label","fqcn","abs_lines",
                  "line_coverage","branch_coverage","patched_line_coverage","status"]

    for slug, (project, bug_id) in TARGETS.items():
        label = 1 if slug in correct_ids else 0
        patch = patches.get(slug, {})
        bug_code = patch.get("bug","").strip()
        fix_code = patch.get("fix","").strip()

        final = final_rows.get(slug, {})
        fqcn  = final.get("fqcn","")
        abs_lines_str = final.get("abs_lines","")
        try:
            abs_lines = ast.literal_eval(abs_lines_str) if abs_lines_str else []
        except:
            abs_lines = []

        print(f"\n--- {slug} label={label} fqcn={fqcn} ---")

        base = {"slug":slug,"project":project,"label":label,"fqcn":fqcn,
                "abs_lines":abs_lines_str,"line_coverage":"","branch_coverage":"",
                "patched_line_coverage":"","status":""}

        if not fqcn or not bug_code:
            row = {**base, "status":"NO_DATA"}
            _write(OUTPUT, fieldnames, row); continue

        checkout_dir = BUGGY_DIR / f"{slug}_buggy"
        if not checkout_dir.exists():
            print(f"  Checking out {project}-{bug_id}b...")
            rc, out, err = run(f"defects4j checkout -p {project} -v {bug_id}b -w {checkout_dir}", timeout=600)
            if not checkout_dir.exists():
                _write(OUTPUT, fieldnames, {**base,"status":"CHECKOUT_FAILED"}); continue

        wdir = WORKDIR / slug
        if wdir.exists(): shutil.rmtree(wdir)
        shutil.copytree(checkout_dir, wdir)

        if project in {"Chart","Closure","Time","Mockito"}:
            for jf in wdir.rglob("*.java"):
                data = jf.read_bytes()
                if b"\r\n" in data: jf.write_bytes(data.replace(b"\r\n",b"\n"))

        java_file = fqcn_to_file(wdir, fqcn, project)
        if not java_file:
            print(f"  File not found for {fqcn}")
            _write(OUTPUT, fieldnames, {**base,"status":"FILE_NOT_FOUND"}); continue
        print(f"  Found: {java_file.name}")

        if not apply_patch(java_file, bug_code, fix_code):
            print(f"  PATCH_APPLY_FAILED")
            _write(OUTPUT, fieldnames, {**base,"status":"PATCH_APPLY_FAILED"}); continue
        print(f"  Patch applied")

        rc, out, err = run("defects4j compile", cwd=str(wdir), timeout=600)
        if rc != 0:
            _write(OUTPUT, fieldnames, {**base,"status":"COMPILE_FAILED"}); continue

        report_dir = str(REPORTS_BASE / slug)
        exec_path  = os.path.join(report_dir, "jacoco.exec")
        os.makedirs(report_dir, exist_ok=True)

        print(f"  Running tests with JaCoCo...")
        rc, out, err = run_tests_with_jacoco(wdir, fqcn, exec_path)
        if not os.path.exists(exec_path):
            _write(OUTPUT, fieldnames, {**base,"status":"JACOCO_NO_EXEC"}); continue

        xml_path = generate_xml(wdir, project, exec_path, report_dir)
        if not xml_path:
            _write(OUTPUT, fieldnames, {**base,"status":"JACOCO_REPORT_FAILED"}); continue

        line_cov, branch_cov, patched_cov = parse_xml(xml_path, fqcn, abs_lines)
        shutil.rmtree(wdir, ignore_errors=True)

        if line_cov is None:
            _write(OUTPUT, fieldnames, {**base,"status":"CLASS_NOT_FOUND"}); continue

        patched_str = f"{patched_cov:.4f}" if patched_cov is not None else ""
        print(f"  line={line_cov:.3f} branch={branch_cov:.3f} patched={patched_str}")
        row = {**base, "line_coverage":f"{line_cov:.4f}",
               "branch_coverage":f"{branch_cov:.4f}",
               "patched_line_coverage":patched_str, "status":"OK"}
        _write(OUTPUT, fieldnames, row)

    print("\nDone.")

def _write(path, fields, row):
    with open(path, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=fields).writerow(row)

if __name__ == "__main__":
    main()