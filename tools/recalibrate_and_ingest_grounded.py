#!/usr/bin/env python3
"""
CLI & Library for stem-first root_anchor recalibration and Category-A regenerated
grounded plant ingestion.
"""

import argparse
import importlib.util
import json
import os
import shutil
import sys
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_PLANTS_DIR = os.path.join(PROJECT_ROOT, "src", "assets", "plants")
DEFAULT_SPECIES_JSON = os.path.join(SCRIPT_DIR, "category_a_66_species.json")
DEFAULT_PROCESSED_DIR = os.path.join(SCRIPT_DIR, "candidates", "processed")

SKIP_VARIANTS = {"v_uncut", "v_semantic"}
REGEN_EXTS = (".png", ".jpg", ".jpeg", ".webp")
TASK_SUFFIX = "_task_stem_anchor_034"


def _load_draft_or_merged(module_name: str, draft_filename: str, merged_filename: str):
    draft_path = os.path.join(SCRIPT_DIR, draft_filename)
    merged_path = os.path.join(SCRIPT_DIR, merged_filename)
    target_path = draft_path if os.path.exists(draft_path) else merged_path
    spec = importlib.util.spec_from_file_location(module_name, target_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from root_anchor_calibrator import calibrate_root_anchor

_build_mod = _load_draft_or_merged(
    "build_plant_asset_calibrator",
    f"build_plant_asset{TASK_SUFFIX}.py",
    "build_plant_asset.py",
)
_manifest_mod = _load_draft_or_merged(
    "generate_plant_manifest_calibrator",
    f"generate_plant_manifest{TASK_SUFFIX}.py",
    "generate_plant_manifest.py",
)

build_asset = _build_mod.build_asset
generate_manifest = _manifest_mod.generate_manifest


def _classify_layer_slot(filename: str):
    lower = filename.lower()
    if any(k in lower for k in ("green", "stem", "trunk")):
        return "green"
    if any(k in lower for k in ("red", "flower", "cap", "leaves")):
        return "red"
    if any(k in lower for k in ("blue", "accent")):
        return "blue"
    return None


def _load_variant_layer_images(variant_dir: str):
    slot_paths = {}
    for fname in sorted(os.listdir(variant_dir)):
        if fname.startswith(".") or not fname.lower().endswith(".png"):
            continue
        fpath = os.path.join(variant_dir, fname)
        if not os.path.isfile(fpath):
            continue
        slot = _classify_layer_slot(fname)
        if slot and slot not in slot_paths:
            slot_paths[slot] = fpath

    if not slot_paths:
        return None, None, None

    opened = {}
    for slot, fpath in slot_paths.items():
        with Image.open(fpath) as im:
            opened[slot] = im.convert("RGBA")

    ref_img = opened.get("green") or opened.get("red") or opened.get("blue")
    W, H = ref_img.size

    def _norm(slot_name: str, required: bool):
        im = opened.get(slot_name)
        if im is None:
            return Image.new("RGBA", (W, H), (0, 0, 0, 0)) if required else None
        if im.size != (W, H):
            return im.resize((W, H), Image.Resampling.NEAREST)
        return im

    return _norm("red", True), _norm("green", True), _norm("blue", False)


def recalibrate_all(plants_dir: str = None) -> dict:
    """Recalibrate root_anchor and footprint metadata for all enabled plant variants."""
    if plants_dir is None:
        plants_dir = DEFAULT_PLANTS_DIR

    updated_variants = 0
    skipped_disabled = 0
    skipped_no_layers = 0

    if os.path.isdir(plants_dir):
        for plant_name in sorted(os.listdir(plants_dir)):
            if plant_name.startswith("."):
                continue
            plant_path = os.path.join(plants_dir, plant_name)
            if not os.path.isdir(plant_path):
                continue

            for variant_id in sorted(os.listdir(plant_path)):
                if variant_id.startswith(".") or variant_id in SKIP_VARIANTS:
                    continue
                variant_dir = os.path.join(plant_path, variant_id)
                if not os.path.isdir(variant_dir):
                    continue

                meta_path = os.path.join(variant_dir, "metadata.json")
                mdata = {}
                if os.path.isfile(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            loaded = json.load(f)
                        if isinstance(loaded, dict):
                            mdata = loaded
                    except Exception:
                        mdata = {}

                if mdata.get("enabled") is False:
                    skipped_disabled += 1
                    continue

                red_img, green_img, blue_img = _load_variant_layer_images(variant_dir)
                if red_img is None and green_img is None and blue_img is None:
                    skipped_no_layers += 1
                    continue

                W, H = (red_img or green_img or blue_img).size
                cal = calibrate_root_anchor(red_img, green_img, blue_img)

                mdata["width"] = W
                mdata["height"] = H
                mdata["root_anchor"] = cal["root_anchor"]
                mdata["root_x"] = cal["root_x"]
                mdata["root_y"] = cal["root_y"]
                mdata["root_width"] = cal["root_width"]
                mdata["root_ratio"] = cal["root_ratio"]
                mdata["ground_ellipse"] = cal["ground_ellipse"]

                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(mdata, f, ensure_ascii=False, indent=4)

                updated_variants += 1

    manifest = generate_manifest(plants_dir=plants_dir)
    return {
        "updated_variants": updated_variants,
        "skipped_disabled": skipped_disabled,
        "skipped_no_layers": skipped_no_layers,
        "total_species": manifest["total_species"],
    }


def _find_regen_image(regen_dir: str, plant_id: str):
    for ext in REGEN_EXTS:
        for cand_ext in (ext, ext.upper()):
            candidate = os.path.join(regen_dir, f"{plant_id}{cand_ext}")
            if os.path.isfile(candidate):
                return candidate, ext
    return None, None


def _select_best_existing_metadata(plant_dir: str, plant_id: str) -> dict:
    if not os.path.isdir(plant_dir):
        return {}

    candidates = []
    for variant_id in sorted(os.listdir(plant_dir)):
        if variant_id.startswith(".") or variant_id in SKIP_VARIANTS:
            continue
        variant_dir = os.path.join(plant_dir, variant_id)
        if not os.path.isdir(variant_dir):
            continue
        meta_path = os.path.join(variant_dir, "metadata.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                mdata = json.load(f)
            if not isinstance(mdata, dict):
                continue
        except Exception:
            continue

        ai = mdata.get("ai_analysis")
        has_good_ai = isinstance(ai, dict) and ai.get("status") == "success"
        has_any_ai = isinstance(ai, dict)
        disp = mdata.get("display_name") or ""
        has_nice_disp = bool(disp and disp != plant_id and not disp.startswith("plant_"))
        rank = (
            has_good_ai,
            has_any_ai,
            mdata.get("enabled", True) is not False,
            bool(mdata.get("true_color", False)),
            has_nice_disp,
            len(variant_id) <= 4,
            variant_id,
        )
        candidates.append((rank, mdata))

    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _disable_all_existing_variants(plant_dir: str, plant_id: str) -> int:
    if not os.path.isdir(plant_dir):
        return 0
    disabled_count = 0
    for variant_id in sorted(os.listdir(plant_dir)):
        if variant_id.startswith("."):
            continue
        variant_dir = os.path.join(plant_dir, variant_id)
        if not os.path.isdir(variant_dir):
            continue
        meta_path = os.path.join(variant_dir, "metadata.json")
        mdata = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    mdata = loaded
            except Exception:
                mdata = {}
        elif variant_id in SKIP_VARIANTS:
            continue
        else:
            mdata = {"name": plant_id, "variant_id": variant_id}

        mdata["enabled"] = False
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(mdata, f, ensure_ascii=False, indent=4)
        disabled_count += 1
    return disabled_count


def _next_variant_id(plant_dir: str) -> str:
    max_num = 0
    if os.path.isdir(plant_dir):
        for entry in os.listdir(plant_dir):
            if entry.isdigit() and os.path.isdir(os.path.join(plant_dir, entry)):
                max_num = max(max_num, int(entry))
    return f"{max_num + 1:02d}"


def ingest_regen_dir(
    regen_dir: str,
    species_json_path: str = None,
    plants_dir: str = None,
    processed_dir: str = None,
    project_root: str = None,
) -> dict:
    """Ingest regenerated Category-A images, soft-disabling old variants and preserving AI analysis."""
    if species_json_path is None:
        species_json_path = DEFAULT_SPECIES_JSON
    if plants_dir is None:
        plants_dir = DEFAULT_PLANTS_DIR
    if processed_dir is None:
        processed_dir = DEFAULT_PROCESSED_DIR
    if project_root is None:
        project_root = PROJECT_ROOT

    with open(species_json_path, "r", encoding="utf-8") as f:
        species_items = json.load(f)

    os.makedirs(processed_dir, exist_ok=True)

    ingested = 0
    skipped_missing = 0
    failed = 0
    variants_disabled = 0
    details = []

    for item in species_items:
        plant_id = item.get("plant_id")
        if not plant_id:
            continue
        src_path, ext = _find_regen_image(regen_dir, plant_id)
        if src_path is None:
            skipped_missing += 1
            continue

        plant_dir = os.path.join(plants_dir, plant_id)
        best_meta = _select_best_existing_metadata(plant_dir, plant_id)

        zh_name = (item.get("zh_name") or "").strip()
        display_name = best_meta.get("display_name") or zh_name or plant_id
        preserved_ai = best_meta.get("ai_analysis")
        if not isinstance(preserved_ai, dict):
            preserved_ai = {
                "zh_name": zh_name or display_name,
                "scientific_name": item.get("scientific_name") or "",
                "plant_type": item.get("plant_type") or "shrub",
                "status": "success",
            }
        else:
            preserved_ai = dict(preserved_ai)
            if not preserved_ai.get("zh_name") and (zh_name or display_name):
                preserved_ai["zh_name"] = zh_name or display_name
            if not preserved_ai.get("scientific_name") and item.get("scientific_name"):
                preserved_ai["scientific_name"] = item["scientific_name"]
            if not preserved_ai.get("plant_type") and item.get("plant_type"):
                preserved_ai["plant_type"] = item["plant_type"]

        new_variant = _next_variant_id(plant_dir)
        disabled_here = _disable_all_existing_variants(plant_dir, plant_id)
        variants_disabled += disabled_here

        out_dir = os.path.join(plant_dir, new_variant)
        ok = build_asset(
            src_path,
            plant_id,
            new_variant,
            out_dir,
            project_root,
            display_name=display_name,
            inject_css=False,
        )
        if not ok:
            failed += 1
            continue

        new_meta_path = os.path.join(out_dir, "metadata.json")
        with open(new_meta_path, "r", encoding="utf-8") as f:
            new_meta = json.load(f)
        new_meta["enabled"] = True
        new_meta["status"] = "ready"
        new_meta["display_name"] = display_name
        new_meta["ai_analysis"] = preserved_ai
        with open(new_meta_path, "w", encoding="utf-8") as f:
            json.dump(new_meta, f, ensure_ascii=False, indent=4)

        safe_zh = (zh_name or display_name).replace("/", "_")
        archived_name = f"candidate_{plant_id}_{safe_zh}_grounded{ext}"
        shutil.copy2(src_path, os.path.join(processed_dir, archived_name))

        ingested += 1
        details.append({"plant_id": plant_id, "variant_id": new_variant, "archived": archived_name})

    manifest = generate_manifest(plants_dir=plants_dir)
    return {
        "ingested": ingested,
        "skipped_missing": skipped_missing,
        "failed": failed,
        "variants_disabled": variants_disabled,
        "total_species": manifest["total_species"],
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Recalibrate stem-first root_anchor across plant assets and ingest regenerated Category-A plants."
    )
    parser.add_argument("--recalibrate-all", action="store_true", help="Recalibrate all enabled plant variants")
    parser.add_argument("--ingest-regen-dir", type=str, default=None, help="Directory with regenerated <plant_id>.(png|jpg|jpeg|webp)")
    parser.add_argument("--species-json", type=str, default=DEFAULT_SPECIES_JSON, help="Path to category_a_66_species.json")
    parser.add_argument("--plants-dir", type=str, default=DEFAULT_PLANTS_DIR, help="Path to src/assets/plants")
    parser.add_argument("--processed-dir", type=str, default=DEFAULT_PROCESSED_DIR, help="Path to tools/candidates/processed")
    args = parser.parse_args()

    if not args.recalibrate_all and not args.ingest_regen_dir:
        parser.print_help()
        return 1

    if args.recalibrate_all:
        res = recalibrate_all(plants_dir=args.plants_dir)
        print(f"Recalibrate summary: {json.dumps(res, ensure_ascii=False)}")

    if args.ingest_regen_dir:
        res = ingest_regen_dir(
            regen_dir=args.ingest_regen_dir,
            species_json_path=args.species_json,
            plants_dir=args.plants_dir,
            processed_dir=args.processed_dir,
            project_root=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(args.plants_dir))))
            if args.plants_dir != DEFAULT_PLANTS_DIR
            else PROJECT_ROOT,
        )
        print(f"Ingest summary: {json.dumps(res, ensure_ascii=False)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
