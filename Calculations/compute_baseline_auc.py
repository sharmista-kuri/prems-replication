#!/usr/bin/env python3
import pandas as pd
import numpy as np
from sklearn.metrics import roc_auc_score

CSV_DIR = "csv"

JACOCO_COLS  = ["line_coverage", "branch_coverage", "patched_line_coverage"]
JACOCO_NAMES = ["Line Cov", "Branch Cov", "Patched-Line"]


def compute_auc(labels, scores):
    mask = scores.notna()
    if mask.sum() < 2 or labels[mask].nunique() < 2:
        return None, 0
    return round(roc_auc_score(labels[mask], scores[mask]), 3), int(mask.sum())


def run_wang():
    prems  = pd.read_csv(f"{CSV_DIR}/wang_prms_results.csv")
    jacoco = pd.read_csv(f"{CSV_DIR}/wang_jacoco_results.csv")
    prems  = prems.dropna(subset=["bug_id"])
    prems["killed"]   = pd.to_numeric(prems["killed"],   errors="coerce").fillna(0)
    prems["survived"] = pd.to_numeric(prems["survived"], errors="coerce").fillna(0)
    prems["prmsc"]    = pd.to_numeric(prems["prmsc"],    errors="coerce")
    prems["label"]    = pd.to_numeric(prems["label"],    errors="coerce")
    assessable = prems[(prems["status"] == "OK") & (prems["killed"] + prems["survived"] > 0)]
    jacoco_dedup = (jacoco.sort_values("status", key=lambda s: (s != "OK").astype(int))
                          .drop_duplicates(subset=["bug_id", "tool"], keep="first"))
    merged = assessable.merge(
        jacoco_dedup[["bug_id", "tool"] + JACOCO_COLS + ["status"]],
        on=["bug_id", "tool"], how="inner", suffixes=("", "_jac"))
    merged["jacoco_status"] = merged["status_jac"]
    return "Wang", merged, "prmsc", "label", "jacoco_status"


def run_prism():
    prems  = pd.read_csv(f"{CSV_DIR}/prism_prms_results.csv")
    jacoco = pd.read_csv(f"{CSV_DIR}/prism_jacoco_results.csv")
    prems["killed"]   = pd.to_numeric(prems["killed"],   errors="coerce").fillna(0)
    prems["survived"] = pd.to_numeric(prems["survived"], errors="coerce").fillna(0)
    prems["prmsc"]    = pd.to_numeric(prems["prmsc"],    errors="coerce")
    prems["label"]    = pd.to_numeric(prems["label"],    errors="coerce")
    assessable = prems[(prems["status"] == "OK") & (prems["killed"] + prems["survived"] > 0)]
    merged = assessable.merge(
        jacoco[["patch_file"] + JACOCO_COLS + ["status"]],
        on="patch_file", how="inner", suffixes=("", "_jac"))
    merged["jacoco_status"] = merged["status_jac"]
    return "PRISM", merged, "prmsc", "label", "jacoco_status"


MISLABELED_LLAMA = {
    "Math_57_repairllama-ir4_patch_1.diff",
    "Math_57_repairllama-ir4_patch_4.diff",
    "Math_57_repairllama-ir4_patch_5.diff",
    "Chart_12_repairllama-ir4_patch_1.diff",
    "Lang_33_repairllama-ir4_patch_2.diff",
    "Lang_33_repairllama-ir4_patch_3.diff",
    "Lang_33_repairllama-ir4_patch_5.diff",
    "Lang_33_repairllama-ir4_patch_6.diff",
    "Lang_33_repairllama-ir4_patch_7.diff",
    "Lang_33_repairllama-ir4_patch_8.diff",
}

