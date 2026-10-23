import os
import shutil

# list of bugs covered in the petke dataset
valid_patches = ['Chart_1', 'Chart_11', 'Chart_12', 'Chart_13', 'Chart_15', 'Chart_17', 'Chart_18', 'Chart_20', 'Chart_24', 'Chart_25', 'Chart_26', 'Chart_4', 'Chart_5', 'Chart_7', 'Chart_8', 'Chart_9', 'Closure_10', 'Closure_115', 'Closure_126', 'Closure_129', 'Closure_13', 'Closure_14', 'Closure_19', 'Closure_2', 'Closure_21', 'Closure_22', 'Closure_46', 'Closure_48', 'Closure_57', 'Closure_62', 'Closure_73', 'Closure_86', 'Lang_10', 'Lang_33', 'Lang_43', 'Lang_44', 'Lang_45', 'Lang_46', 'Lang_51', 'Lang_55', 'Lang_57', 'Lang_58', 'Lang_59',
                 'Lang_6', 'Lang_63', 'Math_101', 'Math_105', 'Math_11', 'Math_15', 'Math_2', 'Math_20', 'Math_28', 'Math_30', 'Math_32', 'Math_33', 'Math_34', 'Math_41', 'Math_42', 'Math_49', 'Math_5', 'Math_50', 'Math_53', 'Math_57', 'Math_58', 'Math_59', 'Math_6', 'Math_62', 'Math_63', 'Math_64', 'Math_65', 'Math_69', 'Math_7', 'Math_70', 'Math_71', 'Math_73', 'Math_75', 'Math_79', 'Math_8', 'Math_80', 'Math_81', 'Math_82', 'Math_84', 'Math_85', 'Math_88', 'Math_89', 'Math_95', 'Math_96', 'Math_97', 'Mockito_29', 'Mockito_38', 'Time_11', 'Time_7']

# get directory of repairllama patches
base_dir = os.path.dirname(os.path.abspath(__file__))
repairllama_patches_dir = os.path.join(
    base_dir, "context_size_3_evaluation_defects4j_repairllama_ir4_or2_martin"
)
repairllama_patches_dir = os.path.abspath(repairllama_patches_dir)
print(f"RepairLlama patches directory: {repairllama_patches_dir}")

# get directory of filtered patches
output_base_dir = os.path.join(
    base_dir, "context_size_3_filtered"
)
output_base_dir = os.path.abspath(output_base_dir)

# Remove the output_base_dir if it exists
if os.path.exists(output_base_dir):
    shutil.rmtree(output_base_dir)

os.makedirs(output_base_dir, exist_ok=True)

valid_correct_patches = []
valid_overfitting_patches = []
for patch_type in ["correct", "overfitting"]:
    patch_dir = os.path.join(repairllama_patches_dir, patch_type)
    if not os.path.exists(patch_dir):
        print(f"Patch directory does not exist: {patch_dir}")
        continue

    # get all files in the patch directory
    files = os.listdir(patch_dir)
    print(f"Files in {patch_type} directory: {files}")
    print(f"Number of files in {patch_type} directory: {len(files)}\n\n\n")

    # filter files by valid_patches list
    for file in files:
        if any(f"{valid_patch}_" in file for valid_patch in valid_patches):
            if patch_type == "correct":
                valid_correct_patches.append(file)
            else:
                valid_overfitting_patches.append(file)

print("Valid Correct Patches:")
print(valid_correct_patches)
print("Number of valid correct patches:", len(valid_correct_patches))
print("\n\n\n")
print("Valid Overfitting Patches:")
print(valid_overfitting_patches)
print("Number of valid overfitting patches:", len(valid_overfitting_patches))

# copy filtered patches to the output directory
for patch_type, valid_patches in zip(["correct", "overfitting"], [valid_correct_patches, valid_overfitting_patches]):
    output_dir = os.path.join(output_base_dir, patch_type)
    os.makedirs(output_dir, exist_ok=True)
    for file in valid_patches:
        src_file = os.path.join(repairllama_patches_dir, patch_type, file)
        dst_file = os.path.join(output_dir, file)
        print(f"Copying {src_file} to {dst_file}")
        shutil.copy(src_file, dst_file)
