#!/usr/bin/env python3

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, matthews_corrcoef


OUT_DIR = 'entropy-delta-results'

SREPAIR_FAILS_TRIGGER = {
    'Compress-34_55', 'Compress-34_56',
    'Compress-46_57', 'Compress-46_58', 'Compress-46_59',
}

DATASETS = [
    ('D4C',         f'{OUT_DIR}/d4c_entropy_delta_results.csv'),
    ('Wang',        f'{OUT_DIR}/wang_entropy_delta_results.csv'),
    ('PRISM',       f'{OUT_DIR}/prism_entropy_delta_results.csv'),
    ('RepairLLaMA', f'{OUT_DIR}/repairllama_entropy_delta_results.csv'),
    ('SRepair',     f'{OUT_DIR}/srepair_entropy_delta_results.csv'),
]


def boot_auc(y, s, n=10000, seed=42):
    rng = np.random.default_rng(seed)
    y, s = np.asarray(y), np.asarray(s)
    vals = []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        if len(set(y[idx])) > 1:
            vals.append(roc_auc_score(y[idx], s[idx]))
    if len(vals) < 100:
        return float('nan'), float('nan')
    return np.percentile(vals, 2.5), np.percentile(vals, 97.5)


def fixed_mcc_prems(overfit, prmsc):
    pred = (np.asarray(prmsc) < 0.5).astype(int)
    return matthews_corrcoef(overfit, pred)

def fixed_mcc_ed(overfit, entropy_delta):
    pred = (np.asarray(entropy_delta) < 0).astype(int)
    return matthews_corrcoef(overfit, pred)


def report(label, d):
    n_c = int((d.label == 1).sum())
    n_o = int((d.label == 0).sum())
    print(f"\n  {label}: n={len(d)} (correct={n_c}, overfitting={n_o})")
    if n_c == 0 or n_o == 0:
        print("    only one class — AUC/MCC undefined")
        return
    for col, lbl in [('prmsc', 'PReMS'), ('entropy_delta', 'Entropy-Delta')]:
        sub = d.dropna(subset=[col])
        if sub.empty:
            continue
        auc = roc_auc_score(sub.overfit, -sub[col])
        lo, hi = boot_auc(sub.overfit, -sub[col])
        ci = f"[{lo:.3f}, {hi:.3f}]" if not np.isnan(lo) else "[nan, nan]"
        if col == 'prmsc':
            mcc = fixed_mcc_prems(sub.overfit, sub[col])
        else:
            mcc = fixed_mcc_ed(sub.overfit, sub[col])
        print(f"    {lbl:14s}  AUC={auc:.3f} {ci}   MCC={mcc:.3f}")


def evaluate(name, path):
    df = pd.read_csv(path)
    ok = df[df.status == 'OK'].copy()
    ok['label'] = pd.to_numeric(ok['label'], errors='coerce')
    ok['overfit'] = 1 - ok['label']
    ok['prmsc'] = pd.to_numeric(ok['prmsc'], errors='coerce')
    ok['entropy_delta'] = pd.to_numeric(ok['entropy_delta'], errors='coerce')
    ok['assessable'] = ok['assessable'].astype(str).str.strip().str.lower() == 'true'

    if name == 'SRepair':
        ft = ok['slug'].isin(SREPAIR_FAILS_TRIGGER)
        ok.loc[ft, 'assessable'] = True
        ok.loc[ft, 'prmsc'] = 0.0

    print(f"\n{'='*60}")
    print(f"{name}: {len(df)} total, {len(ok)} scored OK")
    report('Assessable (same as PReMS)', ok[ok.assessable])
    report('All patches (Entropy-Delta scored)', ok)


for name, path in DATASETS:
    evaluate(name, path)
print()