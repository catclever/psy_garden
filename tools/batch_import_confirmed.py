#!/usr/bin/env python3
"""
Psy Garden - Batch Import Confirmed Plant Candidates
Reads tools/confirmed_candidates.json and packages approved candidates into plant assets.
"""

import os
import sys
import json
import re
import shutil
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CANDIDATES_DIR = os.path.join(SCRIPT_DIR, "candidates")
PROCESSED_DIR = os.path.join(CANDIDATES_DIR, "processed")
PLANTS_DIR = os.path.join(PROJECT_ROOT, "src", "assets", "plants")
CONFIRMED_FILE = os.path.join(SCRIPT_DIR, "confirmed_candidates.json")
CONFIRMED_PLANTS_FILE = os.path.join(SCRIPT_DIR, "confirmed_plants.json")

# Import the asset builder
sys.path.insert(0, SCRIPT_DIR)
import build_plant_asset

def get_next_variant_id(plant_dir):
    """Finds next available variant ID like 01, 02, etc."""
    if not os.path.exists(plant_dir):
        return "01"
    existing = [d for d in os.listdir(plant_dir) if os.path.isdir(os.path.join(plant_dir, d))]
    
    # Try finding numerical variants 01, 02...
    num = 1
    while True:
        candidate_var = f"{num:02d}"
        if candidate_var not in existing:
            return candidate_var
        num += 1

def main():
    if not os.path.exists(CONFIRMED_FILE):
        print(f"❌ Confirmed file not found: {CONFIRMED_FILE}")
        return

    with open(CONFIRMED_FILE, "r", encoding="utf-8") as f:
        try:
            confirmed_data = json.load(f)
        except Exception as e:
            print(f"❌ Failed to parse JSON: {e}")
            return

    if not confirmed_data:
        print("ℹ️ No confirmed candidates to process.")
        return

    filenames = list(confirmed_data.keys())
    total = len(filenames)
    print(f"🌱 Starting batch import of {total} confirmed candidate plants...")
    
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    os.makedirs(PLANTS_DIR, exist_ok=True)
    
    success_count = 0
    failed_count = 0
    remaining_confirmed = dict(confirmed_data)

    for idx, fn in enumerate(filenames, 1):
        input_path = os.path.join(CANDIDATES_DIR, fn)
        if not os.path.exists(input_path):
            print(f"[{idx}/{total}] ⚠️ File not found, skipping: {fn}")
            failed_count += 1
            continue

        # Parse plant identifier and Chinese display name
        m = re.search(r"(?:candidate_)?(?:plant_)+(\d+)_(.+)\.(?:jpg|png|jpeg)", fn)
        if m:
            num = int(m.group(1))
            display_name = m.group(2).strip()
            plant_name = f"plant_{num:04d}"
        else:
            # Fallback
            base_name, _ = os.path.splitext(fn)
            plant_name = base_name.replace("candidate_", "")
            display_name = plant_name

        plant_dir = os.path.join(PLANTS_DIR, plant_name)
        variant_id = get_next_variant_id(plant_dir)
        output_dir = os.path.join(plant_dir, variant_id)

        print(f"\n[{idx}/{total}] 🌿 Processing '{display_name}' ({plant_name}) -> Variant {variant_id}...")
        try:
            ok = build_plant_asset.build_asset(
                input_path=input_path,
                plant_name=plant_name,
                variant_id=variant_id,
                output_dir=output_dir,
                project_root=PROJECT_ROOT,
                display_name=display_name
            )
            if ok:
                success_count += 1
                # Safely archive the original candidate image
                target_archive = os.path.join(PROCESSED_DIR, fn)
                shutil.move(input_path, target_archive)
                # Remove from confirmed list
                if fn in remaining_confirmed:
                    del remaining_confirmed[fn]
                print(f"  ✅ Successfully packaged! Archived to candidates/processed/{fn}")
            else:
                print(f"  ❌ Build failed for {fn}")
                failed_count += 1
        except Exception as e:
            print(f"  ❌ Exception during build of {fn}: {e}")
            failed_count += 1

    # Save remaining confirmed back to disk
    with open(CONFIRMED_FILE, "w", encoding="utf-8") as f:
        json.dump(remaining_confirmed, f, ensure_ascii=False, indent=2)
    with open(CONFIRMED_PLANTS_FILE, "w", encoding="utf-8") as f:
        json.dump(remaining_confirmed, f, ensure_ascii=False, indent=2)

    print("\n==================================================")
    print(f"🎉 Batch import complete!")
    print(f"  - Total candidates processed: {total}")
    print(f"  - Successfully built:         {success_count}")
    print(f"  - Failed / skipped:           {failed_count}")
    print(f"  - Remaining in confirmed:     {len(remaining_confirmed)}")
    print("==================================================")

if __name__ == "__main__":
    main()
