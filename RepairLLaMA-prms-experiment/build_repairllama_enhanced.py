import pickle
import pandas as pd
import difflib

patches  = pd.read_csv('repairllama_patches_export.csv')
mutation = pd.read_csv('repairllama_prms_results.csv')

assert len(patches) == len(mutation), f"Row count mismatch: {len(patches)} vs {len(mutation)}"

label_match = (patches['label'].values == mutation['label'].values).mean()
print(f'Label agreement: {label_match:.1%}')

labels, diffs, bug_ids = [], [], []
fail_test_names, fail_test_src = [], []
coverages, traces_short, traces_full = [], [], []

for i in range(len(patches)):
    p = patches.iloc[i]
    m = mutation.iloc[i]

    labels.append(int(p['label']))

    buggy_lines = str(p['bug_code']).splitlines(keepends=True)
    fixed_lines = str(p['patch_code']).splitlines(keepends=True)
    diff = ''.join(difflib.unified_diff(buggy_lines, fixed_lines, lineterm=''))
    diffs.append(diff)

    bug_ids.append(str(m['bug_id']))
    fail_test_names.append([])
    fail_test_src.append({})
    coverages.append('')
    traces_short.append([])
    traces_full.append([])

enhanced = (labels, diffs, bug_ids, fail_test_names,
            fail_test_src, coverages, traces_short, traces_full)

out = 'repairllama_prems_test_v1_enhanced.pkl'
with open(out, 'wb') as f:
    pickle.dump(enhanced, f)

print(f'\nSaved: {out}')
print(f'Total: {len(labels)}')
print(f'Correct: {sum(labels)}, Overfitting: {len(labels)-sum(labels)}')