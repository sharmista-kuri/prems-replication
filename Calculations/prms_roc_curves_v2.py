import argparse
import csv
import sys
import warnings

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.metrics import roc_curve, roc_auc_score

warnings.filterwarnings("ignore")

DATASETS = [
    ("wang",    "Wang et al. (Classical)",  "#ff7f0e",  "--",            1.8),
    ("prism",   "PRISM (Classical)",        "#1f77b4",  "-",             2.8),
    ("llama",   "RepairLLaMA (LLM)",        "#2ca02c",  "-.",            1.8),
    ("srepair", "SRepair (LLM)",            "#d62728",  ":",             1.8),
    ("d4c",     "D4C (LLM)",                "#9467bd",  (0,(3,1,1,1)),   2.8),
]

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
    if v in ("1", "correct", "true"):
        return "correct"
    if v in ("0", "overfitting", "false"):
        return "overfitting"
    return v


def load(path, name):
    try:
        rows = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({k.strip(): v.strip() for k, v in row.items()})
    except FileNotFoundError:
        sys.exit(f"[{name}] File not found: {path}")

    df = pd.DataFrame(rows)

    for col in ("prmsc", "killed", "survived"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = 0.0
    df["killed"]   = df["killed"].fillna(0)
    df["survived"] = df["survived"].fillna(0)

    df["label"] = df["label"].apply(normalise_label)

    ok  = df[df["status"] == "OK"]
    ok_assessable = ok[(ok["killed"] + ok["survived"]) > 0].copy()

    fails = df[df["status"] == "FAILS_TRIGGER"].copy()
    fails["prmsc"] = 0.0

    assessable = pd.concat([ok_assessable, fails], ignore_index=True)
    assessable = assessable.dropna(subset=["prmsc", "label"]).reset_index(drop=True)
    assessable = assessable[assessable["label"].isin(["correct", "overfitting"])].reset_index(drop=True)

    assessable["y"] = (assessable["label"] == "overfitting").astype(int)

    n_c = (assessable["label"] == "correct").sum()
    n_o = (assessable["label"] == "overfitting").sum()
    print(f"[{name:10s}] n={len(assessable):4d}  correct={n_c:3d}  overfitting={n_o:3d}")
    return assessable


def remove_mislabeled(df, mislabeled_set):
    if "bug_id" not in df.columns or "patch_file" not in df.columns:
        print("  [warn] bug_id/patch_file columns not found — cannot filter mislabeled patches")
        return df
    mask = df.apply(
        lambda r: r["label"] == "overfitting"
                  and (r.get("bug_id", ""), r.get("patch_file", "")) in mislabeled_set,
        axis=1
    )
    removed = mask.sum()
    print(f"  → removed {removed} mislabeled overfitting patches")
    return df[~mask].reset_index(drop=True)


def compute_roc(df):
    y = df["y"].values
    s = -df["prmsc"].values
    fpr, tpr, _ = roc_curve(y, s)
    auc = roc_auc_score(y, s)
    return fpr, tpr, auc


def bootstrap_ci(df, n=10000, seed=42):
    rng = np.random.default_rng(seed)
    y = df["y"].values
    s = -df["prmsc"].values
    aucs = []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        yt, ys = y[idx], s[idx]
        if len(np.unique(yt)) < 2:
            continue
        aucs.append(roc_auc_score(yt, ys))
    return tuple(np.percentile(aucs, [2.5, 97.5]))


def plot(data_filtered, data_original, show_original, output, dpi):
    plt.rcParams.update({"font.family": "sans-serif"})
    fig, ax = plt.subplots(figsize=(6, 5))

    print("\n── AUC summary ──────────────────────────────────────────")
    for key, label, color, ls, lw in DATASETS:
        df_filt = data_filtered[key]
        fpr, tpr, auc = compute_roc(df_filt)
        lo, hi = bootstrap_ci(df_filt)

        suffix = ""
        if key == "llama":
            if show_original:
                df_orig = data_original[key]
                fpr_o, tpr_o, auc_o = compute_roc(df_orig)
                ax.plot(fpr_o, tpr_o,
                        color=color, linestyle=ls, linewidth=lw,
                        alpha=0.30,
                        label=f"{label} – original  (AUC = {auc_o:.3f})")
                print(f"  {key:10s}  original   AUC = {auc_o:.3f}")

        ax.plot(fpr, tpr, color=color, linestyle=ls, linewidth=lw,
                label=f"{label}{suffix}  (AUC = {auc:.3f})")
        print(f"  {key:10s}  filtered   AUC = {auc:.3f}  95% CI [{lo:.3f}, {hi:.3f}]")

    ax.plot([0, 1], [0, 1], "k--", linewidth=0.8, alpha=0.5,
            label="Random (AUC = 0.500)")

    ax.set_xlabel("False Positive Rate", fontsize=11)
    ax.set_ylabel("True Positive Rate", fontsize=11)
    ax.set_title("ROC curves: PReMS across datasets", fontsize=12)
    ax.legend(fontsize=10, loc="lower right", frameon=True)
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1.01])
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output, dpi=dpi, bbox_inches="tight")
    pdf_out = output.rsplit(".", 1)[0] + ".pdf"
    plt.savefig(pdf_out, dpi=dpi, bbox_inches="tight")
    print(f"\nSaved → {output}")
    print(f"Saved → {pdf_out}")
    plt.show()


def main():
    parser = argparse.ArgumentParser(
        description="Overlaid ROC curves for five PReMS datasets."
    )
    parser.add_argument("--wang",          required=True)
    parser.add_argument("--prism",         required=True)
    parser.add_argument("--llama",         required=True)
    parser.add_argument("--srepair",       required=True)
    parser.add_argument("--d4c",           required=True)
    parser.add_argument("--output",        default="PReMS_roc_curves.png")
    parser.add_argument("--dpi",           type=int, default=300)
    parser.add_argument("--show-original", action="store_true",
                        help="Also draw RepairLLaMA original curve (faded) for comparison")
    args = parser.parse_args()

    paths = {
        "wang":    args.wang,
        "prism":   args.prism,
        "llama":   args.llama,
        "srepair": args.srepair,
        "d4c":     args.d4c,
    }

    print("Loading datasets...\n")
    data_original = {key: load(path, key) for key, path in paths.items()}

    print("\nApplying mislabeled-patch filter to RepairLLaMA...\n")
    data_filtered = {}
    for key, df in data_original.items():
        if key == "llama":
            data_filtered[key] = remove_mislabeled(df.copy(), MISLABELED_LLAMA)
        else:
            data_filtered[key] = df

    plot(data_filtered, data_original, args.show_original, args.output, args.dpi)


if __name__ == "__main__":
    main()
