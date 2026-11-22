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

DEFAULT_INPUT_MP4 = REPO_ROOT / "tools" / "candidates" / "turntable_lab_sheets" / "plant_0000_video_360_6s.mp4"
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


def key_white_background_rgba(rgb_frame: np.ndarray, key_mode: str = "pockets") -> np.ndarray:
    """Border-connected flood-fill white background keying + optional interior pocket removal + soft edge de-matting."""
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

    if key_mode == "pockets":
        # Seeded removal for pure-white holes enclosed between overlapping stems/petioles
        # (safe when interior leaf stripes stay r_min <= 237)
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
    elif key_mode == "strict_pockets":
        # Protect watercolor white/lavender petals while clearing larger pure-white background gaps between branches
        enclosed_core = (r_min >= 249) & (chroma <= 5) & (~ext_bg)
        labeled_enc, num_enc = ndimage.label(enclosed_core, structure=np.ones((3, 3), dtype=np.int32))
        if num_enc > 0:
            enc_counts = np.bincount(labeled_enc.ravel())
            valid_enc = np.where(enc_counts[1:] >= 24)[0] + 1
            if len(valid_enc) > 0:
                enc_seeds = np.isin(labeled_enc, valid_enc)
                enclosed_bg = ndimage.binary_propagation(
                    enc_seeds,
                    mask=((r_min >= 244) & (chroma <= 9) & (~ext_bg)),
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


def _sample_segment_indices(frames_seg: np.ndarray, count: int, sampling: str) -> tuple[list[int], dict]:
    n = len(frames_seg)
    if n < 2:
        raise RuntimeError(f"Expected >= 2 frames in segment, got {n}")
    frames_f = frames_seg.astype(np.float32)
    diffs = np.array([float(np.abs(frames_f[i + 1] - frames_f[i]).mean()) for i in range(n - 1)], dtype=np.float64)
    median_raw_diff = float(np.median(diffs))
    max_raw_diff = float(np.max(diffs))
    cut_spike_ratio = round(max_raw_diff / max(median_raw_diff, 1e-6), 2)

    if sampling == "arclength" and n > count:
        w_d = np.maximum(diffs, 0.25 * max(median_raw_diff, 1e-6))
        cum = np.concatenate([[0.0], np.cumsum(w_d)])
        targets = np.linspace(0.0, cum[-1], count, endpoint=False)
        indices = [int(np.clip(np.searchsorted(cum, t, side="left"), 0, n - 2)) for t in targets]
    else:
        indices = np.linspace(0, max(0, n - 2), count, dtype=int).tolist()

    return indices, {
        "segment_frames": int(n),
        "median_raw_diff": round(median_raw_diff, 3),
        "max_raw_diff": round(max_raw_diff, 3),
        "cut_spike_ratio": cut_spike_ratio,
    }


def _infer_path_prefix(out_dir: Path, explicit_prefix: str | None) -> str:
    if explicit_prefix:
        return explicit_prefix.strip("/")
    src_root = (REPO_ROOT / "src").resolve()
    try:
        return out_dir.resolve().relative_to(src_root).as_posix()
    except ValueError:
        return "assets/turntable_lab/plant_0000_video36"


def build_video_atlas(
    input_mp4: Path,
    out_dir: Path,
    reverse_spin: bool = False,
    frame_count: int = 36,
    cell_size: int = 220,
    target_crown_h: float = 188.0,
    sampling: str = "arclength",
    stitch_second_half: Path | None = None,
    second_half_start: int = 48,
    second_half_end: int = -1,
    reverse_first_half: bool = False,
    reverse_second_half: bool = False,
    stitch_segments: list[Path] | None = None,
    path_prefix: str | None = None,
    key_mode: str = "pockets",
    plant_id: str = "plant_0000",
    display_name: str = "孔雀竹芋",
    label_ascii: str = "Calathea",
    plant_type: str = "groundcover",
    root_width: float = 22.0,
    lab_height: float = 150.0,
) -> dict:
    import imageio.v3 as iio

    selected_frames: list[np.ndarray] = []
    if stitch_segments is not None and len(stitch_segments) >= 2:
        k_seg = len(stitch_segments)
        base_c, rem_c = divmod(frame_count, k_seg)
        seg_counts = [base_c + (1 if idx < rem_c else 0) for idx in range(k_seg)]

        all_indices: list[int] = []
        seg_stats_list: list[dict] = []
        seam_boundaries: list[int] = []
        total_raw_frames = 0

        for seg_idx, seg_path in enumerate(stitch_segments):
            seg_frames = iio.imread(str(seg_path), plugin="FFMPEG")
            n_seg = len(seg_frames)
            if n_seg < 8:
                raise RuntimeError(f"Expected >= 8 frames in segment {seg_path}, got {n_seg}")
            idx_seg, st_seg = _sample_segment_indices(seg_frames, seg_counts[seg_idx], sampling)
            if seg_idx > 0:
                seam_boundaries.append(len(selected_frames))
            selected_frames.extend(seg_frames[k] for k in idx_seg)
            all_indices.extend(int(total_raw_frames + k) for k in idx_seg)
            st_seg["segment_path"] = str(seg_path)
            st_seg["sampled_count"] = len(idx_seg)
            seg_stats_list.append(st_seg)
            total_raw_frames += n_seg

        indices = all_indices
        median_raw_diff = round(float(np.mean([s["median_raw_diff"] for s in seg_stats_list])), 3)
        max_raw_diff = round(float(max(s["max_raw_diff"] for s in seg_stats_list)), 3)
        cut_spike_ratio = round(float(max(s["cut_spike_ratio"] for s in seg_stats_list)), 2)
        n_frames = int(total_raw_frames)
        seam_diffs = [
            round(float(np.abs(selected_frames[b].astype(np.float32) - selected_frames[b - 1].astype(np.float32)).mean()), 3)
            for b in seam_boundaries
        ]
        extra_meta = {
            "stitched": True,
            "stitch_mode": f"{k_seg}_segments",
            "segment_stats": seg_stats_list,
            "seam_diffs": seam_diffs,
            "loop_seam_diff": round(float(np.abs(selected_frames[0].astype(np.float32) - selected_frames[-1].astype(np.float32)).mean()), 3),
        }
    elif stitch_second_half is not None:
        frames1 = iio.imread(str(input_mp4), plugin="FFMPEG")
        n1 = len(frames1)
        if n1 < 8:
            raise RuntimeError(f"Expected >= 8 frames in {input_mp4}, got {n1}")
        frames2_all = iio.imread(str(stitch_second_half), plugin="FFMPEG")
        end2 = len(frames2_all) if second_half_end < 0 else min(len(frames2_all), second_half_end)
        seg2 = frames2_all[max(0, second_half_start) : end2]
        if len(seg2) < 8:
            raise RuntimeError(f"Expected >= 8 frames in second half segment [{second_half_start}:{end2}], got {len(seg2)}")

        seg1 = frames1[::-1] if reverse_first_half else frames1
        seg2_ord = seg2[::-1] if reverse_second_half else seg2

        half1 = frame_count // 2
        half2 = frame_count - half1
        idx1, stats1 = _sample_segment_indices(seg1, half1, sampling)
        idx2, stats2 = _sample_segment_indices(seg2_ord, half2, sampling)

        selected_frames = [seg1[k] for k in idx1] + [seg2_ord[k] for k in idx2]
        indices = idx1 + [int(k + max(0, second_half_start)) for k in idx2]
        median_raw_diff = round((stats1["median_raw_diff"] + stats2["median_raw_diff"]) * 0.5, 3)
        max_raw_diff = round(max(stats1["max_raw_diff"], stats2["max_raw_diff"]), 3)
        cut_spike_ratio = round(max(stats1["cut_spike_ratio"], stats2["cut_spike_ratio"]), 2)
        n_frames = int(n1 + len(seg2))
        extra_meta = {
            "stitched": True,
            "first_half_stats": stats1,
            "second_half_stats": stats2,
            "stitch_seam_diff": round(float(np.abs(selected_frames[half1].astype(np.float32) - selected_frames[half1 - 1].astype(np.float32)).mean()), 3),
            "loop_seam_diff": round(float(np.abs(selected_frames[0].astype(np.float32) - selected_frames[-1].astype(np.float32)).mean()), 3),
        }
    else:
        frames1 = iio.imread(str(input_mp4), plugin="FFMPEG")
        n1 = len(frames1)
        if n1 < 8:
            raise RuntimeError(f"Expected >= 8 frames in {input_mp4}, got {n1}")
        n_frames = n1
        indices, stats = _sample_segment_indices(frames1, frame_count, sampling)
        if reverse_spin:
            indices = [indices[0]] + list(reversed(indices[1:]))
        selected_frames = [frames1[k] for k in indices]
        median_raw_diff = stats["median_raw_diff"]
        max_raw_diff = stats["max_raw_diff"]
        cut_spike_ratio = stats["cut_spike_ratio"]
        extra_meta = {
            "stitched": False,
            "loop_seam_diff": round(float(np.abs(selected_frames[0].astype(np.float32) - selected_frames[-1].astype(np.float32)).mean()), 3),
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    processed_rgba: list[np.ndarray] = []
    raw_metrics: list[dict] = []

    for frame_rgb in selected_frames:
        rgba = key_white_background_rgba(frame_rgb, key_mode=key_mode)
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

    bboxes: list[tuple[int, int, int, int] | None] = []
    shrink = 1.0
    for i in range(frame_count):
        alpha = processed_rgba[i][:, :, 3]
        ys, xs = np.where(alpha > 8)
        if len(xs) == 0:
            bboxes.append(None)
            continue
        fx0, fy0, fx1, fy1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        bboxes.append((fx0, fy0, fx1, fy1))
        sm = smoothed[i]
        des_s = target_crown_h / max(sm["crown_h"], 24.0)
        left_allow = float(cell_anchor_x - 4) / max(sm["rx"] - float(fx0), 12.0)
        right_allow = float(cell_size - 4 - cell_anchor_x) / max(float(fx1) - sm["rx"], 12.0)
        top_allow = float(cell_anchor_y - 4) / max(sm["ry"] - float(fy0), 12.0)
        shrink = min(shrink, left_allow / des_s, right_allow / des_s, top_allow / des_s)

    effective_crown_h = round(max(48.0, target_crown_h * max(shrink, 0.25)), 2)

    for i in range(frame_count):
        bbox = bboxes[i]
        if bbox is None:
            continue
        rgba = processed_rgba[i]
        sm = smoothed[i]
        x0, y0, x1, y1 = bbox
        crop_pil = Image.fromarray(rgba[y0:y1, x0:x1], mode="RGBA")
        local_rx = sm["rx"] - float(x0)
        local_ry = sm["ry"] - float(y0)

        scale = effective_crown_h / max(sm["crown_h"], 24.0)
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

    resolved_prefix = _infer_path_prefix(out_dir, path_prefix)
    metadata = {
        "generated_at": int(time.time()),
        "plant_id": plant_id,
        "label_ascii": label_ascii or plant_id,
        "display_name": display_name or label_ascii or plant_id,
        "plant_type": plant_type,
        "atlas_file": "atlas_36.webp",
        "path_prefix": resolved_prefix,
        "hframes": hframes,
        "vframes": vframes,
        "frame_count": frame_count,
        "cell_w": cell_size,
        "cell_h": cell_size,
        "atlas_w": atlas_w,
        "atlas_h": atlas_h,
        "root_anchor_px": [float(cell_anchor_x), float(cell_anchor_y)],
        "root_uv_y": round(float(cell_anchor_y) / float(cell_size), 4),
        "crown_h": float(effective_crown_h),
        "root_width": float(root_width),
        "default_lab_height_px": float(lab_height),
        "key_mode": key_mode,
        "reverse_spin": bool(reverse_spin),
        "sampling_mode": sampling,
        "median_raw_diff": round(median_raw_diff, 3),
        "max_raw_diff": round(max_raw_diff, 3),
        "cut_spike_ratio": cut_spike_ratio,
        "source_frames_total": int(n_frames),
        "sampled_indices": indices,
        "webp_bytes": int(atlas_path.stat().st_size),
        **extra_meta,
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
    parser.add_argument("--sampling", choices=["arclength", "linear"], default="arclength")
    parser.add_argument("--stitch-second-half", type=Path, default=None)
    parser.add_argument("--second-half-start", type=int, default=48)
    parser.add_argument("--second-half-end", type=int, default=-1)
    parser.add_argument("--reverse-first-half", action="store_true")
    parser.add_argument("--reverse-second-half", action="store_true")
    parser.add_argument("--stitch-segments", nargs="+", type=Path, default=None, help="Multiple sequential MP4 segments (e.g. 4 quarters)")
    parser.add_argument("--path-prefix", default=None)
    parser.add_argument("--key-mode", choices=["pockets", "strict_pockets", "border_only"], default="pockets")
    parser.add_argument("--plant-id", default="plant_0000")
    parser.add_argument("--display-name", default="孔雀竹芋")
    parser.add_argument("--label-ascii", default="Calathea")
    parser.add_argument("--plant-type", default="groundcover")
    parser.add_argument("--root-width", type=float, default=22.0)
    parser.add_argument("--lab-height", type=float, default=150.0)
    args = parser.parse_args()

    build_video_atlas(
        input_mp4=args.input,
        out_dir=args.out_dir,
        reverse_spin=args.reverse,
        frame_count=args.frame_count,
        cell_size=args.cell_size,
        target_crown_h=args.crown_h,
        sampling=args.sampling,
        stitch_second_half=args.stitch_second_half,
        second_half_start=args.second_half_start,
        second_half_end=args.second_half_end,
        reverse_first_half=args.reverse_first_half,
        reverse_second_half=args.reverse_second_half,
        stitch_segments=args.stitch_segments,
        path_prefix=args.path_prefix,
        key_mode=args.key_mode,
        plant_id=args.plant_id,
        display_name=args.display_name,
        label_ascii=args.label_ascii,
        plant_type=args.plant_type,
        root_width=args.root_width,
        lab_height=args.lab_height,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
