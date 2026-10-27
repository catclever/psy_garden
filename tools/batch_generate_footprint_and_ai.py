#!/usr/bin/env python3
"""
Batch Footprint & Botanical AI Generator for Psy Garden Assets
Scans all plant asset variants in src/assets/plants/, derives root_width and 2.5D
ground occupancy ellipse from layer alpha channels, and queries the botanical AI endpoint
for natural growth height and game pixel range.
"""

import os
import sys
import glob
import json
import time
import argparse
from PIL import Image, ImageChops

# Ensure tools directory is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from ai_botanist import analyze_plant_botany

PLANTS_DIR = os.path.join(PROJECT_ROOT, "src", "assets", "plants")
CANDIDATES_DIR = os.path.join(SCRIPT_DIR, "candidates")

DEMO_NAMES = {
    "bamboo": "竹子",
    "banana": "芭蕉",
    "bonsai": "盆景松",
    "fern": "蕨类",
    "lily": "百合",
    "mushroom": "蘑菇"
}

def resolve_display_name(plant_name: str, existing_name: str = None) -> str:
    if existing_name and existing_name != plant_name and not existing_name.startswith("plant_"):
        return existing_name
    
    if plant_name in DEMO_NAMES:
        return DEMO_NAMES[plant_name]
    
    # Search candidates folder
    if os.path.exists(CANDIDATES_DIR):
        for fname in os.listdir(CANDIDATES_DIR):
            if plant_name in fname and fname.endswith(('.jpg', '.png', '.jpeg')):
                # format candidate_plant_0009_酒瓶兰.jpg or candidate_0009_酒瓶兰.jpg
                parts = os.path.splitext(fname)[0].split('_')
                if len(parts) >= 3:
                    cand_name = parts[-1].strip()
                    if cand_name and not cand_name.startswith("plant"):
                        return cand_name
                        
    return existing_name or plant_name

def calculate_footprint(vdir: str):
    pngs = [f for f in glob.glob(os.path.join(vdir, "*.png")) if not os.path.basename(f).startswith(".")]
    if not pngs:
        return None
        
    imgs = [Image.open(p).convert("RGBA") for p in pngs]
    w, h = imgs[0].size
    
    mask = Image.new("L", (w, h), 0)
    for img in imgs:
        r, g, b, a = img.split()
        mask = ImageChops.lighter(mask, a)
        
    rows_to_scan = min(15, h)
    max_row_width = 0
    bottom_y = -1
    
    # Find the lowest non-transparent row
    for y in range(h - 1, -1, -1):
        row_pixels = [mask.getpixel((x, y)) for x in range(w)]
        if any(p > 20 for p in row_pixels):
            bottom_y = y
            break
            
    if bottom_y >= 0:
        for y in range(bottom_y, max(-1, bottom_y - rows_to_scan), -1):
            xs = [x for x in range(w) if mask.getpixel((x, y)) > 20]
            if xs:
                rw = max(xs) - min(xs) + 1
                if rw > max_row_width:
                    max_row_width = rw
                    
    root_width = max_row_width if max_row_width > 0 else max(10, int(w * 0.15))
    root_ratio = round(root_width / w, 3)
    ground_ellipse = [root_width, max(4, int(round(root_width * 0.55)))]
    
    layer_names = [os.path.basename(p) for p in pngs]
    
    return {
        "width": w,
        "height": h,
        "root_width": root_width,
        "root_ratio": root_ratio,
        "ground_ellipse": ground_ellipse,
        "layers": layer_names
    }

