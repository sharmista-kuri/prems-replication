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

import os as _os, pathlib as _pathlib
PIT_VER = "1.9.0"
_pit_candidates = ([_pathlib.Path(_os.environ["PIT_LIBS"])] if _os.environ.get("PIT_LIBS") else [])
_pit_candidates += [_pathlib.Path("/opt/pitest-libs"), _pathlib.Path("/workspaces/wang-prms-experiment/pitest-libs")]
PIT_LIBS = str(next((p for p in _pit_candidates if (p / f"pitest-{PIT_VER}.jar").exists()), _pit_candidates[0]))
D4J_JUNIT = "/defects4j/framework/projects/lib/junit-4.12-hamcrest-1.3.jar"
WORK_BASE = "/tmp/prms_runs"
REPORTS_BASE = "/workspaces/wang-prms-experiment/pit_reports"

ENV = dict(os.environ)
ENV["LANG"] = "en_US.UTF-8"
ENV["LC_ALL"] = "en_US.UTF-8"
ENV["JAVA_TOOL_OPTIONS"] = "-Dfile.encoding=UTF-8"


def run(cmd, cwd=None, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, env=ENV, shell=isinstance(cmd, str),
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


def method_line_range(work, rel_path, fqcn, anchor_lines):
    target = os.path.join(work, rel_path)
    if not os.path.exists(target) or not anchor_lines:
        return set()
    with open(target, "r", errors="replace") as f:
        src = f.readlines()
    anchor = min(anchor_lines)
    start = anchor
    for i in range(anchor - 1, 0, -1):
        line = src[i - 1] if i - 1 < len(src) else ""
        if re.search(r"\)\s*(throws[\w,\s\.]*)?\{\s*$", line):
            start = i
            break
    depth = 0
    seen_open = False
    end = anchor
    for i in range(start, len(src) + 1):
        line = src[i - 1]
        depth += line.count("{") - line.count("}")
        if "{" in line:
            seen_open = True
        if seen_open and depth <= 0:
            end = i
            break
    return set(range(start, end + 1))


def find_mutations_xml(report_dir):
    matches = glob.glob(os.path.join(report_dir, "**", "mutations.xml"), recursive=True)
    return matches[0] if matches else None


def compute_prms(xml_path, patched_lines):
    muts = ET.parse(xml_path).getroot().findall("mutation")
    scoped = [m for m in muts if m.find("lineNumber").text and
              int(m.find("lineNumber").text) in patched_lines]
    counts = Counter(m.get("status") for m in scoped)
    killed = counts.get("KILLED", 0)
    survived = counts.get("SURVIVED", 0)
    nocov = counts.get("NO_COVERAGE", 0)
    other = sum(v for k, v in counts.items()
                if k not in ("KILLED", "SURVIVED", "NO_COVERAGE"))
    total = killed + survived + nocov + other
    prms = killed / total if total > 0 else 0.0
    prmsc = killed / (killed + survived) if (killed + survived) > 0 else 0.0
    return killed, survived, nocov, other, total, prms, prmsc


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
        "scope_mode": "", "killed": "", "survived": "", "nocov": "", "other": "",
        "total": "", "prms": "", "prmsc": "", "status": ""
    }

    rc, out, err = run(f"defects4j checkout -p {project} -v {bug_number}b -w {work}")
    if rc != 0:
        result["status"] = "CHECKOUT_FAILED"
        print("  checkout FAILED:", err[-300:])
        return result

    rel_path, fqcn = parse_patch(patch_file)
    if not fqcn or not rel_path:
        result["status"] = "PARSE_FAILED"
        print(f"  parse FAILED: fqcn={fqcn} path={rel_path}")
        return result
    result["fqcn"] = fqcn
    print(f"  class: {fqcn}")

    added, removed_any, del_anchors, ok, why = changed_lines_from_apply(
        work, rel_path, patch_file)
    if not ok:
        result["status"] = why
        print(f"  apply FAILED: {why}")
        return result

    if added:
        scope_lines = added
        scope_mode = "lines"
        print(f"  patched lines (added/modified): {sorted(added)}")
    elif removed_any:
        scope_lines = method_line_range(work, rel_path, fqcn, del_anchors)
        scope_mode = "method"
        print(f"  removal-type patch; enclosing method lines: "
              f"{min(scope_lines) if scope_lines else '?'}-"
              f"{max(scope_lines) if scope_lines else '?'} "
              f"(anchors {sorted(del_anchors)})")
    else:
        result["status"] = "NO_CHANGE_DETECTED"
        print("  no change detected after apply")
        return result

    if not scope_lines:
        result["status"] = "NO_SCOPE_LINES"
        print("  could not determine scope lines")
        return result

    result["scope_mode"] = scope_mode
    result["patched_lines"] = ";".join(str(x) for x in sorted(scope_lines))

    rc, out, err = run("defects4j compile", cwd=work)
    if rc != 0:
        result["status"] = "COMPILE_FAILED"
        print("  compile FAILED:", (out + err)[-300:])
        return result

    rc, out, err = run("defects4j export -p tests.relevant", cwd=work)
    tests = [t.strip() for t in out.splitlines() if t.strip()]
    if not tests:
        result["status"] = "NO_TESTS"
        print("  no relevant tests")
        return result
    target_tests = ",".join(tests)

    report_dir = os.path.join(REPORTS_BASE, f"{bug_id}_{tool}")
    if os.path.exists(report_dir):
        shutil.rmtree(report_dir)
    os.makedirs(report_dir, exist_ok=True)
    cp = ":".join([
        "target/classes", "target/test-classes", D4J_JUNIT,
        f"{PIT_LIBS}/pitest-{PIT_VER}.jar",
        f"{PIT_LIBS}/pitest-entry-{PIT_VER}.jar",
        f"{PIT_LIBS}/pitest-command-line-{PIT_VER}.jar",
    ])
    src_dir = "src/java" if os.path.isdir(os.path.join(work, "src/java")) else "src/main/java"


    SLOW_CLASSES = {
        "org.apache.commons.math3.util.FastMath",
        "org.apache.commons.math3.optimization.direct.CMAESOptimizer",
    }

    
    mutators = "DEFAULTS" if fqcn in SLOW_CLASSES else "ALL"

    pit_cmd = (
        f"java -cp '{cp}' org.pitest.mutationtest.commandline.MutationCoverageReport "
        f"--reportDir {report_dir} "
        f"--targetClasses '{fqcn}' "
        f"--targetTests '{target_tests}' "
        f"--sourceDirs {src_dir} "
        f"--mutators {mutators} "
        f"--timestampedReports=false "
        f"--outputFormats XML"
    )

    rc, out, err = run(pit_cmd, cwd=work, timeout=1800)
    
    xml_path = find_mutations_xml(report_dir)
    if not xml_path:
        result["status"] = "PITEST_FAILED"
        print("  PITest produced no XML:", (out + err)[-300:])
        return result

    used_mode = scope_mode
    killed, survived, nocov, other, total, prms, prmsc = compute_prms(xml_path, scope_lines)

    if total == 0 and scope_mode == "lines":
        widened = set()
        for ln in scope_lines:
            widened.update({ln - 1, ln, ln + 1})
        k2, s2, n2, o2, t2, p2, pc2 = compute_prms(xml_path, widened)
        if t2 > 0:
            killed, survived, nocov, other, total, prms, prmsc = k2, s2, n2, o2, t2, p2, pc2
            used_mode = "lines+-1"
            print(f"  (no mutants on exact lines; widened +/-1 -> {sorted(widened)})")
        else:
            mlines = method_line_range(work, rel_path, fqcn, scope_lines)
            if mlines:
                k3, s3, n3, o3, t3, p3, pc3 = compute_prms(xml_path, mlines)
                if t3 > 0:
                    killed, survived, nocov, other, total, prms, prmsc = k3, s3, n3, o3, t3, p3, pc3
                    used_mode = "method-fallback"
                    print(f"  (no mutants near lines; fell back to enclosing method "
                          f"{min(mlines)}-{max(mlines)})")

    result.update({
        "scope_mode": used_mode,
        "killed": killed, "survived": survived, "nocov": nocov, "other": other,
        "total": total, "prms": f"{prms:.4f}", "prmsc": f"{prmsc:.4f}",
        "status": "OK" if total > 0 else "NO_MUTANTS"
    })
    print(f"  RESULT [{used_mode}]: killed={killed} survived={survived} nocov={nocov} "
          f"total={total} PRMS={prms:.3f} PRMSc={prmsc:.3f}")
    return result


