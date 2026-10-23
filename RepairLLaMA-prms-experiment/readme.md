# RepairLLaMA PReMS Experiment

## Requirements
- Docker Desktop
- VS Code + Dev Containers extension

## How to Run
1. Open this folder in VS Code
2. Click Reopen in Container when prompted
3. Run `python3 run_all_pipeline.py`
4. After step 3 finishes, run the JaCoCo coverage pipeline with `python3 run_all_jacoco_pipeline.py` (it needs `repairllama_prms_results.csv` from step 3)

## Dataset
Patches are from [apr-overfitting-benchmark](https://github.com/SOLAR-group/apr-overfitting-benchmark) (commit `029a0dc`), folder `datasets/repairllama/context_size_3_filtered/`.

## Output
- PReMS results are written to `repairllama_prms_results.csv`
- JaCoCo results are written to `repairllama_jacoco_results.csv`


