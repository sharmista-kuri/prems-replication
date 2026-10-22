#!/usr/bin/env python3

import csv, difflib, re, shutil, subprocess, sys
from pathlib import Path

D4C_DIR  = Path('/workspaces/D4C-prms-experiment/D4C')
PIT_LIB  = Path('/pit-lib')
BUGGY_DIR= Path('/tmp/d4c_regression_buggy')
WORKDIR  = Path('/tmp/d4c_regression_workdirs')
OUTPUT   = Path('/workspaces/D4C-prms-experiment/d4c_prms_results.csv')

PIT_JARS = ['pitest-1.6.7.jar','pitest-entry-1.6.7.jar','pitest-command-line-1.6.7.jar']

JUNIT_JARS = [
    '/defects4j/framework/projects/Time/lib/junit-4.11.jar',
    '/defects4j/framework/projects/Chart/lib/junit.jar',
]

TARGETS = {
    'Time_15':   ('Time',    '15', 900),
    'Time_19':   ('Time',    '19', 900),
    'Time_27':   ('Time',    '27', 900),
}

with open(D4C_DIR/'archive/defects4j/correct_id.txt') as f:
    correct_ids = set(l.strip() for l in f if l.strip())

with open(D4C_DIR/'archive/defects4j/pred_d4c_gpt_archived.csv') as f:
    ALL_PATCHES = {r['slug']: r for r in csv.DictReader(f)}


