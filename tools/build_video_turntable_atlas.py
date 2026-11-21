#!/usr/bin/env python3
"""
Slice a 360-degree pure-white studio turnaround video into a jitter-free 6x6 (36-frame)
RGBA WebP sprite atlas with border-connected white keying, circular 3-tap temporal smoothing,
and per-frame stem-first root_anchor + crown_h alignment.
"""

import argparse
import json
import math
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

from root_anchor_calibrator import calibrate_root_anchor

DEFAULT_INPUT_MP4 = REPO_ROOT / "tools" / "candidates" / "turntable_lab_sheets" / "plant_0000_video_360.mp4"
DEFAULT_OUT_DIR = REPO_ROOT / "src" / "assets" / "turntable_lab" / "plant_0000_video36"


def _split_rgba_hue_layers(rgba_img: Image.Image) -> tuple[Image.Image, Image.Image, Image.Image]:
    arr = np.array(rgba_img.convert("RGBA"), dtype=np.uint8)
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    a = arr[:, :, 3]

    visible = a > 0
    is_red = visible & (((r > g + 15) & (r > b + 15)) | ((r >= g) & (r >= b) & ~((g > r + 15) & (g > b + 15)) & ~((b > r + 15) & (b > g + 15))))
    is_green = visible & ~is_red & (((g > r + 15) & (g > b + 15)) | ((g >= r) & (g >= b)))
    is_blue = visible & ~is_red & ~is_green

    empty = np.zeros_like(arr)
    red_arr = empty.copy()
    green_arr = empty.copy()
    blue_arr = empty.copy()

    red_arr[is_red] = arr[is_red]
    green_arr[is_green] = arr[is_green]
    blue_arr[is_blue] = arr[is_blue]

    return (
        Image.fromarray(red_arr, mode="RGBA"),
        Image.fromarray(green_arr, mode="RGBA"),
        Image.fromarray(blue_arr, mode="RGBA"),
    )