def run_repairllama():
    prems  = pd.read_csv(f"{CSV_DIR}/repairllama_prms_results.csv")
    jacoco = pd.read_csv(f"{CSV_DIR}/repairllama_jacoco_results.csv")
    prems["killed"]   = pd.to_numeric(prems["killed"],   errors="coerce").fillna(0)
    prems["survived"] = pd.to_numeric(prems["survived"], errors="coerce").fillna(0)
    prems["prmsc"]    = pd.to_numeric(prems["prmsc"],    errors="coerce")
    prems["label"]    = pd.to_numeric(prems["label"],    errors="coerce")
    prems  = prems[~prems["patch_file"].isin(MISLABELED_LLAMA)]
    jacoco = jacoco[~jacoco["patch_file"].isin(MISLABELED_LLAMA)]
    assessable = prems[(prems["status"] == "OK") & (prems["killed"] + prems["survived"] > 0)]
    merged = assessable.merge(
        jacoco[["patch_file"] + JACOCO_COLS + ["status"]],
        on="patch_file", how="inner", suffixes=("", "_jac"))
    merged["jacoco_status"] = merged["status_jac"]
    return "RepairLLaMA", merged, "prmsc", "label", "jacoco_status"


def run_srepair():
    prems  = pd.read_csv(f"{CSV_DIR}/srepair_prms_results.csv")
    jacoco = pd.read_csv(f"{CSV_DIR}/srepair_jacoco_results.csv")
    prems["killed"]   = pd.to_numeric(prems["killed"],   errors="coerce").fillna(0)
    prems["survived"] = pd.to_numeric(prems["survived"], errors="coerce").fillna(0)
    prems["prmsc"]    = pd.to_numeric(prems["prmsc"],    errors="coerce")
    prems["label"]    = pd.to_numeric(prems["label"],    errors="coerce")
    jacoco["label"]   = pd.to_numeric(jacoco["label"],   errors="coerce")
    ok = prems[(prems["status"] == "OK") & (prems["killed"] + prems["survived"] > 0)]
    fails = prems[prems["status"] == "FAILS_TRIGGER"].copy()
    fails["prmsc"] = 0.0
    assessable = pd.concat([ok, fails]).sort_index()
    merge_cols = ["bug_id", "label", "scope_lines"]
    assessable = assessable.sort_values(merge_cols).reset_index(drop=True)
    jacoco = jacoco.sort_values(merge_cols).reset_index(drop=True)
    assessable["_rank"] = assessable.groupby(merge_cols).cumcount()
    jacoco["_rank"]     = jacoco.groupby(merge_cols).cumcount()
    merged = assessable.merge(
        jacoco[merge_cols + ["_rank"] + JACOCO_COLS + ["status"]],
        on=merge_cols + ["_rank"], how="left", suffixes=("", "_jac"))
    merged["jacoco_status"] = merged["status_jac"]
    return "SRepair", merged, "prmsc", "label", "jacoco_status"


def run_d4c():
    prems  = pd.read_csv(f"{CSV_DIR}/d4c_prms_results.csv")
    jacoco = pd.read_csv(f"{CSV_DIR}/d4c_jacoco_results.csv")
    prems["killed"]   = pd.to_numeric(prems["killed"],   errors="coerce").fillna(0)
    prems["survived"] = pd.to_numeric(prems["survived"], errors="coerce").fillna(0)
    prems["prmsc"]    = pd.to_numeric(prems["prmsc"],    errors="coerce")
    prems["label"]    = pd.to_numeric(prems["label"],    errors="coerce")
    assessable = prems[(prems["status"] == "OK") & (prems["killed"] + prems["survived"] > 0)]
    jacoco = (jacoco.sort_values("status", key=lambda s: (s != "OK").astype(int))
                    .drop_duplicates(subset=["slug"], keep="first"))
    merged = assessable.merge(
        jacoco[["slug"] + JACOCO_COLS + ["status"]],
        on="slug", how="inner", suffixes=("", "_jac"))
    merged["jacoco_status"] = merged["status_jac"]
    return "D4C", merged, "prmsc", "label", "jacoco_status"


