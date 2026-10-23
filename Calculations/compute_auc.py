import csv
import sys
import warnings

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu
from sklearn.metrics import matthews_corrcoef, roc_auc_score

warnings.filterwarnings("ignore")

MISLABELED_LLAMA = {
    ("Math_57",  "Math_57_repairllama-ir4_patch_1.diff"),
    ("Math_57",  "Math_57_repairllama-ir4_patch_4.diff"),
    ("Math_57",  "Math_57_repairllama-ir4_patch_5.diff"),
    ("Chart_12", "Chart_12_repairllama-ir4_patch_1.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_2.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_3.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_5.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_6.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_7.diff"),
    ("Lang_33",  "Lang_33_repairllama-ir4_patch_8.diff"),
}


def normalise_label(raw):
    v = str(raw).strip().lower()
    if v in ("1", "correct", "true"):      return "correct"
    if v in ("0", "overfitting", "false"): return "overfitting"
    return None


def load_full(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({k.strip(): v.strip() for k, v in row.items()})
    df = pd.DataFrame(rows)
    if "prmsc" in df.columns:
        df["prmsc_no_mutants"] = df["prmsc"].str.strip().str.upper() == "NO_MUTANTS"
    else:
        df["prmsc_no_mutants"] = False
    for col in ("prmsc", "killed", "survived"):
        if col not in df.columns:
            df[col] = 0.0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["label"] = df["label"].apply(normalise_label)
    return df


def make_assessable(df):
    ok    = df[(df["status"] == "OK") & ((df["killed"] + df["survived"]) > 0)
               & ~df["prmsc_no_mutants"]].copy()
    fails = df[df["status"] == "FAILS_TRIGGER"].copy()
    fails["prmsc"] = 0.0
    assessable = (pd.concat([ok, fails], ignore_index=True)
                    .dropna(subset=["prmsc", "label"])
                    .query("label in ['correct', 'overfitting']")
                    .reset_index(drop=True))
    assessable["y"] = (assessable["label"] == "overfitting").astype(int)
    return assessable


def count_columns(df_full):
    if "slug" in df_full.columns:
        def status_priority(s):
            if s == "OK":             return 0
            if s == "FAILS_TRIGGER":  return 1
            return 2
        df_full = df_full.copy()
        df_full["_pri"] = df_full["status"].apply(status_priority)
        df_full = (df_full.sort_values("_pri")
                          .drop_duplicates(subset=["slug"], keep="first")
                          .drop(columns=["_pri"])
                          .reset_index(drop=True))

    total = df_full["label"].notna().sum()

    is_ok        = df_full["status"] == "OK"
    is_fails     = df_full["status"] == "FAILS_TRIGGER"
    is_no_mut_status = df_full["status"] == "NO_MUTANTS"
    has_mutants  = ((df_full["killed"] + df_full["survived"]) > 0) & ~df_full["prmsc_no_mutants"]

    assessable = ((is_ok & has_mutants) | is_fails).sum()
    no_mut     = (is_no_mut_status | (is_ok & ~has_mutants)).sum()
    non_ok     = (~is_ok & ~is_fails & ~is_no_mut_status).sum()

    return total, assessable, no_mut, non_ok


def bootstrap_ci(y, s, n=10_000, seed=42):
    rng = np.random.default_rng(seed)
    aucs = []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        yt, ys = y[idx], s[idx]
        if len(np.unique(yt)) > 1:
            aucs.append(roc_auc_score(yt, ys))
    return tuple(np.percentile(aucs, [2.5, 97.5]))


def mcc_at_threshold(df, threshold=0.5):
    y_pred = (df["prmsc"].values < threshold).astype(int)
    return matthews_corrcoef(df["y"].values, y_pred)


def mannwhitney_p(y, s):
    prmsc       = -s
    correct     = prmsc[y == 0]
    overfitting = prmsc[y == 1]
    _, p = mannwhitneyu(correct, overfitting, alternative="greater")
    return p


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python compute_auc.py file1.csv file2.csv ...")

    hdr = (f"{'File':<35}  {'Total':>6}  {'Assess.':>7}  {'Overfit.':>8}  "
           f"{'Correct':>7}  {'NO_MUT':>6}  {'Non-OK':>6}  "
           f"{'AUC':>6}  {'95% CI':>13}  {'p':>7}  {'MCC@0.5':>8}")
    print(f"\n{hdr}")
    print("─" * len(hdr))

    for path in sys.argv[1:]:
        df_full = load_full(path)
        df      = make_assessable(df_full)
        name    = path.split("/")[-1].split("\\")[-1]

        if "bug_id" in df.columns and "patch_file" in df.columns:
            mask = df.apply(
                lambda r: r["label"] == "overfitting"
                          and (r.get("bug_id", ""), r.get("patch_file", "")) in MISLABELED_LLAMA,
                axis=1)
            df = df[~mask].reset_index(drop=True)

        total, assessable_n, no_mut, non_ok = count_columns(df_full)
        n_correct = (df["label"] == "correct").sum()
        n_over    = (df["label"] == "overfitting").sum()

        y = df["y"].values
        s = -df["prmsc"].values

        if len(np.unique(y)) < 2:
            print(f"  {name:<33}  skipped — only one class present")
            continue

        auc = roc_auc_score(y, s)
        lo, hi = bootstrap_ci(y, s)
        p = mannwhitney_p(y, s)

        p_str   = "<0.001" if p < 0.001 else f"{p:.3f}"
        is_llama = "repairllama" in name.lower()
        mcc_str = f"{mcc_at_threshold(df):>8.3f}" if is_llama else f"{'—':>8}"

        print(f"  {name:<33}  {total:>6}  {len(df):>7}  {n_over:>8}  "
              f"{n_correct:>7}  {no_mut:>6}  {non_ok:>6}  "
              f"{auc:>6.3f}  [{lo:.3f}, {hi:.3f}]  {p_str:>7}  {mcc_str}")

    print()
