#!/usr/bin/env python3
"""
Plant Manifest Generator for Psy Garden.
Scans src/assets/plants/<plant_name>/<variant_id>/, selects one canonical primary
variant per species, inspects actual PNG layer files, and writes manifest.json.
"""

import argparse
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_PLANTS_DIR = os.path.join(PROJECT_ROOT, "src", "assets", "plants")

SKIP_VARIANTS = {"v_uncut", "v_semantic"}
SLOT_ORDER = {"green": 0, "red": 1, "blue": 2}


def _classify_layer_slot(filename: str):
    """Map a PNG layer filename to 'green', 'red', or 'blue' slot, or None."""
    lower = filename.lower()
    if any(k in lower for k in ("green", "stem", "trunk")):
        return "green"
    if any(k in lower for k in ("red", "flower", "cap", "leaves")):
        return "red"
    if any(k in lower for k in ("blue", "accent")):
        return "blue"
    return None


def _collect_layer_files(variant_dir: str) -> list:
    """Inspect PNG files in variant_dir and return ordered [{'slot', 'file'}] entries."""
    entries = []
    for fname in sorted(os.listdir(variant_dir)):
        if fname.startswith(".") or not fname.lower().endswith(".png"):
            continue
        fpath = os.path.join(variant_dir, fname)
        if not os.path.isfile(fpath):
            continue
        slot = _classify_layer_slot(fname)
        if slot is None:
            continue
        entries.append({"slot": slot, "file": fname})

    entries.sort(key=lambda item: (SLOT_ORDER.get(item["slot"], 99), item["file"]))
    return entries


def _infer_image_dimensions(variant_dir: str, layer_files: list):
    """Read (width, height) from the first layer PNG using PIL (lazy-imported)."""
    try:
        from PIL import Image
    except ImportError:
        return None, None
    for item in layer_files:
        fpath = os.path.join(variant_dir, item["file"])
        try:
            with Image.open(fpath) as img:
                return int(img.width), int(img.height)
        except Exception:
            continue
    return None, None


def _build_variant_record(plant_name: str, variant_id: str, variant_dir: str):
    """Build a candidate manifest record for one variant directory, or None if invalid or disabled."""
    layer_files = _collect_layer_files(variant_dir)
    if not layer_files:
        return None

    meta_path = os.path.join(variant_dir, "metadata.json")
    mdata = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    mdata = loaded
        except Exception:
            mdata = {}

    if mdata.get("enabled", True) is False:
        return None

    display_name = mdata.get("display_name") or mdata.get("name") or plant_name
    true_color = bool(mdata.get("true_color", False))
    ai_analysis = mdata.get("ai_analysis")
    if not isinstance(ai_analysis, dict):
        ai_analysis = None

    width = mdata.get("width")
    height = mdata.get("height")
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)) or width <= 0 or height <= 0:
        pil_w, pil_h = _infer_image_dimensions(variant_dir, layer_files)
        if pil_w and pil_h:
            width = pil_w
            height = pil_h
        else:
            width = int(width) if isinstance(width, (int, float)) and width > 0 else 100
            height = int(height) if isinstance(height, (int, float)) and height > 0 else 100
    else:
        width = int(width)
        height = int(height)

    raw_root_width = mdata.get("root_width")
    if isinstance(raw_root_width, (int, float)) and raw_root_width > 0:
        root_width = int(raw_root_width)
    else:
        root_width = max(10, int(width * 0.15))

    raw_root_ratio = mdata.get("root_ratio")
    if isinstance(raw_root_ratio, (int, float)) and raw_root_ratio > 0:
        root_ratio = float(raw_root_ratio)
    else:
        root_ratio = round(root_width / width, 3)

    raw_ellipse = mdata.get("ground_ellipse")
    if (
        isinstance(raw_ellipse, list)
        and len(raw_ellipse) == 2
        and all(isinstance(v, (int, float)) and v > 0 for v in raw_ellipse)
    ):
        ground_ellipse = [int(raw_ellipse[0]), int(raw_ellipse[1])]
    else:
        ground_ellipse = [root_width, max(4, int(round(root_width * 0.55)))]

    raw_root_anchor = mdata.get("root_anchor")
    if (
        isinstance(raw_root_anchor, list)
        and len(raw_root_anchor) == 2
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0
            for v in raw_root_anchor
        )
    ):
        root_anchor = [round(float(raw_root_anchor[0]), 4), round(float(raw_root_anchor[1]), 4)]
    else:
        root_anchor = [0.5, 1.0]

    return {
        "id": f"{plant_name}__{variant_id}",
        "name": plant_name,
        "display_name": display_name,
        "variant_id": variant_id,
        "path_prefix": f"assets/plants/{plant_name}/{variant_id}",
        "width": width,
        "height": height,
        "root_anchor": root_anchor,
        "root_width": root_width,
        "root_ratio": root_ratio,
        "ground_ellipse": ground_ellipse,
        "true_color": true_color,
        "layer_files": layer_files,
        "ai_analysis": ai_analysis,
    }


def _variant_rank_key(record: dict) -> tuple:
    """Ranking tuple for deduplicating variants of the same species."""
    plant_name = record["name"]
    display_name = record["display_name"]
    variant_id = record["variant_id"]
    has_nice_display = bool(
        display_name
        and display_name != plant_name
        and not display_name.startswith("plant_")
    )
    return (
        bool(record["true_color"]),
        has_nice_display,
        len(variant_id) <= 4,
        variant_id,
    )


def generate_manifest(plants_dir=None, output_path=None) -> dict:
    """Scan plants_dir, deduplicate variants per species, write and return manifest dict."""
    if plants_dir is None:
        plants_dir = DEFAULT_PLANTS_DIR
    if output_path is None:
        output_path = os.path.join(plants_dir, "manifest.json")

    plants_list = []
    if os.path.exists(plants_dir):
        for plant_name in sorted(os.listdir(plants_dir)):
            if plant_name.startswith("."):
                continue
            plant_path = os.path.join(plants_dir, plant_name)
            if not os.path.isdir(plant_path):
                continue

            variant_records = []
            for variant_id in sorted(os.listdir(plant_path)):
                if variant_id.startswith(".") or variant_id in SKIP_VARIANTS:
                    continue
                variant_dir = os.path.join(plant_path, variant_id)
                if not os.path.isdir(variant_dir):
                    continue
                record = _build_variant_record(plant_name, variant_id, variant_dir)
                if record is not None:
                    variant_records.append(record)

            if variant_records:
                best_record = max(variant_records, key=_variant_rank_key)
                plants_list.append(best_record)

    plants_list.sort(key=lambda item: item["name"])

    manifest = {
        "generated_at": int(time.time()),
        "total_species": len(plants_list),
        "plants": plants_list,
    }

    out_parent = os.path.dirname(os.path.abspath(output_path))
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate Psy Garden plant asset manifest.json")
    parser.add_argument("--plants-dir", type=str, default=None, help="Path to src/assets/plants directory")
    parser.add_argument("--output", type=str, default=None, help="Output manifest.json path")
    args = parser.parse_args()

    manifest = generate_manifest(plants_dir=args.plants_dir, output_path=args.output)
    target = args.output or os.path.join(args.plants_dir or DEFAULT_PLANTS_DIR, "manifest.json")
    print(f"Generated manifest with {manifest['total_species']} species at {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
