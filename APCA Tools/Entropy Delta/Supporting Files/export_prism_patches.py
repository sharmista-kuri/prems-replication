#!/usr/bin/env python3

import os
import pandas as pd
from pathlib import Path

RESULTS_CSV   = Path('/workspaces/Prism-Prms-Experiment/prism_prms_results.csv')
INVENTORY_CSV = Path('/workspaces/Prism-Prms-Experiment/prism_math_lang_inventory.csv')
OUT_CSV       = Path('prism_patches_export.csv')

def extract_from_diff(patch_text):
    bug_lines, patch_lines = [], []
    in_hunk = False
    for line in patch_text.splitlines():
        if line.startswith('@@'):
            in_hunk = True
            continue
        if line.startswith('---') or line.startswith('+++'):
            continue
        if not in_hunk:
            continue
        if line.startswith('-'):
            bug_lines.append(line[1:])
        elif line.startswith('+'):
            patch_lines.append(line[1:])
        else:
            ctx = line[1:] if line.startswith(' ') else line
            bug_lines.append(ctx)
            patch_lines.append(ctx)
    return '\n'.join(bug_lines), '\n'.join(patch_lines)

results   = pd.read_csv(RESULTS_CSV)
inventory = pd.read_csv(INVENTORY_CSV)

print(f"Results rows: {len(results)}, columns: {results.columns.tolist()}")
print(f"Inventory rows: {len(inventory)}, columns: {inventory.columns.tolist()}")

inv_map = {}
for _, row in inventory.iterrows():
    key = (str(row['tool']), str(row['bug_id']))
    inv_map[key] = str(row['patch_path']).replace('/home/claude/PRISM', '/workspaces/Prism-Prms-Experiment/PRISM')

rows, skipped = [], 0

for _, r in results.iterrows():
    tool   = str(r['tool'])
    bug_id = str(r['bug_id'])
    label  = int(r['label'])
    slug   = f"{bug_id}_{tool}"

    patch_path = inv_map.get((tool, bug_id))
    if patch_path is None:
        print(f"  SKIP {slug}: not in inventory")
        skipped += 1
        continue

    if not os.path.exists(patch_path):
        print(f"  SKIP {slug}: patch file not found at {patch_path}")
        skipped += 1
        continue

    patch_text = open(patch_path, encoding='utf-8', errors='replace').read()
    bug_code, patch_code = extract_from_diff(patch_text)

    if not bug_code.strip() and not patch_code.strip():
        print(f"  SKIP {slug}: empty diff")
        skipped += 1
        continue

    rows.append(dict(dataset='prism', slug=slug, label=label,
                     bug_code=bug_code, patch_code=patch_code))
    print(f"  OK  {slug} label={label}")

print(f"\nExported {len(rows)}, skipped {skipped}")
pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
print(f"Written to {OUT_CSV}")