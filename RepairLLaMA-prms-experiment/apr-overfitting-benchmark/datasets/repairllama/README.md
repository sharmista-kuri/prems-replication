# RepairLlama Dataset

## Source

We analyse the patches from the RepairLlama experiments.
This experiment executes different trials, each one with different LLMs and prompt strategies, and stores the trial results in a file.
The authors of the RepairLlama conducted manual assessment, which is included in the results files.
In this experiment we analyse the results that include the manual analysis of the three authors of repairLlama: https://github.com/ASSERT-KTH/repairllama/tree/main/results/3_martin

## Patches

There are two folders: `context_size_3_evaluation_defects4j_repairllama_ir4_or2_martin` and `context_size_3_filtered`.
The first one, `context_size_3_evaluation_defects4j_repairllama_ir4_or2_martin` contains the diffs computed from the information present on the RepairLlama results for their configuration with the strongest performance [(IR4 x OR2 (RepairLLaMA), found in this file)](https://github.com/ASSERT-KTH/repairllama/blob/main/results/3_martin/evaluation_defects4j_repairllama_ir4_or2_martin.jsonl). `context_size_3` represents the size of the context in the diffs we computed (3 lines before/after a change).

To remain in line with the experiments conducted on the [Petke et al. (classical) dataset](https://github.com/SOLAR-group/overfitting-baseline-artefact/tree/main/scripts/results/manual_output), the full set of patches were filtered to only include bugs covered by the classical dataset.

This filtering is achieved using the following script:
```shell
python repairllama_filtering.py
```

The second folder `context_size_3_filtered` contains the filtered patches used in our experiments.
