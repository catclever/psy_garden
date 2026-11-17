#!/usr/bin/env python3
"""
Concurrent Candidate Batch Ingestion Pipeline for Psy Garden.
Scans tools/candidates/, cleans broken symlinks and Wikipedia placeholders,
deduplicates candidate files/symlinks by species, slices variants concurrently,
runs concurrent botanical AI enrichment, archives processed candidates, cleans
confirmed JSON registries, and regenerates src/assets/plants/manifest.json.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import glob
import json
import os
import re
import shutil
import sys
import time
from PIL import Image, ImageChops

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import build_plant_asset
from ai_botanist import analyze_plant_botany
from generate_plant_manifest import generate_manifest

DEFAULT_CANDIDATES_DIR = os.path.join(SCRIPT_DIR, "candidates")
DEFAULT_PLANTS_DIR = os.path.join(PROJECT_ROOT, "src", "assets", "plants")
DEFAULT_CONFIRMED_CANDIDATES_FILE = os.path.join(SCRIPT_DIR, "confirmed_candidates.json")
DEFAULT_CONFIRMED_PLANTS_FILE = os.path.join(SCRIPT_DIR, "confirmed_plants.json")

PLACEHOLDER_MARKERS = ("User_Hung_Ting", "User:Hung Ting", "條目建立中")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
SKIP_VARIANTS = {"v_uncut", "v_semantic"}

PLANT_NUM_RE = re.compile(r"(?:candidate_)?(?:plant_)+(\d+)_(.+)\.(?:jpg|png|jpeg)$", re.IGNORECASE)
ZEN_BAMBOO_RE = re.compile(r"^zen_bamboo_(.+)_(\d+)\.(?:jpg|png|jpeg)$", re.IGNORECASE)


def parse_candidate_filename(filename: str):
    """Parse (plant_name, display_name) from a candidate image filename."""
    m = PLANT_NUM_RE.search(filename)
    if m:
        num = int(m.group(1))
        display_name = m.group(2).strip()
        plant_name = f"plant_{num:04d}"
        return plant_name, display_name or plant_name

    zm = ZEN_BAMBOO_RE.match(filename)
    if zm:
        slug = zm.group(1).strip()
        return f"zen_bamboo_{slug}", f"禅意翠竹({slug})"

    base_name, _ = os.path.splitext(filename)
    plant_name = re.sub(r"^candidate_", "", base_name)
    return plant_name, plant_name


def _is_placeholder_name(entry_name: str) -> bool:
    return any(marker in entry_name for marker in PLACEHOLDER_MARKERS)


def _move_to_bad_backgrounds(src_path: str, bad_dir: str, entry_name: str):
    os.makedirs(bad_dir, exist_ok=True)
    dst_path = os.path.join(bad_dir, entry_name)
    if os.path.lexists(dst_path):
        if os.path.isdir(dst_path) and not os.path.islink(dst_path):
            dst_path = os.path.join(bad_dir, f"{entry_name}_{int(time.time())}")
        else:
            os.unlink(dst_path)
    shutil.move(src_path, dst_path)


def _find_existing_true_color_variant(plants_dir: str, plant_name: str):
    """Return (variant_id, variant_dir, metadata_dict) if a true_color=True variant exists."""
    plant_dir = os.path.join(plants_dir, plant_name)
    if not os.path.isdir(plant_dir):
        return None

    matches = []
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
            if isinstance(mdata, dict) and mdata.get("true_color") is True:
                matches.append((variant_id, variant_dir, mdata))
        except Exception:
            continue

    if not matches:
        return None

    # Prefer short canonical variant IDs like '01'
    matches.sort(key=lambda item: (len(item[0]) <= 4, item[0]), reverse=True)
    return matches[0]


def _ensure_footprint_metadata(variant_dir: str, mdata: dict, dry_run: bool = False) -> bool:
    """Compute and persist width/height/root_width/root_ratio/ground_ellipse if missing."""
    required = ("width", "height", "root_width", "root_ratio", "ground_ellipse")
    has_all = all(mdata.get(k) is not None for k in required)
    if has_all and isinstance(mdata.get("ground_ellipse"), list) and len(mdata["ground_ellipse"]) == 2:
        return False

    pngs = [
        f for f in sorted(glob.glob(os.path.join(variant_dir, "*.png")))
        if not os.path.basename(f).startswith(".")
    ]
    if not pngs:
        return False

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
    root_ratio = round(root_width / w, 3) if w > 0 else 0.15
    ground_ellipse = [root_width, max(4, int(round(root_width * 0.55)))]

    mdata["width"] = w
    mdata["height"] = h
    mdata["root_width"] = root_width
    mdata["root_ratio"] = root_ratio
    mdata["ground_ellipse"] = ground_ellipse

    if not dry_run:
        meta_path = os.path.join(variant_dir, "metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(mdata, f, ensure_ascii=False, indent=4)

    return True


def _slice_worker(task: dict) -> dict:
    """Top-level picklable worker for ProcessPoolExecutor slicing."""
    plant_name = task["plant_name"]
    display_name = task["display_name"]
    source_path = task["source_path"]
    output_dir = task["output_dir"]
    project_root = task["project_root"]

    try:
        ok = build_plant_asset.build_asset(
            input_path=source_path,
            plant_name=plant_name,
            variant_id="01",
            output_dir=output_dir,
            project_root=project_root,
            display_name=display_name,
            inject_css=False,
        )
        return {"plant_name": plant_name, "ok": bool(ok), "error": None if ok else "build_asset returned False"}
    except Exception as exc:
        return {"plant_name": plant_name, "ok": False, "error": str(exc)}


def _ai_worker(task: dict) -> dict:
    """Worker for ThreadPoolExecutor botanical AI analysis with 1 retry."""
    plant_name = task["plant_name"]
    display_name = task["display_name"]
    meta_path = task["meta_path"]

    last_res = None
    last_err = None
    for attempt in range(2):
        try:
            res = analyze_plant_botany(plant_name, display_name)
            last_res = res
            if isinstance(res, dict) and res.get("status") == "success":
                break
            if isinstance(res, dict) and res.get("status") == "skipped":
                break
        except Exception as exc:
            last_err = exc
        if attempt == 0:
            time.sleep(0.2)

    if last_res is None:
        last_res = {
            "status": "failed",
            "zh_name": display_name or plant_name,
            "scientific_name": "Unknown",
            "botanical_height_m": [0.5, 1.5],
            "plant_type": "shrub",
            "growth_habit": "bush",
            "suggested_game_height_px": [100, 150],
            "brief": f"AI analysis error: {last_err}",
        }

    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                mdata = json.load(f)
        except Exception:
            mdata = {}
    else:
        mdata = {"name": plant_name, "display_name": display_name}

    mdata["status"] = "ready"
    mdata["ai_analysis"] = last_res
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(mdata, f, ensure_ascii=False, indent=4)

    return {
        "plant_name": plant_name,
        "status": last_res.get("status", "failed"),
        "ok": last_res.get("status") == "success",
    }


def _clean_confirmed_registries(archived_names: set, confirmed_files: list):
    """Remove archived candidate keys from confirmed_candidates.json and confirmed_plants.json."""
    if not archived_names:
        return 0
    removed_total = 0
    for cfile in confirmed_files:
        if not cfile or not os.path.isfile(cfile):
            continue
        try:
            with open(cfile, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            keys_to_remove = [k for k in data if k in archived_names]
            if keys_to_remove:
                for k in keys_to_remove:
                    del data[k]
                with open(cfile, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                removed_total += len(keys_to_remove)
        except Exception as exc:
            print(f"[WARN] Failed to clean confirmed registry {cfile}: {exc}")
    return removed_total


def run_batch_ingest(
    candidates_dir: str = DEFAULT_CANDIDATES_DIR,
    plants_dir: str = DEFAULT_PLANTS_DIR,
    project_root: str = PROJECT_ROOT,
    confirmed_candidates_file: str = DEFAULT_CONFIRMED_CANDIDATES_FILE,
    confirmed_plants_file: str = DEFAULT_CONFIRMED_PLANTS_FILE,
    slice_workers: int = 6,
    ai_workers: int = 8,
    limit: int = None,
    dry_run: bool = False,
    force_reslice: bool = False,
) -> dict:
    """Execute the full candidate scan, slice, AI enrichment, archive, and manifest pipeline."""
    start_time = time.time()
    bad_dir = os.path.join(candidates_dir, "bad_backgrounds")
    processed_dir = os.path.join(candidates_dir, "processed")

    broken_symlinks_removed = 0
    placeholders_moved = 0
    groups = {}

    if not os.path.exists(candidates_dir):
        print(f"Candidates directory not found: {candidates_dir}")
        return {
            "broken_symlinks_removed": 0,
            "placeholders_moved": 0,
            "unique_species": 0,
            "skipped_existing": 0,
            "sliced_ok": 0,
            "sliced_failed": 0,
            "ai_ok": 0,
            "ai_failed": 0,
            "archived_entries": 0,
            "manifest_total_species": 0,
        }

    entries = sorted(os.listdir(candidates_dir))
    for entry in entries:
        if entry.startswith("."):
            continue
        p = os.path.join(candidates_dir, entry)

        # a) Remove broken symlinks
        if os.path.islink(p) and not os.path.exists(p):
            broken_symlinks_removed += 1
            if not dry_run:
                os.unlink(p)
            print(f"[CLEAN] Broken symlink removed: {entry}")
            continue

        # b) Move Wikipedia placeholders (including stray 'User:Hung Ting' directories)
        if _is_placeholder_name(entry):
            placeholders_moved += 1
            if not dry_run:
                _move_to_bad_backgrounds(p, bad_dir, entry)
            continue

        # Ignore subdirectories like processed/, bad_backgrounds/, etc.
        if os.path.isdir(p):
            continue

        if not entry.lower().endswith(IMAGE_EXTENSIONS):
            continue

        # c) Parse plant_name & display_name
        plant_name, display_name = parse_candidate_filename(entry)
        real_path = os.path.realpath(p)
        is_regular = not os.path.islink(p) and os.path.isfile(p)

        if plant_name not in groups:
            groups[plant_name] = {
                "plant_name": plant_name,
                "display_name": display_name,
                "entries": [],
                "source_path": None,
                "source_is_regular": False,
            }

        grp = groups[plant_name]
        grp["entries"].append(entry)

        # Prefer regular file target via os.path.realpath
        if grp["source_path"] is None or (is_regular and not grp["source_is_regular"]):
            if os.path.isfile(real_path):
                grp["source_path"] = real_path
                grp["source_is_regular"] = is_regular

        # Prefer meaningful display_name
        if (
            display_name
            and display_name != plant_name
            and not display_name.startswith("plant_")
            and (grp["display_name"] == plant_name or grp["display_name"].startswith("plant_"))
        ):
            grp["display_name"] = display_name

    if placeholders_moved:
        print(f"[PLACEHOLDERS] Moved {placeholders_moved} placeholder drafts/directories to bad_backgrounds/")

    species_list = [groups[k] for k in sorted(groups.keys()) if groups[k]["source_path"]]
    if limit is not None and limit >= 0:
        species_list = species_list[:limit]

    slice_tasks = []
    ai_tasks = []
    archivable_groups = []
    skipped_existing = 0

    for grp in species_list:
        plant_name = grp["plant_name"]
        display_name = grp["display_name"]

        existing = None if force_reslice else _find_existing_true_color_variant(plants_dir, plant_name)
        if existing is not None:
            _, variant_dir, mdata = existing
            skipped_existing += 1
            _ensure_footprint_metadata(variant_dir, mdata, dry_run=dry_run)

            ai_info = mdata.get("ai_analysis")
            if not isinstance(ai_info, dict) or ai_info.get("status") != "success":
                ai_tasks.append({
                    "plant_name": plant_name,
                    "display_name": mdata.get("display_name") or display_name,
                    "meta_path": os.path.join(variant_dir, "metadata.json"),
                })
            elif mdata.get("status") != "ready" and not dry_run:
                mdata["status"] = "ready"
                with open(os.path.join(variant_dir, "metadata.json"), "w", encoding="utf-8") as f:
                    json.dump(mdata, f, ensure_ascii=False, indent=4)

            archivable_groups.append(grp)
        else:
            out_dir = os.path.join(plants_dir, plant_name, "01")
            slice_tasks.append({
                "plant_name": plant_name,
                "display_name": display_name,
                "source_path": grp["source_path"],
                "output_dir": out_dir,
                "project_root": project_root,
                "group": grp,
            })

    sliced_ok = 0
    sliced_failed = 0

    # Phase 1: Concurrent slicing
    if slice_tasks:
        print(f"[PHASE 1] Slicing {len(slice_tasks)} species with {slice_workers} workers (dry_run={dry_run})...")
        if dry_run:
            sliced_ok = len(slice_tasks)
        else:
            os.makedirs(plants_dir, exist_ok=True)
            task_by_name = {t["plant_name"]: t for t in slice_tasks}
            worker_payloads = [
                {
                    "plant_name": t["plant_name"],
                    "display_name": t["display_name"],
                    "source_path": t["source_path"],
                    "output_dir": t["output_dir"],
                    "project_root": t["project_root"],
                }
                for t in slice_tasks
            ]
            with ProcessPoolExecutor(max_workers=max(1, slice_workers)) as pool:
                futures = [pool.submit(_slice_worker, payload) for payload in worker_payloads]
                for fut in as_completed(futures):
                    res = fut.result()
                    pname = res["plant_name"]
                    t = task_by_name[pname]
                    if res["ok"]:
                        sliced_ok += 1
                        archivable_groups.append(t["group"])
                        ai_tasks.append({
                            "plant_name": pname,
                            "display_name": t["display_name"],
                            "meta_path": os.path.join(t["output_dir"], "metadata.json"),
                        })
                    else:
                        sliced_failed += 1
                        print(f"[ERROR] Slice failed for {pname}: {res['error']}")

    # Phase 2: Concurrent AI botanical analysis
    ai_ok = 0
    ai_failed = 0
    if ai_tasks:
        print(f"[PHASE 2] Running AI botany analysis for {len(ai_tasks)} species with {ai_workers} workers (dry_run={dry_run})...")
        if not dry_run:
            with ThreadPoolExecutor(max_workers=max(1, ai_workers)) as pool:
                futures = [pool.submit(_ai_worker, task) for task in ai_tasks]
                for fut in as_completed(futures):
                    res = fut.result()
                    if res["ok"]:
                        ai_ok += 1
                    else:
                        ai_failed += 1

    # Phase 3: Archive real files, unlink symlinks, clean confirmed JSONs, regenerate manifest
    archived_entries = 0
    archived_names = set()
    if not dry_run and archivable_groups:
        os.makedirs(processed_dir, exist_ok=True)
        for grp in archivable_groups:
            for fname in grp["entries"]:
                fpath = os.path.join(candidates_dir, fname)
                if os.path.islink(fpath):
                    os.unlink(fpath)
                    archived_entries += 1
                    archived_names.add(fname)
                elif os.path.exists(fpath):
                    dst = os.path.join(processed_dir, fname)
                    if os.path.lexists(dst):
                        os.unlink(dst)
                    shutil.move(fpath, dst)
                    archived_entries += 1
                    archived_names.add(fname)

            # Safety: if resolved realpath was inside candidates_dir under another name, archive it too
            src_real = grp["source_path"]
            if src_real and os.path.exists(src_real):
                if os.path.dirname(src_real) == os.path.realpath(candidates_dir):
                    base_real = os.path.basename(src_real)
                    dst = os.path.join(processed_dir, base_real)
                    if os.path.lexists(dst):
                        os.unlink(dst)
                    shutil.move(src_real, dst)
                    archived_entries += 1
                    archived_names.add(base_real)

    _clean_confirmed_registries(archived_names, [confirmed_candidates_file, confirmed_plants_file])

    manifest_total = 0
    if not dry_run:
        manifest = generate_manifest(plants_dir=plants_dir)
        manifest_total = manifest.get("total_species", 0)

    elapsed = round(time.time() - start_time, 2)
    summary = {
        "broken_symlinks_removed": broken_symlinks_removed,
        "placeholders_moved": placeholders_moved,
        "unique_species": len(species_list),
        "skipped_existing": skipped_existing,
        "sliced_ok": sliced_ok,
        "sliced_failed": sliced_failed,
        "ai_ok": ai_ok,
        "ai_failed": ai_failed,
        "archived_entries": archived_entries,
        "manifest_total_species": manifest_total,
        "elapsed_sec": elapsed,
    }

    print("=" * 60)
    print("Batch Ingestion Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("=" * 60)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Batch ingest plant candidates and generate manifest.json")
    parser.add_argument("--slice-workers", type=int, default=6, help="Number of concurrent slicing worker processes")
    parser.add_argument("--ai-workers", type=int, default=8, help="Number of concurrent AI analysis threads")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of unique species to process")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without modifying files")
    parser.add_argument("--force-reslice", action="store_true", help="Re-slice even if true_color=True asset exists")
    args = parser.parse_args()

    run_batch_ingest(
        slice_workers=args.slice_workers,
        ai_workers=args.ai_workers,
        limit=args.limit,
        dry_run=args.dry_run,
        force_reslice=args.force_reslice,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
