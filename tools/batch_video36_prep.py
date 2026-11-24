#!/usr/bin/env python3
"""
Batch Stage-1 (`prep`) runner + Google Flow export packager for 360-degree 36F assets.

Usage examples:
  python tools/batch_video36_prep.py --list-file tools/video36_tier1_batch.txt
  python tools/batch_video36_prep.py --plant-ids plant_0134,plant_0065 --workers 2
  python tools/batch_video36_prep.py --auto-select 16 --dry-run
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import shutil
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import plant_video36_pipeline as pvp  # noqa: E402

DEFAULT_LIST_FILE = TOOLS_DIR / "video36_tier1_batch.txt"
BACKUP_FILES = (
    "ref_180_raw.png",
    "ref_180.png",
    "ref_000_vs_180_check.jpg",
    "prep_meta.json",
    "flow_prompts.json",
    "flow_prompts.txt",
)


def parse_list_file(list_file: Path) -> list[str]:
    """Parse plant IDs from a text file ignoring blanks and inline '#' comments."""
    ids: list[str] = []
    seen: set[str] = set()
    for raw_line in list_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        for token in line.replace(",", " ").split():
            if token and token not in seen:
                seen.add(token)
                ids.append(token)
    return ids


def auto_select_species(count: int) -> list[str]:
    """Select up to `count` enabled true-color species balanced across 4 plant types."""
    manifest = json.loads(pvp.MANIFEST_PATH.read_text(encoding="utf-8"))
    index_path = pvp.LAB_ROOT / "video36_index.json"
    published: set[str] = set()
    if index_path.exists():
        try:
            idx_data = json.loads(index_path.read_text(encoding="utf-8"))
            published = {
                str(sp.get("plant_id"))
                for sp in (idx_data.get("species") or [])
                if isinstance(sp, dict) and sp.get("plant_id")
            }
        except Exception:
            published = set()

    buckets: dict[str, list[str]] = {"tree": [], "shrub": [], "flower": [], "groundcover": []}
    for rec in manifest.get("plants") or []:
        if not isinstance(rec, dict):
            continue
        if not (rec.get("enabled", True) and rec.get("true_color", True)):
            continue
        pid = str(rec.get("name") or "").strip()
        if not pid or pid in published:
            continue
        ai = rec.get("ai_analysis") if isinstance(rec.get("ai_analysis"), dict) else {}
        ptype = str(ai.get("plant_type") or "shrub")
        if ptype not in buckets:
            ptype = "shrub"
        buckets[ptype].append(pid)

    selected: list[str] = []
    order = ("tree", "shrub", "flower", "groundcover")
    cursors = {k: 0 for k in order}
    while len(selected) < count:
        progressed = False
        for k in order:
            if cursors[k] < len(buckets[k]):
                selected.append(buckets[k][cursors[k]])
                cursors[k] += 1
                progressed = True
                if len(selected) >= count:
                    break
        if not progressed:
            break
    return selected


def resolve_target_ids(args: argparse.Namespace) -> list[str]:
    if args.plant_ids:
        raw_tokens = args.plant_ids.replace(",", " ").split()
        out: list[str] = []
        for tok in raw_tokens:
            if tok and tok not in out:
                out.append(tok)
        return out
    if args.auto_select and args.auto_select > 0:
        return auto_select_species(args.auto_select)
    list_path = Path(args.list_file)
    if not list_path.exists():
        raise FileNotFoundError(f"Missing batch list file: {list_path}")
    return parse_list_file(list_path)


def _is_prep_complete(run_dir: Path, mode: str, min_iou: float) -> dict | None:
    meta_path = run_dir / "prep_meta.json"
    if not meta_path.is_file():
        return None
    if not (run_dir / "ref_000.png").is_file() or not (run_dir / "ref_180.png").is_file():
        return None
    if mode == "4quarter":
        if not (run_dir / "ref_090.png").is_file() or not (run_dir / "ref_270.png").is_file():
            return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if mode == "4quarter" and meta.get("mode") != "4quarter":
        return None
    if float(meta.get("mirror_silhouette_iou") or 0.0) < min_iou:
        return None
    return meta


def _backup_prep_artifacts(run_dir: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    for fname in BACKUP_FILES:
        src = run_dir / fname
        if src.exists():
            shutil.copy2(src, backup_dir / fname)


def _restore_prep_artifacts(backup_dir: Path, run_dir: Path) -> None:
    for fname in BACKUP_FILES:
        src = backup_dir / fname
        if src.exists():
            shutil.copy2(src, run_dir / fname)


def _build_prep_namespace(plant_id: str, args: argparse.Namespace, force_flag: bool) -> argparse.Namespace:
    return argparse.Namespace(
        plant_id=plant_id,
        mode=args.mode,
        stages="prep",
        front_image=None,
        right_image=None,
        back_image=None,
        left_image=None,
        key_mode=args.key_mode,
        image_model=args.image_model,
        video_model="gemini-omni-1.2-flash",
        duration="3s",
        resolution="720p",
        timeout=args.timeout,
        force=force_flag,
        draft=True,
        segment="all",
        half="both",
        mirror_q0_for_270=False,
        reverse_first_half=False,
        reverse_second_half=False,
    )


def _finalize_meta(prep_meta: dict, run_dir: Path, min_iou: float) -> dict:
    iou = float(prep_meta.get("mirror_silhouette_iou") or 0.0)
    prep_meta["qc_status"] = "PASS" if iou >= min_iou else "REVIEW"
    if not (run_dir / "flow_prompts.txt").exists() or not (run_dir / "flow_prompts.json").exists():
        info = pvp.write_flow_prompt_files(prep_meta, run_dir)
        prep_meta["flow_prompts_json"] = info["flow_prompts_json"]
        prep_meta["flow_prompts_txt"] = info["flow_prompts_txt"]
    (run_dir / "prep_meta.json").write_text(json.dumps(prep_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return prep_meta


def run_single_plant_prep(plant_id: str, args: argparse.Namespace, export_root: Path) -> dict:
    t0 = time.time()
    rec = pvp._load_manifest_record(plant_id)
    run_dir = pvp.RUNS_ROOT / plant_id

    if args.skip_existing and not args.force:
        cached = _is_prep_complete(run_dir, args.mode, args.min_iou)
        if cached is not None:
            cached = _finalize_meta(cached, run_dir, args.min_iou)
            bundle_dir = pvp.export_flow_bundle(plant_id, run_dir, cached, export_root=export_root)
            return {
                "plant_id": plant_id,
                "display_name": cached.get("display_name") or rec.get("display_name") or plant_id,
                "plant_type": cached.get("plant_type") or "shrub",
                "status": "SKIPPED",
                "qc_status": cached["qc_status"],
                "mirror_silhouette_iou": float(cached.get("mirror_silhouette_iou") or 0.0),
                "resolved_key_mode": cached.get("resolved_key_mode") or "pockets",
                "retries": 0,
                "elapsed_sec": round(time.time() - t0, 2),
                "check_jpg": str(run_dir / "ref_000_vs_180_check.jpg"),
                "flow_bundle": str(bundle_dir),
                "error": None,
            }

    best_meta = pvp.run_prep_stage(_build_prep_namespace(plant_id, args, args.force), rec, run_dir)
    best_iou = float(best_meta.get("mirror_silhouette_iou") or 0.0)
    retries_used = 0

    backup_dir = run_dir / "_best_prep_backup"
    try:
        while best_iou < args.min_iou and retries_used < args.max_retries:
            retries_used += 1
            _backup_prep_artifacts(run_dir, backup_dir)
            print(
                f"[batch_prep] {plant_id} IoU={best_iou:.4f} < {args.min_iou:.2f}; "
                f"retry {retries_used}/{args.max_retries}..."
            )
            retry_meta = pvp.run_prep_stage(_build_prep_namespace(plant_id, args, True), rec, run_dir)
            retry_iou = float(retry_meta.get("mirror_silhouette_iou") or 0.0)
            if retry_iou >= best_iou:
                best_meta = retry_meta
                best_iou = retry_iou
            else:
                _restore_prep_artifacts(backup_dir, run_dir)
                best_meta = json.loads((run_dir / "prep_meta.json").read_text(encoding="utf-8"))
                best_iou = float(best_meta.get("mirror_silhouette_iou") or 0.0)
    finally:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

    best_meta = _finalize_meta(best_meta, run_dir, args.min_iou)
    bundle_dir = pvp.export_flow_bundle(plant_id, run_dir, best_meta, export_root=export_root)

    return {
        "plant_id": plant_id,
        "display_name": best_meta.get("display_name") or rec.get("display_name") or plant_id,
        "plant_type": best_meta.get("plant_type") or "shrub",
        "status": best_meta["qc_status"],
        "qc_status": best_meta["qc_status"],
        "mirror_silhouette_iou": round(best_iou, 4),
        "resolved_key_mode": best_meta.get("resolved_key_mode") or "pockets",
        "retries": retries_used,
        "elapsed_sec": round(time.time() - t0, 2),
        "check_jpg": str(run_dir / "ref_000_vs_180_check.jpg"),
        "flow_bundle": str(bundle_dir),
        "error": None,
    }


def _build_contact_sheet(results: list[dict], out_jpg: Path) -> Path:
    tile_w, tile_h = 640, 200
    cols = 2
    valid = [r for r in results if r.get("check_jpg") and Path(r["check_jpg"]).is_file()]
    rows = max(1, math.ceil(max(1, len(valid)) / cols))
    header_h = 44
    sheet = Image.new("RGB", (cols * tile_w, header_h + rows * tile_h), (24, 28, 34))
    draw = ImageDraw.Draw(sheet)

    n_pass = sum(1 for r in results if r.get("qc_status") == "PASS")
    n_rev = sum(1 for r in results if r.get("qc_status") == "REVIEW")
    n_fail = sum(1 for r in results if r.get("status") == "FAILED")
    draw.text(
        (16, 14),
        f"Psy Garden 36F Prep Batch  |  Total: {len(results)}  |  PASS: {n_pass}  |  REVIEW: {n_rev}  |  FAILED: {n_fail}",
        fill=(242, 204, 96),
    )

    for idx, res in enumerate(valid):
        r, c = divmod(idx, cols)
        x0, y0 = c * tile_w, header_h + r * tile_h
        im = Image.open(res["check_jpg"]).convert("RGB").resize((tile_w, tile_h), Image.Resampling.LANCZOS)
        sheet.paste(im, (x0, y0))
        badge_col = (62, 180, 95) if res.get("qc_status") == "PASS" else (235, 165, 50)
        draw.rectangle([x0, y0, x0 + tile_w - 1, y0 + tile_h - 1], outline=badge_col, width=2)

    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_jpg, quality=92)
    return out_jpg


def _write_batch_reports(results: list[dict], args: argparse.Namespace, runs_root: Path) -> dict:
    runs_root.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": int(time.time()),
        "mode": args.mode,
        "min_iou": args.min_iou,
        "total": len(results),
        "pass": sum(1 for r in results if r.get("qc_status") == "PASS"),
        "review": sum(1 for r in results if r.get("qc_status") == "REVIEW"),
        "failed": sum(1 for r in results if r.get("status") == "FAILED"),
        "skipped": sum(1 for r in results if r.get("status") == "SKIPPED"),
        "retried": sum(int(r.get("retries") or 0) for r in results),
        "results": results,
    }
    json_path = runs_root / "prep_batch_report.json"
    md_path = runs_root / "prep_batch_report.md"
    sheet_path = runs_root / "prep_batch_contact_sheet.jpg"

    _build_contact_sheet(results, sheet_path)
    summary["contact_sheet"] = str(sheet_path)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# 36F Batch Prep Report",
        "",
        f"- Total: `{summary['total']}` | PASS: `{summary['pass']}` | REVIEW: `{summary['review']}` | FAILED: `{summary['failed']}` | Skipped: `{summary['skipped']}`",
        "",
        "| Plant ID | 中文名 | Type | IoU | QC | Status | Key Mode | Flow Export |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- |",
    ]
    for r in results:
        iou_str = f"{r['mirror_silhouette_iou']:.4f}" if r.get("mirror_silhouette_iou") is not None else "--"
        md_lines.append(
            f"| `{r['plant_id']}` | {r['display_name']} | {r['plant_type']} | {iou_str} | "
            f"{r.get('qc_status') or 'FAIL'} | {r['status']} | `{r.get('resolved_key_mode') or '--'}` | `{r.get('flow_bundle') or ''}` |"
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary


def run_batch(args: argparse.Namespace) -> dict:
    plant_ids = resolve_target_ids(args)
    if args.dry_run:
        preview = []
        for pid in plant_ids:
            rec = pvp._load_manifest_record(pid)
            ai = rec.get("ai_analysis") or {}
            preview.append(
                {
                    "plant_id": pid,
                    "display_name": rec.get("display_name") or pid,
                    "plant_type": ai.get("plant_type") or "shrub",
                    "has_cached": _is_prep_complete(pvp.RUNS_ROOT / pid, args.mode, args.min_iou) is not None,
                }
            )
        payload = {"dry_run": True, "count": len(preview), "targets": preview}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload

    pvp._ensure_api_key_silently()
    export_root = Path(args.export_flow_dir)
    results_by_id: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futs = {pool.submit(run_single_plant_prep, pid, args, export_root): pid for pid in plant_ids}
        for fut in as_completed(futs):
            pid = futs[fut]
            try:
                results_by_id[pid] = fut.result()
            except Exception as exc:
                rec = pvp._load_manifest_record(pid)
                ai = rec.get("ai_analysis") or {}
                results_by_id[pid] = {
                    "plant_id": pid,
                    "display_name": rec.get("display_name") or pid,
                    "plant_type": ai.get("plant_type") or "shrub",
                    "status": "FAILED",
                    "qc_status": "FAIL",
                    "mirror_silhouette_iou": None,
                    "resolved_key_mode": None,
                    "retries": 0,
                    "elapsed_sec": 0.0,
                    "check_jpg": None,
                    "flow_bundle": None,
                    "error": str(exc),
                }

    ordered = [results_by_id[pid] for pid in plant_ids if pid in results_by_id]
    summary = _write_batch_reports(ordered, args, pvp.RUNS_ROOT)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Concurrent batch prep runner + Google Flow export packager")
    parser.add_argument("--plant-ids", default="", help="Comma/space separated plant IDs")
    parser.add_argument("--list-file", type=Path, default=DEFAULT_LIST_FILE, help="Path to plant ID list file")
    parser.add_argument("--auto-select", type=int, default=0, help="Auto-pick N species balanced across 4 types")
    parser.add_argument("--mode", choices=["2half", "4quarter"], default="2half")
    parser.add_argument("--key-mode", choices=["auto", "pockets", "strict_pockets", "border_only"], default="auto")
    parser.add_argument("--image-model", default="gemini-nano-banana-2.1")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--min-iou", type=float, default=0.78)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--export-flow-dir", type=Path, default=pvp.RUNS_ROOT.parent / "flow_export")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    summary = run_batch(args)
    if args.dry_run:
        return 0
    return 1 if summary.get("failed", 0) == summary.get("total", 0) and summary.get("total", 0) > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
