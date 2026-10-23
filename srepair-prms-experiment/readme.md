# SRepair PReMS Experiment

Runs mutation-based (PReMS, via PIT) and JaCoCo coverage analysis on SRepair patches for Defects4J bugs.

## Requirements
- Docker Desktop
- VS Code + Dev Containers extension

The dev container provides Java 11, Defects4J, and Python 3. PIT 1.9.0 jars are in `pitest-libs/` and JaCoCo jars are in `jacoco-libs/`.

## Inputs
- `srepair_labeled.json`: labeled SRepair patches
- `SRepair/SRepair/dataset/defects4j-sf.json`: SRepair Defects4J dataset

## How to Run
1. Open this folder in VS Code.
2. Click **Reopen in Container** when prompted.
3. Run the PReMS pipeline:
   ```bash
   python3 run_srepair.py              
   ```
4. Run the JaCoCo coverage pipeline:
   ```bash
   python3 run_jacoco_srepair_unified.py            
  
   ```

## Output

- `srepair_prms_results.csv`: final merged PReMS results
- `srepair_jacoco_results.csv`: JaCoCo coverage results
