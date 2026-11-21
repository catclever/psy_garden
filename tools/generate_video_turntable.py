#!/usr/bin/env python3
"""
Prepare all-8-angle pure-white studio reference images for plant_0000 and
generate a 4s 720p 360-degree seamless loop video via gemini-omni-1.2-flash.
"""

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_turntable_lab_assets import clean_view_image, load_source_views

DEFAULT_OUT_MP4 = REPO_ROOT / "tools" / "candidates" / "turntable_lab_sheets" / "plant_0000_video_360.mp4"
DEFAULT_META_JSON = REPO_ROOT / "tools" / "candidates" / "turntable_lab_sheets" / "plant_0000_video_360_meta.json"
DEFAULT_REFS_DIR = Path(
    "/Users/kael/.gemini/antigravity/brain/5d790e93-04e6-43ef-b03b-bd7f64dac343/scratch/video_refs_0000"
)
DEFAULT_PREVIEW_GRID = Path(
    "/Users/kael/.gemini/antigravity/brain/5d790e93-04e6-43ef-b03b-bd7f64dac343/scratch/raw_video_36frames_preview.jpg"
)
OMNI_VIDEO_SCRIPT = Path(
    "/Users/kael/.gemini/config/plugins/gemini-api/skills/gemini-omni-flash-api/scripts/video/generate_video.py"
)

VIDEO_PROMPT = (
    "Single unbroken locked-camera studio turnaround animation of the exact Calathea makoyana "
    "botanical watercolor and fine-ink illustration on a pure solid #FFFFFF seamless white background, "
    "with zero scene cuts, zero camera pan, zero zoom, zero floor shadows, zero turntable pedestal, "
    "zero wind sway, and completely rigid leaves. "
    "Use the first image (0-degree green feathered front view) as both the starting frame at 0s and "
    "the final frame at 4s (the second image). Rotate the whole plant clump smoothly clockwise when "
    "viewed from above around its fixed bottom-center brown petiole stem axis at a perfectly constant "
    "angular speed of 90 degrees per second, completing one full 360-degree revolution in 4 seconds: "
    "[0s] start on the first image (0-degree cream-and-green feathered front); "
    "[1s] pass smoothly through the third image (90-degree narrow right-side profile); "
    "[2s] reach the fourth image (180-degree pure rear view showing rich deep burgundy-purple leaf undersides); "
    "[3s] pass smoothly through the fifth image (270-degree narrow left-side profile); "
    "[4s] seamlessly close the 360-degree loop back into the second image (0-degree front). "
    "Use the sixth 2x2 image strictly as multi-angle structural reference for the intermediate diagonal "
    "angles (top-left 45-degree, top-right 135-degree, bottom-left 225-degree, bottom-right 315-degree), "
    "never showing a grid. Maintain the exact hand-drawn botanical watercolor and fine dark ink outline "
    "style on every frame. Silent video, no music, no sound effects, no dialogue."
)


def _crop_fg(cleaned_img: Image.Image) -> tuple[Image.Image, np.ndarray]:
    rgb = np.array(cleaned_img.convert("RGB"), dtype=np.uint8)
    mask = (255 - rgb.min(axis=2)) > 15
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return cleaned_img.convert("RGB"), rgb
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    cropped = cleaned_img.convert("RGB").crop(box)
    return cropped, np.array(cropped, dtype=np.uint8)


def _load_0000_anchors() -> dict[str, list[float]]:
    idx_path = REPO_ROOT / "src" / "assets" / "turntable_lab" / "index.json"
    if idx_path.exists():
        data = json.loads(idx_path.read_text(encoding="utf-8"))
        for sp in data.get("species", []):
            if sp.get("name") == "plant_0000":
                return {k: v["root_anchor"] for k, v in sp.get("views", {}).items()}
    return {str(a): [0.5, 0.996] for a in [0, 45, 90, 135, 180, 225, 270, 315]}