def process_variant(plant_name: str, variant_id: str, vdir: str, force_ai: bool = False, dry_run: bool = False):
    meta_path = os.path.join(vdir, "metadata.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
        except Exception as e:
            print(f"  [WARN] Failed to read {meta_path}: {e}")
            
    display_name = resolve_display_name(plant_name, meta.get("display_name"))
    
    fp_data = calculate_footprint(vdir)
    if not fp_data:
        print(f"  [SKIP] No PNG layers found in {vdir}")
        return False
        
    meta["name"] = plant_name
    meta["display_name"] = display_name
    meta["variant_id"] = variant_id
    meta["width"] = fp_data["width"]
    meta["height"] = fp_data["height"]
    meta["root_width"] = fp_data["root_width"]
    meta["root_ratio"] = fp_data["root_ratio"]
    meta["ground_ellipse"] = fp_data["ground_ellipse"]
    meta["status"] = "ready"
    
    if "layers" not in meta:
        meta["layers"] = fp_data["layers"]
    if "true_color" not in meta:
        meta["true_color"] = True
    if "created_at" not in meta:
        meta["created_at"] = int(time.time())
        
    # Check if AI analysis is needed
    existing_ai = meta.get("ai_analysis")
    needs_ai = force_ai or not existing_ai or existing_ai.get("status") != "success"
    
    if needs_ai:
        print(f"  [AI] Querying botanical AI for {display_name} ({plant_name})...")
        ai_res = analyze_plant_botany(plant_name, display_name)
        meta["ai_analysis"] = ai_res
        if ai_res.get("status") == "success":
            print(f"       ✅ AI Success: {ai_res.get('scientific_name')} | Height: {ai_res.get('botanical_height_m')}m -> {ai_res.get('suggested_game_height_px')}px")
        else:
            print(f"       ⚠️ AI Warning: {ai_res.get('brief')}")
    else:
        print(f"  [AI] Cached: {existing_ai.get('scientific_name')} ({existing_ai.get('botanical_height_m')}m)")
        
    print(f"  [Footprint] Stem: {fp_data['root_width']}px (Ratio: {fp_data['root_ratio']:.1%}) | Ground Ellipse: {fp_data['ground_ellipse'][0]}x{fp_data['ground_ellipse'][1]}px")
    
    if not dry_run:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=4)
        print(f"  [SAVED] {meta_path}")
        
    return True

def main():
    parser = argparse.ArgumentParser(description="Batch generate footprint and AI botanical data")
    parser.add_argument("--plant", type=str, help="Specific plant to process (e.g. plant_0000)")
    parser.add_argument("--force-ai", action="store_true", help="Force re-query AI even if cached")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to disk")
    args = parser.parse_args()
    
    if not os.path.exists(PLANTS_DIR):
        print(f"Error: Plants dir not found at {PLANTS_DIR}")
        sys.exit(1)
        
    print("=" * 60)
    print("🌱 Starting Batch Footprint & Botanical AI Generator")
    print(f"   Target Dir: {PLANTS_DIR}")
    print(f"   Force AI:   {args.force_ai}")
    print(f"   Dry Run:    {args.dry_run}")
    print("=" * 60)
    
    plants = sorted(os.listdir(PLANTS_DIR))
    if args.plant:
        plants = [p for p in plants if p == args.plant]
        
    total_processed = 0
    total_success = 0
    
    for plant_name in plants:
        pdir = os.path.join(PLANTS_DIR, plant_name)
        if not os.path.isdir(pdir):
            continue
            
        variants = sorted([v for v in os.listdir(pdir) if os.path.isdir(os.path.join(pdir, v))])
        
        # If no subdirs, check if plant_name itself has pngs
        if not variants:
            if glob.glob(os.path.join(pdir, "*.png")):
                variants = ["default"]
                
        for v in variants:
            vdir = os.path.join(pdir, v) if v != "default" else pdir
            print(f"\n▶ Processing {plant_name}/{v}...")
            total_processed += 1
            if process_variant(plant_name, v, vdir, force_ai=args.force_ai, dry_run=args.dry_run):
                total_success += 1
                
    print("\n" + "=" * 60)
    print(f"🎉 Batch Generation Completed: {total_success}/{total_processed} variants successfully updated.")
    print("=" * 60)

if __name__ == "__main__":
    main()
