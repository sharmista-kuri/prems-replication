#!/usr/bin/env python3

import csv
import os
import re
import sys
import glob
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter

JACOCO_LIBS = "/workspaces/wang-prms-experiment/jacoco-libs"
JACOCO_AGENT = f"{JACOCO_LIBS}/jacocoagent.jar"
JACOCO_CLI = f"{JACOCO_LIBS}/jacococli.jar"
D4J_JUNIT = "/defects4j/framework/projects/lib/junit-4.12-hamcrest-1.3.jar"
WORK_BASE = "/workspaces/wang-prms-experiment/jacoco_checkouts"
REPORTS_BASE = "/workspaces/wang-prms-experiment/jacoco_reports"

ENV = dict(os.environ)
ENV["LANG"] = "en_US.UTF-8"
ENV["LC_ALL"] = "en_US.UTF-8"
ENV["JAVA_TOOL_OPTIONS"] = "-Dfile.encoding=UTF-8"

def make_test_env(exec_path, fqcn_prefix):
    env = dict(ENV)
    env["JAVA_TOOL_OPTIONS"] = (
        f"-Dfile.encoding=UTF-8 "
        f"-javaagent:{JACOCO_AGENT}=destfile={exec_path},"
        f"append=false,includes={fqcn_prefix}.*"
    )
    return env


def run(cmd, cwd=None, timeout=900, env=None):
    if env is None:
        env = ENV
    p = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str),
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr



def parse_patch(patch_path):
    rel_path = None
    with open(patch_path, "r", errors="replace") as f:
        for ln in f:
            if ln.startswith("+++ "):
                raw = ln[4:].strip().split("\t")[0]
                raw = raw.lstrip("/")
                if raw.startswith("b/"):
                    raw = raw[2:]
                rel_path = raw
                break
    fqcn = None
    if rel_path and rel_path.endswith(".java"):
        p = rel_path
        for root in ("src/java/", "src/main/java/", "src/"):
            if p.startswith(root):
                p = p[len(root):]
                break
        fqcn = p[:-len(".java")].replace("/", ".")
    return rel_path, fqcn


def changed_lines_from_apply(work, rel_path, patch_file):
    target = os.path.join(work, rel_path)
    if not os.path.exists(target):
        return set(), False, set(), False, "TARGET_NOT_FOUND"
    backup = target + ".orig_backup"
    shutil.copy2(target, backup)
    rc, out, err = run(
        f"patch -p1 --fuzz=3 --ignore-whitespace < '{patch_file}'", cwd=work)
    if rc != 0:
        shutil.copy2(backup, target)
        os.remove(backup)
        return set(), False, set(), False, "APPLY_FAILED"
    rc2, dout, derr = run(f"diff '{backup}' '{target}'")
    os.remove(backup)
    added = set()
    removed_any = False
    del_anchors = set()
    for dl in dout.splitlines():
        m = re.match(r"^(\d+)(?:,(\d+))?([acd])(\d+)(?:,(\d+))?$", dl)
        if not m:
            continue
        op = m.group(3)
        new_start = int(m.group(4))
        new_end = int(m.group(5)) if m.group(5) else new_start
        if op in ("a", "c"):
            for n in range(new_start, new_end + 1):
                added.add(n)
        if op in ("d", "c"):
            removed_any = True
            del_anchors.add(new_start)
    return added, removed_any, del_anchors, True, "OK"



def run_tests_with_jacoco(work, exec_path, fqcn):
    parts = fqcn.split(".")
    prefix = ".".join(parts[:4]) if len(parts) >= 4 else ".".join(parts[:-1])
    test_env = make_test_env(exec_path, prefix)
    rc, out, err = run(
        "defects4j test -r",
        cwd=work,
        timeout=600,
        env=test_env
    )
    return rc, out, err