def _place_on_16x9(fg_crop: Image.Image, anchor_uv: list[float], crown_h_target: float = 540.0) -> Image.Image:
    w, h = fg_crop.size
    ax, ay = float(anchor_uv[0]), float(anchor_uv[1])
    crown_h_src = max(24.0, ay * h)
    scale = crown_h_target / crown_h_src
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    resized = fg_crop.resize((nw, nh), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (1280, 720), (255, 255, 255))
    paste_x = int(round(640.0 - ax * nw))
    paste_y = int(round(635.0 - ay * nh))
    canvas.paste(resized, (paste_x, paste_y))
    return canvas


def _build_diagonals_grid(fg_crops: dict[int, Image.Image], anchors: dict[str, list[float]]) -> Image.Image:
    grid = Image.new("RGB", (1024, 1024), (255, 255, 255))
    quad_map = [
        (45, 0, 0),
        (135, 512, 0),
        (225, 0, 512),
        (315, 512, 512),
    ]
    margin = 44
    target_box = 512 - 2 * margin
    for ang, qx, qy in quad_map:
        crop = fg_crops[ang]
        cw, ch = crop.size
        ax, ay = anchors.get(str(ang), [0.5, 0.996])
        scale = min(target_box / max(cw, 1), target_box / max(ch, 1))
        nw = max(1, int(round(cw * scale)))
        nh = max(1, int(round(ch * scale)))
        resized = crop.resize((nw, nh), Image.Resampling.LANCZOS)
        px = int(round(qx + 256.0 - float(ax) * nw))
        py = int(round(qy + 512 - margin - float(ay) * nh))
        px = max(qx + margin, min(qx + 512 - margin - nw, px))
        py = max(qy + margin, min(qy + 512 - margin - nh, py))
        grid.paste(resized, (px, py))
    return grid


def prepare_reference_images(refs_dir: Path) -> dict[str, Path]:
    refs_dir.mkdir(parents=True, exist_ok=True)
    source_views = load_source_views()["plant_0000"]
    anchors = _load_0000_anchors()

    fg_crops: dict[int, Image.Image] = {}
    for ang, raw_img in source_views.items():
        cleaned = clean_view_image(raw_img, border_px=6)
        fg_crop, _ = _crop_fg(cleaned)
        fg_crops[ang] = fg_crop

    out_paths: dict[str, Path] = {}
    for ang, key in [(0, "ref_000"), (90, "ref_090"), (180, "ref_180"), (270, "ref_270")]:
        anchor = anchors.get(str(ang), [0.5, 0.996])
        frame = _place_on_16x9(fg_crops[ang], anchor, crown_h_target=540.0)
        fpath = refs_dir / f"{key}.png"
        frame.save(fpath)
        out_paths[key] = fpath

    diag_grid = _build_diagonals_grid(fg_crops, anchors)
    diag_path = refs_dir / "ref_diagonals_45_135_225_315.png"
    diag_grid.save(diag_path)
    out_paths["ref_diagonals"] = diag_path
    return out_paths


