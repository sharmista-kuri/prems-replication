import csv, os, re, subprocess, difflib, shutil, sys, xml.etree.ElementTree as ET
from pathlib import Path

D4C_DIR      = Path("/workspaces/D4C-prms-experiment/D4C")
WORKDIR      = Path("/workspaces/D4C-prms-experiment/jacoco_workdirs")
BUGGY_DIR    = Path("/workspaces/D4C-prms-experiment/d4c_buggy")
REPORTS_BASE = Path("/workspaces/D4C-prms-experiment/jacoco_reports")
JACOCO_LIBS  = Path("/workspaces/D4C-prms-experiment/jacoco-libs")
JACOCO_AGENT = JACOCO_LIBS / "jacocoagent.jar"
JACOCO_CLI   = JACOCO_LIBS / "jacococli.jar"
OUTPUT       = Path("/workspaces/D4C-prms-experiment/d4c_jacoco_results.csv")

ANT_PROJECTS = {"Chart", "Closure", "Time", "Mockito"}

FIELDNAMES = ["slug", "project", "label", "fqcn", "abs_lines",
              "line_coverage", "branch_coverage", "patched_line_coverage", "status"]

ALL_TARGETS = {
    "Chart_1": ("Chart","1"),   "Chart_5": ("Chart","5"),
    "Chart_6": ("Chart","6"),   "Chart_9": ("Chart","9"),
    "Chart_11":("Chart","11"),  "Chart_13":("Chart","13"),
    "Chart_24":("Chart","24"),
    "Closure_2":  ("Closure","2"),   "Closure_5":  ("Closure","5"),
    "Closure_36": ("Closure","36"),  "Closure_38": ("Closure","38"),
    "Closure_44": ("Closure","44"),  "Closure_52": ("Closure","52"),
    "Closure_56": ("Closure","56"),  "Closure_57": ("Closure","57"),
    "Closure_58": ("Closure","58"),  "Closure_61": ("Closure","61"),
    "Closure_62": ("Closure","62"),  "Closure_64": ("Closure","64"),
    "Closure_65": ("Closure","65"),  "Closure_66": ("Closure","66"),
    "Closure_73": ("Closure","73"),  "Closure_77": ("Closure","77"),
    "Closure_78": ("Closure","78"),  "Closure_92": ("Closure","92"),
    "Closure_94": ("Closure","94"),  "Closure_97": ("Closure","97"),
    "Closure_104":("Closure","104"), "Closure_114":("Closure","114"),
    "Closure_119":("Closure","119"), "Closure_124":("Closure","124"),
    "Closure_125":("Closure","125"), "Closure_126":("Closure","126"),
    "Closure_128":("Closure","128"), "Closure_129":("Closure","129"),
    "Closure_152":("Closure","152"), "Closure_161":("Closure","161"),
    "Closure_166":("Closure","166"), "Closure_19":("Closure", "19"),
    "Lang_3": ("Lang","3"),  "Lang_6": ("Lang","6"),  "Lang_9": ("Lang","9"),
    "Lang_12":("Lang","12"), "Lang_13":("Lang","13"), "Lang_14":("Lang","14"),
    "Lang_16":("Lang","16"), "Lang_17":("Lang","17"), "Lang_18":("Lang","18"),
    "Lang_21":("Lang","21"), "Lang_22":("Lang","22"), "Lang_24":("Lang","24"),
    "Lang_31":("Lang","31"), "Lang_33":("Lang","33"), "Lang_39":("Lang","39"),
    "Lang_42":("Lang","42"), "Lang_43":("Lang","43"), "Lang_45":("Lang","45"),
    "Lang_51":("Lang","51"), "Lang_52":("Lang","52"), "Lang_54":("Lang","54"),
    "Lang_55":("Lang","55"), "Lang_58":("Lang","58"), "Lang_59":("Lang","59"),
    "Lang_61":("Lang","61"),
    "Math_5": ("Math","5"),  "Math_10":("Math","10"), "Math_11":("Math","11"),
    "Math_25":("Math","25"), "Math_30":("Math","30"), "Math_32":("Math","32"),
    "Math_41":("Math","41"), "Math_57":("Math","57"), "Math_60":("Math","60"),
    "Math_65":("Math","65"), "Math_73":("Math","73"), "Math_80":("Math","80"),
    "Math_82":("Math","82"), "Math_85":("Math","85"), "Math_87":("Math","87"),
    "Math_97":("Math","97"), "Math_101":("Math","101"),"Math_102":("Math","102"),
    "Math_58":("Math","58"), "Lang_11":("Lang","11"), "Lang_44":("Lang","44"),
    "Mockito_5": ("Mockito","5"),  "Mockito_8": ("Mockito","8"),
    "Mockito_13":("Mockito","13"), "Mockito_24":("Mockito","24"),
    "Mockito_29":("Mockito","29"), "Mockito_33":("Mockito","33"),
    "Mockito_34":("Mockito","34"),
    "Time_7": ("Time","7"),  "Time_9": ("Time","9"),
    "Time_15":("Time","15"), "Time_16":("Time","16"),
    "Time_18":("Time","18"), "Time_19":("Time","19"), "Time_27":("Time","27"),
}

