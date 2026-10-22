#!/usr/bin/env python3

import re, csv, difflib, shutil, subprocess
from pathlib import Path

D4C_DIR  = Path('/workspaces/D4C-prms-experiment/D4C')
PIT_LIB  = Path('/pit-lib')
BUGGY_DIR= Path('/workspaces/D4C-prms-experiment/d4c_buggy')
WORKDIR  = Path('/tmp/fix_abs_workdirs')
OUTPUT   = Path('/workspaces/D4C-prms-experiment/d4c_prms_results.csv')

PIT_JARS = ['pitest-1.6.7.jar','pitest-entry-1.6.7.jar','pitest-command-line-1.6.7.jar']
JUNIT_JARS = ['/defects4j/framework/projects/Closure/lib/junit4-4.10.jar',
              '/defects4j/framework/projects/lib/junit-4.11.jar']

with open(D4C_DIR/'archive/defects4j/pred_d4c_gpt_archived.csv') as f:
    ALL_PATCHES = {r['slug']: r for r in csv.DictReader(f)}

SLUGS = {
    'Closure_61': ('Closure', '61', 'com.google.javascript.jscomp.NodeUtil'),
}

def get_changed_lines(bug_code, fix_code):
    bug_ls = bug_code.splitlines(keepends=True)
    fix_ls = fix_code.splitlines(keepends=True)
    changed = []
    matcher = difflib.SequenceMatcher(None, bug_ls, fix_ls)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('replace','insert'):
            for idx in range(j1, j2):
                s = fix_ls[idx].rstrip().strip()
                if s and not s.startswith('//') and not s.startswith('*'):
                    changed.append(idx+1)
    return sorted(set(changed))

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

def run_pit(project_dir, fqcn, trigger_tests, slug, timeout=900):
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
           '--mutators','ALL','--outputFormats','XML','--failWhenNoMutations','false',
           '--threads','1']
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
        for m in re.finditer(r'<mutation[^/].*?</mutation>', content, re.DOTALL):
            mt = m.group(0)
            sm = re.search(r"status='(\w+)'", mt)
            lm = re.search(r'<lineNumber>(\d+)</lineNumber>', mt)
            if sm and lm and int(lm.group(1)) in patched_set:
                st = sm.group(1)
                if st=='KILLED': killed+=1
                elif st=='SURVIVED': survived+=1
                elif st in ('NO_COVERAGE','NOT_COVERED'): nocov+=1
    total = killed+survived+nocov
    prmsc = killed/(killed+survived) if (killed+survived)>0 else None
    return {'killed':killed,'survived':survived,'nocov':nocov,'total':total,'prmsc':prmsc}

def main():
    BUGGY_DIR.mkdir(parents=True, exist_ok=True)
    WORKDIR.mkdir(parents=True, exist_ok=True)

    results = {}

    for slug, (project, bug_id, fqcn) in SLUGS.items():
        print(f"\n{'='*55}")
        print(f"{slug}")
        print(f"{'='*55}")

        patch = ALL_PATCHES.get(slug, {})
        bug_code = patch.get('bug','').strip()
        fix_code = patch.get('fix','').strip()

        rel_lines = get_changed_lines(bug_code, fix_code)
        print(f"  Relative lines: {rel_lines}")

        project_dir = BUGGY_DIR/slug
        if not project_dir.exists():
            print(f'  ERROR: {project_dir} does not exist - run main pipeline first')
            continue
        print(f'  Using existing checkout: {project_dir}')

        for jf in project_dir.rglob('*.java'):
            data = jf.read_bytes()
            if b'\r\n' in data:
                jf.write_bytes(data.replace(b'\r\n', b'\n'))

        trigger_tests = get_trigger_tests(project_dir)
        print(f'  Trigger tests: {trigger_tests}')

        java_file = None
        for src_dir in [project_dir/'src/main/java', project_dir/'src/java', project_dir/'src']:
            if not src_dir.exists(): continue
            matches = list(src_dir.rglob('NodeUtil.java'))
            if matches:
                java_file = matches[0]
                break

        if not java_file:
            print('  Java file not found'); continue
        print(f'  Java file: {java_file.name}')

        file_lines = java_file.read_text(errors='ignore').splitlines()
        bug_lines = bug_code.splitlines()
        n = len(bug_lines)
        bug_stripped = [l.strip() for l in bug_lines]

        hits = [i for i in range(len(file_lines)-n+1)
                if [l.strip() for l in file_lines[i:i+n]] == bug_stripped]
        if not hits:
            print('  Bug block not found in file'); continue
        match_idx = hits[0]

        abs_lines = [match_idx + r for r in rel_lines]
        print(f'  match_idx={match_idx+1} (1-based), abs_lines={abs_lines}')

        print(f'  Running PIT...')
        report_dir, pit_log = run_pit(project_dir, fqcn, trigger_tests, slug)
        if not report_dir:
            print(f'  PIT failed: {pit_log[-500:]}'); continue

        stats = parse_xml(report_dir, abs_lines)
        if not stats:
            print('  No mutations on abs_lines'); continue

        prmsc_str = f"{stats['prmsc']:.3f}" if stats['prmsc'] is not None else 'NO_MUTANTS'
        print(f"  killed={stats['killed']} survived={stats['survived']} nocov={stats['nocov']} PRMSc={prmsc_str}")

        results[slug] = {
            'abs_lines': str(abs_lines),
            'killed': str(stats['killed']),
            'survived': str(stats['survived']),
            'nocov': str(stats['nocov']),
            'total': str(stats['total']),
            'prmsc': prmsc_str,
            'status': 'OK',
        }

    if not results:
        print('\nNo results to update.')
        return

    print(f"\nUpdating {OUTPUT}...")
    rows = list(csv.DictReader(open(OUTPUT)))
    fields = list(rows[0].keys())
    for r in rows:
        if r['slug'] in results:
            r.update(results[r['slug']])
            print(f"  Updated {r['slug']}: PRMSc={r['prmsc']}")

    with open(OUTPUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print("Done.")

if __name__ == '__main__':
    main()