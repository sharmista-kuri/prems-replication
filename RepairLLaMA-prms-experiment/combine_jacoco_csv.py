#!/usr/bin/env python3

import csv

ORIGINAL = "repairllama_jacoco_results.csv"
RERUN    = "repairllama_jacoco_rerun.csv"
OUTPUT   = "repairllama_jacoco_results.csv"

rerun_rows = {}
with open(RERUN, newline="") as f:
    for row in csv.DictReader(f):
        rerun_rows[row["patch_file"].strip()] = row

rows_out = []
with open(ORIGINAL, newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    for row in reader:
        pf = row["patch_file"].strip()
        if row["status"].strip() != "OK" and pf in rerun_rows:
            rows_out.append(rerun_rows[pf])
        else:
            rows_out.append(row)

with open(OUTPUT, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows_out)

from collections import Counter
counts = Counter(r["status"].strip() for r in rows_out)
print(f"Written {len(rows_out)} rows to {OUTPUT}")
for status, n in counts.most_common():
    print(f"  {status}: {n}")