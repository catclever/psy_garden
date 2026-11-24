#!/usr/bin/env python3
"""
Reusable end-to-end pipeline to turn any grounded botanical plant asset into
a cut-free 36-frame (6x6) RGBA WebP 360-degree turntable sprite atlas via:
  Stage 1 (prep)  : Extract high-res 0-deg foreground (black or white bg),
                    generate/clean matching 180-deg rear view (Nano Banana 2.1),
                    lock both stems identically onto 1280x720 #FFFFFF studio frames.
  Stage 2 (video) : Sequentially generate two zero-intermediate-ref 3s 720p halves
                    (0->180 deg & 180->0 deg CW) using gemini-omni-1.2-flash (v1beta),
                    computing hard-cut spike ratios & anti-pendulum metrics.
  Stage 3 (atlas) : Sample 18+18 frames via arc-length motion uniformity, key
                    pure-white background (border/pockets/strict_pockets), smooth root
                    anchors across 36 frames, pack 1320x1320 RGBA WebP, and update
                    src/assets/turntable_lab/video36_index.json.
"""

import argparse
import base64
import json
import math
import shutil
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

from build_video_turntable_atlas import (
    _split_rgba_hue_layers,
    build_video_atlas,
    detect_turntable_frame_root,
    key_white_background_rgba,
)
from generate_video_turntable import (
    _crop_fg,
    _ensure_api_key_silently,
    _find_output_video,
    _load_omni_video_module,
    _place_on_16x9,
    _upload_and_wait,
)
from root_anchor_calibrator import calibrate_root_anchor

MANIFEST_PATH = REPO_ROOT / "src" / "assets" / "plants" / "manifest.json"
CANDIDATES_PROCESSED_DIR = REPO_ROOT / "tools" / "candidates" / "processed"
RUNS_ROOT = REPO_ROOT / "tools" / "candidates" / "video36_runs"
LAB_ROOT = REPO_ROOT / "src" / "assets" / "turntable_lab"

# Clean ASCII labels for Godot HUD & verification captions
KNOWN_LABELS = {
    "plant_0000": "Calathea",
    "plant_0003": "Poppy",
    "plant_0005": "Podocarpus",
    "plant_0013": "Crabapple",
    "plant_0019": "Desert Rose",
    "plant_0177": "Datura",
}

DEFAULT_HEIGHT_BY_TYPE = {
    "tree": 195.0,
    "shrub": 155.0,
    "flower": 120.0,
    "groundcover": 140.0,
}


def _load_manifest_record(plant_id: str) -> dict:
    if not MANIFEST_PATH.exists():
        return {"name": plant_id, "display_name": plant_id}
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for rec in data.get("plants", []):
        if rec.get("name") == plant_id:
            return rec
    return {"name": plant_id, "display_name": plant_id}


def _extract_ascii_genus(rec: dict, plant_id: str) -> str:
    if plant_id in KNOWN_LABELS:
        return KNOWN_LABELS[plant_id]
    ai = rec.get("ai_analysis") or {}
    sci = str(ai.get("scientific_name") or rec.get("latin_name") or "").strip()
    if sci:
        genus = "".join(ch for ch in sci.split()[0] if ch.isascii() and ch.isalnum())
        if genus:
            return genus
    return plant_id


def _pick_gallery_height(rec: dict) -> float:
    ai = rec.get("ai_analysis") or {}
    ptype = str(ai.get("plant_type") or "shrub")
    h_arr = ai.get("suggested_game_height_px")
    if isinstance(h_arr, list) and len(h_arr) >= 2:
        mid = (float(h_arr[0]) + float(h_arr[1])) * 0.5
        if ptype == "tree":
            return round(max(165.0, min(205.0, mid)), 1)
        if ptype == "flower":
            return round(max(115.0, min(145.0, mid * 1.5)), 1)
        if ptype == "groundcover":
            return 140.0
        return round(max(135.0, min(170.0, mid)), 1)
    return DEFAULT_HEIGHT_BY_TYPE.get(ptype, 150.0)