def generate_jacoco_xml(work, exec_path, report_dir, fqcn):
    os.makedirs(report_dir, exist_ok=True)
    xml_path = os.path.join(report_dir, "jacoco.xml")

    classes_dir = os.path.join(work, "target/classes")
    if not os.path.isdir(classes_dir):
        classes_dir = os.path.join(work, "build/classes")
    if not os.path.isdir(classes_dir):
        rc, out, err = run(f"find {work} -name '*.class' -path '*/classes/*' | head -1")
        if out.strip():
            classes_dir = out.strip().split("/classes/")[0] + "/classes"

    src_dir = os.path.join(work, "src/main/java")
    if not os.path.isdir(src_dir):
        src_dir = os.path.join(work, "src/java")
    if not os.path.isdir(src_dir):
        src_dir = os.path.join(work, "src")

    cmd = (
        f"java -jar {JACOCO_CLI} report {exec_path} "
        f"--classfiles {classes_dir} "
        f"--sourcefiles {src_dir} "
        f"--xml {xml_path}"
    )
    rc, out, err = run(cmd, cwd=work, timeout=120)
    if rc != 0 or not os.path.exists(xml_path):
        return None, f"JACOCO_REPORT_FAILED: {err[-200:]}"
    return xml_path, "OK"


def parse_jacoco_xml(xml_path, fqcn, patched_lines):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    parts = fqcn.split(".")
    class_name = parts[-1] + ".java"
    package_path = "/".join(parts[:-1])

    sourcefile = None
    for sf in root.iter("sourcefile"):
        if sf.get("name") == class_name:
            parent = sf.getparent() if hasattr(sf, 'getparent') else None
            sourcefile = sf
            break

    if sourcefile is None:
        for pkg in root.iter("package"):
            if pkg.get("name", "").replace("/", ".") == ".".join(parts[:-1]):
                for sf in pkg.findall("sourcefile"):
                    if sf.get("name") == class_name:
                        sourcefile = sf
                        break

    if sourcefile is None:
        return None, None, None, f"CLASS_NOT_FOUND_IN_XML: {fqcn}"

    line_data = {}
    for line_elem in sourcefile.findall("line"):
        nr = int(line_elem.get("nr", 0))
        ci = int(line_elem.get("ci", 0))
        cb = int(line_elem.get("cb", 0))
        mb = int(line_elem.get("mb", 0))
        mi = int(line_elem.get("mi", 0))
        line_data[nr] = {"ci": ci, "cb": cb, "mb": mb, "mi": mi}

    line_covered = 0
    line_missed = 0
    branch_covered = 0
    branch_missed = 0

    for counter in sourcefile.findall("counter"):
        ctype = counter.get("type")
        covered = int(counter.get("covered", 0))
        missed = int(counter.get("missed", 0))
        if ctype == "LINE":
            line_covered = covered
            line_missed = missed
        elif ctype == "BRANCH":
            branch_covered = covered
            branch_missed = missed

    total_lines = line_covered + line_missed
    line_coverage = line_covered / total_lines if total_lines > 0 else 0.0

    total_branches = branch_covered + branch_missed
    branch_coverage = branch_covered / total_branches if total_branches > 0 else 0.0

    if patched_lines:
        covered_patched = sum(
            1 for ln in patched_lines
            if line_data.get(ln, {}).get("ci", 0) > 0
        )
        patched_line_coverage = covered_patched / len(patched_lines)
    else:
        patched_line_coverage = None

    return line_coverage, branch_coverage, patched_line_coverage, "OK"


