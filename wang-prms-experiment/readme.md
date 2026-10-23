# Wang PReMS Experiment


## Requirements
- Docker Desktop
- VS Code + Dev Containers extension

The dev container provides Java 11, Defects4J, and Python 3 (numpy, pandas, scipy, scikit-learn, matplotlib). PIT 1.9.0 jars are downloaded to `/opt/pitest-libs` at image build time (override with `PIT_LIBS`), and JaCoCo jars are in `jacoco-libs/`.


## How to Run
1. Open this folder in VS Code.
2. Click **Reopen in Container** when prompted.
3. Build the patch inventory and run the PReMS pipeline:
   ```bash
   python3 run_all_pipeline.py
   ```

4. Run the JaCoCo coverage pipeline:
   ```bash
   python3 run_wang_jacoco.py patch_inventory.csv wang_jacoco_results.csv
   ```


## Output
- `patch_inventory.csv`: one row per patch, with its label
- `wang_prms_results.csv`: PReMS results (PIT reports in `pit_reports/`)
- `wang_jacoco_results.csv`: JaCoCo coverage results (reports in `jacoco_reports/`)

