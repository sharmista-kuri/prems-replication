#!/usr/bin/env python3

import os, sys, csv, re, shutil, subprocess, glob
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT      = os.path.abspath(".")
PIT_LIBS = os.environ.get("PIT_LIBS", os.path.join(ROOT, "pitest-libs"))
JUNIT     = "/defects4j/framework/projects/lib/junit-4.12-hamcrest-1.3.jar"
WORK_BASE = "/tmp/prism_prms"
TIMEOUT   = 1800

ALL_TOOLS = ["tbar_gen","ar_gen","selfapr","reward_repair",
             "recoder","coconut","cure","sequencer","simfix","edits"]

CSV_FIELDS = ["tool","bug_id","project","bug_num","label","patch_file",
              "fqcns","killed","survived","nocov","total","prmsc","status","notes"]


def run(cmd, cwd=None, timeout=TIMEOUT):
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
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


def run_pit(wdir, fqcn, tests, src_dir, bin_classes, bin_tests):
    pit_jars = ":".join(glob.glob(os.path.join(PIT_LIBS, "*.jar")))
    cp = f"{bin_classes}:{bin_tests}:{JUNIT}:{pit_jars}"
    if len(tests) > 40:
        pkg = ".".join(fqcn.split(".")[:-1])
        tests = sorted(tests, key=lambda t: (0 if ".".join(t.split(".")[:-1])==pkg else 1))[:40]
    report = os.path.join(wdir, "pit_report")
    cmd = ["java", "-cp", cp,
           "org.pitest.mutationtest.commandline.MutationCoverageReport",
           "--reportDir", report,
           "--targetClasses", fqcn,
           "--targetTests", ",".join(tests),
           "--sourceDirs", src_dir,
           "--mutators", "ALL",
           "--timestampedReports=false",
           "--outputFormats", "XML"]
    rc, o, e = run(cmd, cwd=wdir, timeout=TIMEOUT)
    xml = os.path.join(report, "mutations.xml")
    return xml if os.path.exists(xml) else None

def score_xml(xml_path, scope):
    if not xml_path or not os.path.exists(xml_path):
        return None
    killed = survived = nocov = 0
    for m in ET.parse(xml_path).getroot().findall("mutation"):
        ln = int(m.findtext("lineNumber","0"))
        if scope and ln not in scope:
            continue
        st = m.get("status","")
        if st == "KILLED":      killed += 1
        elif st == "SURVIVED":  survived += 1
        elif st == "NO_COVERAGE": nocov += 1
    total = killed + survived + nocov
    denom = killed + survived
    prmsc = killed / denom if denom > 0 else None
    return {"killed":killed,"survived":survived,"nocov":nocov,
            "total":total,"prmsc":prmsc}