def process_patch(row):
    bug_id = row["bug_id"]
    project = row["project"]
    bug_number = row["bug_number"]
    patch_file = row["patch_file"]
    tool = row["tool"]
    label = row["label"]

    print(f"\n{'='*60}\nProcessing {bug_id} [{tool}] label={label}\n{'='*60}")

    work = os.path.join(WORK_BASE, f"{bug_id}_{tool}")
    if os.path.exists(work):
        shutil.rmtree(work)

    result = {
        "bug_id": bug_id, "project": project, "tool": tool, "label": label,
        "patch_file": patch_file, "fqcn": "", "patched_lines": "",
        "scope_mode": "",
        "line_coverage": "", "branch_coverage": "", "patched_line_coverage": "",
        "status": ""
    }

    rc, out, err = run(f"defects4j checkout -p {project} -v {bug_number}b -w {work}")
    if rc != 0:
        result["status"] = "CHECKOUT_FAILED"
        print("  checkout FAILED:", err[-300:])
        return result

    rel_path, fqcn = parse_patch(patch_file)
    if not fqcn or not rel_path:
        result["status"] = "PARSE_FAILED"
        return result
    result["fqcn"] = fqcn
    print(f"  class: {fqcn}")

    added, removed_any, del_anchors, ok, why = changed_lines_from_apply(
        work, rel_path, patch_file)
    if not ok:
        result["status"] = why
        return result

    if added:
        scope_lines = added
        scope_mode = "lines"
    elif removed_any:
        scope_lines = del_anchors
        scope_mode = "deletion"
    else:
        result["status"] = "NO_CHANGE_DETECTED"
        return result

    result["scope_mode"] = scope_mode
    result["patched_lines"] = ";".join(str(x) for x in sorted(scope_lines))
    print(f"  patched lines: {sorted(scope_lines)}")

    rc, out, err = run("defects4j compile", cwd=work)
    if rc != 0:
        result["status"] = "COMPILE_FAILED"
        print("  compile FAILED")
        return result

    rc, out, err = run("defects4j export -p tests.relevant", cwd=work)
    tests = [t.strip() for t in out.splitlines() if t.strip()]
    if not tests:
        result["status"] = "NO_TESTS"
        return result

    report_dir = os.path.join(REPORTS_BASE, f"{bug_id}_{tool}")
    os.makedirs(report_dir, exist_ok=True)
    exec_path = os.path.join(report_dir, "jacoco.exec")

    print(f"  running tests with JaCoCo...")
    rc, out, err = run_tests_with_jacoco(work, exec_path, fqcn)

    if not os.path.exists(exec_path):
        result["status"] = "JACOCO_NO_EXEC"
        print("  JaCoCo produced no .exec file")
        print("  stdout:", out[-200:])
        print("  stderr:", err[-200:])
        return result

    print(f"  .exec file: {os.path.getsize(exec_path)} bytes")

    xml_path, why = generate_jacoco_xml(work, exec_path, report_dir, fqcn)
    if xml_path is None:
        result["status"] = why
        print(f"  {why}")
        return result

    line_cov, branch_cov, patched_cov, why = parse_jacoco_xml(
        xml_path, fqcn, scope_lines)

    if why != "OK":
        result["status"] = why
        print(f"  parse failed: {why}")
        return result

    result.update({
        "line_coverage": f"{line_cov:.4f}",
        "branch_coverage": f"{branch_cov:.4f}",
        "patched_line_coverage": f"{patched_cov:.4f}" if patched_cov is not None else "",
        "status": "OK"
    })
    patched_str = f"{patched_cov:.3f}" if patched_cov is not None else "N/A"
    print(f"  line_cov={line_cov:.3f} branch_cov={branch_cov:.3f} patched_line_cov={patched_str}")
    return result


def main():
    in_csv = sys.argv[1] if len(sys.argv) > 1 else "patch_inventory.csv"
    out_csv = sys.argv[2] if len(sys.argv) > 2 else "wang_jacoco_results.csv"

    os.makedirs(WORK_BASE, exist_ok=True)
    os.makedirs(REPORTS_BASE, exist_ok=True)

    with open(in_csv) as f:
        rows = list(csv.DictReader(f))

    fieldnames = [
        "bug_id", "project", "tool", "label", "patch_file", "fqcn",
        "patched_lines", "scope_mode",
        "line_coverage", "branch_coverage", "patched_line_coverage", "status"
    ]

    with open(out_csv, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    results = []
    total_n = len(rows)
    for i, row in enumerate(rows, 1):
        print(f"\n[{i}/{total_n}]", end=" ")
        try:
            res = process_patch(row)
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            res = {**{k: "" for k in fieldnames},
                   "bug_id": row.get("bug_id", "?"),
                   "tool": row.get("tool", "?"),
                   "status": f"EXCEPTION:{e}"}
        results.append(res)

        with open(out_csv, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writerow(res)

        work = os.path.join(WORK_BASE, f"{res.get('bug_id','')}_{res.get('tool','')}")
        if os.path.isdir(work):
            shutil.rmtree(work, ignore_errors=True)

    print(f"\n\n{'='*60}\nSUMMARY\n{'='*60}")
    ok = [r for r in results if r["status"] == "OK"]
    failed = [r for r in results if r["status"] != "OK"]
    print(f"Total: {len(results)}, OK: {len(ok)}, FAILED: {len(failed)}")
    if failed:
        fc = Counter(r["status"] for r in failed)
        for s, c in fc.most_common():
            print(f"  {s}: {c}")
    print(f"\nResults written to {out_csv}")


if __name__ == "__main__":
    main()