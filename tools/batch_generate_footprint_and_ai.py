#!/usr/bin/env python3
"""
Batch Footprint & Botanical AI Generator for Psy Garden Assets.
Scans all plant asset variants in src/assets/plants/, derives root_width and 2.5D
ground occupancy ellipse from layer alpha channels (when missing or --recalc-footprint),
queries the botanical AI endpoint in multi-plant chunks for fast/low-RPM enrichment,
and refreshes src/assets/plants/manifest.json.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from ai_botanist import analyze_plant_botany, analyze_plants_batch
from generate_plant_manifest import generate_manifest

PLANTS_DIR = os.path.join(PROJECT_ROOT, "src", "assets", "plants")
CANDIDATES_DIR = os.path.join(SCRIPT_DIR, "candidates")

DEMO_NAMES = {
    "bamboo": "竹子",
    "banana": "芭蕉",
    "bonsai": "盆景松",
    "fern": "蕨类",
    "lily": "百合",
    "mushroom": "蘑菇",
}


def resolve_display_name(plant_name: str, existing_name: str = None) -> str:
    if existing_name and existing_name != plant_name and not existing_name.startswith("plant_"):
        return existing_name

    if plant_name in DEMO_NAMES:
        return DEMO_NAMES[plant_name]

    for search_dir in (CANDIDATES_DIR, os.path.join(CANDIDATES_DIR, "processed")):
        if os.path.exists(search_dir):
            for fname in os.listdir(search_dir):
                if plant_name in fname and fname.endswith((".jpg", ".png", ".jpeg")):
                    parts = os.path.splitext(fname)[0].split("_")
                    if len(parts) >= 3:
                        cand_name = parts[-1].strip()
                        if cand_name and not cand_name.startswith("plant"):
                            return cand_name

    return existing_name or plant_name


def calculate_footprint(vdir: str):
    from PIL import Image, ImageChops

    pngs = [f for f in glob.glob(os.path.join(vdir, "*.png")) if not os.path.basename(f).startswith(".")]
    if not pngs:
        return None

    imgs = [Image.open(p).convert("RGBA") for p in pngs]
    try:
        w, h = imgs[0].size
        mask = Image.new("L", (w, h), 0)
        for img in imgs:
            _, _, _, a = img.split()
            mask = ImageChops.lighter(mask, a)
    finally:
        for img in imgs:
            img.close()

    rows_to_scan = min(15, h)
    max_row_width = 0
    bottom_y = -1

    for y in range(h - 1, -1, -1):
        if any(mask.getpixel((x, y)) > 20 for x in range(w)):
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
        "layers": layer_names,
    }


def _enrich_chunk(chunk_idx: int, total_chunks: int, chunk_items: list) -> dict:
    """Run batch AI query on up to N species, with per-item fallback for any missed entry."""
    unique_map = {}
    for item in chunk_items:
        unique_map[item["plant_name"]] = item["display_name"]
    query_list = [{"plant_name": k, "display_name": v} for k, v in unique_map.items()]

    results = {}
    try:
        results = analyze_plants_batch(query_list)
    except Exception as exc:
        print(f"  [Batch {chunk_idx}/{total_chunks}] Batch call error ({exc}), falling back to single requests...")

    for q in query_list:
        pname = q["plant_name"]
        if pname not in results:
            single = analyze_plant_botany(pname, q["display_name"])
            results[pname] = single

    ok_cnt = sum(1 for v in results.values() if v.get("status") == "success")
    print(f"  ✅ [Batch {chunk_idx}/{total_chunks}] Enriched {ok_cnt}/{len(query_list)} species")
    return results


def main():
    parser = argparse.ArgumentParser(description="Batch generate footprint and AI botanical data")
    parser.add_argument("--plant", type=str, help="Specific plant to process (e.g. plant_0000)")
    parser.add_argument("--force-ai", action="store_true", help="Force re-query AI even if cached")
    parser.add_argument("--recalc-footprint", action="store_true", help="Force recalculate PIL alpha footprint")
    parser.add_argument("--batch-size", type=int, default=24, help="Species per AI batch request")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent AI batch workers")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to disk")
    args = parser.parse_args()

    if not os.path.exists(PLANTS_DIR):
        print(f"Error: Plants dir not found at {PLANTS_DIR}")
        sys.exit(1)

    start_t = time.time()
    plants = sorted(p for p in os.listdir(PLANTS_DIR) if not p.startswith("."))
    if args.plant:
        plants = [p for p in plants if p == args.plant]

    variant_records = []
    pending_ai = []

    for plant_name in plants:
        pdir = os.path.join(PLANTS_DIR, plant_name)
        if not os.path.isdir(pdir):
            continue

        variants = sorted(
            v for v in os.listdir(pdir) if not v.startswith(".") and os.path.isdir(os.path.join(pdir, v))
        )
        if not variants and glob.glob(os.path.join(pdir, "*.png")):
            variants = ["default"]

        for v in variants:
            vdir = os.path.join(pdir, v) if v != "default" else pdir
            pngs = [f for f in glob.glob(os.path.join(vdir, "*.png")) if not os.path.basename(f).startswith(".")]
            if not pngs:
                continue

            meta_path = os.path.join(vdir, "metadata.json")
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}

            display_name = resolve_display_name(plant_name, meta.get("display_name"))
            has_fp = (
                not args.recalc_footprint
                and meta.get("width")
                and meta.get("height")
                and meta.get("root_width")
                and meta.get("root_ratio") is not None
                and isinstance(meta.get("ground_ellipse"), list)
                and len(meta.get("ground_ellipse")) == 2
            )

            if not has_fp:
                fp = calculate_footprint(vdir)
                if not fp:
                    continue
                meta["width"] = fp["width"]
                meta["height"] = fp["height"]
                meta["root_width"] = fp["root_width"]
                meta["root_ratio"] = fp["root_ratio"]
                meta["ground_ellipse"] = fp["ground_ellipse"]
                if "layers" not in meta:
                    meta["layers"] = fp["layers"]

            meta["name"] = plant_name
            meta["display_name"] = display_name
            meta["variant_id"] = v
            meta["status"] = "ready"
            meta.setdefault("true_color", True)
            meta.setdefault("created_at", int(time.time()))

            rec = {
                "plant_name": plant_name,
                "variant_id": v,
                "display_name": display_name,
                "meta_path": meta_path,
                "meta": meta,
            }
            variant_records.append(rec)

            existing_ai = meta.get("ai_analysis")
            needs_ai = args.force_ai or not isinstance(existing_ai, dict) or existing_ai.get("status") != "success"
            if needs_ai:
                pending_ai.append(rec)

    print("=" * 60)
    print(f"🌱 Scanned {len(variant_records)} total variants | Pending AI enrichment: {len(pending_ai)}")
    print("=" * 60)

    ai_success_total = 0
    ai_fail_total = 0

    if pending_ai and not args.dry_run:
        bsize = max(1, args.batch_size)
        chunks = [pending_ai[i : i + bsize] for i in range(0, len(pending_ai), bsize)]
        ai_by_plant = {}

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = [
                pool.submit(_enrich_chunk, idx, len(chunks), chunk)
                for idx, chunk in enumerate(chunks, 1)
            ]
            for fut in as_completed(futs):
                chunk_res = fut.result()
                ai_by_plant.update(chunk_res)

        for rec in pending_ai:
            res = ai_by_plant.get(rec["plant_name"])
            if res:
                rec["meta"]["ai_analysis"] = res
                if res.get("status") == "success":
                    ai_success_total += 1
                else:
                    ai_fail_total += 1

    if not args.dry_run:
        for rec in variant_records:
            with open(rec["meta_path"], "w", encoding="utf-8") as f:
                json.dump(rec["meta"], f, ensure_ascii=False, indent=4)
        manifest = generate_manifest(plants_dir=PLANTS_DIR)
        manifest_cnt = manifest.get("total_species", 0)
    else:
        manifest_cnt = 0

    elapsed = round(time.time() - start_t, 2)
    print("=" * 60)
    print(
        f"🎉 Done in {elapsed}s! AI newly enriched: {ai_success_total} ok, {ai_fail_total} failed | Manifest species: {manifest_cnt}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
