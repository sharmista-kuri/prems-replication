import pickle
import pandas as pd
import difflib

patches  = pd.read_csv('pred_d4c_gpt_archived.csv')
mutation = pd.read_csv('d4c_prms_results.csv')

mutation = mutation.drop_duplicates(subset='slug', keep='first')

merged = pd.merge(patches, mutation, on='slug', how='inner')
print(f'Patches in pred csv:    {len(patches)}')
print(f'Patches in mutation csv: {len(mutation)}')
print(f'Matched after merge:     {len(merged)}')

labels, diffs, bug_ids = [], [], []
fail_test_names, fail_test_src = [], []
coverages, traces_short, traces_full = [], [], []

for _, row in merged.iterrows():
    labels.append(int(row['label']))

    buggy_lines = str(row['bug']).splitlines(keepends=True)
    fixed_lines = str(row['fix']).splitlines(keepends=True)
    diff = ''.join(difflib.unified_diff(buggy_lines, fixed_lines, lineterm=''))
    diffs.append(diff)

    bug_ids.append(str(row['slug']).replace('_', '-'))

    fail_test_names.append([])
    fail_test_src.append({})
    coverages.append('')
    traces_short.append([])
    traces_full.append([])

enhanced = (labels, diffs, bug_ids, fail_test_names,
            fail_test_src, coverages, traces_short, traces_full)

out = 'd4c_test_v1_enhanced.pkl'
with open(out, 'wb') as f:
    pickle.dump(enhanced, f)

print(f'Saved: {out}  Total: {len(labels)}  Correct: {sum(labels)}  Overfitting: {len(labels)-sum(labels)}')