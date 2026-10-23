#!/usr/bin/env python3

import os, sys, csv, re, shutil, subprocess, glob
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter

ROOT        = os.path.abspath(".")
JACOCO_LIBS = os.path.join(ROOT, "jacoco-libs")
JACOCO_AGENT = os.path.join(JACOCO_LIBS, "jacocoagent.jar")
JACOCO_CLI   = os.path.join(JACOCO_LIBS, "jacococli.jar")
JUNIT        = "/defects4j/framework/projects/lib/junit-4.12-hamcrest-1.3.jar"
WORK_BASE    = os.path.join(ROOT, "jacoco_prism_checkouts")
REPORTS_BASE = os.path.join(ROOT, "jacoco_prism_reports")
TIMEOUT      = 1800

ALL_TOOLS = ["tbar_gen","ar_gen","selfapr","reward_repair",
             "recoder","coconut","cure","sequencer","simfix","edits"]

CSV_FIELDS = ["tool","bug_id","project","bug_num","label","patch_file",
              "fqcns","patched_lines","scope_mode",
              "line_coverage","branch_coverage","patched_line_coverage",
              "status","notes"]


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

def run(cmd, cwd=None, timeout=TIMEOUT, env=None):
    if env is None:
        env = BASE_ENV
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str),
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


def d4j_prop(wdir, prop, default=""):
    rc, o, e = run(["defects4j","export","-p",prop], cwd=wdir, timeout=120)
    val = [v for v in (o or "").strip().splitlines()
           if v and not v.startswith("Running")]
    return val[-1].strip() if val else default

def project_dirs(wdir):
    src = d4j_prop(wdir, "dir.src.classes", "src/main/java")
    bcl = d4j_prop(wdir, "dir.bin.classes", "target/classes")
    bte = d4j_prop(wdir, "dir.bin.tests",   "target/test-classes")
    return src, bcl, bte

def relevant_tests(wdir):
    rc, o, e = run(["defects4j","export","-p","tests.relevant"], cwd=wdir, timeout=120)
    tests = [t.strip() for t in o.splitlines() if t.strip()]
    if not tests:
        rc, o, e = run(["defects4j","export","-p","tests.all"], cwd=wdir, timeout=120)
        tests = [t.strip() for t in o.splitlines() if t.strip()]
    return tests


def ensure_checkout(project, bug_num, buggy_base):
    dest = os.path.join(buggy_base, f"{project}_{bug_num}_buggy")
    if os.path.isdir(dest):
        return dest
    print(f"  Checking out {project}-{bug_num}b ...")
    rc, o, e = run(["defects4j","checkout","-p",project,"-v",f"{bug_num}b","-w",dest], timeout=300)
    if not os.path.isdir(dest):
        print(f"  CHECKOUT FAILED: {e[:200]}")
        return None
    fix_pom(dest)
    return dest

def fix_pom(wdir):
    pom = os.path.join(wdir, "pom.xml")
    if not os.path.exists(pom):
        return
    text = open(pom).read()
    text = re.sub(
        r'<groupId>junit</groupId>\s*<artifactId>junit</artifactId>\s*<version>[^<]+</version>',
        '<groupId>junit</groupId><artifactId>junit</artifactId><version>4.13</version>', text)
    text = re.sub(r'<source>1\.[567]</source>', '<source>1.8</source>', text)
    text = re.sub(r'<target>1\.[567]</target>', '<target>1.8</target>', text)
    text = re.sub(
        r'(<artifactId>maven-surefire-plugin</artifactId>\s*<version>)[^<]+(</version>)',
        r'\1RELEASE\2', text)
    open(pom, "w").write(text)


def path_to_fqcn(java_path):
    m = re.search(r"src/(?:main/)?java/(.+\.java)", java_path)
    if not m:
        return ""
    return m.group(1).replace("/", ".").replace(".java", "")

