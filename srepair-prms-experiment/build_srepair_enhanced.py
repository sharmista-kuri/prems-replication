import pickle
import pandas as pd
import difflib
import ast

patches  = pd.read_csv('srepair_patches_export.csv')
mutation = pd.read_csv('srepair_prms_results.csv')



mutation['slug'] = mutation['bug_id'] + '_' + mutation.index.astype(str)

merged = pd.merge(patches, mutation, on='slug', how='inner')

print(f'Patches in patches csv:  {len(patches)}')
print(f'Patches in mutation csv: {len(mutation)}')
print(f'Matched after merge:     {len(merged)}')

labels, diffs, bug_ids = [], [], []
fail_test_names, fail_test_src = [], []
coverages, traces_short, traces_full = [], [], []

for _, row in merged.iterrows():
    labels.append(int(row['label_x']))

    buggy_lines = str(row['bug_code']).splitlines(keepends=True)
    fixed_lines = str(row['patch_code']).splitlines(keepends=True)
    diff = ''.join(difflib.unified_diff(buggy_lines, fixed_lines, lineterm=''))
    diffs.append(diff)

    bug_ids.append(str(row['bug_id']))

    fail_test_names.append([])

    fail_test_src.append({})
    coverages.append('')
    traces_short.append([])
    traces_full.append([])

enhanced = (labels, diffs, bug_ids, fail_test_names,
            fail_test_src, coverages, traces_short, traces_full)

out = 'srepair_test_v1_enhanced.pkl'
with open(out, 'wb') as f:
    pickle.dump(enhanced, f)

print(f'\nSaved: {out}')
print(f'Total: {len(labels)}')
print(f'Correct: {sum(labels)}, Overfitting: {len(labels)-sum(labels)}')