TEST_TARGETS = {
    "Math_60": ("Math","60"),
    "Lang_12": ("Lang","12"),
    "Lang_45": ("Lang","45"),
}

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
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
        return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"

def load_d4c():
    with open(D4C_DIR / "archive/defects4j/correct_id.txt") as f:
        correct_ids = set(l.strip() for l in f if l.strip())
    with open(D4C_DIR / "archive/defects4j/pred_d4c_gpt_archived.csv") as f:
        patches = {r["slug"]: r for r in csv.DictReader(f)}
    return correct_ids, patches

def extract_method_signature(bug_code):
    for line in bug_code.strip().splitlines():
        s = line.strip()
        if s and not s.startswith("//") and not s.startswith("*"):
            return s
    return ""

def get_body_snippet(bug_code, start=4, length=8):
    lines = bug_code.strip().splitlines()
    body = []
    for line in lines[start:start+length]:
        s = line.strip()
        if s and not s.startswith("//") and not s.startswith("*"):
            body.append(s)
    return " ".join(body)[:200]

def find_fqcn(project_dir, bug_code):
    sig = extract_method_signature(bug_code)
    method_m = re.search(
        r'(?:public|protected|private|static|final|\s)+\s+[\w\[\]<>,\s]+\s+(\w+)\s*\(', sig)
    method_name = method_m.group(1) if method_m else None
    body_snippet_early = get_body_snippet(bug_code, start=1, length=5)
    body_snippet_deep  = get_body_snippet(bug_code, start=5, length=8)
    all_tokens = re.findall(r'\b[A-Za-z][A-Za-z0-9_]{7,}\b', bug_code)
    rare_tokens = [t for t in all_tokens if len(t) > 9][:5]

    src_dirs = [
        project_dir / "src" / "main" / "java",
        project_dir / "src" / "java",
        project_dir / "src",
        project_dir / "source",
    ]
    candidates = []
    for src_dir in src_dirs:
        if not src_dir.exists():
            continue
        for java_file in src_dir.rglob("*.java"):
            try:
                content = java_file.read_text(errors="ignore")
                score = 0
                if method_name and re.search(r'\b' + re.escape(method_name) + r'\s*\(', content):
                    score += 1
                if sig.strip() in content:
                    score += 3
                if body_snippet_early and body_snippet_early[:80] in content:
                    score += 3
                if body_snippet_deep and body_snippet_deep[:80] in content:
                    score += 4
                for tok in rare_tokens:
                    if tok in content:
                        score += 1
                if score >= 3:
                    rel = java_file.relative_to(src_dir)
                    fqcn = str(rel).replace("/", ".").replace("\\", ".").removesuffix(".java")
                    candidates.append((score, fqcn, java_file))
            except Exception:
                continue

    if not candidates:
        return None, None
    candidates.sort(reverse=True)
    return candidates[0][1], candidates[0][2]

def get_absolute_lines(java_file, bug_code, relative_lines):
    sig = extract_method_signature(bug_code).strip()
    file_lines = java_file.read_text(errors="ignore").splitlines()
    offset = 0
    for i, line in enumerate(file_lines):
        if sig in line:
            offset = i
            break
    return [offset + rel for rel in relative_lines]

def apply_patch(java_file, bug_code, fix_code):
    content = java_file.read_text(errors="ignore")
    file_lines = content.splitlines()
    bug_lines_raw = bug_code.strip().splitlines()
    n = len(bug_lines_raw)

    if bug_code.strip() in content:
        java_file.write_text(content.replace(bug_code.strip(), fix_code.strip(), 1))
        return True

    bug_rstripped = [l.rstrip() for l in bug_lines_raw]
    for i in range(len(file_lines) - n + 1):
        if [l.rstrip() for l in file_lines[i:i+n]] == bug_rstripped:
            new = "\n".join(file_lines[:i] + fix_code.strip().splitlines() + file_lines[i+n:]) + "\n"
            java_file.write_text(new)
            return True

    bug_stripped = [l.strip() for l in bug_lines_raw]
    for i in range(len(file_lines) - n + 1):
        if [l.strip() for l in file_lines[i:i+n]] == bug_stripped:
            new = "\n".join(file_lines[:i] + fix_code.strip().splitlines() + file_lines[i+n:]) + "\n"
            java_file.write_text(new)
            return True

    sig = extract_method_signature(bug_code).strip()
    last = bug_lines_raw[-1].strip()
    for i in range(len(file_lines) - n + 1):
        if sig in file_lines[i] and file_lines[i+n-1].strip() == last:
            interior_bug  = [l.strip() for l in bug_lines_raw[1:-1]]
            interior_file = [l.strip() for l in file_lines[i+1:i+n-1]]
            if len(interior_bug) == len(interior_file):
                matches = sum(1 for a, b in zip(interior_bug, interior_file) if a == b)
                if matches / max(len(interior_bug), 1) >= 0.6:
                    new = "\n".join(file_lines[:i] + fix_code.strip().splitlines() + file_lines[i+n:]) + "\n"
                    java_file.write_text(new)
                    return True
    return False

