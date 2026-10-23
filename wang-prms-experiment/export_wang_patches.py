#!/usr/bin/env python3

import os
import pandas as pd
from pathlib import Path

RESULTS_CSV = Path('/workspaces/wang-prms-experiment/wang_prms_results.csv')
OUT_CSV     = Path('wang_patches_export.csv')

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

results = pd.read_csv(RESULTS_CSV).dropna(subset=['bug_id', 'label', 'patch_file'])
print(f"Results rows after dropping NaN: {len(results)}")

rows, skipped = [], 0
for _, r in results.iterrows():
    slug       = f"{r['bug_id']}_{r['tool']}"
    label      = int(r['label'])
    patch_file = str(r['patch_file'])

    if not os.path.exists(patch_file):
        print(f"  SKIP {slug}: patch file not found")
        skipped += 1
        continue

    patch_text = open(patch_file, encoding='utf-8', errors='replace').read()
    bug_code, patch_code = extract_from_diff(patch_text)

    if not bug_code.strip() and not patch_code.strip():
        print(f"  SKIP {slug}: empty diff")
        skipped += 1
        continue

    rows.append(dict(dataset='wang', slug=slug, label=label,
                     bug_code=bug_code, patch_code=patch_code))
    print(f"  OK  {slug}")

print(f"\nExported {len(rows)}, skipped {skipped}")
pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
print(f"Written to {OUT_CSV}")