def parse_diff(patch_path):
    files = []
    current_fqcn = None
    new_line = None
    with open(patch_path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("+++ "):
                path_part = line[4:].split("\t")[0].strip()
                fqcn = path_to_fqcn(path_part)
                if fqcn:
                    current_fqcn = fqcn
                    files.append({"fqcn": fqcn, "changed_lines": set()})
            elif line.startswith("@@ "):
                m = re.search(r"\+(\d+)(?:,\d+)?", line)
                if m:
                    new_line = int(m.group(1))
            elif new_line is not None and files:
                if line.startswith("+") and not line.startswith("+++"):
                    if current_fqcn == files[-1]["fqcn"]:
                        files[-1]["changed_lines"].add(new_line)
                    new_line += 1
                elif line.startswith("-"):
                    pass
                else:
                    new_line += 1
    return files

def parse_filename(fname):
    m = re.match(r"^([A-Za-z]+)-(\d+)-patch\d+-(.+?)-(correct|incorrect)-.+\.patch$", fname)
    if not m:
        raise ValueError(fname)
    return m.group(1), int(m.group(2)), m.group(3), 1 if m.group(4)=="correct" else 0


def run_tests_with_jacoco(wdir, exec_path, fqcn_prefix):
    test_env = make_test_env(exec_path, fqcn_prefix)
    rc, out, err = run(["defects4j","test","-r"], cwd=wdir, timeout=600, env=test_env)
    return rc, out, err

def generate_jacoco_xml(wdir, exec_path, report_dir):
    os.makedirs(report_dir, exist_ok=True)
    xml_path = os.path.join(report_dir, "jacoco.xml")

    src_dir, bin_classes, bin_tests = project_dirs(wdir)
    classes_dir = os.path.join(wdir, bin_classes)
    if not os.path.isdir(classes_dir):
        classes_dir = os.path.join(wdir, "target/classes")

    src_full = os.path.join(wdir, src_dir)
    if not os.path.isdir(src_full):
        src_full = os.path.join(wdir, "src/main/java")
    if not os.path.isdir(src_full):
        src_full = os.path.join(wdir, "src/java")

    cmd = (f"java -jar {JACOCO_CLI} report {exec_path} "
           f"--classfiles {classes_dir} "
           f"--sourcefiles {src_full} "
           f"--xml {xml_path}")
    rc, out, err = run(cmd, cwd=wdir, timeout=120)
    if rc != 0 or not os.path.exists(xml_path):
        return None, f"JACOCO_REPORT_FAILED: {err[-200:]}"
    return xml_path, "OK"

def parse_jacoco_xml(xml_path, fqcn, patched_lines):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    parts = fqcn.split(".")
    class_name = parts[-1] + ".java"

    sourcefile = None
    for sf in root.iter("sourcefile"):
        if sf.get("name") == class_name:
            sourcefile = sf
            break

    if sourcefile is None:
        pkg_path = "/".join(parts[:-1])
        for pkg in root.iter("package"):
            if pkg.get("name", "") == pkg_path:
                for sf in pkg.findall("sourcefile"):
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
        ctype = counter.get("type")
        covered = int(counter.get("covered", 0))
        missed = int(counter.get("missed", 0))
        if ctype == "LINE":
            line_covered, line_missed = covered, missed
        elif ctype == "BRANCH":
            branch_covered, branch_missed = covered, missed

    total_lines = line_covered + line_missed
    line_cov = line_covered / total_lines if total_lines > 0 else 0.0

    total_branches = branch_covered + branch_missed
    branch_cov = branch_covered / total_branches if total_branches > 0 else 0.0

    if patched_lines:
        covered_patched = sum(1 for ln in patched_lines if line_data.get(ln, 0) > 0)
        patched_line_cov = covered_patched / len(patched_lines)
    else:
        patched_line_cov = None

    return line_cov, branch_cov, patched_line_cov, "OK"


def process_patch(patch_path, tool, project, bug_num, label, buggy_checkout):
    fname  = os.path.basename(patch_path)
    bug_id = f"{project}_{bug_num}"
    base   = {k: "" for k in CSV_FIELDS}
    base.update({"tool": tool, "bug_id": bug_id, "project": project,
                 "bug_num": bug_num, "label": label, "patch_file": fname})

    try:
        file_entries = parse_diff(patch_path)
    except Exception as e:
        return {**base, "status": "PARSE_FAILED", "notes": str(e)[:200]}

    if not file_entries:
        return {**base, "status": "PARSE_FAILED", "notes": "no Java files in diff"}

    fqcns_str = "|".join(e["fqcn"] for e in file_entries)
    base["fqcns"] = fqcns_str

    all_patched_lines = set()
    for e in file_entries:
        all_patched_lines.update(e["changed_lines"])
    base["patched_lines"] = ";".join(str(x) for x in sorted(all_patched_lines))
    base["scope_mode"] = "lines" if all_patched_lines else "unknown"

    wdir = os.path.join(WORK_BASE, f"{bug_id}_{fname[:40]}")
    if os.path.isdir(wdir):
        shutil.rmtree(wdir, ignore_errors=True)
    shutil.copytree(buggy_checkout, wdir)

    try:
        with open(patch_path, "rb") as fh:
            res = subprocess.run(["patch", "-p1"], stdin=fh,
                                 capture_output=True, cwd=wdir)
        if res.returncode != 0:
            return {**base, "status": "PATCH_FAILED",
                    "notes": res.stderr.decode(errors="replace")[:200]}

        rc, o, e = run(["defects4j", "compile"], cwd=wdir, timeout=600)
        src_dir, bin_classes, bin_tests = project_dirs(wdir)
        if not os.path.isdir(os.path.join(wdir, bin_classes)):
            return {**base, "status": "COMPILE_FAILED", "notes": e[:200]}

        primary_fqcn = file_entries[0]["fqcn"]
        parts = primary_fqcn.split(".")
        fqcn_prefix = ".".join(parts[:4]) if len(parts) >= 4 else ".".join(parts[:-1])

        report_dir = os.path.join(REPORTS_BASE, f"{bug_id}_{tool}_{fname[:30]}")
        exec_path  = os.path.join(report_dir, "jacoco.exec")
        os.makedirs(report_dir, exist_ok=True)

        rc, out, err = run_tests_with_jacoco(wdir, exec_path, fqcn_prefix)
        if not os.path.exists(exec_path):
            return {**base, "status": "JACOCO_NO_EXEC",
                    "notes": (out + err)[-200:]}

        xml_path, why = generate_jacoco_xml(wdir, exec_path, report_dir)
        if xml_path is None:
            return {**base, "status": why}

        total_line_cov = total_branch_cov = 0.0
        n_files = 0
        all_covered_patched = 0
        total_patched = len(all_patched_lines)

        tree = ET.parse(xml_path)
        xml_root = tree.getroot()

        for entry in file_entries:
            fqcn = entry["fqcn"]
            fparts = fqcn.split(".")
            class_name = fparts[-1] + ".java"
            pkg_path = "/".join(fparts[:-1])

            sf = None
            for pkg in xml_root.iter("package"):
                if pkg.get("name", "") == pkg_path:
                    for s in pkg.findall("sourcefile"):
                        if s.get("name") == class_name:
                            sf = s
                            break
            if sf is None:
                for s in xml_root.iter("sourcefile"):
                    if s.get("name") == class_name:
                        sf = s
                        break
            if sf is None:
                continue

            line_data = {}
            for line_elem in sf.findall("line"):
                nr = int(line_elem.get("nr", 0))
                ci = int(line_elem.get("ci", 0))
                line_data[nr] = ci

            lc = lm = bc = bm = 0
            for counter in sf.findall("counter"):
                ctype = counter.get("type")
                cv = int(counter.get("covered", 0))
                ms = int(counter.get("missed", 0))
                if ctype == "LINE":
                    lc, lm = cv, ms
                elif ctype == "BRANCH":
                    bc, bm = cv, ms

            tl = lc + lm
            tb = bc + bm
            total_line_cov   += lc / tl if tl > 0 else 0.0
            total_branch_cov += bc / tb if tb > 0 else 0.0
            n_files += 1

            for ln in entry["changed_lines"]:
                if line_data.get(ln, 0) > 0:
                    all_covered_patched += 1

        if n_files == 0:
            return {**base, "status": "CLASS_NOT_FOUND_IN_XML",
                    "notes": fqcns_str}

        line_cov   = total_line_cov / n_files
        branch_cov = total_branch_cov / n_files
        patched_line_cov = all_covered_patched / total_patched if total_patched > 0 else None

        result = dict(base)
        result.update({
            "line_coverage":         f"{line_cov:.4f}",
            "branch_coverage":       f"{branch_cov:.4f}",
            "patched_line_coverage": f"{patched_line_cov:.4f}" if patched_line_cov is not None else "",
            "status": "OK"
        })
        patched_str = f"{patched_line_cov:.3f}" if patched_line_cov is not None else "N/A"
        print(f"  line={line_cov:.3f} branch={branch_cov:.3f} patched_line={patched_str}")
        return result

    finally:
        if os.path.isdir(wdir):
            shutil.rmtree(wdir, ignore_errors=True)


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--prism-dir",  required=True)
    p.add_argument("--buggy-dir",  default="buggy_checkouts")
    p.add_argument("--output",     default="prism_jacoco_results.csv")
    p.add_argument("--projects",   nargs="+", default=["Math","Lang"])
    p.add_argument("--tools",      nargs="+", default=ALL_TOOLS)
    p.add_argument("--bugs",       nargs="+", default=[],
                   help="e.g. Math:85 Lang:33")
    p.add_argument("--resume",     action="store_true")
    args = p.parse_args()

    prism_base = os.path.join(args.prism_dir, "experiment","benchmarks","rq1")
    os.makedirs(args.buggy_dir, exist_ok=True)
    os.makedirs(WORK_BASE, exist_ok=True)
    os.makedirs(REPORTS_BASE, exist_ok=True)

    bug_filter = set()
    for b in args.bugs:
        proj, num = b.split(":")
        bug_filter.add((proj, int(num)))

    done = set()
    if args.resume and os.path.exists(args.output):
        with open(args.output) as f:
            for row in csv.DictReader(f):
                done.add(row["patch_file"])
        print(f"Resuming: {len(done)} patches already done")

    write_header = not os.path.exists(args.output) or not args.resume
    out = open(args.output, "a" if args.resume else "w", newline="")
    writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
    if write_header:
        writer.writeheader()

    patches = []
    for tool in args.tools:
        for project in args.projects:
            d = os.path.join(prism_base, tool, project)
            if not os.path.isdir(d):
                continue
            for bug_dir in sorted(os.listdir(d), key=lambda x: int(x)):
                bug_num = int(bug_dir)
                if bug_filter and (project, bug_num) not in bug_filter:
                    continue
                bug_path = os.path.join(d, bug_dir)
                for pf in sorted(Path(bug_path).glob("*.patch")):
                    if pf.name not in done:
                        patches.append((str(pf), tool, project, bug_num))

    total = len(patches)
    print(f"Patches to process: {total}")

    checkout_cache = {}
    ok_count = fail_count = 0

    for i, (patch_path, tool, project, bug_num) in enumerate(patches, 1):
        fname = os.path.basename(patch_path)
        print(f"\n[{i}/{total}] {fname}")

        key = (project, bug_num)
        if key not in checkout_cache:
            checkout_cache[key] = ensure_checkout(project, bug_num, args.buggy_dir)
        buggy_checkout = checkout_cache[key]

        if buggy_checkout is None:
            row = {k: "" for k in CSV_FIELDS}
            row.update({"tool": tool, "bug_id": f"{project}_{bug_num}",
                        "project": project, "bug_num": bug_num,
                        "label": -1, "patch_file": fname,
                        "status": "CHECKOUT_FAILED"})
            writer.writerow(row); out.flush(); fail_count += 1; continue

        try:
            _, _, _, label = parse_filename(fname)
        except ValueError:
            label = -1

        result = process_patch(patch_path, tool, project, bug_num, label, buggy_checkout)
        writer.writerow(result)
        out.flush()

        if result["status"] == "OK":
            ok_count += 1
        else:
            fail_count += 1
            print(f"  FAILED: {result['status']}: {result.get('notes','')[:100]}")

    out.close()

    print(f"\n{'='*60}")
    print(f"SUMMARY: Total={total} OK={ok_count} Failed={fail_count}")
    print(f"Results written to {args.output}")


if __name__ == "__main__":
    main()