def extract_preview_contact_sheet(mp4_path: Path, out_jpg: Path, sample_count: int = 36) -> dict:
    import imageio.v3 as iio
    from PIL import ImageDraw

    meta = iio.immeta(str(mp4_path), plugin="FFMPEG")
    frames = iio.imread(str(mp4_path), plugin="FFMPEG")
    n_frames = len(frames)
    if n_frames == 0:
        raise RuntimeError(f"Decoded 0 frames from {mp4_path}")

    indices = np.linspace(0, max(0, n_frames - 2), sample_count, dtype=int)
    cell_w, cell_h = 240, 200
    cols, rows = 6, 6
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)

    for idx_pos, f_idx in enumerate(indices):
        r, c = divmod(idx_pos, cols)
        x0, y0 = c * cell_w, r * cell_h
        arr = frames[f_idx]
        fh, fw, _ = arr.shape
        cx = fw // 2
        half_crop = min(fh // 2, cx, fw - cx)
        sq = Image.fromarray(arr[:, cx - half_crop : cx + half_crop, :]).resize(
            (cell_w - 8, cell_h - 24), Image.Resampling.LANCZOS
        )
        sheet.paste(sq, (x0 + 4, y0 + 2))
        draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h - 1], outline=(210, 210, 210), width=1)
        draw.text((x0 + 8, y0 + cell_h - 18), f"F{idx_pos:02d} (raw #{int(f_idx)})", fill=(20, 20, 20))

    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_jpg, quality=90)
    return {
        "total_decoded_frames": int(n_frames),
        "fps": float(meta.get("fps", 24.0)),
        "size": list(meta.get("size", [frames.shape[2], frames.shape[1]])),
        "duration": float(meta.get("duration", n_frames / max(float(meta.get("fps", 24.0)), 1.0))),
        "preview_contact_sheet": str(out_jpg),
    }


def _ensure_api_key_silently() -> None:
    if os.environ.get("GEMINI_API_KEY"):
        return
    cfg_path = TOOLS_DIR / "ai_config.json"
    if cfg_path.exists():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        key = (data.get("api_key") or "").strip()
        if key:
            os.environ["GEMINI_API_KEY"] = key
            return
    raise RuntimeError("Missing GEMINI_API_KEY in environment or tools/ai_config.json")


