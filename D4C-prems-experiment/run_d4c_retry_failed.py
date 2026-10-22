#!/usr/bin/env python3

import csv, os, subprocess, difflib, re, shutil, sys
from pathlib import Path

D4C_DIR   = Path("/workspaces/D4C-prms-experiment/D4C")
WORKDIR   = Path("/tmp/d4c_retry_workdirs")
BUGGY_DIR = Path("/tmp/d4c_retry_buggy")
PIT_LIB   = Path("/pit-lib")

OUTPUT = Path("/workspaces/D4C-prms-experiment/d4c_prms_results.csv")

PIT_JARS = [
    "pitest-1.6.7.jar",
    "pitest-entry-1.6.7.jar",
    "pitest-command-line-1.6.7.jar",
]

FAILED_TARGETS = {
    "Chart_1":   ("Chart", "1"),
    "Chart_6":   ("Chart", "6"),
    "Chart_9":   ("Chart", "9"),
    "Chart_11":  ("Chart", "11"),
    "Chart_24":  ("Chart", "24"),
    "Closure_2":   ("Closure", "2"),
    "Closure_5":   ("Closure", "5"),
    "Closure_19":  ("Closure", "19"),
    "Closure_36":  ("Closure", "36"),
    "Closure_38":  ("Closure", "38"),
    "Closure_44":  ("Closure", "44"),
    "Closure_52":  ("Closure", "52"),
    "Closure_56":  ("Closure", "56"),
    "Closure_57":  ("Closure", "57"),
    "Closure_62":  ("Closure", "62"),
    "Closure_64":  ("Closure", "64"),
    "Closure_65":  ("Closure", "65"),
    "Closure_66":  ("Closure", "66"),
    "Closure_73":  ("Closure", "73"),
    "Closure_77":  ("Closure", "77"),
    "Closure_92":  ("Closure", "92"),
    "Closure_97":  ("Closure", "97"),
    "Closure_104": ("Closure", "104"),
    "Closure_114": ("Closure", "114"),
    "Closure_124": ("Closure", "124"),
    "Closure_125": ("Closure", "125"),
    "Closure_126": ("Closure", "126"),
    "Closure_129": ("Closure", "129"),
    "Closure_152": ("Closure", "152"),
    "Closure_166": ("Closure", "166"),
    "Lang_9":   ("Lang", "9"),
    "Lang_18":  ("Lang", "18"),
    "Math_5":   ("Math", "5"),
    "Math_11":  ("Math", "11"),
    "Math_32":  ("Math", "32"),
    "Math_60":  ("Math", "60"),
    "Math_80":  ("Math", "80"),
    "Math_82":  ("Math", "82"),
    "Mockito_5":  ("Mockito", "5"),
    "Mockito_24": ("Mockito", "24"),
    "Mockito_29": ("Mockito", "29"),
    "Time_7":   ("Time", "7"),
}


def load_d4c():
    with open(D4C_DIR / "archive/defects4j/correct_id.txt") as f:
        correct_ids = set(l.strip() for l in f if l.strip())
    with open(D4C_DIR / "archive/defects4j/pred_d4c_gpt_archived.csv") as f:
        patches = {r["slug"]: r for r in csv.DictReader(f)}
    return correct_ids, patches

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
                if method_name and re.search(
                        r'\b' + re.escape(method_name) + r'\s*\(', content):
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
                    fqcn = str(rel).replace("/", ".").replace(
                        "\\", ".").removesuffix(".java")
                    candidates.append((score, fqcn, java_file))
            except Exception:
                continue

    if not candidates:
        return None, None
    candidates.sort(reverse=True)
    if len(candidates) > 1:
        print(f"  [FQCN candidates: "
              f"{', '.join(f'{c[1]}(score={c[0]})' for c in candidates[:3])}]")
    return candidates[0][1], candidates[0][2]