def main():
    in_csv = sys.argv[1] if len(sys.argv) > 1 else "patch_inventory.csv"
    out_csv = sys.argv[2] if len(sys.argv) > 2 else "wang_prms_results.csv"

    os.makedirs(WORK_BASE, exist_ok=True)
    os.makedirs(REPORTS_BASE, exist_ok=True)

    with open(in_csv) as f:
        rows = list(csv.DictReader(f))

    fieldnames = ["bug_id", "project", "tool", "label", "patch_file", "fqcn",
                  "patched_lines", "scope_mode", "killed", "survived", "nocov",
                  "other", "total", "prms", "prmsc", "status"]

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
                   "project": row.get("project", ""),
                   "tool": row.get("tool", "?"),
                   "label": row.get("label", ""),
                   "patch_file": row.get("patch_file", ""),
                   "status": f"EXCEPTION:{e}"}
        results.append(res)

        with open(out_csv, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writerow(res)

        work = os.path.join(WORK_BASE, f"{res.get('bug_id','')}_{res.get('tool','')}")
        if os.path.isdir(work):
            shutil.rmtree(work, ignore_errors=True)

    print(f"\n\n{'='*60}\nSUMMARY\n{'='*60}")
    ok = [r for r in results if r["status"] == "OK"]
    nomut = [r for r in results if r["status"] == "NO_MUTANTS"]
    failed = [r for r in results if r["status"] not in ("OK", "NO_MUTANTS")]
    print(f"Total: {len(results)}, OK: {len(ok)}, NO_MUTANTS: {len(nomut)}, "
          f"FAILED: {len(failed)}")
    if failed:
        print("\nFailures by status:")
        fc = Counter(r["status"] for r in failed)
        for s, c in fc.most_common():
            print(f"  {s}: {c}")
    print(f"\nResults written to {out_csv}")


if __name__ == "__main__":
    main()