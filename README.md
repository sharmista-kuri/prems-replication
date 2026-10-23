# PReMS Replication

This is the replication package for **PReMS**, a mutation-based score for spotting overfitting patches from automated program repair (APR). PReMS runs PIT mutation testing on every patched program and gives the patch a score, `prmsc`. A low `prmsc` predicts that the patch is overfitting.

The experiments use patches from five APR datasets, all on Defects4J bugs. PReMS is compared against JaCoCo coverage baselines and against three automated patch-correctness assessment (APCA) tools.

## Repository layout

| Folder | What it contains |
|---|---|
| `wang-prms-experiment/` | PReMS and JaCoCo pipeline for the Wang dataset |
| `Prism-Prms-Experiment/` | PReMS and JaCoCo pipeline for PRISM |
| `RepairLLaMA-prms-experiment/` | PReMS and JaCoCo pipeline for RepairLLaMA |
| `srepair-prms-experiment/` | PReMS and JaCoCo pipeline for SRepair |
| `D4C-prems-experiment/` | PReMS and JaCoCo pipeline for D4C |
| `APCA Tools/` | Comparison with Entropy Delta, FixCheck, and LLM4PatchCorrect |
| `Calculations/` | Scripts that compute the AUC tables, the baseline comparison, and the ROC figure |

Every folder has its own README with full instructions.

## How to reproduce

1. **Run the experiments.** Each `*-prms-experiment/` folder ships a dev container with Java 11, Defects4J, PIT, and JaCoCo. To use it you need Docker Desktop and VS Code with the Dev Containers extension. Open the folder, choose **Reopen in Container**, and then run the commands in that folder's README. 
2. **Compute the results.** Follow the instructions in `Calculations/README.md`.
3. **APCA comparison.** Run the notebooks and scripts in each subfolder of `APCA Tools/`.



## Results

- PReMS AUC per dataset: printed by `Calculations/compute_auc.py`
- Comparison with the coverage baselines: `Calculations/csv/baseline_auc_summary.csv`
- ROC curves: `Calculations/PReMS_roc_curves.png`
- APCA tool comparison: the `*-results/` folders inside `APCA Tools/`