def find_fqcn_with_hint(project_dir, bug_code, trigger_tests=None):
    fqcn, java_file = find_fqcn(project_dir, bug_code)
    if not fqcn:
        return None, None

    if trigger_tests:
        for test_class in trigger_tests:
            test_simple = test_class.split(".")[-1]
            expected = re.sub(r'Tests?$', '', test_simple)
            if expected and expected.lower() not in fqcn.split(".")[-1].lower():
                sig = extract_method_signature(bug_code).strip()
                src_dirs = [
                    project_dir / "src" / "main" / "java",
                    project_dir / "src" / "java",
                    project_dir / "src",
                    project_dir / "source",
                ]
                for src_dir in src_dirs:
                    if not src_dir.exists():
                        continue
                    for candidate_file in src_dir.rglob(f"{expected}.java"):
                        try:
                            content2 = candidate_file.read_text(errors="ignore")
                            if sig in content2:
                                rel = candidate_file.relative_to(src_dir)
                                new_fqcn = str(rel).replace("/", ".").replace(
                                    "\\", ".").removesuffix(".java")
                                print(f"  [FQCN hint override: {fqcn} -> {new_fqcn}]")
                                return new_fqcn, candidate_file
                        except Exception:
                            continue
    return fqcn, java_file

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
            new = "\n".join(
                file_lines[:i] + fix_code.strip().splitlines() + file_lines[i+n:]
            ) + "\n"
            java_file.write_text(new)
            return True

    bug_stripped = [l.strip() for l in bug_lines_raw]
    for i in range(len(file_lines) - n + 1):
        if [l.strip() for l in file_lines[i:i+n]] == bug_stripped:
            new = "\n".join(
                file_lines[:i] + fix_code.strip().splitlines() + file_lines[i+n:]
            ) + "\n"
            java_file.write_text(new)
            return True

    sig = extract_method_signature(bug_code).strip()
    last = bug_lines_raw[-1].strip()
    for i in range(len(file_lines) - n + 1):
        if sig in file_lines[i] and file_lines[i+n-1].strip() == last:
            interior_bug  = [l.strip() for l in bug_lines_raw[1:-1]]
            interior_file = [l.strip() for l in file_lines[i+1:i+n-1]]
            if len(interior_bug) == len(interior_file):
                matches = sum(1 for a, b in zip(interior_bug, interior_file)
                              if a == b)
                if matches / max(len(interior_bug), 1) >= 0.6:
                    new = "\n".join(
                        file_lines[:i] + fix_code.strip().splitlines() +
                        file_lines[i+n:]
                    ) + "\n"
                    java_file.write_text(new)
                    return True

    return False

