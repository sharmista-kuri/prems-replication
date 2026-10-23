#!/usr/bin/env python3

import os
import pandas as pd
from pathlib import Path

RESULTS_CSV  = Path('/workspaces/RepairLLaMA-prms-experiment/repairllama_prms_results.csv')
CORRECT_DIR  = Path('/workspaces/RepairLLaMA-prms-experiment/apr-overfitting-benchmark/datasets/repairllama/context_size_3_filtered/correct')
OVERFIT_DIR  = Path('/workspaces/RepairLLaMA-prms-experiment/apr-overfitting-benchmark/datasets/repairllama/context_size_3_filtered/overfitting')
OUT_CSV      = Path('repairllama_patches_export.csv')

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
            stripped = line[1:].strip()
            if stripped.startswith('// buggy') or stripped.startswith('//         '):
                continue
            patch_lines.append(line[1:])
        else:
            ctx = line[1:] if line.startswith(' ') else line
            bug_lines.append(ctx)
            patch_lines.append(ctx)
    return '\n'.join(bug_lines), '\n'.join(patch_lines)

results = pd.read_csv(RESULTS_CSV)
print(f"Results rows: {len(results)}, columns: {results.columns.tolist()}")

rows, skipped = [], 0

for _, r in results.iterrows():
    bug_id     = str(r['bug_id'])
    label      = int(r['label'])
    patch_file = str(r['patch_file'])
    slug       = f"{bug_id}_{Path(patch_file).stem}"

    label_dir = CORRECT_DIR if label == 1 else OVERFIT_DIR
    diff_path = label_dir / Path(patch_file).name

    if not diff_path.exists():
        found = list(label_dir.glob(f"**/{Path(patch_file).name}"))
        if found:
            diff_path = found[0]
        else:
            print(f"  SKIP {slug}: diff not found")
            skipped += 1
            continue

    patch_text = diff_path.read_text(encoding='utf-8', errors='replace')
    bug_code, patch_code = extract_from_diff(patch_text)

    if not bug_code.strip() and not patch_code.strip():
        print(f"  SKIP {slug}: empty diff")
        skipped += 1
        continue

    rows.append(dict(dataset='repairllama', slug=slug, label=label,
                     bug_code=bug_code, patch_code=patch_code))
    print(f"  OK  {slug} label={label}")

print(f"\nExported {len(rows)}, skipped {skipped}")
pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
print(f"Written to {OUT_CSV}")