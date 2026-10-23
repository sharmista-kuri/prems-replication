#!/usr/bin/env python3

import numpy as np
import pandas as pd
from sklearn.metrics import matthews_corrcoef, roc_auc_score

UPLOAD = 'FixCheck-results'

DATASETS = [
    ('D4C',         f'{UPLOAD}/d4c_prems_vs_fixcheck.csv'),
    ('Wang',        f'{UPLOAD}/wang_prems_vs_fixcheck.csv'),
    ('SRepair',     f'{UPLOAD}/srepair_prems_vs_fixcheck.csv'),
    ('RepairLlama', f'{UPLOAD}/repairllama_prems_vs_fixcheck.csv'),
    ('PRISM',       f'{UPLOAD}/prism_prems_vs_fixcheck.csv'),
]

PREMS_THRESHOLD = 0.5
BOOT = 0
SEED = 0
LATEX = True

NUMERIC = ['label', 'prmsc', 'fixcheck_failing', 'fixcheck_pred']


def load(path):
    df = pd.read_csv(path)
    a = df[df['fixcheck_pred'].notna()].copy()
    for c in NUMERIC:
        a[c] = pd.to_numeric(a[c], errors='coerce')
    return len(df), a.dropna(subset=NUMERIC)


def scores(a):
    y = a['label'].to_numpy()
    fc_pred = a['fixcheck_pred'].to_numpy()
    pr_pred = (a['prmsc'].to_numpy() >= PREMS_THRESHOLD).astype(int)
    out = {'fc_mcc': matthews_corrcoef(y, fc_pred),
           'pr_mcc': matthews_corrcoef(y, pr_pred)}
    if len(np.unique(y)) < 2:
        out['fc_auc'] = out['pr_auc'] = np.nan
    else:
        out['fc_auc'] = roc_auc_score(1 - y, a['fixcheck_failing'].to_numpy())
        out['pr_auc'] = roc_auc_score(1 - y, 1 - a['prmsc'].to_numpy())
    return out


def bootstrap(a, n_boot, seed=SEED):
    rng = np.random.default_rng(seed)
    y = a['label'].to_numpy()
    fc_s = a['fixcheck_failing'].to_numpy()
    pr_s = 1 - a['prmsc'].to_numpy()
    fc_p = a['fixcheck_pred'].to_numpy()
    pr_p = (a['prmsc'].to_numpy() >= PREMS_THRESHOLD).astype(int)

    keys = ['fc_auc', 'pr_auc', 'fc_mcc', 'pr_mcc']
    draws, wins = {k: [] for k in keys}, 0
    for _ in range(n_boot):
        i = rng.choice(len(y), len(y), replace=True)
        yi = y[i]
        if len(np.unique(yi)) < 2:
            continue
        d = {'fc_auc': roc_auc_score(1 - yi, fc_s[i]),
             'pr_auc': roc_auc_score(1 - yi, pr_s[i]),
             'fc_mcc': matthews_corrcoef(yi, fc_p[i]),
             'pr_mcc': matthews_corrcoef(yi, pr_p[i])}
        for k in keys:
            draws[k].append(d[k])
        wins += d['pr_auc'] > d['fc_auc']

    ci = {k: (np.percentile(v, 2.5), np.percentile(v, 97.5)) if (v := draws[k])
          else (np.nan, np.nan) for k in keys}
    n = len(draws['pr_auc'])
    ci['p_prems_better'] = wins / n if n else np.nan
    return ci


def main():
    rows = []
    for name, path in DATASETS:
        try:
            total, a = load(path)
        except FileNotFoundError:
            print(f'[skip] {name}: {path} not found')
            continue
        n0, n1 = int((a['label'] == 0).sum()), int((a['label'] == 1).sum())
        r = {'dataset': name, 'total': total, 'assess': len(a),
             'cov': 100 * len(a) / total if total else np.nan,
             'ovf': n0, 'corr': n1, **scores(a)}
        if BOOT and n0 and n1:
            r['ci'] = bootstrap(a, BOOT)
        rows.append(r)

    if not rows:
        print('No datasets loaded. Check UPLOAD and the paths in DATASETS.')
        return

    W = 15
    print(f"{'':14}{'':>6}{'AUC':^{2 * W}}{'MCC':^{2 * W}}")
    hdr = (f"{'Dataset':<14}{'n':>6}{'FixCheck':>{W}}{'PReMS':>{W}}"
           f"{'FixCheck':>{W}}{'PReMS':>{W}}")
    print(hdr)
    print('-' * len(hdr))
    for r in rows:
        print(f"{r['dataset']:<14}{r['assess']:>6}{r['fc_auc']:>{W}.3f}"
              f"{r['pr_auc']:>{W}.3f}{r['fc_mcc']:>{W}.3f}{r['pr_mcc']:>{W}.3f}")
        if 'ci' in r:
            c = r['ci']
            cis = ''.join(f'{f"[{c[k][0]:.2f},{c[k][1]:.2f}]":>{W}}'
                          for k in ('fc_auc', 'pr_auc', 'fc_mcc', 'pr_mcc'))
            print(f"{'95% CI':<14}{'':>6}{cis}")

    ok = [r for r in rows if not np.isnan(r['fc_auc'])]
    print(f"\nPReMS AUC higher: {sum(r['pr_auc'] > r['fc_auc'] for r in ok)}/{len(ok)}")
    print(f"PReMS MCC higher: {sum(r['pr_mcc'] > r['fc_mcc'] for r in rows)}/{len(rows)}")
    for label, key in (('FixCheck AUC', 'fc_auc'), ('PReMS AUC', 'pr_auc')):
        v = [r[key] for r in ok]
        print(f'{label:<14} range {min(v):.3f}-{max(v):.3f}  (spread {max(v) - min(v):.3f})')

    


if __name__ == '__main__':
    main()