def get_trigger_tests(project_dir):
    try:
        result = subprocess.check_output(
            ["defects4j", "export", "-p", "tests.trigger"],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
        classes = set()
        for line in result.splitlines():
            line = line.strip()
            if "::" in line:
                classes.add(line.split("::")[0])
            elif line:
                classes.add(line)
        return sorted(classes)
    except Exception:
        return None

def run_pit(project_dir, fqcn, patched_lines_abs, project, bug_id,
            trigger_tests=None, timeout=900):
    try:
        cp_compile = subprocess.check_output(
            ["defects4j","export","-p","cp.compile"],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        cp_test = subprocess.check_output(
            ["defects4j","export","-p","cp.test"],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        src_dir = subprocess.check_output(
            ["defects4j","export","-p","dir.src.classes"],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        bin_classes = subprocess.check_output(
            ["defects4j","export","-p","dir.bin.classes"],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        test_classes = subprocess.check_output(
            ["defects4j","export","-p","dir.bin.tests"],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception as e:
        return None, f"defects4j export failed: {e}"

    pit_cp = ":".join(str(PIT_LIB / j) for j in PIT_JARS)
    src_path = str(project_dir / src_dir)
    bin_path  = str(project_dir / bin_classes)
    test_path = str(project_dir / test_classes)
    full_cp = f"{cp_compile}:{cp_test}:{bin_path}:{test_path}"

    target_tests = ",".join(trigger_tests) if trigger_tests else "*Test,*Tests,*TestCase"

    report_dir = WORKDIR / f"{project}_{bug_id}_retry_pit"
    if report_dir.exists():
        shutil.rmtree(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "java", "-cp", pit_cp,
        "org.pitest.mutationtest.commandline.MutationCoverageReport",
        "--reportDir", str(report_dir),
        "--targetClasses", fqcn,
        "--targetTests", target_tests,
        "--sourceDirs", src_path,
        "--mutators", "ALL",
        "--outputFormats", "XML",
        "--failWhenNoMutations", "false",
    ]
    for elem in full_cp.split(":"):
        if elem.strip():
            cmd += ["--classPath", elem.strip()]

    try:
        r = subprocess.run(cmd, cwd=project_dir,
                           capture_output=True, text=True, timeout=timeout)
        return report_dir, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return None, "PIT timeout"
    except Exception as e:
        return None, str(e)

def parse_pit_xml(report_dir, patched_lines_abs):
    xml_files = list(report_dir.glob("**/*.xml"))
    if not xml_files:
        return None
    killed = survived = nocov = 0
    patched_set = set(patched_lines_abs)
    for xml_file in xml_files:
        xml_content = xml_file.read_text(errors="ignore")
        for mutation in re.finditer(
                r"<mutation[^/].*?</mutation>", xml_content, re.DOTALL):
            m_text = mutation.group(0)
            status_m = re.search(r"status='(\w+)'", m_text)
            line_m = re.search(r"<lineNumber>(\d+)</lineNumber>", m_text)
            if not status_m or not line_m:
                continue
            ln = int(line_m.group(1))
            st = status_m.group(1)
            if ln in patched_set:
                if st == "KILLED": killed += 1
                elif st == "SURVIVED": survived += 1
                elif st in ("NO_COVERAGE","NOT_COVERED"): nocov += 1
    total = killed + survived + nocov
    prmsc = killed / (killed + survived) if (killed + survived) > 0 else None
    return {"killed": killed, "survived": survived,
            "nocov": nocov, "total": total, "prmsc": prmsc}


def main():
    targets = dict(FAILED_TARGETS)

    if len(sys.argv) > 1:
        slug_arg = sys.argv[1]
        if slug_arg not in targets:
            print(f"'{slug_arg}' not in the failed-slug list. Nothing to do.")
            return
        targets = {slug_arg: targets[slug_arg]}

    print(f"Retrying {len(targets)} failed slug(s)\n")

    correct_ids, patches = load_d4c()
    WORKDIR.mkdir(parents=True, exist_ok=True)
    BUGGY_DIR.mkdir(parents=True, exist_ok=True)

    fields = ["slug","label","fqcn","trigger_tests","rel_lines","abs_lines",
              "killed","survived","nocov","total","prmsc","status"]

    done = set()
    if OUTPUT.exists():
        with open(OUTPUT) as f:
            for r in csv.DictReader(f):
                if r.get("status") == "OK":
                    done.add(r["slug"])
        print(f"Resuming retry: {len(done)} already OK in retry CSV\n")

    def write_row(row):
        mode = "a" if OUTPUT.exists() else "w"
        with open(OUTPUT, mode, newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if mode == "w":
                w.writeheader()
            w.writerow(row)

    for slug, (project, bug_id) in targets.items():
        if slug in done:
            print(f"SKIP {slug} (already OK in retry CSV)")
            continue

        label = "1" if slug in correct_ids else "0"
        print(f"\n{'='*55}")
        print(f"{slug} [{label.upper()}]  RETRY")
        print(f"{'='*55}")

        patch = patches.get(slug, {})
        bug_code = patch.get("bug","") or ""
        fix_code = patch.get("fix","") or ""
        if not bug_code or not fix_code:
            write_row({"slug":slug,"label":label,"status":"NO_CODE"})
            continue

        rel_lines = get_changed_lines(bug_code, fix_code)
        print(f"  Relative changed lines: "
              f"{rel_lines[:8]}{'...' if len(rel_lines)>8 else ''}")
        if not rel_lines:
            write_row({"slug":slug,"label":label,"status":"NO_CHANGED_LINES"})
            continue

        project_dir = BUGGY_DIR / slug
        if project_dir.exists():
            print(f"  Removing stale checkout dir ...")
            shutil.rmtree(project_dir)

        print(f"  Checking out {project} {bug_id}b ...")
        try:
            subprocess.run(
                ["defects4j","checkout","-p",project,"-v",f"{bug_id}b",
                 "-w",str(project_dir)],
                check=True, capture_output=True, timeout=300)
        except Exception as e:
            write_row({"slug":slug,"label":label,"status":"CHECKOUT_FAILED"})
            print(f"  CHECKOUT_FAILED: {e}")
            continue

        try:
            subprocess.run(["defects4j","compile"], cwd=project_dir,
                           check=True, capture_output=True, timeout=600)
        except Exception as e:
            write_row({"slug":slug,"label":label,"status":"COMPILE_FAILED"})
            print(f"  COMPILE_FAILED: {e}")
            continue
        print("  Compile OK")

        test_bin = subprocess.check_output(
            ["defects4j","export","-p","dir.bin.tests"],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        test_bin_path = project_dir / test_bin
        if not test_bin_path.exists() or not any(test_bin_path.rglob("*.class")):
            print(f"  Compiling test classes (defects4j test) ...")
            try:
                subprocess.run(["defects4j","test"], cwd=project_dir,
                               capture_output=True, timeout=900)
                print(f"  Test classes compiled OK")
            except subprocess.TimeoutExpired:
                print(f"  WARNING: test compile timed out (continuing anyway)")
            except Exception as e:
                print(f"  WARNING: defects4j test failed: {e} (continuing anyway)")

        trigger_tests = get_trigger_tests(project_dir)
        if trigger_tests:
            print(f"  Trigger tests: {trigger_tests}")
        else:
            print(f"  Trigger tests: not found, using wildcard")

        fqcn, java_file = find_fqcn_with_hint(project_dir, bug_code, trigger_tests)
        if not fqcn:
            sig = extract_method_signature(bug_code)
            print(f"  ERROR: Java file not found (sig: {sig[:60]})")
            write_row({"slug":slug,"label":label,"status":"FQCN_NOT_FOUND"})
            continue
        print(f"  FQCN: {fqcn}")

        abs_lines = get_absolute_lines(java_file, bug_code, rel_lines)
        print(f"  Absolute lines: "
              f"{abs_lines[:8]}{'...' if len(abs_lines)>8 else ''}")

        backup = Path(str(java_file) + ".d4c_orig")
        if not backup.exists():
            shutil.copy2(java_file, backup)

        if not apply_patch(java_file, bug_code, fix_code):
            shutil.copy2(backup, java_file)
            print("  ERROR: patch apply failed")
            write_row({"slug":slug,"label":label,"fqcn":fqcn,
                       "status":"PATCH_APPLY_FAILED"})
            continue
        print("  Patch applied OK")

        try:
            subprocess.run(["defects4j","compile"], cwd=project_dir,
                           check=True, capture_output=True, timeout=600)
            print("  Recompile OK")
        except Exception as e:
            shutil.copy2(backup, java_file)
            write_row({"slug":slug,"label":label,"fqcn":fqcn,
                       "status":"RECOMPILE_FAILED"})
            print(f"  ERROR: recompile failed: {e}")
            continue

        timeout = 900
        print(f"  Running PIT (timeout={timeout}s) ...")
        report_dir, pit_log = run_pit(
            project_dir, fqcn, abs_lines, project, bug_id,
            trigger_tests, timeout=timeout)
        shutil.copy2(backup, java_file)

        if not report_dir:
            status = "PIT_TIMEOUT" if "timeout" in pit_log.lower() else "PIT_FAILED"
            write_row({"slug":slug,"label":label,"fqcn":fqcn,
                       "trigger_tests":str(trigger_tests),
                       "rel_lines":str(rel_lines),"abs_lines":str(abs_lines),
                       "status":status})
            print(f"  {status}: {pit_log[-200:]}")
            continue

        stats = parse_pit_xml(report_dir, abs_lines)
        if not stats:
            if "did not pass without mutation" in pit_log:
                status = "NOT_GREEN_SUITE"
                print("  SKIP: patch breaks test suite")
            else:
                status = "NO_XML"
                print(f"  ERROR: no XML. Log tail: {pit_log[-300:]}")
            write_row({"slug":slug,"label":label,"fqcn":fqcn,
                       "trigger_tests":str(trigger_tests),
                       "rel_lines":str(rel_lines),"abs_lines":str(abs_lines),
                       "status":status})
            continue

        prmsc_str = (f"{stats['prmsc']:.3f}"
                     if stats['prmsc'] is not None else "NO_MUTANTS")
        print(f"  killed={stats['killed']} survived={stats['survived']} "
              f"nocov={stats['nocov']} PRMSc={prmsc_str}")

        write_row({
            "slug":slug, "label":label, "fqcn":fqcn,
            "trigger_tests":str(trigger_tests),
            "rel_lines":str(rel_lines), "abs_lines":str(abs_lines),
            "killed":stats["killed"], "survived":stats["survived"],
            "nocov":stats["nocov"], "total":stats["total"],
            "prmsc":prmsc_str, "status":"OK"
        })

    print(f"\n{'='*55}")
    print(f"Retry done. Results: {OUTPUT}")
    print("Merge these into your v1 CSV manually once you're happy with them.")

if __name__ == "__main__":
    main()