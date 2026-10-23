import pickle
import pandas as pd
import difflib

patches  = pd.read_csv('wang_patches_export.csv')
mutation = pd.read_csv('wang_prms_results.csv').dropna(subset=['bug_id'])
mutation['slug'] = mutation['bug_id'] + '_' + mutation['tool']

merged = pd.merge(patches, mutation, on='slug', how='inner')

patches_sorted  = patches.sort_values('slug').reset_index(drop=True)
mutation_sorted = mutation.sort_values('slug').reset_index(drop=True)

mutation_filtered = mutation[mutation['slug'].isin(patches['slug'].unique())]
mutation_filtered = mutation_filtered.sort_values('slug').reset_index(drop=True)
patches_sorted    = patches.sort_values('slug').reset_index(drop=True)

assert len(patches_sorted) == len(mutation_filtered), \
    f"Still mismatched: {len(patches_sorted)} vs {len(mutation_filtered)}"

label_match = (patches_sorted['label'].values == mutation_filtered['label'].values).mean()
print(f'Label agreement: {label_match:.1%}')

labels, diffs, bug_ids = [], [], []
fail_test_names, fail_test_src = [], []
coverages, traces_short, traces_full = [], [], []

for i in range(len(patches_sorted)):
    p = patches_sorted.iloc[i]
    m = mutation_filtered.iloc[i]

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

out = 'wang_test_v1_enhanced.pkl'
with open(out, 'wb') as f:
    pickle.dump(enhanced, f)

print(f'\nSaved: {out}')
print(f'Total: {len(labels)}')
print(f'Correct: {sum(labels)}, Overfitting: {len(labels)-sum(labels)}')