def print_dataset(name, merged, prmsc_col, label_col, jac_status_col):
    print(f"\n{'='*50}")
    print(f"  {name}")
    print(f"{'='*50}")
    labels = merged[label_col]
    prmsc  = merged[prmsc_col]
    auc_p, n_p = compute_auc(labels, prmsc)
    print(f"  {'Metric':<26} {'AUC':>6}  {'n':>5}")
    print(f"  {'-'*40}")
    print(f"  {'PReMS':<26} {auc_p:>6.3f}  {n_p:>5}")
    row = {"dataset": name, "n": n_p, "PReMS": auc_p}
    jac_ok = merged[jac_status_col] == "OK"
    n_cov = n_p
    for col, cname in zip(JACOCO_COLS, JACOCO_NAMES):
        if col not in merged.columns:
            print(f"  {cname:<26} {'N/A':>6}")
            row[cname] = None
            continue
        vals = pd.to_numeric(merged[col], errors="coerce").copy()
        vals[~jac_ok] = np.nan
        auc_j, n_j = compute_auc(labels, vals)
        if auc_j is not None:
            print(f"  {cname:<26} {auc_j:>6.3f}  {n_j:>5}")
            n_cov = min(n_cov, n_j)
        else:
            print(f"  {cname:<26} {'N/A':>6}  {n_j:>5}")
        row[cname] = auc_j
    row["n_coverage"] = n_cov
    row["PReMS_same_subset"] = None
    if n_cov < n_p:
        auc_s, n_s = compute_auc(labels[jac_ok], prmsc[jac_ok])
        row["PReMS_same_subset"] = auc_s
        print(f"  {'PReMS (same ' + str(n_s) + ' patches)':<26} {auc_s:>6.3f}  {n_s:>5}")
    return row


def print_summary(results):
    results = [r for r in results if r is not None]
    cols = JACOCO_NAMES + ["PReMS"]
    print(f"\n\n{'='*72}")
    print("  SUMMARY — AUC: Coverage Baselines vs PReMS   (* = best in row)")
    print(f"{'='*72}")
    print(f"  {'Dataset':<14} {'n':>5} | {'Line':>7} {'Branch':>7} {'P-Line':>7} | {'PReMS':>7}")
    print(f"  {'-'*56}")

    def cell(v, best):
        if v is None:
            return f"{'N/A':>7}"
        return f"{v:>6.3f}" + ("*" if v == best else " ")

    notes, wins = [], 0
    for r in results:
        vals = [r.get(c) for c in cols]
        best = max(v for v in vals if v is not None)
        wins += (r["PReMS"] == best)
        mark = ""
        if r["PReMS_same_subset"] is not None:
            mark = "+"
            notes.append(f"  + {r['dataset']}: coverage AUCs on the {r['n_coverage']} patches where "
                         f"JaCoCo succeeded; PReMS on those same patches = {r['PReMS_same_subset']:.3f}")
        ds = r["dataset"] + mark
        print(f"  {ds:<14} {r['n']:>5} | {cell(vals[0], best)}{cell(vals[1], best)}"
              f"{cell(vals[2], best)} | {cell(vals[3], best)}")
    print()
    print(f"  PReMS has the highest AUC on {wins} of {len(results)} datasets")
    print("  n = PReMS assessable patches")
    for line in notes:
        print(line)

    out = f"{CSV_DIR}/baseline_auc_summary.csv"
    df = pd.DataFrame(results)
    for c in ("n", "n_coverage"):
        df[c] = df[c].astype("Int64")
    df.to_csv(out, index=False)
    print(f"  Saved: {out}")


def main():
    runners = [run_wang, run_prism, run_repairllama, run_srepair, run_d4c]
    results = []
    for runner in runners:
        try:
            name, merged, prmsc_col, label_col, jac_status_col = runner()
            row = print_dataset(name, merged, prmsc_col, label_col, jac_status_col)
            results.append(row)
        except FileNotFoundError as e:
            print(f"\nSKIPPED — {e}")
            results.append(None)
        except Exception as e:
            print(f"\nERROR in {runner.__name__}: {e}")
            import traceback; traceback.print_exc()
            results.append(None)
    print_summary(results)


if __name__ == "__main__":
    main()