def get_changed_lines(bug_code, fix_code):
    bug_lines = bug_code.splitlines(keepends=True)
    fix_lines = fix_code.splitlines(keepends=True)
    changed = []
    matcher = difflib.SequenceMatcher(None, bug_lines, fix_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('replace', 'insert'):
            for idx in range(j1, j2):
                s = fix_lines[idx].rstrip().strip()
                if s and not s.startswith('//') and not s.startswith('*'):
                    changed.append(idx+1)
    return sorted(set(changed))

def extract_method_signature(code):
    for line in code.strip().splitlines():
        s = line.strip()
        if s and not s.startswith('//') and not s.startswith('*'):
            return s
    return ''

def get_body_snippet(code, start=4, length=8):
    lines = code.strip().splitlines()
    body = []
    for line in lines[start:start+length]:
        s = line.strip()
        if s and not s.startswith('//') and not s.startswith('*'):
            body.append(s)
    return ' '.join(body)[:200]

def find_fqcn(project_dir, bug_code):
    sig = extract_method_signature(bug_code)
    method_m = re.search(
        r'(?:public|protected|private|static|final|\s)+\s+[\w\[\]<>,\s]+\s+(\w+)\s*\(', sig)
    method_name = method_m.group(1) if method_m else None
    body_early = get_body_snippet(bug_code, start=1, length=5)
    body_deep  = get_body_snippet(bug_code, start=5, length=8)
    all_tokens = re.findall(r'\b[A-Za-z][A-Za-z0-9_]{7,}\b', bug_code)
    rare_tokens = [t for t in all_tokens if len(t) > 9][:5]

    src_dirs = [
        project_dir/'src/main/java', project_dir/'src/java',
        project_dir/'src', project_dir/'source',
    ]
    candidates = []
    for src_dir in src_dirs:
        if not src_dir.exists(): continue
        for java_file in src_dir.rglob('*.java'):
            try:
                content = java_file.read_text(errors='ignore')
                score = 0
                if method_name and re.search(r'\b'+re.escape(method_name)+r'\s*\(', content):
                    score += 1
                if sig.strip() in content: score += 3
                if body_early and body_early[:80] in content: score += 3
                if body_deep  and body_deep[:80]  in content: score += 4
                for tok in rare_tokens:
                    if tok in content: score += 1
                if score >= 3:
                    rel = java_file.relative_to(src_dir)
                    fqcn = str(rel).replace('/','.',).removesuffix('.java')
                    candidates.append((score, fqcn, java_file))
            except: continue
    if not candidates: return None, None
    candidates.sort(reverse=True)
    if len(candidates) > 1:
        print(f"  [FQCN candidates: {', '.join(f'{c[1]}(score={c[0]})' for c in candidates[:3])}]")
    return candidates[0][1], candidates[0][2]

def find_fqcn_with_hint(project_dir, bug_code, trigger_tests=None):
    fqcn, java_file = find_fqcn(project_dir, bug_code)
    if not fqcn: return None, None
    if trigger_tests:
        for test_class in trigger_tests:
            test_simple = test_class.split('.')[-1]
            expected = re.sub(r'Tests?$', '', test_simple)
            if expected and expected.lower() not in fqcn.split('.')[-1].lower():
                sig = extract_method_signature(bug_code).strip()
                src_dirs = [project_dir/'src/main/java', project_dir/'src/java',
                            project_dir/'src', project_dir/'source']
                for src_dir in src_dirs:
                    if not src_dir.exists(): continue
                    for cf in src_dir.rglob(f'{expected}.java'):
                        try:
                            if sig in cf.read_text(errors='ignore'):
                                rel = cf.relative_to(src_dir)
                                new_fqcn = str(rel).replace('/','.',).removesuffix('.java')
                                print(f'  [FQCN hint override: {fqcn} -> {new_fqcn}]')
                                return new_fqcn, cf
                        except: continue
    return fqcn, java_file

def find_match_idx(java_file, bug_code):
    file_lines = java_file.read_text(errors='ignore').splitlines()
    bug_lines = bug_code.splitlines()
    n = len(bug_lines)
    bug_stripped = [l.strip() for l in bug_lines]
    for i in range(len(file_lines)-n+1):
        if [l.strip() for l in file_lines[i:i+n]] == bug_stripped:
            return i
    sig = extract_method_signature(bug_code).strip()
    for i, line in enumerate(file_lines):
        if sig in line:
            return i
    return 0

def apply_patch(java_file, bug_code, fix_code, match_idx):
    file_lines = java_file.read_text(errors='ignore').splitlines()
    n = len(bug_code.splitlines())
    file_indent = len(file_lines[match_idx]) - len(file_lines[match_idx].lstrip()) if file_lines[match_idx].strip() else 0
    fix_first = fix_code.splitlines()[0]
    fix_indent = len(fix_first) - len(fix_first.lstrip()) if fix_first.strip() else 0
    delta = file_indent - fix_indent

    def reindent(line, d):
        if not line.strip(): return ''
        c = len(line) - len(line.lstrip())
        return ' ' * max(0, c + d) + line.lstrip()

    fix_lines = [reindent(l, delta) for l in fix_code.splitlines()]
    java_file.write_text('\n'.join(file_lines[:match_idx] + fix_lines + file_lines[match_idx+n:]) + '\n')

def get_trigger_tests(project_dir):
    try:
        result = subprocess.check_output(['defects4j','export','-p','tests.trigger'],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        classes = set()
        for line in result.splitlines():
            line = line.strip()
            if '::' in line: classes.add(line.split('::')[0])
            elif line: classes.add(line)
        return sorted(classes)
    except: return []

def run_pit(project_dir, fqcn, trigger_tests, slug, project, timeout=900):
    try:
        cp_compile = subprocess.check_output(['defects4j','export','-p','cp.compile'],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        cp_test = subprocess.check_output(['defects4j','export','-p','cp.test'],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        src_d  = subprocess.check_output(['defects4j','export','-p','dir.src.classes'],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        bin_c  = subprocess.check_output(['defects4j','export','-p','dir.bin.classes'],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        test_c = subprocess.check_output(['defects4j','export','-p','dir.bin.tests'],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception as e:
        return None, str(e)

    pit_cp = ':'.join(str(PIT_LIB/j) for j in PIT_JARS)
    junit_prefix = ':'.join(j for j in JUNIT_JARS if Path(j).exists())
    if junit_prefix:
        full_cp = f"{junit_prefix}:{cp_compile}:{cp_test}:{project_dir/bin_c}:{project_dir/test_c}"
    else:
        full_cp = f"{cp_compile}:{cp_test}:{project_dir/bin_c}:{project_dir/test_c}"

    target_tests = ','.join(trigger_tests) if trigger_tests else '*Test,*Tests,*TestCase'

    report_dir = WORKDIR/f'{slug}_pit'
    if report_dir.exists(): shutil.rmtree(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    cmd = ['java','-cp',pit_cp,
           'org.pitest.mutationtest.commandline.MutationCoverageReport',
           '--reportDir', str(report_dir),
           '--targetClasses', fqcn,
           '--targetTests', target_tests,
           '--sourceDirs', str(project_dir/src_d),
           '--mutators','ALL','--outputFormats','XML','--failWhenNoMutations','false']
    for elem in full_cp.split(':'):
        if elem.strip(): cmd += ['--classPath', elem.strip()]

    try:
        r = subprocess.run(cmd, cwd=project_dir, capture_output=True,
                           text=True, timeout=timeout)
        return report_dir, r.stdout+r.stderr
    except subprocess.TimeoutExpired:
        return None, 'PIT timeout'
    except Exception as e:
        return None, str(e)

def parse_xml(report_dir, abs_lines):
    xml_files = list(report_dir.glob('**/*.xml'))
    if not xml_files: return None
    killed = survived = nocov = 0
    patched_set = set(abs_lines)
    for xf in xml_files:
        content = xf.read_text(errors='ignore')
        for mutation in re.finditer(r'<mutation[^/].*?</mutation>', content, re.DOTALL):
            m_text = mutation.group(0)
            sm = re.search(r"status='(\w+)'", m_text)
            lm = re.search(r'<lineNumber>(\d+)</lineNumber>', m_text)
            if sm and lm and int(lm.group(1)) in patched_set:
                st = sm.group(1)
                if st=='KILLED': killed+=1
                elif st=='SURVIVED': survived+=1
                elif st in ('NO_COVERAGE','NOT_COVERED'): nocov+=1
    total = killed+survived+nocov
    prmsc = killed/(killed+survived) if (killed+survived)>0 else None
    return {'killed':killed,'survived':survived,'nocov':nocov,'total':total,'prmsc':prmsc}


def main():
    targets = dict(TARGETS)

    if len(sys.argv) > 1:
        slug_arg = sys.argv[1]
        if slug_arg not in targets:
            print(f"'{slug_arg}' not in target list. Options: {list(targets)}")
            return
        targets = {slug_arg: targets[slug_arg]}

    print(f"Running {len(targets)} regression slug(s)\n")

    BUGGY_DIR.mkdir(parents=True, exist_ok=True)
    WORKDIR.mkdir(parents=True, exist_ok=True)

    fields = ['slug','label','fqcn','trigger_tests','rel_lines','abs_lines',
              'killed','survived','nocov','total','prmsc','status']

    done = set()
    if OUTPUT.exists():
        with open(OUTPUT) as f:
            for r in csv.DictReader(f):
                if r.get('status') == 'OK':
                    done.add(r['slug'])
        print(f"Resuming: {len(done)} already OK\n")

    def write_row(row):
        mode = 'a' if OUTPUT.exists() else 'w'
        with open(OUTPUT, mode, newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            if mode == 'w': w.writeheader()
            w.writerow(row)

    for slug, (project, bug_id, pit_timeout) in targets.items():
        if slug in done:
            print(f"SKIP {slug} (already OK)")
            continue

        label = '1' if slug in correct_ids else '0'
        print(f"\n{'='*55}")
        print(f"{slug} [{'CORRECT' if label=='1' else 'OVERFITTING'}]  (PIT timeout={pit_timeout}s)")
        print(f"{'='*55}")

        patch = ALL_PATCHES.get(slug, {})
        bug_code = patch.get('bug','').strip()
        fix_code = patch.get('fix','').strip()
        if not bug_code or not fix_code:
            write_row({'slug':slug,'label':label,'status':'NO_CODE'})
            continue

        rel_lines = get_changed_lines(bug_code, fix_code)
        print(f"  Relative lines: {rel_lines[:8]}{'...' if len(rel_lines)>8 else ''}")
        if not rel_lines:
            write_row({'slug':slug,'label':label,'status':'NO_CHANGED_LINES'})
            continue

        project_dir = BUGGY_DIR/slug
        if project_dir.exists():
            print('  Removing stale checkout dir...')
            shutil.rmtree(project_dir)

        print(f'  Checking out {project} {bug_id}b...')
        try:
            subprocess.run(
                ['defects4j','checkout','-p',project,'-v',f'{bug_id}b','-w',str(project_dir)],
                check=True, capture_output=True, timeout=300)
        except Exception as e:
            print(f'  CHECKOUT_FAILED: {e}')
            write_row({'slug':slug,'label':label,'status':'CHECKOUT_FAILED'})
            continue

        try:
            subprocess.run(['defects4j','compile'], cwd=project_dir,
                           check=True, capture_output=True, timeout=600)
        except Exception as e:
            print(f'  COMPILE_FAILED: {e}')
            write_row({'slug':slug,'label':label,'status':'COMPILE_FAILED'})
            continue
        print('  Compile OK')

        test_bin = subprocess.check_output(['defects4j','export','-p','dir.bin.tests'],
            cwd=project_dir, text=True, stderr=subprocess.DEVNULL).strip()
        test_bin_path = project_dir/test_bin
        if not test_bin_path.exists() or not any(test_bin_path.rglob('*.class')):
            print('  Compiling test classes...')
            try:
                subprocess.run(['defects4j','test'], cwd=project_dir,
                               capture_output=True, timeout=900)
                print('  Test classes OK')
            except subprocess.TimeoutExpired:
                print('  WARNING: test compile timed out (continuing anyway)')
            except Exception as e:
                print(f'  WARNING: {e} (continuing anyway)')

        trigger_tests = get_trigger_tests(project_dir)
        print(f'  Trigger tests: {trigger_tests}')

        fqcn, java_file = find_fqcn_with_hint(project_dir, bug_code, trigger_tests)
        if not fqcn:
            print('  ERROR: FQCN not found')
            write_row({'slug':slug,'label':label,'status':'FQCN_NOT_FOUND'})
            continue
        print(f'  FQCN: {fqcn}')

        match_idx = find_match_idx(java_file, bug_code)
        abs_lines = [match_idx + r for r in rel_lines]
        print(f'  match_idx={match_idx+1} (1-based), abs_lines: {abs_lines[:8]}{"..." if len(abs_lines)>8 else ""}')

        backup = Path(str(java_file)+'.orig')
        if not backup.exists(): shutil.copy2(java_file, backup)

        apply_patch(java_file, bug_code, fix_code, match_idx)
        print('  Patch applied')

        try:
            subprocess.run(['defects4j','compile'], cwd=project_dir,
                           check=True, capture_output=True, timeout=600)
            print('  Recompile OK')
        except Exception as e:
            shutil.copy2(backup, java_file)
            print(f'  RECOMPILE_FAILED: {e}')
            write_row({'slug':slug,'label':label,'fqcn':fqcn,
                       'trigger_tests':str(trigger_tests),
                       'rel_lines':str(rel_lines),'abs_lines':str(abs_lines),
                       'status':'RECOMPILE_FAILED'})
            continue

        print(f'  Running PIT (timeout={pit_timeout}s)...')
        report_dir, pit_log = run_pit(project_dir, fqcn, trigger_tests,
                                       slug, project, timeout=pit_timeout)
        shutil.copy2(backup, java_file)

        if not report_dir:
            status = 'PIT_TIMEOUT' if 'timeout' in pit_log.lower() else 'PIT_FAILED'
            print(f'  {status}: {pit_log[-200:]}')
            write_row({'slug':slug,'label':label,'fqcn':fqcn,
                       'trigger_tests':str(trigger_tests),
                       'rel_lines':str(rel_lines),'abs_lines':str(abs_lines),
                       'status':status})
            continue

        stats = parse_xml(report_dir, abs_lines)
        if not stats:
            if 'did not pass without mutation' in pit_log:
                status = 'NOT_GREEN_SUITE'
            else:
                status = 'NO_XML'
                print(f'  NO_XML log: {pit_log[-300:]}')
            write_row({'slug':slug,'label':label,'fqcn':fqcn,
                       'trigger_tests':str(trigger_tests),
                       'rel_lines':str(rel_lines),'abs_lines':str(abs_lines),
                       'status':status})
            continue

        prmsc_str = f"{stats['prmsc']:.3f}" if stats['prmsc'] is not None else 'NO_MUTANTS'
        print(f"  killed={stats['killed']} survived={stats['survived']} nocov={stats['nocov']} PRMSc={prmsc_str}")

        write_row({
            'slug':slug, 'label':label, 'fqcn':fqcn,
            'trigger_tests':str(trigger_tests),
            'rel_lines':str(rel_lines), 'abs_lines':str(abs_lines),
            'killed':stats['killed'], 'survived':stats['survived'],
            'nocov':stats['nocov'], 'total':stats['total'],
            'prmsc':prmsc_str, 'status':'OK'
        })

    print(f"\n{'='*55}")
    print(f"Done. Results: {OUTPUT}")
    print("Merge with: python3 merge_d4c_csvs.py d4c_final.csv d4c_regression_results.csv --output d4c_final2.csv")

if __name__ == '__main__':
    main()