def process_patch(patch_path, tool, project, bug_num, label, buggy_checkout, dry_run):
    fname   = os.path.basename(patch_path)
    bug_id  = f"{project}_{bug_num}"
    base    = {"tool":tool,"bug_id":bug_id,"project":project,"bug_num":bug_num,
               "label":label,"patch_file":fname,"fqcns":"",
               "killed":"","survived":"","nocov":"","total":"","prmsc":"",
               "status":"","notes":""}

    try:
        file_entries = parse_diff(patch_path)
    except Exception as e:
        return {**base, "status":"PARSE_FAILED", "notes":str(e)[:200]}

    if not file_entries:
        return {**base, "status":"PARSE_FAILED", "notes":"no Java files in diff"}

    fqcns_str = "|".join(e["fqcn"] for e in file_entries)
    base["fqcns"] = fqcns_str

    if dry_run:
        return {**base, "status":"DRY_RUN"}

    wdir = os.path.join(WORK_BASE, f"{bug_id}_{fname[:40]}")
    if os.path.isdir(wdir):
        shutil.rmtree(wdir, ignore_errors=True)
    shutil.copytree(buggy_checkout, wdir)

    try:
        with open(patch_path, "rb") as fh:
            res = subprocess.run(["patch","-p1"], stdin=fh,
                                 capture_output=True, cwd=wdir)
        if res.returncode != 0:
            return {**base, "status":"PATCH_FAILED",
                    "notes": res.stderr.decode(errors="replace")[:200]}

        rc, o, e = run(["defects4j","compile"], cwd=wdir, timeout=600)
        src_dir, bin_classes, bin_tests = project_dirs(wdir)
        if not os.path.isdir(os.path.join(wdir, bin_classes)):
            return {**base, "status":"COMPILE_FAILED", "notes":e[:200]}

        rc, o, e = run(["defects4j","test"], cwd=wdir, timeout=300)
        if rc != 0:
            return {**base, "status":"TEST_FAILED"}

        tests = relevant_tests(wdir)
        if not tests:
            return {**base, "status":"PIT_FAILED", "notes":"no tests exported"}

        total_k = total_s = total_n = 0
        any_xml = False
        for entry in file_entries:
            fqcn  = entry["fqcn"]
            scope = entry["changed_lines"]
            xml   = run_pit(wdir, fqcn, tests, src_dir, bin_classes, bin_tests)
            r     = score_xml(xml, scope)
            if r is None:
                continue
            any_xml = True
            total_k += r["killed"]
            total_s += r["survived"]
            total_n += r["nocov"]

        if not any_xml:
            return {**base, "status":"PIT_FAILED", "notes":"mutations.xml not found"}

        total = total_k + total_s + total_n
        denom = total_k + total_s
        if denom == 0:
            return {**base, "killed":total_k,"survived":total_s,"nocov":total_n,
                    "total":total,"status":"NO_MUTANTS"}

        prmsc = round(total_k / denom, 6)
        return {**base, "killed":total_k,"survived":total_s,"nocov":total_n,
                "total":total,"prmsc":prmsc,"status":"OK"}

    finally:
        shutil.rmtree(wdir, ignore_errors=True)


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--prism-dir",  required=True)
    p.add_argument("--buggy-dir",  required=True)
    p.add_argument("--output",     default="prism_prms_results.csv")
    p.add_argument("--projects",   nargs="+", default=["Math","Lang"])
    p.add_argument("--tools",      nargs="+", default=ALL_TOOLS)
    p.add_argument("--bugs",       nargs="+", default=[],
                   help="e.g. Math:85 Lang:33")
    p.add_argument("--resume",     action="store_true")
    p.add_argument("--dry-run",    action="store_true")
    args = p.parse_args()

    prism_base = os.path.join(args.prism_dir, "experiment","benchmarks","rq1")
    os.makedirs(args.buggy_dir, exist_ok=True)
    os.makedirs(WORK_BASE, exist_ok=True)

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
    ok = fail = 0

    for i, (patch_path, tool, project, bug_num) in enumerate(patches, 1):
        fname = os.path.basename(patch_path)
        print(f"[{i}/{total}] {fname}")

        key = (project, bug_num)
        if key not in checkout_cache:
            checkout_cache[key] = ensure_checkout(project, bug_num, args.buggy_dir)
        buggy_checkout = checkout_cache[key]

        if buggy_checkout is None:
            row = {**{k:"" for k in CSV_FIELDS},
                   "tool":tool,"bug_id":f"{project}_{bug_num}","project":project,
                   "bug_num":bug_num,"label":-1,"patch_file":fname,
                   "status":"CHECKOUT_FAILED"}
            writer.writerow(row); out.flush(); fail+=1; continue

        try:
            _, _, _, label = parse_filename(fname)
        except ValueError:
            label = -1

        result = process_patch(patch_path, tool, project, bug_num, label,
                               buggy_checkout, args.dry_run)
        writer.writerow(result)
        out.flush()

        if result["status"] == "OK":
            ok += 1
            print(f"  ✓ PRMSc={result['prmsc']}  killed={result['killed']} survived={result['survived']}")
        else:
            fail += 1
            print(f"  ✗ {result['status']}: {result.get('notes','')[:100]}")

    out.close()
    print(f"\nDone. Total={total} OK={ok} Failed={fail}")
    print(f"Results: {args.output}")
    

if __name__ == "__main__":
    main()