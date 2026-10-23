import os
import re
import csv

PATCHES_ROOT = "/workspaces/wang-prms-experiment/wang_patches/Patches"

LABEL_MAP = {
    "Dsame": 1,
    "Ddifferent": 1,
    "Doverfitting": 0,
}

PROJECTS = ["Math", "Lang"]
SOURCES = ["Patches_ICSE", "Patches_others"]

rows = []

for source in SOURCES:
    for label_dir, label in LABEL_MAP.items():
        label_path = os.path.join(PATCHES_ROOT, source, label_dir)
        if not os.path.exists(label_path):
            continue
        for tool in os.listdir(label_path):
            tool_path = os.path.join(label_path, tool)
            if not os.path.isdir(tool_path):
                continue
            for project in PROJECTS:
                proj_path = os.path.join(tool_path, project)
                if not os.path.exists(proj_path):
                    continue
                for fname in os.listdir(proj_path):
                    if not fname.endswith(".patch"):
                        continue
                    patch_file = os.path.join(proj_path, fname)

                    m = re.search(r'-(Math|Lang)-(\d+)-', fname)
                    if not m:
                        print(f"  WARNING: could not parse bug id from {fname}")
                        continue

                    bug_project = m.group(1)
                    bug_number = int(m.group(2))
                    bug_id = f"{bug_project}-{bug_number}"

                    rows.append({
                        "patch_file": patch_file,
                        "bug_id": bug_id,
                        "project": bug_project,
                        "bug_number": bug_number,
                        "tool": tool,
                        "label": label,
                        "label_dir": label_dir,
                        "source": source,
                    })

out_file = "/workspaces/wang-prms-experiment/patch_inventory.csv"
fieldnames = ["patch_file", "bug_id", "project", "bug_number", "tool", "label", "label_dir", "source"]

with open(out_file, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"\nTotal patches: {len(rows)}")
print(f"Correct (label=1): {sum(1 for r in rows if r['label'] == 1)}")
print(f"Overfitting (label=0): {sum(1 for r in rows if r['label'] == 0)}")
print(f"\nBy project:")
for proj in PROJECTS:
    proj_rows = [r for r in rows if r['project'] == proj]
    correct = sum(1 for r in proj_rows if r['label'] == 1)
    overfit = sum(1 for r in proj_rows if r['label'] == 0)
    print(f"  {proj}: {len(proj_rows)} total ({correct} correct, {overfit} overfitting)")

print(f"\nInventory written to {out_file}")