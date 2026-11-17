#!/usr/bin/env python3
"""
Idempotent CLI script to soft-disable early/legacy test plant species and
legacy non-aliased timestamp variants without deleting any PNG assets.
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_PLANTS_DIR = os.path.join(PROJECT_ROOT, "src", "assets", "plants")

LEGACY_SPECIES = [
    "bamboo",
    "banana",
    "bonsai",
    "fern",
    "lily",
    "mushroom",
    "zen_bamboo_cluster_green",
    "zen_bamboo_cluster_redgreen",
    "zen_bamboo_single_green",
    "zen_bamboo_single_redgreen",
    "zen_bamboo_thicket_green",
]

LEGACY_VARIANTS = [
    ("plant_0000", "1791079136"),
]


def _get_generate_manifest():
    """Resolve generate_manifest across pre-merge draft, test alias, and post-merge modules."""
    if "generate_plant_manifest" in sys.modules:
        return sys.modules["generate_plant_manifest"].generate_manifest
    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)
    draft_mod_name = "generate_plant_manifest_task_asset_soft_disable_toggle_and_hide_legacy_plants_032"
    if os.path.exists(os.path.join(SCRIPT_DIR, f"{draft_mod_name}.py")):
        mod = __import__(draft_mod_name)
        return mod.generate_manifest
    import generate_plant_manifest
    return generate_plant_manifest.generate_manifest


def _set_variant_disabled(variant_dir: str, plant_name: str, variant_id: str) -> bool:
    """Set enabled=False in variant_dir/metadata.json; return True if updated/present."""
    if not os.path.isdir(variant_dir):
        return False

    meta_path = os.path.join(variant_dir, "metadata.json")
    has_png = any(
        fname.lower().endswith(".png") and os.path.isfile(os.path.join(variant_dir, fname))
        for fname in os.listdir(variant_dir)
    )
    if not os.path.exists(meta_path) and not has_png:
        return False

    mdata = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    mdata = loaded
        except Exception:
            mdata = {}

    if not mdata:
        mdata = {"name": plant_name, "variant_id": variant_id}

    mdata["enabled"] = False
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(mdata, f, ensure_ascii=False, indent=4)
    return True


def disable_legacy_plants(
    plants_dir=None,
    legacy_species=None,
    legacy_variants=None,
) -> dict:
    """Mark all legacy species/variants enabled=False and regenerate manifest.json."""
    if plants_dir is None:
        plants_dir = DEFAULT_PLANTS_DIR
    if legacy_species is None:
        legacy_species = LEGACY_SPECIES
    if legacy_variants is None:
        legacy_variants = LEGACY_VARIANTS

    updated_count = 0

    for plant_name in legacy_species:
        plant_path = os.path.join(plants_dir, plant_name)
        if not os.path.isdir(plant_path):
            continue

        subdirs = [
            item
            for item in sorted(os.listdir(plant_path))
            if not item.startswith(".") and os.path.isdir(os.path.join(plant_path, item))
        ]
        if subdirs:
            for variant_id in subdirs:
                variant_dir = os.path.join(plant_path, variant_id)
                if _set_variant_disabled(variant_dir, plant_name, variant_id):
                    updated_count += 1
        else:
            if _set_variant_disabled(plant_path, plant_name, "default"):
                updated_count += 1

    for plant_name, variant_id in legacy_variants:
        variant_dir = os.path.join(plants_dir, plant_name, variant_id)
        if _set_variant_disabled(variant_dir, plant_name, variant_id):
            updated_count += 1

    generate_manifest = _get_generate_manifest()
    manifest = generate_manifest(plants_dir=plants_dir)

    return {
        "updated_variants": updated_count,
        "total_species": manifest["total_species"],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Soft-disable legacy test plant species and regenerate manifest.json"
    )
    parser.add_argument(
        "--plants-dir",
        type=str,
        default=None,
        help="Path to src/assets/plants directory",
    )
    args = parser.parse_args()

    result = disable_legacy_plants(plants_dir=args.plants_dir)
    print(
        f"Disabled {result['updated_variants']} legacy variant(s); "
        f"manifest total_species: {result['total_species']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
