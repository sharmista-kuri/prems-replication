#!/usr/bin/env python3

import csv, difflib, re, shutil, subprocess
from pathlib import Path

D4C_DIR  = Path('/workspaces/D4C-prms-experiment/D4C')
PIT_LIB  = Path('/pit-lib')
BUGGY_DIR= Path('/tmp/d4c_math41_buggy')
WORKDIR  = Path('/tmp/d4c_math41_workdirs')
OUTPUT   = Path('/workspaces/D4C-prms-experiment/d4c_prms_results.csv')
PIT_JARS = ['pitest-1.6.7.jar','pitest-entry-1.6.7.jar','pitest-command-line-1.6.7.jar']

with open(D4C_DIR/'archive/defects4j/pred_d4c_gpt_archived.csv') as f:
    ALL_PATCHES = {r['slug']: r for r in csv.DictReader(f)}

def get_changed_lines(bug_code, fix_code):
    bug_lines = bug_code.splitlines(keepends=True)
    fix_lines = fix_code.splitlines(keepends=True)
    changed = []
    matcher = difflib.SequenceMatcher(None, bug_lines, fix_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('replace','insert'):
            for idx in range(j1, j2):
                s = fix_lines[idx].rstrip().strip()
                if s and not s.startswith('//') and not s.startswith('*'):
                    changed.append(idx+1)
    return sorted(set(changed))

def find_match_idx_context(java_file, bug_code):
    file_lines = java_file.read_text(errors='ignore').splitlines()
    bug_lines = bug_code.splitlines()
    n = len(bug_lines)
    bug_stripped = [l.strip() for l in bug_lines]

    for i in range(len(file_lines)-n+1):
        if [l.strip() for l in file_lines[i:i+n]] == bug_stripped:
            return i

    sig = bug_stripped[0]
    last = bug_stripped[-1]
    interior = bug_stripped[1:-1]
    for i in range(len(file_lines)-n+1):
        if file_lines[i].strip() == sig and file_lines[i+n-1].strip() == last:
            file_interior = [l.strip() for l in file_lines[i+1:i+n-1]]
            if len(interior) == len(file_interior):
                matches = sum(1 for a,b in zip(interior, file_interior) if a==b)
                if matches / max(len(interior),1) >= 0.6:
                    return i

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

def run_pit(project_dir, fqcn, trigger_tests, timeout=900):
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
    full_cp = f"{cp_compile}:{cp_test}:{project_dir/bin_c}:{project_dir/test_c}"
    target_tests = ','.join(trigger_tests) if trigger_tests else '*Test,*Tests,*TestCase'

    report_dir = WORKDIR/'Math_41_pit'
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
    slug = 'Math_41'
    project, bug_id = 'Math', '41'
    fqcn = 'org.apache.commons.math.stat.descriptive.moment.Variance'

    print(f"\n{'='*55}")
    print(f"{slug} [CORRECT]")
    print(f"{'='*55}")

    BUGGY_DIR.mkdir(parents=True, exist_ok=True)
    WORKDIR.mkdir(parents=True, exist_ok=True)

    patch = ALL_PATCHES.get(slug, {})
    bug_code = patch.get('bug','').strip()
    fix_code = patch.get('fix','').strip()

    rel_lines = get_changed_lines(bug_code, fix_code)
    print(f"  Relative lines: {rel_lines}")

    project_dir = BUGGY_DIR/slug
    if project_dir.exists():
        print('  Removing stale checkout dir...')
        shutil.rmtree(project_dir)

    print(f'  Checking out {project} {bug_id}b...')
    subprocess.run(
        ['defects4j','checkout','-p',project,'-v',f'{bug_id}b','-w',str(project_dir)],
        check=True, capture_output=True, timeout=300)

    subprocess.run(['defects4j','compile'], cwd=project_dir,
                   check=True, capture_output=True, timeout=600)
    print('  Compile OK')

    trigger_tests = get_trigger_tests(project_dir)
    print(f'  Trigger tests: {trigger_tests}')

    java_file = None
    for src_dir in [project_dir/'src/main/java', project_dir/'src/java', project_dir/'src']:
        if not src_dir.exists(): continue
        for f in src_dir.rglob('Variance.java'):
            java_file = f
            break
        if java_file: break
    print(f'  Java file: {java_file}')

    match_idx = find_match_idx_context(java_file, bug_code)
    abs_lines = [match_idx + r for r in rel_lines]
    print(f'  match_idx={match_idx+1} (1-based)')
    print(f'  abs_lines: {abs_lines}')
    print(f'  (expected match_idx=500, abs_lines ~[519,520,521,522])')

    backup = Path(str(java_file)+'.orig')
    shutil.copy2(java_file, backup)
    apply_patch(java_file, bug_code, fix_code, match_idx)
    print('  Patch applied')

    subprocess.run(['defects4j','compile'], cwd=project_dir,
                   check=True, capture_output=True, timeout=600)
    print('  Recompile OK')

    print('  Running PIT...')
    report_dir, pit_log = run_pit(project_dir, fqcn, trigger_tests)
    shutil.copy2(backup, java_file)

    if not report_dir:
        print(f'  FAILED: {pit_log[-200:]}')
        return

    stats = parse_xml(report_dir, abs_lines)
    if not stats:
        print('  NO_XML')
        return

    prmsc_str = f"{stats['prmsc']:.3f}" if stats['prmsc'] is not None else 'NO_MUTANTS'
    print(f"  killed={stats['killed']} survived={stats['survived']} nocov={stats['nocov']} PRMSc={prmsc_str}")

    fields = ['slug','label','fqcn','trigger_tests','rel_lines','abs_lines',
              'killed','survived','nocov','total','prmsc','status']

    existing_rows = []
    if OUTPUT.exists():
        with open(OUTPUT, newline='') as f:
            existing_rows = list(csv.DictReader(f))

    new_row = {
        'slug': slug, 'label': '1', 'fqcn': fqcn,
        'trigger_tests': str(trigger_tests),
        'rel_lines': str(rel_lines), 'abs_lines': str(abs_lines),
        'killed': stats['killed'], 'survived': stats['survived'],
        'nocov': stats['nocov'], 'total': stats['total'],
        'prmsc': prmsc_str, 'status': 'OK'
    }

    updated = False
    for i, row in enumerate(existing_rows):
        if row.get('slug') == slug:
            existing_rows[i] = new_row
            updated = True
            break
    if not updated:
        existing_rows.append(new_row)

    with open(OUTPUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(existing_rows)
    print(f"  {'Updated' if updated else 'Appended'} {slug} in {OUTPUT}")

if __name__ == '__main__':
    main()