def key_white_background_rgba(rgb_frame: np.ndarray) -> np.ndarray:
    """Border-connected flood-fill white background keying + soft edge de-matting."""
    h, w, _ = rgb_frame.shape
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    sq = rgb_frame[y0 : y0 + side, x0 : x0 + side, :3].astype(np.uint8)

    r_min = sq.min(axis=2)
    r_max = sq.max(axis=2)
    chroma = r_max.astype(np.int16) - r_min.astype(np.int16)

    # Strict exterior white candidate connected to borders
    pure_white = (r_min > 234) & (chroma < 22)
    labeled_bg, num_bg = ndimage.label(pure_white, structure=np.ones((3, 3), dtype=np.int32))
    border_labels = set()
    if num_bg > 0:
        border_labels.update(np.unique(labeled_bg[0, :]).tolist())
        border_labels.update(np.unique(labeled_bg[-1, :]).tolist())
        border_labels.update(np.unique(labeled_bg[:, 0]).tolist())
        border_labels.update(np.unique(labeled_bg[:, -1]).tolist())
    border_labels.discard(0)

    ext_bg = np.isin(labeled_bg, list(border_labels)) if border_labels else np.zeros((side, side), dtype=bool)

    # Seeded removal for pure-white holes enclosed between overlapping stems/petioles
    # (never triggers inside cream-green watercolor leaf stripes, which stay r_min <= 237)
    enclosed_core = (r_min >= 240) & (chroma <= 16) & (~ext_bg)
    labeled_enc, num_enc = ndimage.label(enclosed_core, structure=np.ones((3, 3), dtype=np.int32))
    if num_enc > 0:
        enc_counts = np.bincount(labeled_enc.ravel())
        valid_enc = np.where(enc_counts[1:] >= 3)[0] + 1
        if len(valid_enc) > 0:
            enc_seeds = np.isin(labeled_enc, valid_enc)
            enclosed_bg = ndimage.binary_propagation(
                enc_seeds,
                mask=((r_min > 230) & (chroma < 22) & (~ext_bg)),
                structure=np.ones((3, 3), dtype=bool),
            )
            ext_bg = ext_bg | enclosed_bg

    # Gently absorb slightly off-white halo connected to ext_bg (both outer silhouette and stem pockets)
    soft_white = (r_min > 218) & (chroma < 28)
    ext_soft_grow = ndimage.binary_dilation(ext_bg, iterations=2) & soft_white
    ext_bg = ext_bg | ext_soft_grow

    fg_binary = ~ext_bg
    # Keep largest component + nearby pieces (dist <= 20px), discard compression specks
    labeled_fg, num_fg = ndimage.label(fg_binary, structure=np.ones((3, 3), dtype=np.int32))
    if num_fg > 1:
        counts = np.bincount(labeled_fg.ravel())
        main_id = int(1 + np.argmax(counts[1:]))
        slices = ndimage.find_objects(labeled_fg)
        ms = slices[main_id - 1]
        my0, my1 = ms[0].start, ms[0].stop - 1
        mx0, mx1 = ms[1].start, ms[1].stop - 1
        keep_mask = np.zeros_like(fg_binary)
        keep_mask[labeled_fg == main_id] = True
        for cid in range(1, num_fg + 1):
            if cid == main_id:
                continue
            cs = slices[cid - 1]
            if cs is None:
                continue
            cy0, cy1 = cs[0].start, cs[0].stop - 1
            cx0, cx1 = cs[1].start, cs[1].stop - 1
            dx = max(0, cx0 - mx1, mx0 - cx1)
            dy = max(0, cy0 - my1, my0 - cy1)
            if math.hypot(dx, dy) <= 20.0 and counts[cid] >= 12:
                keep_mask[labeled_fg == cid] = True
        fg_binary = keep_mask
        ext_bg = ~fg_binary

    # Soft edge band: 3px inward from ext_bg (interior white stripes remain 255 opaque!)
    edge_band = ndimage.binary_dilation(ext_bg, iterations=3) & fg_binary
    alpha = np.where(fg_binary, 255.0, 0.0)

    bg_dist = 255.0 - r_min.astype(np.float32)
    soft_alpha = np.clip((bg_dist - 10.0) / 38.0, 0.0, 1.0) * 255.0
    alpha[edge_band] = np.minimum(alpha[edge_band], soft_alpha[edge_band])

    # Clear stray isolated faint alpha outside fg_binary
    alpha[alpha < 8.0] = 0.0

    # White de-matting on semi-transparent boundary pixels
    out_rgb = sq.astype(np.float32)
    semi = (alpha > 0.0) & (alpha < 254.5)
    a_norm = np.maximum(alpha[semi] / 255.0, 0.05)[:, None]
    out_rgb[semi] = np.clip((out_rgb[semi] - 255.0 * (1.0 - a_norm)) / a_norm, 0.0, 255.0)
    out_rgb[alpha <= 0.0] = 0.0

    rgba = np.zeros((side, side, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.round(out_rgb).astype(np.uint8)
    rgba[:, :, 3] = np.round(alpha).astype(np.uint8)
    return rgba


def build_video_atlas(
    input_mp4: Path,
    out_dir: Path,
    reverse_spin: bool = False,
    frame_count: int = 36,
    cell_size: int = 220,
    target_crown_h: float = 188.0,
) -> dict:
    import imageio.v3 as iio

    frames = iio.imread(str(input_mp4), plugin="FFMPEG")
    n_frames = len(frames)
    if n_frames < 8:
        raise RuntimeError(f"Expected >= 8 frames in {input_mp4}, got {n_frames}")

    indices = np.linspace(0, max(0, n_frames - 2), frame_count, dtype=int).tolist()
    if reverse_spin:
        indices = [indices[0]] + list(reversed(indices[1:]))

    out_dir.mkdir(parents=True, exist_ok=True)
    processed_rgba: list[np.ndarray] = []
    raw_metrics: list[dict] = []

    for i, f_idx in enumerate(indices):
        rgba = key_white_background_rgba(frames[f_idx])
        processed_rgba.append(rgba)

        alpha = rgba[:, :, 3]
        ys, xs = np.where(alpha > 38)
        if len(xs) == 0:
            h_s, w_s = alpha.shape
            raw_metrics.append({"rx": w_s * 0.5, "ry": h_s * 0.9, "top_y": 0.0, "crown_h": float(h_s), "fg_w": float(w_s)})
            continue

        top_y = float(ys.min())
        min_x = float(xs.min())
        max_x = float(xs.max())
        fg_w = max(24.0, max_x - min_x + 1.0)

        pil_rgba = Image.fromarray(rgba, mode="RGBA")
        red_img, green_img, blue_img = _split_rgba_hue_layers(pil_rgba)
        cal = calibrate_root_anchor(red_img, green_img, blue_img)
        rx = float(cal["root_x"])
        ry = float(cal["root_y"])
        crown_h = max(24.0, ry - top_y)
        raw_metrics.append({"rx": rx, "ry": ry, "top_y": top_y, "crown_h": crown_h, "fg_w": fg_w})

    # Circular 3-tap temporal smoothing on (rx, ry, crown_h)
    smoothed = []
    for i in range(frame_count):
        prev_m = raw_metrics[(i - 1) % frame_count]
        curr_m = raw_metrics[i]
        next_m = raw_metrics[(i + 1) % frame_count]
        srx = 0.25 * prev_m["rx"] + 0.50 * curr_m["rx"] + 0.25 * next_m["rx"]
        sry = 0.25 * prev_m["ry"] + 0.50 * curr_m["ry"] + 0.25 * next_m["ry"]
        sch = 0.25 * prev_m["crown_h"] + 0.50 * curr_m["crown_h"] + 0.25 * next_m["crown_h"]
        smoothed.append({"rx": srx, "ry": sry, "crown_h": sch, "fg_w": curr_m["fg_w"]})

    hframes, vframes = 6, 6
    atlas_w = hframes * cell_size
    atlas_h = vframes * cell_size
    atlas_img = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))

    contact_sheet = Image.new("RGB", (atlas_w, atlas_h), (111, 102, 75))
    cs_draw = ImageDraw.Draw(contact_sheet)

    cell_anchor_x = cell_size // 2  # 110
    cell_anchor_y = cell_size - 8   # 212

    for i in range(frame_count):
        rgba = processed_rgba[i]
        sm = smoothed[i]
        alpha = rgba[:, :, 3]
        ys, xs = np.where(alpha > 8)
        if len(xs) == 0:
            continue

        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        crop_pil = Image.fromarray(rgba[y0:y1, x0:x1], mode="RGBA")
        local_rx = sm["rx"] - float(x0)
        local_ry = sm["ry"] - float(y0)

        max_w_cell = float(cell_size - 8)
        scale = min(target_crown_h / max(sm["crown_h"], 24.0), max_w_cell / max(sm["fg_w"], 24.0))
        nw = max(1, int(round(crop_pil.width * scale)))
        nh = max(1, int(round(crop_pil.height * scale)))
        resized = crop_pil.resize((nw, nh), Image.Resampling.LANCZOS)

        px_in_cell = int(round(cell_anchor_x - local_rx * scale))
        py_in_cell = int(round(cell_anchor_y - local_ry * scale))

        # Clamp strictly inside own cell to guarantee zero bleed into neighbor frames
        src_x0 = max(0, -px_in_cell)
        src_y0 = max(0, -py_in_cell)
        src_x1 = min(nw, cell_size - px_in_cell)
        src_y1 = min(nh, cell_size - py_in_cell)
        if src_x1 > src_x0 and src_y1 > src_y0:
            clipped = resized.crop((src_x0, src_y0, src_x1, src_y1))
            dst_x = max(0, px_in_cell)
            dst_y = max(0, py_in_cell)

            row, col = divmod(i, hframes)
            cx0 = col * cell_size
            cy0 = row * cell_size
            atlas_img.paste(clipped, (cx0 + dst_x, cy0 + dst_y))

            cs_draw.rectangle([cx0, cy0, cx0 + cell_size - 1, cy0 + cell_size - 1], outline=(95, 88, 66), width=1)
            cs_draw.line([cx0 + 10, cy0 + cell_anchor_y, cx0 + cell_size - 10, cy0 + cell_anchor_y], fill=(165, 185, 145), width=1)
            contact_sheet.paste(clipped, (cx0 + dst_x, cy0 + dst_y), clipped)
            cs_draw.ellipse(
                [cx0 + cell_anchor_x - 3, cy0 + cell_anchor_y - 3, cx0 + cell_anchor_x + 3, cy0 + cell_anchor_y + 3],
                fill=(235, 50, 50),
            )
            ang_deg = int(round(i * (360.0 / frame_count)))
            cs_draw.text((cx0 + 6, cy0 + 4), f"F{i:02d} {ang_deg:03d}d", fill=(245, 240, 220))

    atlas_path = out_dir / "atlas_36.webp"
    atlas_img.save(atlas_path, format="WEBP", quality=88, method=6, lossless=False)

    cs_path = out_dir / "video_36_contact_sheet.jpg"
    contact_sheet.save(cs_path, quality=92)

    metadata = {
        "generated_at": int(time.time()),
        "plant_id": "plant_0000",
        "label_ascii": "Calathea",
        "display_name": "孔雀竹芋",
        "atlas_file": "atlas_36.webp",
        "path_prefix": "assets/turntable_lab/plant_0000_video36",
        "hframes": hframes,
        "vframes": vframes,
        "frame_count": frame_count,
        "cell_w": cell_size,
        "cell_h": cell_size,
        "atlas_w": atlas_w,
        "atlas_h": atlas_h,
        "root_anchor_px": [float(cell_anchor_x), float(cell_anchor_y)],
        "root_uv_y": round(float(cell_anchor_y) / float(cell_size), 4),
        "crown_h": float(target_crown_h),
        "root_width": 22.0,
        "default_lab_height_px": 150.0,
        "reverse_spin": bool(reverse_spin),
        "source_frames_total": int(n_frames),
        "sampled_indices": indices,
        "webp_bytes": int(atlas_path.stat().st_size),
    }
    meta_path = out_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Slice 360-deg video into 6x6 RGBA WebP sprite atlas")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_MP4)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--reverse", action="store_true", help="Reverse frame order if generated video spun CCW")
    parser.add_argument("--frame-count", type=int, default=36)
    parser.add_argument("--cell-size", type=int, default=220)
    parser.add_argument("--crown-h", type=float, default=188.0)
    args = parser.parse_args()

    build_video_atlas(
        input_mp4=args.input,
        out_dir=args.out_dir,
        reverse_spin=args.reverse,
        frame_count=args.frame_count,
        cell_size=args.cell_size,
        target_crown_h=args.crown_h,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
