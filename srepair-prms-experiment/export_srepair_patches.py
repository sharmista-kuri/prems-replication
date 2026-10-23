#!/usr/bin/env python3

import json, os, subprocess
import pandas as pd
from pathlib import Path

RESULTS_CSV  = Path('/workspaces/srepair-prms-experiment/srepair_prms_results.csv')
LABELED_JSON = Path('/workspaces/srepair-prms-experiment/srepair_labeled.json')
OUT_CSV      = Path('srepair_patches_export.csv')
CHECKOUT_DIR = Path('/tmp/srepair_buggy')

CHECKOUT_DIR.mkdir(exist_ok=True)

results = pd.read_csv(RESULTS_CSV)
labeled = json.load(open(LABELED_JSON))

patch_map = {}
for entry in labeled:
    key = (entry['bug_id'].replace('_', '-'), int(entry['label']))
    patch_map.setdefault(key, []).append(entry['patch_code'])

print(f"Results rows: {len(results)}, labeled entries: {len(labeled)}")
print(f"Columns: {results.columns.tolist()}")

checked_out = {}

def get_buggy_file(bug_id, fqcn):
    project, num = bug_id.split('-', 1)
    checkout = CHECKOUT_DIR / bug_id
    if bug_id not in checked_out:
        checkout.mkdir(exist_ok=True)
        r = subprocess.run(
            ['defects4j', 'checkout', '-p', project, '-v', f'{num}b', '-w', str(checkout)],
            capture_output=True, text=True, timeout=120)
        checked_out[bug_id] = r.returncode == 0
        if not checked_out[bug_id]:
            print(f"    checkout failed {bug_id}: {r.stderr[:100]}")
    if not checked_out[bug_id]:
        return None
    rel = fqcn.replace('.', '/') + '.java'
    for root, _, files in os.walk(checkout):
        for f in files:
            full = Path(root) / f
            if full.as_posix().endswith(rel):
                return full.read_text(encoding='utf-8', errors='replace')
    return None

def extract_method(source, fqcn):
    class_name = fqcn.split('.')[-1]
    lines = source.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if f'class {class_name}' in line or f'interface {class_name}' in line:
            start = i
            break
    return '\n'.join(lines[start:start+200])

rows, skipped = [], 0
used_idx = {}

for row_i, r in results.iterrows():
    bug_id = str(r['bug_id'])
    label  = int(r['label'])
    fqcn   = str(r['fqcn'])
    slug   = f"{bug_id}_{row_i}"

    key = (bug_id, label)
    patches = patch_map.get(key, [])
    use_i = used_idx.get(key, 0)
    if use_i >= len(patches):
        print(f"  SKIP {slug}: no patch_code for {key}")
        skipped += 1
        continue
    patch_code = patches[use_i]
    used_idx[key] = use_i + 1

    bug_source = get_buggy_file(bug_id, fqcn)
    if bug_source is None:
        print(f"  SKIP {slug}: checkout failed")
        skipped += 1
        continue
    bug_code = extract_method(bug_source, fqcn)

    rows.append(dict(dataset='srepair', slug=slug, label=label,
                     bug_code=bug_code, patch_code=patch_code))
    print(f"  OK  {slug} label={label}")

print(f"\nExported {len(rows)}, skipped {skipped}")
pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
print(f"Written to {OUT_CSV}")