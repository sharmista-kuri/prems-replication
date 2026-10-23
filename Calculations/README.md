# Calculations

This folder has the scripts that turn the per-patch PReMS and JaCoCo result CSVs from the five experiments (Wang, PRISM, RepairLLaMA, SRepair, D4C) into the paper's numbers: AUC tables, the coverage-baseline comparison, and the ROC figure.

## Contents

| Path | What it is |
|---|---|
| `compute_auc.py` | Per-dataset PReMS AUC with a 95% bootstrap CI, a Mann-Whitney p-value, patch counts, and MCC@0.5 (MCC for RepairLLaMA only) |
| `compute_baseline_auc.py` | Compares PReMS against JaCoCo coverage baselines (line, branch, and patched-line coverage) |
| `prms_roc_curves_v2.py` | Draws the ROC curves of all five datasets on one plot (PNG and PDF) |
| `csv/<dataset>_prms_results.csv` | PReMS results for each patch: `status`, `killed`, `survived`, `prmsc`, `label` |
| `csv/<dataset>_jacoco_results.csv` | JaCoCo coverage for each patch: `line_coverage`, `branch_coverage`, `patched_line_coverage`, `status` |
| `csv/baseline_auc_summary.csv` | Written by `compute_baseline_auc.py` |
| `csv/baseline_auc_paired.csv` | Paired PReMS-vs-baseline AUC deltas with DeLong p-values. No script in this folder writes this file. |
| `PReMS_roc_curves.png`, `prms_roc_curves.pdf` | The ROC figure that was already generated |

## Setup

You need Python 3.9 or later.

```bash
pip install pandas numpy scikit-learn scipy matplotlib
```

Run every command below from inside this `Calculations/` folder. The scripts use relative `csv/` paths.

> **Windows note:** `compute_auc.py` prints box-drawing characters. In a cp1252 console it crashes with `UnicodeEncodeError`. Before you run it, set `PYTHONIOENCODING=utf-8`:
> PowerShell: `$env:PYTHONIOENCODING="utf-8"`
> Git Bash: `export PYTHONIOENCODING=utf-8`

## How the scripts score patches

- **Assessable patches:**
  - Patches with `status == OK` that produced at least one mutant (`killed + survived > 0`).
- **Positive class:** overfitting. A low PReMSc predicts overfitting, so the scripts compute AUC on `-prmsc`.
- **Label filter:** 10 RepairLLaMA patches that were confirmed as mislabeled are removed. They come from Math_57, Chart_12, and Lang_33. The patches are labeled overfitting but are semantically correct.
- **Label formats:** `1`/`correct` and `0`/`overfitting` are both accepted.

## 1. PReMS AUC table: `compute_auc.py`

```bash
python compute_auc.py csv/wang_prms_results.csv csv/prism_prms_results.csv csv/repairllama_prms_results.csv csv/srepair_prms_results.csv csv/d4c_prms_results.csv
```



## 2. PReMS vs. coverage baselines: `compute_baseline_auc.py`

```bash
python compute_baseline_auc.py
```

This script takes no arguments. It reads the `csv/*_prms_results.csv` and `csv/*_jacoco_results.csv` file for each dataset and matches up rows for the same patch. 
```

- An asterisk (`*`) marks the best AUC in each row.
- If JaCoCo failed on some patches, the script also reports PReMS on the same subset of patches. In the dataset name, a `+` marks this case.
- The summary is saved to `csv/baseline_auc_summary.csv`. **This overwrites the existing file.**

## 3. ROC figure: `prms_roc_curves_v2.py`

```bash
python prms_roc_curves_v2.py --wang csv/wang_prms_results.csv --prism csv/prism_prms_results.csv --llama csv/repairllama_prms_results.csv --srepair csv/srepair_prms_results.csv --d4c csv/d4c_prms_results.csv --output PReMS_roc_curves.png
```




