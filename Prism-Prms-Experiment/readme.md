# PRISM PReMS Experiment

## Requirements
- Docker Desktop
- VS Code + Dev Containers extension

The dev container installs Java 11, Defects4J, PIT 1.9.0 (in `/opt/pitest-libs`)
and the Python dependencies. The JaCoCo jars are in `jacoco-libs/`.

## How to Run
1. Open this folder in VS Code.
2. Click **Reopen in Container** when prompted.
3. Run the PReMS pipeline:
   ```
   python3 prms_prism_runner.py --prism-dir PRISM --buggy-dir buggy_checkouts
   ```
4. Run the JaCoCo coverage pipeline:
   ```
   python3 jacoco_prism_runner.py --prism-dir PRISM
   ```


## Output
- `prism_prms_results.csv`: PReMS results (killed/survived/no-coverage mutants and the `prmsc` score per patch)
- `prism_jacoco_results.csv`: line, branch and patched-line coverage per patch