def _load_omni_video_module():
    spec = importlib.util.spec_from_file_location("omni_generate_video", str(OMNI_VIDEO_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["omni_generate_video"] = mod
    spec.loader.exec_module(mod)
    return mod


def _upload_and_wait(client, file_path: str, mime_type: str, normalize_fn) -> tuple[str, str]:
    from google.genai import types

    p = Path(file_path)
    print(f"Uploading reference '{p.name}' via Files API...")
    file_obj = client.files.upload(
        file=str(p),
        config=types.UploadFileConfig(display_name=p.name, mime_type=mime_type),
    )
    file_name = getattr(file_obj, "name", None)
    if not file_name:
        raise RuntimeError(f"Upload did not return file name for {p.name}")

    deadline = time.time() + 180.0
    interval = 1.5
    while True:
        current = client.files.get(name=file_name)
        state = getattr(current, "state", None)
        state_str = state.name if hasattr(state, "name") else str(state)
        if state == types.FileState.ACTIVE or state_str == "ACTIVE":
            uri = normalize_fn(getattr(current, "uri", None))
            ret_mime = getattr(current, "mime_type", None) or mime_type
            return uri, ret_mime
        if state == types.FileState.FAILED or state_str == "FAILED":
            raise RuntimeError(f"File processing FAILED for {file_name}")
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for {file_name} to become ACTIVE")
        time.sleep(interval)
        interval = min(interval * 1.25, 8.0)


def _find_output_video(interaction):
    output_video = getattr(interaction, "output_video", None)
    if output_video is not None:
        return output_video
    steps = getattr(interaction, "steps", None) or []
    for step in reversed(steps):
        if getattr(step, "type", None) != "model_output":
            continue
        for item in reversed(getattr(step, "content", None) or []):
            if getattr(item, "type", None) == "video":
                return item
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate 360-deg studio turnaround video for plant_0000")
    parser.add_argument("--prep-only", action="store_true", help="Only build reference images and exit")
    parser.add_argument("--refs-dir", type=Path, default=DEFAULT_REFS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT_MP4)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_JSON)
    parser.add_argument("--preview-grid", type=Path, default=DEFAULT_PREVIEW_GRID)
    parser.add_argument("--model", default="gemini-omni-1.2-flash")
    parser.add_argument("--api-version", default="v1beta")
    parser.add_argument("--duration", default="4s")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    ref_paths = prepare_reference_images(args.refs_dir)
    for k, p in ref_paths.items():
        print(f"Prepared {k}: {p}")

    if args.prep_only:
        return 0

    _ensure_api_key_silently()
    omni_mod = _load_omni_video_module()
    from google import genai
    from google.genai import types, errors

    args.output.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    client = genai.Client(http_options=types.HttpOptions(api_version=args.api_version, timeout=int(args.timeout) * 1000))

    first_path = str(ref_paths["ref_000"])
    ref_list = [
        str(ref_paths["ref_090"]),
        str(ref_paths["ref_180"]),
        str(ref_paths["ref_270"]),
        str(ref_paths["ref_diagonals"]),
    ]

    f1_uri, f1_mime = _upload_and_wait(client, first_path, "image/png", omni_mod.normalize_file_uri)
    input_parts = [
        {"type": "image", "uri": f1_uri, "mime_type": f1_mime},
        {"type": "image", "uri": f1_uri, "mime_type": f1_mime},
    ]
    for rpath in ref_list:
        r_uri, r_mime = _upload_and_wait(client, rpath, "image/png", omni_mod.normalize_file_uri)
        input_parts.append({"type": "image", "uri": r_uri, "mime_type": r_mime})
    input_parts.append({"type": "text", "text": VIDEO_PROMPT})

    video_config = {
        "type": "video",
        "delivery": "uri",
        "aspect_ratio": "16:9",
        "duration": omni_mod.parse_and_validate_duration(args.duration),
        "resolution": omni_mod.parse_and_validate_resolution(args.resolution),
    }

    print(
        f"\nSending generation request using model '{args.model}' (api_version='{args.api_version}')...\n"
        f"Duration: {video_config['duration']} | Resolution: {video_config['resolution']} | Refs: {len(input_parts) - 1} images"
    )
    try:
        interaction = client.interactions.create(
            api_version=args.api_version,
            model=args.model,
            input=input_parts,
            response_format=video_config,
            timeout=float(args.timeout),
        )
    except errors.APIError as e:
        raise RuntimeError(f"API Error generating video via SDK: {omni_mod.sanitize_error(e)}")

    interaction_id = getattr(interaction, "id", None)
    print(f"Generation complete! Interaction ID: {interaction_id}")
    output_video = _find_output_video(interaction)
    if not output_video:
        raise RuntimeError(f"No video content found in interaction {interaction_id} (status={getattr(interaction, 'status', None)})")

    video_uri = getattr(output_video, "uri", None)
    video_data = getattr(output_video, "data", None)
    if video_uri:
        try:
            omni_mod.download_video_file(output_video, str(args.output), client=client)
        except Exception as dl_err:
            print(f"Retrying download with default client after {args.api_version} download note: {omni_mod.sanitize_error(dl_err)}")
            omni_mod.download_video_file(output_video, str(args.output), client=genai.Client())
    elif video_data:
        import base64

        raw_bytes = video_data if isinstance(video_data, (bytes, bytearray)) else base64.b64decode(video_data)
        args.output.write_bytes(raw_bytes)
        print(f"Video bytes saved directly to: {args.output}")
    else:
        raise RuntimeError(f"No video URI or inline bytes found in interaction {interaction_id} (status={getattr(interaction, 'status', None)})")

    elapsed = round(time.time() - t0, 2)

    preview_info = extract_preview_contact_sheet(args.output, args.preview_grid, sample_count=36)
    meta_out = {
        "plant_id": "plant_0000",
        "model": args.model,
        "api_version": args.api_version,
        "interaction_id": interaction_id,
        "aspect_ratio": "16:9",
        "requested_duration": args.duration,
        "resolution": args.resolution,
        "elapsed_sec": elapsed,
        "output_mp4": str(args.output),
        "mp4_bytes": args.output.stat().st_size,
        **preview_info,
    }
    args.meta_output.write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Video generation summary saved to {args.meta_output}")
    print(json.dumps(meta_out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