def _resolve_front_source(plant_id: str, rec: dict, override: Path | None) -> Path | None:
    if override is not None:
        return override
    if CANDIDATES_PROCESSED_DIR.exists():
        grounded = sorted(CANDIDATES_PROCESSED_DIR.glob(f"*{plant_id}*grounded*"))
        if grounded:
            return grounded[0]
        matches = sorted(p for p in CANDIDATES_PROCESSED_DIR.glob(f"*{plant_id}*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
        if matches:
            return matches[0]
    return None


def _composite_manifest_rgba(rec: dict) -> Image.Image | None:
    prefix = rec.get("path_prefix")
    layers = rec.get("layer_files") or []
    if not prefix or not layers:
        return None
    base_dir = REPO_ROOT / "src" / prefix
    comp: Image.Image | None = None
    for lf in layers:
        fpath = base_dir / lf.get("file", "")
        if not fpath.exists():
            continue
        im = Image.open(fpath).convert("RGBA")
        if comp is None:
            comp = Image.new("RGBA", im.size, (0, 0, 0, 0))
        comp = Image.alpha_composite(comp, im)
    return comp


def _key_black_background_rgba(rgb_arr: np.ndarray) -> np.ndarray:
    """Flood-fill exterior black background removal preserving dark bark and interior shadows."""
    h, w, _ = rgb_arr.shape
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    sq = rgb_arr[y0 : y0 + side, x0 : x0 + side, :3].astype(np.uint8)

    r_max = sq.max(axis=2)
    pure_black = r_max < 20
    labeled_bg, num_bg = ndimage.label(pure_black, structure=np.ones((3, 3), dtype=np.int32))
    border_labels = set()
    if num_bg > 0:
        border_labels.update(np.unique(labeled_bg[0, :]).tolist())
        border_labels.update(np.unique(labeled_bg[-1, :]).tolist())
        border_labels.update(np.unique(labeled_bg[:, 0]).tolist())
        border_labels.update(np.unique(labeled_bg[:, -1]).tolist())
    border_labels.discard(0)

    ext_bg = np.isin(labeled_bg, list(border_labels)) if border_labels else np.zeros((side, side), dtype=bool)

    enclosed_dark = (r_max <= 10) & (~ext_bg)
    labeled_enc, num_enc = ndimage.label(enclosed_dark, structure=np.ones((3, 3), dtype=np.int32))
    if num_enc > 0:
        enc_counts = np.bincount(labeled_enc.ravel())
        valid_enc = np.where(enc_counts[1:] >= 4)[0] + 1
        if len(valid_enc) > 0:
            enc_seeds = np.isin(labeled_enc, valid_enc)
            enclosed_bg = ndimage.binary_propagation(
                enc_seeds,
                mask=((r_max < 22) & (~ext_bg)),
                structure=np.ones((3, 3), dtype=bool),
            )
            ext_bg = ext_bg | enclosed_bg

    soft_black = r_max < 36
    ext_bg = ext_bg | (ndimage.binary_dilation(ext_bg, iterations=2) & soft_black)

    fg_binary = ~ext_bg
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
            if math.hypot(dx, dy) <= 40.0 and counts[cid] >= 8:
                keep_mask[labeled_fg == cid] = True
        fg_binary = keep_mask
        ext_bg = ~fg_binary

    edge_band = ndimage.binary_dilation(ext_bg, iterations=3) & fg_binary
    alpha = np.where(fg_binary, 255.0, 0.0)
    soft_alpha = np.clip((r_max.astype(np.float32) - 10.0) / 30.0, 0.0, 1.0) * 255.0
    alpha[edge_band] = np.minimum(alpha[edge_band], soft_alpha[edge_band])
    alpha[alpha < 8.0] = 0.0

    out_rgb = sq.astype(np.float32)
    semi = (alpha > 0.0) & (alpha < 254.5)
    a_norm = np.maximum(alpha[semi] / 255.0, 0.05)[:, None]
    out_rgb[semi] = np.clip(out_rgb[semi] / a_norm, 0.0, 255.0)
    out_rgb[alpha <= 0.0] = 0.0

    rgba = np.zeros((side, side, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.round(out_rgb).astype(np.uint8)
    rgba[:, :, 3] = np.round(alpha).astype(np.uint8)
    return rgba


def _extract_clean_rgba(img: Image.Image, white_key_mode: str = "strict_pockets") -> tuple[Image.Image, str]:
    rgba_in = img.convert("RGBA")
    arr = np.array(rgba_in, dtype=np.uint8)
    h, w, _ = arr.shape
    corners = [arr[0, 0], arr[0, w - 1], arr[h - 1, 0], arr[h - 1, w - 1]]
    if any(int(c[3]) < 128 for c in corners):
        return rgba_in, "alpha"
    white_corners = sum(1 for c in corners if int(min(c[:3])) > 200)
    if white_corners >= 2:
        out = key_white_background_rgba(arr[:, :, :3], key_mode=white_key_mode)
        return Image.fromarray(out, mode="RGBA"), "white"
    out = _key_black_background_rgba(arr[:, :, :3])
    return Image.fromarray(out, mode="RGBA"), "black"


def _calibrate_and_align_16x9(
    rgba_img: Image.Image,
    target_crown_h: float = 540.0,
    override_anchor_uv: list[float] | None = None,
    plant_type: str = "",
) -> tuple[Image.Image, Image.Image, list[float]]:
    arr = np.array(rgba_img.convert("RGBA"), dtype=np.uint8)
    alpha = arr[:, :, 3]
    ys, xs = np.where(alpha > 12)
    if len(xs) == 0:
        raise RuntimeError("Foreground mask empty after extraction")
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    fg_rgba = Image.fromarray(arr[y0:y1, x0:x1], mode="RGBA")
    if override_anchor_uv is not None:
        anchor_uv = [float(override_anchor_uv[0]), float(override_anchor_uv[1])]
    else:
        det = detect_turntable_frame_root(np.array(fg_rgba, dtype=np.uint8), plant_type=plant_type)
        anchor_uv = [
            round(float(det["rx"]) / max(fg_rgba.width, 1), 4),
            round(float(det["ry"]) / max(fg_rgba.height, 1), 4),
        ]

    # Composite foreground onto pure white before 16:9 placement
    fg_white = Image.new("RGB", fg_rgba.size, (255, 255, 255))
    fg_white.paste(fg_rgba, (0, 0), fg_rgba)
    frame_16x9 = _place_on_16x9(fg_white, anchor_uv, crown_h_target=target_crown_h)

    # Also emit a balanced 1024x1024 square pure-white reference for image-to-image 180-deg generation
    sq = Image.new("RGB", (1024, 1024), (255, 255, 255))
    cw, ch = fg_white.size
    box = 860
    s = min(box / max(cw, 1), box / max(ch, 1))
    nw, nh = max(1, int(round(cw * s))), max(1, int(round(ch * s)))
    sq_fg = fg_white.resize((nw, nh), Image.Resampling.LANCZOS)
    px = int(round(512.0 - anchor_uv[0] * nw))
    py = int(round(920.0 - anchor_uv[1] * nh))
    px = max(32, min(1024 - 32 - nw, px))
    py = max(32, min(1024 - 32 - nh, py))
    sq.paste(sq_fg, (px, py))
    return frame_16x9, sq, anchor_uv


def _detect_auto_key_mode(rgba_img: Image.Image) -> tuple[str, float]:
    arr = np.array(rgba_img.convert("RGBA"), dtype=np.uint8)
    opaque = arr[:, :, 3] >= 200
    n_op = int(opaque.sum())
    if n_op == 0:
        return "pockets", 0.0
    rgb = arr[:, :, :3]
    rmin = rgb.min(axis=2)
    chroma = rgb.max(axis=2).astype(np.int16) - rmin.astype(np.int16)
    bright_white_fg = opaque & (rmin >= 238) & (chroma <= 18)
    ratio = float(bright_white_fg.sum()) / float(n_op)
    mode = "strict_pockets" if ratio >= 0.015 else "pockets"
    return mode, round(ratio, 4)


def _extract_output_image_bytes(interaction, client) -> bytes:
    out_img = getattr(interaction, "output_image", None)
    if out_img is None:
        for step in reversed(getattr(interaction, "steps", None) or []):
            if getattr(step, "type", None) != "model_output":
                continue
            for item in reversed(getattr(step, "content", None) or []):
                if getattr(item, "type", None) == "image":
                    out_img = item
                    break
            if out_img is not None:
                break
    if out_img is None:
        raise RuntimeError(f"No output_image in interaction {getattr(interaction, 'id', None)}")
    data = getattr(out_img, "data", None)
    if data:
        return data if isinstance(data, (bytes, bytearray)) else base64.b64decode(data)
    uri = getattr(out_img, "uri", None)
    if uri:
        return client.files.download(file=uri)
    raise RuntimeError("output_image contained neither data nor uri")


def _compute_mirror_iou(img_a_path: Path, img_b_path: Path) -> float:
    a = np.array(Image.open(img_a_path).convert("RGB"), dtype=np.uint8)
    b = np.array(Image.open(img_b_path).convert("RGB"), dtype=np.uint8)
    ma = (255 - a.min(axis=2)) > 45
    mb_flip = ((255 - b.min(axis=2)) > 45)[:, ::-1]
    union = float((ma | mb_flip).sum())
    if union <= 0.0:
        return 0.0
    return round(float((ma & mb_flip).sum()) / union, 4)


def _synthesize_rear_image(front_sq_path: Path, out_raw_path: Path, species_desc: str, model: str, timeout: int) -> None:
    _ensure_api_key_silently()
    from google import genai
    from google.genai import types

    flipped_path = front_sq_path.with_name(front_sq_path.stem + "_flipped.png")
    Image.open(front_sq_path).transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(flipped_path)
    b64_img = base64.b64encode(flipped_path.read_bytes()).decode("utf-8")
    prompt = (
        f"This image is the horizontally-mirrored silhouette of a {species_desc} botanical illustration. "
        "STRICT PIXEL-SILHOUETTE LOCK: Paint 100% strictly inside the exact outer pixel contour of this "
        "horizontally-mirrored plant image. Do NOT expand, shrink, shift, re-center, add, or remove any outer "
        "branch, leaf tip, or flower silhouette—keep the exact same crown width, height, and bottom-center root "
        "position while ONLY repainting the interior surfaces into the true 180-degree REAR view seen from directly "
        "behind the plant (reversing front-to-back depth occlusion so rear branches/stems pass in front, and turning "
        "forward-facing blossoms/leaves so we see green calyxes, sepals, leaf undersides, and rear petal faces). "
        "Keep exact hand-drawn botanical watercolor brushwork and fine dark ink outlines isolated on pure solid "
        "#FFFFFF white background, with zero floor shadow, zero soil line, zero pot, zero grid, and zero text."
    )
    img_timeout = min(int(timeout), 120)
    client = genai.Client(
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=img_timeout * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
    )
    print(f"Synthesizing 180-deg rear view from mirrored input with '{model}'...")
    interaction = client.interactions.create(
        api_version="v1beta",
        model=model,
        input=[
            {"type": "text", "text": prompt},
            {"type": "image", "data": b64_img, "mime_type": "image/png"},
        ],
        timeout=float(img_timeout),
    )
    img_bytes = _extract_output_image_bytes(interaction, client)
    out_raw_path.write_bytes(img_bytes)
    print(f"Saved raw synthesized 180-deg rear image -> {out_raw_path} ({len(img_bytes)} bytes)")


def _synthesize_side_image(
    src_sq_path: Path,
    out_raw_path: Path,
    species_desc: str,
    angle_deg: int,
    model: str,
    timeout: int,
) -> None:
    _ensure_api_key_silently()
    from google import genai
    from google.genai import types

    img_timeout = min(int(timeout), 120)
    client = genai.Client(
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=img_timeout * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
    )

    if angle_deg == 270:
        flipped_path = src_sq_path.with_name("ref_090_sq_flipped.png")
        Image.open(src_sq_path).transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(flipped_path)
        b64_img = base64.b64encode(flipped_path.read_bytes()).decode("utf-8")
        prompt = (
            f"This image is the horizontally-mirrored silhouette of the 90-degree right-side view of this exact {species_desc} plant. "
            "Repaint it into the true 270-degree LEFT-SIDE view seen from the exact opposite side of the plant, keeping this "
            "exact mirrored silhouette, bottom-center trunk/stem base, branch layout, crown spread, and height. "
            "Reverse front-to-back depth occlusion so branches, leaves, and blossoms that were farther away now pass in front, "
            "and show accurate botanical details (bark, leaf veins/undersides, blossoms, buds) consistent with the plant. "
            "Keep exact hand-drawn botanical watercolor brushwork and fine dark ink outlines isolated on pure solid #FFFFFF white "
            "background, with zero floor shadow, zero soil line, zero pot, zero grid, and zero text."
        )
        print(f"Synthesizing 270-deg left-side view from mirrored 90-deg input with '{model}'...")
    else:
        b64_img = base64.b64encode(src_sq_path.read_bytes()).decode("utf-8")
        prompt = (
            f"This is the 0-degree front view of a full-volume three-dimensional {species_desc} plant illustration. "
            "Draw the exact same individual plant rotated 90 degrees clockwise when viewed from above, seen straight-on from its "
            "90-degree RIGHT-SIDE profile. Keep the exact same species, trunk/stem thickness, exact total height, bottom-center root/stem "
            "anchor, foliage density, flower count, and hand-drawn botanical watercolor brushwork with fine dark ink outlines. "
            "Show a full, lush, natural 3D botanical side profile (~65% to 90% as wide as the front crown, with branches and leaves "
            "projecting toward both viewer-left and viewer-right in realistic 3D depth—NOT a thin flattened 2D card). Isolate cleanly on a "
            "pure solid #FFFFFF white background, with zero floor shadow, zero soil line, zero pot, zero grid, and zero text."
        )
        print(f"Synthesizing 90-deg right-side view from front input with '{model}'...")

    interaction = client.interactions.create(
        api_version="v1beta",
        model=model,
        input=[
            {"type": "text", "text": prompt},
            {"type": "image", "data": b64_img, "mime_type": "image/png"},
        ],
        timeout=float(img_timeout),
    )
    img_bytes = _extract_output_image_bytes(interaction, client)
    out_raw_path.write_bytes(img_bytes)
    print(f"Saved raw synthesized {angle_deg}-deg side image -> {out_raw_path} ({len(img_bytes)} bytes)")


def _make_prep_comparison_jpg(ref_000_path: Path, ref_180_path: Path, out_jpg: Path, title_left: str, title_right: str) -> None:
    im0 = Image.open(ref_000_path).convert("RGB").resize((640, 360), Image.Resampling.LANCZOS)
    im1 = Image.open(ref_180_path).convert("RGB").resize((640, 360), Image.Resampling.LANCZOS)
    panel = Image.new("RGB", (1280, 400), (111, 102, 75))
    panel.paste(im0, (0, 36))
    panel.paste(im1, (640, 36))
    d = ImageDraw.Draw(panel)
    # Show root anchor alignment horizontal guide + red root dots at scaled (320, 353.5) and (960, 353.5)
    ay = int(round(36 + 635 * 0.5))
    d.line([0, ay, 1280, ay], fill=(220, 80, 80), width=1)
    d.ellipse([320 - 4, ay - 4, 320 + 4, ay + 4], fill=(235, 45, 45))
    d.ellipse([960 - 4, ay - 4, 960 + 4, ay + 4], fill=(235, 45, 45))
    d.text((16, 10), title_left, fill=(245, 245, 235))
    d.text((656, 10), title_right, fill=(245, 245, 235))
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    panel.save(out_jpg, quality=92)


def _make_4views_comparison_jpg(view_paths: list[Path], titles: list[str], out_jpg: Path) -> None:
    cell_w, cell_h = 480, 270
    panel = Image.new("RGB", (cell_w * len(view_paths), cell_h + 34), (111, 102, 75))
    d = ImageDraw.Draw(panel)
    scale = cell_w / 1280.0
    ay = int(round(30 + 635.0 * scale))
    for i, (vp, title) in enumerate(zip(view_paths, titles)):
        x0 = i * cell_w
        im = Image.open(vp).convert("RGB").resize((cell_w, cell_h), Image.Resampling.LANCZOS)
        panel.paste(im, (x0, 30))
        d.text((x0 + 12, 8), title, fill=(245, 245, 235))
        d.ellipse([x0 + cell_w // 2 - 4, ay - 4, x0 + cell_w // 2 + 4, ay + 4], fill=(235, 45, 45))
    d.line([0, ay, panel.width, ay], fill=(220, 80, 80), width=1)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    panel.save(out_jpg, quality=92)


def run_prep_stage(args: argparse.Namespace, rec: dict, run_dir: Path) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    ai = rec.get("ai_analysis") or {}
    sci = str(ai.get("scientific_name") or "").strip()
    zh = str(ai.get("zh_name") or rec.get("display_name") or args.plant_id).strip()
    ptype = str(ai.get("plant_type") or "shrub").strip()
    label_ascii = _extract_ascii_genus(rec, args.plant_id)
    species_desc = f"{sci} ({label_ascii}, {ptype})" if sci else f"{label_ascii} ({ptype})"

    front_src = _resolve_front_source(args.plant_id, rec, args.front_image)
    if front_src is not None and front_src.exists():
        raw_front_img = Image.open(front_src)
        front_rgba, detected_bg = _extract_clean_rgba(raw_front_img, white_key_mode="border_only")
        src_desc = str(front_src)
    else:
        front_rgba = _composite_manifest_rgba(rec)
        if front_rgba is None:
            raise RuntimeError(f"Could not locate high-res or manifest layers for {args.plant_id}")
        detected_bg = "manifest_rgba"
        src_desc = f"manifest:{rec.get('path_prefix')}"

    auto_km, bright_ratio = _detect_auto_key_mode(front_rgba)
    resolved_key_mode = auto_km if args.key_mode == "auto" else args.key_mode

    ref_000_path = run_dir / "ref_000.png"
    ref_000_sq_path = run_dir / "ref_000_sq.png"
    frame0, sq0, anchor0 = _calibrate_and_align_16x9(front_rgba, target_crown_h=540.0, plant_type=ptype)
    frame0.save(ref_000_path)
    sq0.save(ref_000_sq_path)

    ref_180_raw = run_dir / "ref_180_raw.png"
    if args.back_image is not None:
        if not args.back_image.exists():
            raise FileNotFoundError(f"Back image not found: {args.back_image}")
        Image.open(args.back_image).save(ref_180_raw)
        back_source = str(args.back_image)
    elif (not ref_180_raw.exists()) or args.force:
        _synthesize_rear_image(ref_000_sq_path, ref_180_raw, species_desc, args.image_model, args.timeout)
        back_source = f"generated:{args.image_model}"
    else:
        back_source = f"cached:{ref_180_raw}"

    raw_back_img = Image.open(ref_180_raw)
    back_rgba, back_bg = _extract_clean_rgba(raw_back_img, white_key_mode="border_only")
    ref_180_path = run_dir / "ref_180.png"
    mirrored_back_anchor = [round(1.0 - anchor0[0], 4), round(anchor0[1], 4)]
    frame180, _, anchor180 = _calibrate_and_align_16x9(
        back_rgba, target_crown_h=540.0, override_anchor_uv=mirrored_back_anchor
    )
    frame180.save(ref_180_path)

    mirror_iou = _compute_mirror_iou(ref_000_path, ref_180_path)
    if mirror_iou < 0.78:
        print(f"[prep WARNING] {args.plant_id} mirror_silhouette_iou={mirror_iou:.4f} < 0.78 (risk of snap/ghost in 2half)")

    check_jpg = run_dir / "ref_000_vs_180_check.jpg"
    _make_prep_comparison_jpg(
        ref_000_path,
        ref_180_path,
        check_jpg,
        f"{args.plant_id} {label_ascii} 0-deg ({detected_bg})",
        f"{args.plant_id} {label_ascii} 180-deg ({back_bg}) IoU={mirror_iou:.3f}",
    )

    prep_meta = {
        "plant_id": args.plant_id,
        "mode": args.mode,
        "display_name": zh,
        "label_ascii": label_ascii,
        "scientific_name": sci,
        "plant_type": ptype,
        "front_source": src_desc,
        "front_bg": detected_bg,
        "back_source": back_source,
        "back_bg": back_bg,
        "front_anchor_uv": anchor0,
        "back_anchor_uv": anchor180,
        "mirror_silhouette_iou": mirror_iou,
        "bright_white_fg_ratio": bright_ratio,
        "resolved_key_mode": resolved_key_mode,
        "root_width": float(rec.get("root_width") or 22.0),
        "gallery_height_px": _pick_gallery_height(rec),
        "ref_000": str(ref_000_path),
        "ref_180": str(ref_180_path),
        "check_jpg": str(check_jpg),
    }

    if args.mode == "4quarter":
        ref_090_raw = run_dir / "ref_090_raw.png"
        if args.right_image is not None:
            if not args.right_image.exists():
                raise FileNotFoundError(f"Right-side image not found: {args.right_image}")
            Image.open(args.right_image).save(ref_090_raw)
            right_source = str(args.right_image)
        elif (not ref_090_raw.exists()) or args.force:
            _synthesize_side_image(ref_000_sq_path, ref_090_raw, species_desc, 90, args.image_model, args.timeout)
            right_source = f"generated:{args.image_model}"
        else:
            right_source = f"cached:{ref_090_raw}"

        right_rgba, right_bg = _extract_clean_rgba(Image.open(ref_090_raw), white_key_mode="border_only")
        ref_090_path = run_dir / "ref_090.png"
        ref_090_sq_path = run_dir / "ref_090_sq.png"
        frame90, sq90, anchor90 = _calibrate_and_align_16x9(right_rgba, target_crown_h=540.0, plant_type=ptype)
        frame90.save(ref_090_path)
        sq90.save(ref_090_sq_path)

        ref_270_raw = run_dir / "ref_270_raw.png"
        if args.left_image is not None:
            if not args.left_image.exists():
                raise FileNotFoundError(f"Left-side image not found: {args.left_image}")
            Image.open(args.left_image).save(ref_270_raw)
            left_source = str(args.left_image)
        elif (not ref_270_raw.exists()) or args.force:
            _synthesize_side_image(ref_090_sq_path, ref_270_raw, species_desc, 270, args.image_model, args.timeout)
            left_source = f"generated:{args.image_model}"
        else:
            left_source = f"cached:{ref_270_raw}"

        left_rgba, left_bg = _extract_clean_rgba(Image.open(ref_270_raw), white_key_mode="border_only")
        ref_270_path = run_dir / "ref_270.png"
        mirrored_side_anchor = [round(1.0 - anchor90[0], 4), round(anchor90[1], 4)]
        frame270, _, anchor270 = _calibrate_and_align_16x9(
            left_rgba, target_crown_h=540.0, override_anchor_uv=mirrored_side_anchor
        )
        frame270.save(ref_270_path)

        check_4views_jpg = run_dir / "ref_4views_check.jpg"
        _make_4views_comparison_jpg(
            [ref_000_path, ref_090_path, ref_180_path, ref_270_path],
            [
                f"{args.plant_id} {label_ascii} 0d ({detected_bg})",
                f"90d Right ({right_bg})",
                f"180d Rear ({back_bg})",
                f"270d Left ({left_bg})",
            ],
            check_4views_jpg,
        )
        prep_meta.update(
            {
                "right_source": right_source,
                "right_bg": right_bg,
                "left_source": left_source,
                "left_bg": left_bg,
                "right_anchor_uv": anchor90,
                "left_anchor_uv": anchor270,
                "ref_090": str(ref_090_path),
                "ref_270": str(ref_270_path),
                "check_4views_jpg": str(check_4views_jpg),
            }
        )

    (run_dir / "prep_meta.json").write_text(json.dumps(prep_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(prep_meta, ensure_ascii=False, indent=2))
    return prep_meta


def _compute_frame_stats(frames: np.ndarray) -> dict:
    n = len(frames)
    if n < 8:
        raise RuntimeError(f"Decoded too few frames ({n})")
    frames_f = frames.astype(np.float32)
    diffs = [float(np.abs(frames_f[i + 1] - frames_f[i]).mean()) for i in range(n - 1)]
    med = float(np.median(diffs))
    mx = float(max(diffs))
    ratio = round(mx / max(med, 1e-6), 2)
    top5 = sorted([(round(d, 2), idx) for idx, d in enumerate(diffs)], reverse=True)[:5]

    widths = []
    for f in frames:
        mask = (255 - f.min(axis=2)) > 45
        xs = np.where(mask.sum(axis=0) >= 3)[0]
        widths.append(int(xs.max() - xs.min() + 1) if len(xs) else 0)
    min_w = min(widths)
    min_w_frame = widths.index(min_w)
    ref_w = max(widths[0], widths[-1], 1)

    return {
        "total_frames": int(n),
        "median_diff": round(med, 3),
        "max_diff": round(mx, 3),
        "cut_spike_ratio": ratio,
        "top_5_jumps": top5,
        "front_width_px": int(widths[0]),
        "end_width_px": int(widths[-1]),
        "min_width_px": int(min_w),
        "min_width_frame": int(min_w_frame),
        "min_width_ratio": round(min_w / ref_w, 3),
    }


def _analyze_mp4(mp4_path: Path) -> tuple[np.ndarray, dict]:
    import imageio.v3 as iio

    frames = iio.imread(str(mp4_path), plugin="FFMPEG")
    if len(frames) < 8:
        raise RuntimeError(f"Decoded too few frames ({len(frames)}) from {mp4_path}")
    return frames, _compute_frame_stats(frames)


def _maybe_trim_tail_snap(mp4_path: Path, trimmed_mp4_path: Path, chained_png_path: Path) -> tuple[Path, Path, dict]:
    import imageio.v3 as iio

    frames, stats = _analyze_mp4(mp4_path)
    n = len(frames)
    ratio = float(stats["cut_spike_ratio"])
    max_idx = int(stats["top_5_jumps"][0][1])

    if ratio >= 2.2 and max_idx >= int(n * 0.65) and max_idx >= 36:
        trimmed = frames[: max_idx + 1]
        cand_stats = _compute_frame_stats(trimmed)
        if float(cand_stats["cut_spike_ratio"]) < ratio * 0.85:
            iio.imwrite(str(trimmed_mp4_path), trimmed, plugin="FFMPEG", fps=24)
            _, trimmed_stats = _analyze_mp4(trimmed_mp4_path)
            trimmed_stats["auto_trimmed_at_frame"] = max_idx
            trimmed_stats["orig_cut_spike_ratio"] = ratio
            Image.fromarray(trimmed[-1]).save(chained_png_path)
            print(
                f"[auto-heal] Trimmed tail snap on {mp4_path.name} at frame {max_idx}: "
                f"cut_spike_ratio {ratio} -> {trimmed_stats['cut_spike_ratio']}"
            )
            return trimmed_mp4_path, chained_png_path, trimmed_stats

    if trimmed_mp4_path.exists():
        trimmed_mp4_path.unlink()
    Image.fromarray(frames[-1]).save(chained_png_path)
    return mp4_path, chained_png_path, stats


def _extract_output_video(interaction):
    output_video = _find_output_video(interaction)
    if output_video is not None:
        return output_video
    for item in reversed(getattr(interaction, "outputs", None) or []):
        if getattr(item, "type", None) == "video":
            return item
    return None


def _get_interaction_with_retry(client, interaction_id: str, omni_mod, max_attempts: int = 4):
    from google.genai import errors

    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return client.interactions.get(interaction_id, api_version="v1beta", timeout=30.0)
        except errors.APIError:
            raise
        except Exception as exc:
            last_err = exc
            print(
                f"Transient GET error ({attempt}/{max_attempts}) for {interaction_id}: "
                f"{omni_mod.sanitize_error(exc)}"
            )
            if attempt < max_attempts:
                time.sleep(3.0)
    raise RuntimeError(
        f"Polling interaction {interaction_id} failed after {max_attempts} network retries: "
        f"{omni_mod.sanitize_error(last_err)}"
    )


def _generate_single_half_video(
    client,
    omni_mod,
    first_uri: str,
    first_mime: str,
    last_uri: str,
    last_mime: str,
    prompt_text: str,
    out_mp4: Path,
    model: str,
    duration: str,
    resolution: str,
    timeout: int,
    force: bool = False,
) -> tuple[str | None, float]:
    from google import genai
    from google.genai import errors

    input_parts = [
        {"type": "image", "uri": first_uri, "mime_type": first_mime},
        {"type": "image", "uri": last_uri, "mime_type": last_mime},
        {"type": "text", "text": prompt_text},
    ]
    video_config = {
        "type": "video",
        "delivery": "uri",
        "aspect_ratio": "16:9",
        "duration": omni_mod.parse_and_validate_duration(duration),
        "resolution": omni_mod.parse_and_validate_resolution(resolution),
    }
    id_file = out_mp4.with_suffix(".interaction_id.txt")
    t0 = time.time()
    interaction = None
    interaction_id = None

    if id_file.exists() and not force:
        saved_id = id_file.read_text(encoding="utf-8").strip()
        if saved_id:
            interaction_id = saved_id
            print(f"Resuming polling for existing interaction {interaction_id}...")
            try:
                interaction = _get_interaction_with_retry(client, interaction_id, omni_mod)
            except errors.APIError as e:
                raise RuntimeError(f"API Error resuming interaction {interaction_id}: {omni_mod.sanitize_error(e)}")

    if interaction is None:
        try:
            interaction = client.interactions.create(
                api_version="v1beta",
                model=model,
                input=input_parts,
                response_format=video_config,
                background=True,
                timeout=60.0,
            )
        except errors.APIError as e:
            err_msg = str(e).lower()
            if getattr(e, "code", None) == 400 and "background" in err_msg:
                print(
                    f"background=True unsupported for '{model}' ({omni_mod.sanitize_error(e)}); "
                    "falling back once to synchronous create (attempts=1)..."
                )
                try:
                    interaction = client.interactions.create(
                        api_version="v1beta",
                        model=model,
                        input=input_parts,
                        response_format=video_config,
                        timeout=float(timeout),
                    )
                except errors.APIError as sync_err:
                    raise RuntimeError(f"API Error generating video: {omni_mod.sanitize_error(sync_err)}")
            else:
                raise RuntimeError(f"API Error generating video: {omni_mod.sanitize_error(e)}")

        interaction_id = getattr(interaction, "id", None)
        if interaction_id:
            id_file.write_text(str(interaction_id), encoding="utf-8")

    last_log_t = t0
    while True:
        status = str(getattr(interaction, "status", "") or "").lower()
        if status in ("failed", "cancelled", "incomplete"):
            if id_file.exists():
                id_file.unlink()
            raise RuntimeError(f"Interaction {interaction_id} ended with status={status}")

        output_video = _extract_output_video(interaction)
        if status == "completed":
            break
        if status == "" and output_video is not None:
            break
        if status not in ("in_progress", "queued", "running", "pending", ""):
            break

        if time.time() - t0 > float(timeout):
            raise TimeoutError(
                f"Polling interaction {interaction_id} exceeded {timeout}s; re-run to resume without re-creating"
            )
        if not interaction_id:
            break

        time.sleep(5.0)
        try:
            interaction = _get_interaction_with_retry(client, interaction_id, omni_mod)
        except errors.APIError as e:
            raise RuntimeError(f"API Error polling interaction {interaction_id}: {omni_mod.sanitize_error(e)}")

        now = time.time()
        if now - last_log_t >= 15.0:
            cur_status = str(getattr(interaction, "status", "") or "")
            print(f"Polling {interaction_id}... status={cur_status} ({now - t0:.1f}s elapsed)")
            last_log_t = now

    output_video = _extract_output_video(interaction)
    if not output_video:
        raise RuntimeError(
            f"No video output in interaction {interaction_id} (status={getattr(interaction, 'status', None)})"
        )

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if getattr(output_video, "uri", None):
        try:
            omni_mod.download_video_file(output_video, str(out_mp4), client=client)
        except Exception as dl_err:
            print(f"Retrying download with default client: {omni_mod.sanitize_error(dl_err)}")
            omni_mod.download_video_file(output_video, str(out_mp4), client=genai.Client())
    elif getattr(output_video, "data", None):
        raw = output_video.data
        raw_bytes = raw if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
        out_mp4.write_bytes(raw_bytes)
    else:
        raise RuntimeError(f"Video output lacked uri and data in {interaction_id}")

    if not out_mp4.exists() or out_mp4.stat().st_size == 0:
        raise RuntimeError(f"Downloaded video file missing or empty: {out_mp4}")

    if id_file.exists():
        id_file.unlink()

    return interaction_id, round(time.time() - t0, 2)


def _save_preview_grid(labeled_frames: list[tuple[str, np.ndarray]], cols: int, rows: int, out_jpg: Path) -> None:
    cell_w, cell_h = 240, 200
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)

    for idx_pos, (label, arr) in enumerate(labeled_frames[: cols * rows]):
        r, c = divmod(idx_pos, cols)
        x0, y0 = c * cell_w, r * cell_h
        fh, fw, _ = arr.shape
        cx = fw // 2
        half_crop = min(fh // 2, cx, fw - cx)
        sq = Image.fromarray(arr[:, cx - half_crop : cx + half_crop, :]).resize(
            (cell_w - 8, cell_h - 24), Image.Resampling.LANCZOS
        )
        sheet.paste(sq, (x0 + 4, y0 + 2))
        draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h - 1], outline=(210, 210, 210), width=1)
        draw.text((x0 + 8, y0 + cell_h - 18), label, fill=(20, 20, 20))

    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_jpg, quality=90)


def _save_combined_preview(frames_a: np.ndarray, frames_b: np.ndarray, out_jpg: Path) -> None:
    idx_a = np.linspace(0, max(0, len(frames_a) - 2), 18, dtype=int)
    idx_b = np.linspace(0, max(0, len(frames_b) - 2), 18, dtype=int)
    combined = [("A", int(k), frames_a[int(k)]) for k in idx_a] + [("B", int(k), frames_b[int(k)]) for k in idx_b]
    labeled = [(f"F{idx_pos:02d} ({tag}#{f_idx})", arr) for idx_pos, (tag, f_idx, arr) in enumerate(combined)]
    _save_preview_grid(labeled, 6, 6, out_jpg)


def _save_single_segment_preview(frames: np.ndarray, tag: str, out_jpg: Path) -> None:
    idxs = np.linspace(0, max(0, len(frames) - 1), 9, dtype=int)
    labeled = [(f"{tag}#{int(k)}", frames[int(k)]) for k in idxs]
    _save_preview_grid(labeled, 3, 3, out_jpg)


def _save_four_quarter_preview(quarter_frames: list[tuple[str, np.ndarray]], out_jpg: Path) -> None:
    labeled: list[tuple[str, np.ndarray]] = []
    idx_pos = 0
    for qtag, frames in quarter_frames:
        idxs = np.linspace(0, max(0, len(frames) - 2), 9, dtype=int)
        for k in idxs:
            labeled.append((f"F{idx_pos:02d} ({qtag.upper()}#{int(k)})", frames[int(k)]))
            idx_pos += 1
    _save_preview_grid(labeled, 6, 6, out_jpg)


def _run_2half_video_stage(args: argparse.Namespace, prep_meta: dict, run_dir: Path, client, omni_mod, species_desc: str) -> dict:
    half_a_mp4 = run_dir / "half_a_0_180.mp4"
    half_b_mp4 = run_dir / "half_b_180_360.mp4"

    seg_sel = getattr(args, "segment", "all")
    half_sel = getattr(args, "half", "both")
    if seg_sel in ("a", "b"):
        half_sel = seg_sel
    want_a = half_sel in ("both", "a")
    want_b = half_sel in ("both", "b")
    need_a = want_a and (args.force or not half_a_mp4.exists())
    need_b = want_b and (args.force or not half_b_mp4.exists())

    uri_000 = mime_000 = uri_180 = mime_180 = None
    if need_a or need_b:
        uri_000, mime_000 = _upload_and_wait(client, prep_meta["ref_000"], "image/png", omni_mod.normalize_file_uri)
        uri_180, mime_180 = _upload_and_wait(client, prep_meta["ref_180"], "image/png", omni_mod.normalize_file_uri)

    prompt_a = (
        f"One single continuous unbroken locked-camera 180-degree 3D studio turntable half-turn animation of this full-volume "
        f"three-dimensional {species_desc} plant (rendered in crisp botanical watercolor and fine dark ink line art) on a pure "
        "solid #FFFFFF seamless white background, with ZERO cuts, ZERO jump cuts, ZERO cross-dissolves, and ZERO pauses. "
        "Treat the plant as a solid, deeply three-dimensional radial specimen whose branches, leaves, and blossoms extend outward "
        "in all 360 horizontal directions around its central stem—NOT a flat 2D paper cutout or cardboard sheet. Use the first "
        "image (0-degree front view) as the starting frame and the second image (180-degree rear view) as the final frame. "
        "Right from the very first frame, smoothly rotate the entire 3D plant clockwise when viewed from above at a steady "
        "constant angular velocity (60 degrees per second) around its fixed bottom-center trunk or stem axis until arriving "
        "cleanly at the exact 180-degree rear view on the final frame. As it rotates through its 90-degree right-side profile "
        "at mid-clip, branches and foliage sweep out in rich 3D parallax toward and away from the camera, maintaining a wide, "
        "full, lush 3D rounded crown silhouette (about 65% to 90% as wide as the front view)—NEVER flatten, thin out, or turn "
        "edge-on like a thin 2D flat plane. Rigid 3D physical rotation only: never fade, morph-dissolve, or cut. Locked static "
        "tripod camera, zero camera pan, zero zoom, zero floor shadows, zero turntable pedestal, completely rigid foliage with "
        "zero wind sway, exact botanical watercolor and fine dark ink outline style on every frame. Silent video."
    )
    prompt_b = (
        f"One single continuous unbroken locked-camera 180-degree 3D studio turntable continuation half-turn animation of this "
        f"full-volume three-dimensional {species_desc} plant (rendered in crisp botanical watercolor and fine dark ink line art) "
        "on a pure solid #FFFFFF seamless white background, with ZERO cuts, ZERO jump cuts, ZERO cross-dissolves, and ZERO "
        "pauses. Treat the plant as a solid, deeply three-dimensional radial specimen whose branches, leaves, and blossoms extend "
        "outward in all 360 horizontal directions around its central stem—NOT a flat 2D paper cutout or cardboard sheet. Use the "
        "first image (180-degree rear view) as the starting frame and the second image (0-degree front view) as the final frame. "
        "Right from the very first frame, continue rotating in the exact same clockwise direction when viewed from above at a "
        "steady constant angular velocity (60 degrees per second) around its fixed bottom-center trunk or stem axis until "
        "arriving cleanly at the exact 0-degree front view on the final frame. As it rotates through its 270-degree left-side "
        "profile at mid-clip, branches and foliage sweep out in rich 3D parallax toward and away from the camera, maintaining a "
        "wide, full, lush 3D rounded crown silhouette (about 65% to 90% as wide as the front view)—NEVER flatten, thin out, or "
        "turn edge-on like a thin 2D flat plane, and NEVER reverse direction or bounce back. Rigid 3D physical continuation "
        "only: never fade, morph-dissolve, or cut. Locked static tripod camera, zero camera pan, zero zoom, zero floor shadows, "
        "zero turntable pedestal, completely rigid foliage with zero wind sway, exact botanical watercolor and fine dark ink "
        "outline style on every frame. Silent video."
    )

    id_a, elapsed_a = (None, 0.0)
    if need_a:
        print(f"\n[{args.plant_id}] Stage 2A: Generating Half A (0 -> 180 deg, {args.duration})...")
        id_a, elapsed_a = _generate_single_half_video(
            client, omni_mod, uri_000, mime_000, uri_180, mime_180, prompt_a, half_a_mp4,
            args.video_model, args.duration, args.resolution, args.timeout, force=args.force,
        )
    frames_a, stats_a = (_analyze_mp4(half_a_mp4) if half_a_mp4.exists() else (None, None))
    if stats_a is not None:
        print(f"[{args.plant_id}] Half A stats:", json.dumps(stats_a))

    id_b, elapsed_b = (None, 0.0)
    if need_b:
        print(f"\n[{args.plant_id}] Stage 2B: Generating Half B (180 -> 0 deg, {args.duration})...")
        id_b, elapsed_b = _generate_single_half_video(
            client, omni_mod, uri_180, mime_180, uri_000, mime_000, prompt_b, half_b_mp4,
            args.video_model, args.duration, args.resolution, args.timeout, force=args.force,
        )
    frames_b, stats_b = (_analyze_mp4(half_b_mp4) if half_b_mp4.exists() else (None, None))
    if stats_b is not None:
        print(f"[{args.plant_id}] Half B stats:", json.dumps(stats_b))

    mid_direct_mad = None
    mid_mirror_mad = None
    raw_preview_jpg = None
    if frames_a is not None and frames_b is not None:
        mid_a = frames_a[len(frames_a) // 2].astype(np.float32)
        mid_b = frames_b[len(frames_b) // 2].astype(np.float32)
        mid_direct_mad = round(float(np.abs(mid_a - mid_b).mean()), 3)
        mid_mirror_mad = round(float(np.abs(mid_a - mid_b[:, ::-1, :]).mean()), 3)
        raw_preview_jpg = run_dir / "raw_preview_36.jpg"
        _save_combined_preview(frames_a, frames_b, raw_preview_jpg)

    summary = {
        "plant_id": args.plant_id,
        "mode": "2half",
        "half_a_mp4": str(half_a_mp4) if half_a_mp4.exists() else None,
        "half_b_mp4": str(half_b_mp4) if half_b_mp4.exists() else None,
        "half_a_interaction_id": id_a,
        "half_b_interaction_id": id_b,
        "elapsed_sec": {"half_a": elapsed_a, "half_b": elapsed_b},
        "half_a_stats": stats_a,
        "half_b_stats": stats_b,
        "mid_a_vs_mid_b_mad": mid_direct_mad,
        "mid_a_vs_mirrored_mid_b_mad": mid_mirror_mad,
        "raw_preview_jpg": str(raw_preview_jpg) if raw_preview_jpg is not None else None,
    }
    (run_dir / "video_run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def run_video_stage(args: argparse.Namespace, prep_meta: dict, run_dir: Path) -> dict:
    _ensure_api_key_silently()
    omni_mod = _load_omni_video_module()
    from google import genai
    from google.genai import types

    species_desc = f"{prep_meta.get('scientific_name') or prep_meta['label_ascii']} ({prep_meta['plant_type']})"
    client = genai.Client(
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=180 * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
    )

    if getattr(args, "mode", "2half") == "2half":
        return _run_2half_video_stage(args, prep_meta, run_dir, client, omni_mod, species_desc)

    if "ref_090" not in prep_meta or "ref_270" not in prep_meta:
        raise FileNotFoundError("Missing ref_090/ref_270 in prep_meta.json; run --stages prep --mode 4quarter first")

    quarter_specs = [
        (
            "q0",
            run_dir / "q0_0_90.mp4",
            "ref_000",
            "ref_090",
            "0-degree front view",
            "90-degree right-side view",
            "turning through a lush, full-volume 3D front-right three-quarter view as right-side branches swing smoothly toward the camera",
        ),
        (
            "q1",
            run_dir / "q1_90_180.mp4",
            "ref_090",
            "ref_180",
            "90-degree right-side view",
            "180-degree rear view",
            "turning through a lush, full-volume 3D rear-right three-quarter view as back branches and foliage undersides swing smoothly into view",
        ),
        (
            "q2",
            run_dir / "q2_180_270.mp4",
            "ref_180",
            "ref_270",
            "180-degree rear view",
            "270-degree left-side view",
            "turning through a lush, full-volume 3D rear-left three-quarter view as left-side branches swing smoothly toward the camera",
        ),
        (
            "q3",
            run_dir / "q3_270_360.mp4",
            "ref_270",
            "ref_000",
            "270-degree left-side view",
            "0-degree front view",
            "turning through a lush, full-volume 3D front-left three-quarter view as front branches and blossoms swing smoothly back into view",
        ),
    ]

    seg_sel = getattr(args, "segment", "all")
    if seg_sel == "all":
        want_keys = {"q0", "q1", "q2", "q3"}
    elif seg_sel in {"q0", "q1", "q2", "q3"}:
        want_keys = {seg_sel}
    else:
        raise ValueError(f"--segment {seg_sel} is only valid with --mode 2half")

    uploaded: dict[str, tuple[str, str]] = {}

    def _upload_ref(path_obj: Path) -> tuple[str, str]:
        p_str = str(path_obj)
        if p_str not in uploaded:
            uploaded[p_str] = _upload_and_wait(client, p_str, "image/png", omni_mod.normalize_file_uri)
        return uploaded[p_str]

    def _build_quarter_prompt(start_desc: str, end_desc: str, mid_motion: str) -> str:
        return (
            f"One single continuous unbroken locked-camera 90-degree 3D studio turntable quarter-turn animation of this exact "
            f"full-volume three-dimensional {species_desc} plant (rendered in crisp botanical watercolor and fine dark ink line art) "
            "on a pure solid #FFFFFF seamless white background, with ZERO cuts, ZERO jump cuts, ZERO cross-dissolves, and ZERO pauses. "
            f"Use the first image ({start_desc}) as the exact starting frame and the second image ({end_desc}) as the exact final frame. "
            "Right from the very first frame, smoothly rotate the entire 3D plant clockwise when viewed from above at a steady uniform "
            f"angular velocity (30 degrees per second) around its fixed bottom-center trunk or stem axis—{mid_motion}—until arriving "
            f"cleanly at the exact {end_desc} on the final frame. Maintain full 3D branch and foliage volume in parallax across every "
            "frame (NEVER flatten into a 2D cardboard cutout, fade, morph-dissolve, or reverse direction). Locked static tripod camera, "
            "zero camera pan, zero zoom, zero floor shadows, zero turntable pedestal, completely rigid foliage with zero wind sway, "
            "exact hand-drawn botanical watercolor and fine dark ink outline style on every frame. Silent video."
        )

    quarter_ids: dict[str, str | None] = {}
    quarter_elapsed: dict[str, float] = {}
    quarter_stats: dict[str, dict | None] = {}
    quarter_frames: dict[str, np.ndarray] = {}
    active_mp4s: dict[str, str | None] = {}

    q0_mp4, q0_trim_mp4, ref_090_chained = quarter_specs[0][1], run_dir / "q0_0_90_trimmed.mp4", run_dir / "ref_090_chained.png"
    q2_mp4, q2_trim_mp4, ref_270_chained = quarter_specs[2][1], run_dir / "q2_180_270_trimmed.mp4", run_dir / "ref_270_chained.png"

    for qk, qmp4, _skey, _ekey, start_desc, end_desc, mid_motion in quarter_specs:
        if qk == "q0":
            start_path, end_path = Path(prep_meta["ref_000"]), Path(prep_meta["ref_090"])
        elif qk == "q1":
            start_path = ref_090_chained if ref_090_chained.exists() else Path(prep_meta["ref_090"])
            end_path = Path(prep_meta["ref_180"])
        elif qk == "q2":
            start_path = Path(prep_meta["ref_180"])
            if getattr(args, "mirror_q0_for_270", False) and ref_090_chained.exists():
                end_path = run_dir / "ref_270_from_q0.png"
                Image.open(ref_090_chained).transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(end_path)
            else:
                end_path = Path(prep_meta["ref_270"])
        else:
            start_path = ref_270_chained if ref_270_chained.exists() else Path(prep_meta["ref_270"])
            end_path = Path(prep_meta["ref_000"])

        if qk in want_keys and (args.force or not qmp4.exists()):
            s_uri, s_mime = _upload_ref(start_path)
            e_uri, e_mime = _upload_ref(end_path)
            prompt = _build_quarter_prompt(start_desc, end_desc, mid_motion)
            print(f"\n[{args.plant_id}] Stage 2 {qk.upper()}: Generating ({start_path.name} -> {end_path.name}, {args.duration})...")
            qid, qel = _generate_single_half_video(
                client, omni_mod, s_uri, s_mime, e_uri, e_mime, prompt, qmp4,
                args.video_model, args.duration, args.resolution, args.timeout, force=args.force,
            )
            quarter_ids[qk] = qid
            quarter_elapsed[qk] = qel
        else:
            quarter_ids[qk] = None
            quarter_elapsed[qk] = 0.0

        if qmp4.exists():
            if qk == "q0":
                q_active, _, stats_q = _maybe_trim_tail_snap(qmp4, q0_trim_mp4, ref_090_chained)
                frames_q, _ = _analyze_mp4(q_active)
            elif qk == "q2":
                q_active, _, stats_q = _maybe_trim_tail_snap(qmp4, q2_trim_mp4, ref_270_chained)
                frames_q, _ = _analyze_mp4(q_active)
            else:
                q_active = qmp4
                frames_q, stats_q = _analyze_mp4(qmp4)

            active_mp4s[qk] = str(q_active)
            quarter_frames[qk] = frames_q
            quarter_stats[qk] = stats_q
            print(f"[{args.plant_id}] {qk.upper()} active stats ({q_active.name}):", json.dumps(stats_q))
            if qk in want_keys and len(want_keys) == 1:
                _save_single_segment_preview(frames_q, qk.upper(), run_dir / f"{qk}_preview_9.jpg")
        else:
            active_mp4s[qk] = None
            quarter_stats[qk] = None

    raw_preview_jpg = None
    if len(quarter_frames) == 4:
        raw_preview_jpg = run_dir / "raw_preview_36.jpg"
        _save_four_quarter_preview([(qk, quarter_frames[qk]) for qk, *_ in quarter_specs], raw_preview_jpg)

    summary = {
        "plant_id": args.plant_id,
        "mode": "4quarter",
        "quarters_mp4": active_mp4s,
        "raw_quarters_mp4": {qk: (str(qmp4) if qmp4.exists() else None) for qk, qmp4, *_ in quarter_specs},
        "quarter_interaction_ids": quarter_ids,
        "elapsed_sec": quarter_elapsed,
        "quarter_stats": quarter_stats,
        "raw_preview_jpg": str(raw_preview_jpg) if raw_preview_jpg is not None else None,
    }
    (run_dir / "video_run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def rebuild_video36_index(lab_root: Path | None = None, manifest_path: Path | None = None) -> Path:
    resolved_lab_root = lab_root or LAB_ROOT
    resolved_manifest_path = manifest_path or MANIFEST_PATH
    manifest_by_id = {}
    if resolved_manifest_path.exists():
        m_data = json.loads(resolved_manifest_path.read_text(encoding="utf-8"))
        manifest_by_id = {p.get("name"): p for p in m_data.get("plants", []) if p.get("name")}

    species = []
    for meta_file in sorted(resolved_lab_root.glob("plant_*_video36/metadata.json")):
        if "_opt" in meta_file.parent.name:
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if meta.get("published") is False:
            continue
        pid = str(meta.get("plant_id") or meta_file.parent.name.replace("_video36", ""))
        rec = manifest_by_id.get(pid, {})
        ai = rec.get("ai_analysis") or {}
        ptype = str(meta.get("plant_type") or ai.get("plant_type") or "shrub")
        gallery_h = _pick_gallery_height(rec) if rec else float(meta.get("default_lab_height_px", 150.0))
        species.append(
            {
                "plant_id": pid,
                "label_ascii": str(meta.get("label_ascii") or _extract_ascii_genus(rec, pid)),
                "display_name": str(meta.get("display_name") or rec.get("display_name") or pid),
                "plant_type": ptype,
                "path_prefix": str(meta.get("path_prefix") or f"assets/turntable_lab/{meta_file.parent.name}"),
                "atlas_file": str(meta.get("atlas_file") or "atlas_36.webp"),
                "hframes": int(meta.get("hframes", 6)),
                "vframes": int(meta.get("vframes", 6)),
                "frame_count": int(meta.get("frame_count", 36)),
                "root_anchor_px": meta.get("root_anchor_px", [110.0, 212.0]),
                "crown_h": float(meta.get("crown_h", 188.0)),
                "root_width": float(meta.get("root_width", rec.get("root_width") or 22.0)),
                "gallery_height_px": gallery_h,
                "webp_bytes": int(meta.get("webp_bytes", 0)),
            }
        )

    resolved_lab_root.mkdir(parents=True, exist_ok=True)
    out_path = resolved_lab_root / "video36_index.json"
    payload = {"updated_at": int(time.time()), "species": species}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Updated {out_path} with {len(species)} species: {[s['plant_id'] for s in species]}")
    return out_path


def run_atlas_stage(args: argparse.Namespace, prep_meta: dict, run_dir: Path) -> dict:
    out_dir = LAB_ROOT / f"{args.plant_id}_video36"
    resolved_key = prep_meta["resolved_key_mode"] if args.key_mode == "auto" else args.key_mode
    mode = getattr(args, "mode", prep_meta.get("mode", "2half"))

    if mode == "4quarter":
        q_stems = ("q0_0_90", "q1_90_180", "q2_180_270", "q3_270_360")
        q_paths = []
        missing = []
        for stem in q_stems:
            trim_p = run_dir / f"{stem}_trimmed.mp4"
            raw_p = run_dir / f"{stem}.mp4"
            if trim_p.exists():
                q_paths.append(trim_p)
            elif raw_p.exists():
                q_paths.append(raw_p)
            else:
                missing.append(f"{stem}.mp4")
        if missing:
            raise FileNotFoundError(f"Missing 4-quarter video files in {run_dir}: {missing}; run --stages video --mode 4quarter first")
        meta = build_video_atlas(
            input_mp4=q_paths[0],
            out_dir=out_dir,
            reverse_spin=False,
            frame_count=36,
            cell_size=220,
            target_crown_h=188.0,
            sampling="arclength",
            stitch_segments=q_paths,
            path_prefix=f"assets/turntable_lab/{args.plant_id}_video36",
            key_mode=resolved_key,
            plant_id=args.plant_id,
            display_name=prep_meta["display_name"],
            label_ascii=prep_meta["label_ascii"],
            plant_type=prep_meta["plant_type"],
            root_width=float(prep_meta["root_width"]),
            lab_height=float(prep_meta["gallery_height_px"]),
        )
    else:
        half_a_mp4 = run_dir / "half_a_0_180.mp4"
        half_b_mp4 = run_dir / "half_b_180_360.mp4"
        if not half_a_mp4.exists() or not half_b_mp4.exists():
            raise FileNotFoundError(f"Missing half video files in {run_dir}; run --stages video --mode 2half first")
        meta = build_video_atlas(
            input_mp4=half_a_mp4,
            out_dir=out_dir,
            reverse_spin=False,
            frame_count=36,
            cell_size=220,
            target_crown_h=188.0,
            sampling="arclength",
            stitch_second_half=half_b_mp4,
            second_half_start=0,
            second_half_end=-1,
            reverse_first_half=args.reverse_first_half,
            reverse_second_half=args.reverse_second_half,
            path_prefix=f"assets/turntable_lab/{args.plant_id}_video36",
            key_mode=resolved_key,
            plant_id=args.plant_id,
            display_name=prep_meta["display_name"],
            label_ascii=prep_meta["label_ascii"],
            plant_type=prep_meta["plant_type"],
            root_width=float(prep_meta["root_width"]),
            lab_height=float(prep_meta["gallery_height_px"]),
        )

    rebuild_video36_index()
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end 360-deg Video Turntable Atlas Pipeline (4quarter / 2half)")
    parser.add_argument("--plant-id", required=True, help="Manifest plant ID, e.g. plant_0005")
    parser.add_argument("--mode", choices=["4quarter", "2half"], default="2half")
    parser.add_argument("--stages", choices=["all", "prep", "video", "atlas"], default="all")
    parser.add_argument("--front-image", type=Path, default=None)
    parser.add_argument("--right-image", type=Path, default=None, help="Explicit 90-deg right-side image")
    parser.add_argument("--back-image", type=Path, default=None)
    parser.add_argument("--left-image", type=Path, default=None, help="Explicit 270-deg left-side image")
    parser.add_argument("--key-mode", choices=["auto", "pockets", "strict_pockets", "border_only"], default="auto")
    parser.add_argument("--image-model", default="gemini-nano-banana-2.1")
    parser.add_argument("--video-model", default="gemini-omni-1.2-flash")
    parser.add_argument("--duration", default="3s")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--segment", choices=["all", "q0", "q1", "q2", "q3", "a", "b"], default="all")
    parser.add_argument("--half", choices=["both", "a", "b"], default="both", help="2half alias for --segment")
    parser.add_argument("--mirror-q0-for-270", action="store_true", help="Use horizontally flipped chained Q0 end frame as Q2 270-deg guide")
    parser.add_argument("--reverse-first-half", action="store_true")
    parser.add_argument("--reverse-second-half", action="store_true")
    args = parser.parse_args()

    rec = _load_manifest_record(args.plant_id)
    run_dir = RUNS_ROOT / args.plant_id
    prep_meta_path = run_dir / "prep_meta.json"

    if args.stages in ("all", "prep"):
        prep_meta = run_prep_stage(args, rec, run_dir)
    else:
        if not prep_meta_path.exists():
            raise FileNotFoundError(f"Missing {prep_meta_path}; run --stages prep first")
        prep_meta = json.loads(prep_meta_path.read_text(encoding="utf-8"))

    if args.stages in ("all", "video"):
        run_video_stage(args, prep_meta, run_dir)

    if args.stages in ("all", "atlas"):
        run_atlas_stage(args, prep_meta, run_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
