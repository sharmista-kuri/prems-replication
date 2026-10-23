#!/usr/bin/env python3

import os
import pandas as pd
import numpy as np
from sklearn.metrics import roc_auc_score, matthews_corrcoef

UPLOAD = 'LLM4PatchCorrect-results'
UPLOAD_PReMS = 'PReMS-results'

DATASETS = [
    ('D4C',         f'{UPLOAD}/d4c_results.csv',           f'{UPLOAD_PReMS}/d4c_prms_results.csv'),
    ('Wang',        f'{UPLOAD}/wang_results.csv',           f'{UPLOAD_PReMS}/wang_prms_results.csv'),
    ('SRepair',     f'{UPLOAD}/srepair_results.csv',        f'{UPLOAD_PReMS}/srepair_prms_results.csv'),
    ('RepairLlama', f'{UPLOAD}/repairllama_results.csv',    f'{UPLOAD_PReMS}/repairllama_prms_results.csv'),
    ('PRISM',       f'{UPLOAD}/prism_results.csv',          f'{UPLOAD_PReMS}/prism_prms_results.csv'),
]


def normalize_id(s):
    return str(s).strip().replace('_', '-').lower()


def load_prems(path):
    df = pd.read_csv(path)
    id_col = 'slug' if 'slug' in df.columns else 'bug_id'
    df = df.copy()
    df['_norm_id'] = df[id_col].apply(normalize_id)

    def parse_prmsc(row):
        s = str(row['status']).strip().upper()
        if s == 'FAILS_TRIGGER':
            return 0.0, True
        if s == 'OK':
            try:
                p = float(row['prmsc'])
                k = int(row['killed']) if pd.notna(row.get('killed', np.nan)) else 0
                sv = int(row['survived']) if pd.notna(row.get('survived', np.nan)) else 0
                if (k + sv) > 0:
                    return p, True
            except (ValueError, TypeError):
                pass
        return np.nan, False

    df['_prmsc'] = np.nan
    df['_assessable'] = False
    for i, row in df.iterrows():
        p, ok = parse_prmsc(row)
        df.at[i, '_prmsc'] = p
        df.at[i, '_assessable'] = ok
    return df


def compute(name, llm_path, prems_path):
    llm = pd.read_csv(llm_path)
    prems = load_prems(prems_path)
    llm = llm.copy()
    llm['_norm_id'] = llm['bug_id'].apply(normalize_id)

    if len(llm) == len(prems):
        ls = llm.sort_values(['_norm_id', 'true_label']).reset_index(drop=True)
        ps = prems.sort_values(['_norm_id', 'label']).reset_index(drop=True)
        if (ls['_norm_id'].values == ps['_norm_id'].values).all() and \
           (ls['true_label'].values == ps['label'].values).all():
            combined = pd.DataFrame({
                'true_label': ls['true_label'].values,
                'score':      ls['score'].values,
                'prmsc':      ps['_prmsc'].values,
                'assessable': ps['_assessable'].values,
            })
        else:
            combined = _id_join(llm, prems)
    else:
        combined = _id_join(llm, prems)

    a = combined[combined['assessable']].copy()
    labels = a['true_label'].values.astype(int)
    scores = a['score'].values.astype(float)
    prmsc  = a['prmsc'].values.astype(float)

    n_correct = int((labels == 1).sum())
    n_overfit = int((labels == 0).sum())

    lauc = roc_auc_score(labels, scores)
    lmcc = matthews_corrcoef(labels, (scores > 0).astype(int))
    pauc = roc_auc_score(labels, prmsc)
    pmcc = matthews_corrcoef(labels, (prmsc >= 0.5).astype(int))

    print(f'\n{name}  (n={len(a)}, correct={n_correct}, overfit={n_overfit})')
    print(f'  {"Tool":<22} {"AUC":>8}  {"MCC":>8}')
    print(f'  {"-"*40}')
    print(f'  {"LLM4PatchCorrect":<22} {lauc:>8.4f}  {lmcc:>8.4f}')
    print(f'  {"PReMS":<22} {pauc:>8.4f}  {pmcc:>8.4f}')


def _id_join(llm, prems):
    prems_dedup = prems.drop_duplicates(subset='_norm_id', keep='first')
    merged = llm.merge(
        prems_dedup[['_norm_id', '_prmsc', '_assessable', 'label']],
        on='_norm_id', how='inner'
    )
    return pd.DataFrame({
        'true_label': merged['true_label'].values,
        'score':      merged['score'].values,
        'prmsc':      merged['_prmsc'].values,
        'assessable': merged['_assessable'].values,
    })


if __name__ == '__main__':
    print('=' * 50)
    for name, llm_path, prems_path in DATASETS:
        compute(name, llm_path, prems_path)
    print()