def get_changed_lines(bug_code, fix_code):
    bug_lines = bug_code.splitlines(keepends=True)
    fix_lines = fix_code.splitlines(keepends=True)
    changed = []
    matcher = difflib.SequenceMatcher(None, bug_lines, fix_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            for idx in range(j1, j2):
                s = fix_lines[idx].rstrip().strip()
                if s and not s.startswith("//") and not s.startswith("*"):
                    changed.append(idx + 1)
    return sorted(set(changed))

def run_tests_with_jacoco(project_dir, fqcn, exec_path):
    parts = fqcn.split(".")
    if parts[0] in ("org", "com", "net", "io"):
        prefix = ".".join(parts[:3]) if len(parts) >= 3 else ".".join(parts[:-1])
    else:
        prefix = ".".join(parts[:4]) if len(parts) >= 4 else ".".join(parts[:-1])
    test_env = make_test_env(exec_path, prefix)
    rc, out, err = run("defects4j test -r", cwd=project_dir, timeout=600, env=test_env)
    return rc, out, err

def generate_jacoco_xml(project_dir, project, exec_path, report_dir):
    os.makedirs(report_dir, exist_ok=True)
    xml_path = os.path.join(report_dir, "jacoco.xml")

    rc, src_dir, _ = run("defects4j export -p dir.src.classes", cwd=project_dir)
    src_dir = src_dir.strip() or ("source" if project == "Chart" else "src/main/java")
    rc, bin_classes, _ = run("defects4j export -p dir.bin.classes", cwd=project_dir)
    bin_classes = bin_classes.strip() or "target/classes"

    classes_full = str(Path(project_dir) / bin_classes)
    src_full     = str(Path(project_dir) / src_dir)

    cmd = (f"java -jar {JACOCO_CLI} report {exec_path} "
           f"--classfiles {classes_full} "
           f"--sourcefiles {src_full} "
           f"--xml {xml_path}")
    rc, out, err = run(cmd, cwd=project_dir, timeout=120)
    if rc != 0 or not os.path.exists(xml_path):
        return None, f"JACOCO_REPORT_FAILED: {err[-200:]}"
    return xml_path, "OK"

def parse_jacoco_xml(xml_path, fqcn, abs_lines):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    parts      = fqcn.split(".")
    class_name = parts[-1] + ".java"
    pkg_path   = "/".join(parts[:-1])

    sf = None
    for pkg in root.iter("package"):
        if pkg.get("name", "") == pkg_path:
            for s in pkg.findall("sourcefile"):
                if s.get("name") == class_name:
                    sf = s; break
    if sf is None:
        for s in root.iter("sourcefile"):
            if s.get("name") == class_name:
                sf = s; break
    if sf is None:
        return None, None, None, f"CLASS_NOT_FOUND: {fqcn}"

    line_data = {int(l.get("nr", 0)): int(l.get("ci", 0)) for l in sf.findall("line")}
    lc = lm = bc = bm = 0
    for c in sf.findall("counter"):
        cv, ms = int(c.get("covered", 0)), int(c.get("missed", 0))
        if c.get("type") == "LINE":   lc, lm = cv, ms
        elif c.get("type") == "BRANCH": bc, bm = cv, ms

    tl = lc + lm
    tb = bc + bm
    line_cov   = lc / tl if tl > 0 else 0.0
    branch_cov = bc / tb if tb > 0 else 0.0
    patched_cov = sum(1 for ln in abs_lines if line_data.get(ln, 0) > 0) / len(abs_lines) if abs_lines else None

    return line_cov, branch_cov, patched_cov, "OK"

def process_patch(slug, project, bug_id, label, patch_row):
    base = {k: "" for k in FIELDNAMES}
    base.update({"slug": slug, "project": project, "label": label})

    bug_code = patch_row.get("bug", "")
    fix_code = patch_row.get("fix", "")
    if not bug_code or not fix_code:
        return {**base, "status": "NO_CODE"}

    checkout_dir = BUGGY_DIR / f"{slug}_buggy"
    if not checkout_dir.exists():
        print(f"  Checking out {project}-{bug_id}b ...")
        rc, out, err = run(
            f"defects4j checkout -p {project} -v {bug_id}b -w {checkout_dir}",
            timeout=600)
        if not checkout_dir.exists():
            return {**base, "status": "CHECKOUT_FAILED"}

    wdir = WORKDIR / slug
    if wdir.exists():
        shutil.rmtree(wdir)
    shutil.copytree(checkout_dir, wdir)

    if project in ANT_PROJECTS:
        for jf in wdir.rglob("*.java"):
            data = jf.read_bytes()
            if b"\r\n" in data:
                jf.write_bytes(data.replace(b"\r\n", b"\n"))

    fqcn, java_file = find_fqcn(wdir, bug_code)
    if not fqcn or not java_file:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "NO_FQCN"}
    base["fqcn"] = fqcn
    print(f"  fqcn={fqcn}")

    rel_lines = get_changed_lines(bug_code, fix_code)
    abs_lines = get_absolute_lines(java_file, bug_code, rel_lines) if rel_lines else []
    base["abs_lines"] = ";".join(map(str, abs_lines))

    if not apply_patch(java_file, bug_code, fix_code):
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "PATCH_APPLY_FAILED"}

    rc, out, err = run("defects4j compile", cwd=wdir, timeout=600)
    if rc != 0:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "COMPILE_FAILED"}

    report_dir = str(REPORTS_BASE / slug)
    exec_path  = os.path.join(report_dir, "jacoco.exec")
    os.makedirs(report_dir, exist_ok=True)

    print(f"  running tests with JaCoCo...")
    try:
        rc, out, err = run_tests_with_jacoco(str(wdir), fqcn, exec_path)
    except subprocess.TimeoutExpired:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "TIMEOUT"}

    if not os.path.exists(exec_path):
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": "JACOCO_NO_EXEC"}

    xml_path, why = generate_jacoco_xml(str(wdir), project, exec_path, report_dir)
    if xml_path is None:
        shutil.rmtree(wdir, ignore_errors=True)
        return {**base, "status": why}

    line_cov, branch_cov, patched_cov, why = parse_jacoco_xml(xml_path, fqcn, set(abs_lines))
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
    test_mode = "--test" in sys.argv
    resume    = "--resume" in sys.argv
    targets   = TEST_TARGETS if test_mode else ALL_TARGETS

    correct_ids, patches = load_d4c()
    WORKDIR.mkdir(parents=True, exist_ok=True)
    BUGGY_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_BASE.mkdir(parents=True, exist_ok=True)

    skip_rows = 0
    if resume and OUTPUT.exists():
        with open(OUTPUT) as f:
            skip_rows = sum(1 for _ in csv.DictReader(f))
        print(f"Resuming: skipping first {skip_rows} patches")

    write_header = not OUTPUT.exists() or not resume
    csvfile = open(OUTPUT, "a" if resume else "w", newline="")
    writer  = csv.DictWriter(csvfile, fieldnames=FIELDNAMES)
    if write_header:
        writer.writeheader()

    ok_count = fail_count = 0
    total = len(targets)

    for i, (slug, (project, bug_id)) in enumerate(targets.items(), 1):
        if i <= skip_rows:
            continue

        label = 1 if slug in correct_ids else 0
        patch_row = patches.get(slug)
        if not patch_row:
            print(f"[{i}/{total}] {slug} — no patch data, skipping")
            row = {k: "" for k in FIELDNAMES}
            row.update({"slug": slug, "project": project, "label": label, "status": "NO_DATA"})
            writer.writerow(row); csvfile.flush(); fail_count += 1
            continue

        print(f"\n[{i}/{total}] {slug} label={label}")
        try:
            result = process_patch(slug, project, bug_id, label, patch_row)
        except Exception as e:
            result = {k: "" for k in FIELDNAMES}
            result.update({"slug": slug, "project": project, "label": label,
                           "status": f"EXCEPTION:{str(e)[:100]}"})

        writer.writerow(result); csvfile.flush()

        if result["status"] == "OK":
            ok_count += 1
        else:
            fail_count += 1
            print(f"  FAILED: {result['status']}")

    csvfile.close()
    print(f"\n{'='*60}")
    print(f"SUMMARY: Total={total} OK={ok_count} Failed={fail_count}")
    print(f"Results: {OUTPUT}")


if __name__ == "__main__":
    main()