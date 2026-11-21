import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_plant_asset import build_asset

ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
TMP_CLEAN_DIR = Path("/tmp/turntable_lab_clean")
OUT_LAB_DIR = REPO_ROOT / "src" / "assets" / "turntable_lab"
MANIFEST_PATH = REPO_ROOT / "src" / "assets" / "plants" / "manifest.json"


def _crop_quadrant(img: Image.Image, quadrant: str) -> Image.Image:
    w, h = img.size
    hw, hh = w // 2, h // 2
    boxes = {
        "TL": (0, 0, hw, hh),
        "TR": (hw, 0, w, hh),
        "BL": (0, hh, hw, h),
        "BR": (hw, hh, w, h),
    }
    return img.crop(boxes[quadrant])


def clean_view_image(img: Image.Image, border_px: int = 6) -> Image.Image:
    """Whiten outer border frame and remove tiny distant corner specks."""
    rgb = np.array(img.convert("RGB"), dtype=np.uint8)
    h, w, _ = rgb.shape

    # 1. Whiten 6px outer border frame (#FFFFFF)
    rgb[:border_px, :, :] = 255
    rgb[max(0, h - border_px) :, :, :] = 255
    rgb[:, :border_px, :] = 255
    rgb[:, max(0, w - border_px) :, :] = 255

    # 2. Non-white mask: 255 - min(r,g,b) > 15
    mask = (255 - rgb.min(axis=2)) > 15
    total_fg = int(mask.sum())
    if total_fg == 0:
        return Image.fromarray(rgb, mode="RGB")

    # 3. Connected component filtering
    structure = np.ones((3, 3), dtype=np.int32)
    labeled, num_features = ndimage.label(mask, structure=structure)
    if num_features <= 1:
        return Image.fromarray(rgb, mode="RGB")

    counts = np.bincount(labeled.ravel())
    # Ignore label 0 (background)
    comp_ids = np.arange(1, num_features + 1)
    main_id = int(comp_ids[np.argmax(counts[1:])])

    slices = ndimage.find_objects(labeled)
    main_slice = slices[main_id - 1]
    my0, my1 = main_slice[0].start, main_slice[0].stop - 1
    mx0, mx1 = main_slice[1].start, main_slice[1].stop - 1

    area_thresh = 0.015 * float(total_fg)
    for cid in range(1, num_features + 1):
        if cid == main_id:
            continue
        cnt = int(counts[cid])
        if cnt >= area_thresh:
            continue
        c_slice = slices[cid - 1]
        if c_slice is None:
            continue
        cy0, cy1 = c_slice[0].start, c_slice[0].stop - 1
        cx0, cx1 = c_slice[1].start, c_slice[1].stop - 1
        dx = max(0, cx0 - mx1, mx0 - cx1)
        dy = max(0, cy0 - my1, my0 - cy1)
        dist = math.hypot(dx, dy)
        if dist > 25.0:
            rgb[labeled == cid] = 255

    return Image.fromarray(rgb, mode="RGB")


def compute_top_y_and_crown_h(view_dir: Path, root_anchor_uv: list[float], height: int) -> tuple[int, float]:
    """Compute highest row with alpha > 38 across layers and crown_h."""
    top_y = height
    for fname in ("layer_red.png", "layer_green.png", "layer_blue.png"):
        fpath = view_dir / fname
        if not fpath.exists():
            continue
        arr = np.array(Image.open(fpath).convert("RGBA"))
        alpha_rows = np.where(arr[:, :, 3].max(axis=1) > 38)[0]
        if len(alpha_rows) > 0:
            top_y = min(top_y, int(alpha_rows[0]))
    if top_y >= height:
        top_y = 0
    crown_h = round(max(24.0, float(root_anchor_uv[1]) * float(height) - float(top_y)), 2)
    return top_y, crown_h


def load_legacy_records() -> dict[str, dict]:
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    found: dict[str, dict] = {}
    for rec in manifest.get("plants", []):
        name = rec.get("name")
        if name in ("plant_0000", "plant_0177"):
            if rec.get("enabled", True) and rec.get("true_color", True):
                if name not in found:
                    found[name] = rec
    for key in ("plant_0000", "plant_0177"):
        if key not in found:
            raise RuntimeError(f"Active legacy manifest record not found for {key}")
    return found


def load_source_views() -> dict[str, dict[int, Image.Image]]:
    sheets_dir = REPO_ROOT / "tools" / "candidates" / "turntable_lab_sheets"
    processed_dir = REPO_ROOT / "tools" / "candidates" / "processed"

    cal_000 = Image.open(processed_dir / "candidate_plant_0000_孔雀竹芋.jpg")
    sheet1 = Image.open(sheets_dir / "plant_0000_sheet1.jpg")
    sheet2 = Image.open(sheets_dir / "plant_0000_sheet2.jpg")

    views_0000 = {
        0: cal_000,
        45: _crop_quadrant(sheet1, "TL"),
        90: _crop_quadrant(sheet1, "TR"),
        135: _crop_quadrant(sheet1, "BL"),
        180: _crop_quadrant(sheet1, "BR"),
        225: _crop_quadrant(sheet2, "TL"),
        270: _crop_quadrant(sheet2, "TR"),
        315: _crop_quadrant(sheet2, "BL"),
    }

    views_0177 = {}
    for ang in ANGLES:
        img_path = sheets_dir / f"plant_0177_{ang:03d}.jpg"
        views_0177[ang] = Image.open(img_path)

    return {
        "plant_0000": views_0000,
        "plant_0177": views_0177,
    }


def composite_view_rgba(view_dir: Path) -> Image.Image:
    base = None
    for fname in ("layer_red.png", "layer_green.png", "layer_blue.png"):
        fpath = view_dir / fname
        if not fpath.exists():
            continue
        layer = Image.open(fpath).convert("RGBA")
        if base is None:
            base = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        base = Image.alpha_composite(base, layer)
    if base is None:
        return Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    return base


def render_contact_sheet(species_entries: list[dict], out_path: Path) -> None:
    cell_w = 240
    cell_h = 300
    baseline_y = 258
    cols = len(ANGLES)
    rows = len(species_entries)

    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)

    for r_idx, sp in enumerate(species_entries):
        label_ascii = sp["label_ascii"]
        views = sp["views"]
        for c_idx, ang in enumerate(ANGLES):
            x0 = c_idx * cell_w
            y0 = r_idx * cell_h
            view_info = views[str(ang)]
            view_dir = REPO_ROOT / "src" / view_info["path_prefix"]
            comp = composite_view_rgba(view_dir)

            vw, vh = comp.size
            max_draw_h = 220.0
            max_draw_w = 200.0
            scale = min(max_draw_h / max(vh, 1), max_draw_w / max(vw, 1), 1.0)
            dw = max(1, int(round(vw * scale)))
            dh = max(1, int(round(vh * scale)))
            resized = comp.resize((dw, dh), Image.Resampling.LANCZOS)

            ra_u, ra_v = view_info["root_anchor"]
            anchor_x_scaled = ra_u * dw
            anchor_y_scaled = ra_v * dh

            paste_x = int(round(x0 + cell_w * 0.5 - anchor_x_scaled))
            paste_y = int(round(y0 + baseline_y - anchor_y_scaled))

            # Draw cell border and baseline
            draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h - 1], outline=(225, 225, 225), width=1)
            draw.line([x0 + 12, y0 + baseline_y, x0 + cell_w - 12, y0 + baseline_y], fill=(190, 210, 190), width=1)

            sheet.paste(resized, (paste_x, paste_y), resized)

            # Red dot at root_anchor
            dot_x = x0 + cell_w * 0.5
            dot_y = y0 + baseline_y
            draw.ellipse([dot_x - 4, dot_y - 4, dot_x + 4, dot_y + 4], fill=(225, 35, 35))

            caption = f"{label_ascii} {ang:03d}"
            draw.text((x0 + 10, y0 + baseline_y + 12), caption, fill=(30, 30, 30))

    sheet.save(out_path, quality=92)


def main() -> int:
    TMP_CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    OUT_LAB_DIR.mkdir(parents=True, exist_ok=True)

    legacy_records = load_legacy_records()
    source_views = load_source_views()

    species_specs = [
        {
            "name": "plant_0000",
            "label_ascii": "Calathea",
            "default_lab_height_px": 150.0,
        },
        {
            "name": "plant_0177",
            "label_ascii": "Datura",
            "default_lab_height_px": 185.0,
        },
    ]

    species_entries = []
    for spec in species_specs:
        pid = spec["name"]
        legacy = legacy_records[pid]
        display_name = legacy.get("display_name", pid)
        views_dict = {}

        for ang in ANGLES:
            angle_str = f"{ang:03d}"
            raw_img = source_views[pid][ang]
            cleaned_img = clean_view_image(raw_img, border_px=6)
            tmp_png = TMP_CLEAN_DIR / f"{pid}_{angle_str}.png"
            cleaned_img.save(tmp_png)

            view_out_dir = OUT_LAB_DIR / pid / angle_str
            ok = build_asset(
                str(tmp_png),
                pid,
                angle_str,
                str(view_out_dir),
                str(REPO_ROOT),
                display_name=display_name,
                inject_css=False,
            )
            if not ok:
                raise RuntimeError(f"build_asset failed for {pid} angle {angle_str}")

            meta_path = view_out_dir / "metadata.json"
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            _, crown_h = compute_top_y_and_crown_h(view_out_dir, meta["root_anchor"], meta["height"])

            layer_files = []
            for slot in ("green", "red", "blue"):
                fname = f"layer_{slot}.png"
                if (view_out_dir / fname).exists():
                    layer_files.append({"slot": slot, "file": fname})

            views_dict[str(ang)] = {
                "path_prefix": f"assets/turntable_lab/{pid}/{angle_str}",
                "width": meta["width"],
                "height": meta["height"],
                "root_anchor": meta["root_anchor"],
                "crown_h": crown_h,
                "root_width": meta["root_width"],
                "layer_files": layer_files,
            }

        species_entries.append(
            {
                "name": pid,
                "label_ascii": spec["label_ascii"],
                "display_name": display_name,
                "default_lab_height_px": spec["default_lab_height_px"],
                "root_width": legacy.get("root_width", views_dict["0"]["root_width"]),
                "legacy": legacy,
                "views": views_dict,
            }
        )

    index_data = {
        "generated_at": int(time.time()),
        "angles": ANGLES,
        "species": species_entries,
    }
    index_path = OUT_LAB_DIR / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    contact_sheet_path = OUT_LAB_DIR / "turntable_contact_sheet.jpg"
    render_contact_sheet(species_entries, contact_sheet_path)
    print(f"Built turntable lab assets at {OUT_LAB_DIR}")
    print(f"Index written: {index_path}")
    print(f"Contact sheet written: {contact_sheet_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
