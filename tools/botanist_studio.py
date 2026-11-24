import http.server
import socketserver
import json
import os
import threading
import sys
sys.path.insert(0, os.path.dirname(__file__))
import ai_botanist
import glob
import subprocess
import traceback
import urllib.parse
import shutil
import time
import re
from pathlib import Path

PORT = int(os.environ.get("PORT", 18087))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATES_DIR = os.path.join(PROJECT_ROOT, 'tools', 'candidates')
TRASH_DIR = os.path.join(PROJECT_ROOT, 'tools', 'trash')
PLANTS_DIR = os.path.join(PROJECT_ROOT, 'src', 'assets', 'plants')
MANIFEST_JSON_PATH = os.path.join(PLANTS_DIR, 'manifest.json')
VIDEO36_RUNS_DIR = os.path.join(CANDIDATES_DIR, 'video36_runs')
TURNTABLE_LAB_DIR = os.path.join(PROJECT_ROOT, 'src', 'assets', 'turntable_lab')
VIDEO36_INDEX_PATH = os.path.join(TURNTABLE_LAB_DIR, 'video36_index.json')
CONFIRMED_CANDIDATES_FILE = os.path.join(PROJECT_ROOT, 'tools', 'confirmed_candidates.json')
CONFIRMED_PLANTS_FILE = os.path.join(PROJECT_ROOT, 'tools', 'confirmed_plants.json')

VIDEO36_JOBS = {}
VIDEO36_JOBS_LOCK = threading.Lock()
VIDEO36_FILE_ALLOWLIST = {
    'ref_000_vs_180_check.jpg',
    'ref_4views_check.jpg',
    'raw_preview_36.jpg',
    'video_36_contact_sheet.jpg',
    'atlas_36.webp',
    'ref_000.png',
    'ref_180.png',
    'flow_prompts.txt',
    'flow_prompts.json',
}
VIDEO36_MP4_SLOTS = {
    'half_a': 'half_a_0_180.mp4',
    'half_b': 'half_b_180_360.mp4',
    'single_360': 'single_360.mp4',
}
MAX_VIDEO36_MP4_BYTES = 120 * 1024 * 1024

def refresh_plant_manifest():
    try:
        if 'generate_plant_manifest' in sys.modules:
            import generate_plant_manifest
            generate_plant_manifest.generate_manifest(plants_dir=PLANTS_DIR)
        else:
            draft_mod = 'generate_plant_manifest_task_asset_soft_disable_toggle_and_hide_legacy_plants_032'
            if os.path.exists(os.path.join(os.path.dirname(__file__), f'{draft_mod}.py')):
                mod = __import__(draft_mod)
                mod.generate_manifest(plants_dir=PLANTS_DIR)
            else:
                import generate_plant_manifest
                generate_plant_manifest.generate_manifest(plants_dir=PLANTS_DIR)
    except Exception as e:
        print(f"⚠️ 刷新 manifest.json 失败: {e}")


def _is_safe_path_segment(value) -> bool:
    """Validate that a plant_name or variant_id is a single safe path segment."""
    return (
        isinstance(value, str)
        and len(value) > 0
        and value not in ('.', '..')
        and '..' not in value
        and '/' not in value
        and '\\' not in value
    )


def _load_asset_variant_entry(plant_name: str, variant_id: str, item_path: str, path_prefix: str):
    """Build a variant metadata dict for GET /api/assets, or None if no PNG layers exist."""
    layers = [f for f in os.listdir(item_path) if f.endswith('.png')]
    if not layers:
        return None

    display_name = plant_name
    true_color = False
    enabled = True
    source_image = None
    status = 'ready'
    ai_analysis = None
    root_width = None
    root_ratio = None
    ground_ellipse = None
    width = None
    height = None

    meta_file = os.path.join(item_path, 'metadata.json')
    if os.path.exists(meta_file):
        try:
            with open(meta_file, 'r', encoding='utf-8') as mf:
                mdata = json.load(mf)
                if isinstance(mdata, dict):
                    display_name = mdata.get('display_name') or mdata.get('name') or plant_name
                    true_color = bool(mdata.get('true_color', False))
                    enabled = (mdata.get('enabled') is not False)
                    source_image = mdata.get('source_image')
                    status = mdata.get('status', 'ready')
                    ai_analysis = mdata.get('ai_analysis')
                    root_width = mdata.get('root_width')
                    root_ratio = mdata.get('root_ratio')
                    ground_ellipse = mdata.get('ground_ellipse')
                    width = mdata.get('width')
                    height = mdata.get('height')
        except Exception:
            pass

    return {
        'plant_name': plant_name,
        'display_name': display_name,
        'variant_id': variant_id,
        'layers': layers,
        'true_color': true_color,
        'enabled': enabled,
        'source_image': source_image,
        'status': status,
        'ai_analysis': ai_analysis,
        'root_width': root_width,
        'root_ratio': root_ratio,
        'ground_ellipse': ground_ellipse,
        'width': width,
        'height': height,
        'path_prefix': path_prefix,
    }

def get_confirmed_candidates():
    target_file = CONFIRMED_CANDIDATES_FILE if os.path.exists(CONFIRMED_CANDIDATES_FILE) else CONFIRMED_PLANTS_FILE
    if os.path.exists(target_file):
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_confirmed_candidates(data):
    os.makedirs(os.path.dirname(CONFIRMED_CANDIDATES_FILE), exist_ok=True)
    with open(CONFIRMED_CANDIDATES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # Also sync to confirmed_plants.json for backward compatibility
    try:
        with open(CONFIRMED_PLANTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def find_source_image_for_asset(plant_name, variant_id=None):
    if variant_id:
        v_meta = os.path.join(PLANTS_DIR, plant_name, variant_id, 'metadata.json')
        if os.path.exists(v_meta):
            try:
                with open(v_meta, 'r', encoding='utf-8') as f:
                    m = json.load(f)
                    src = m.get('source_image')
                    if src:
                        for sdir in [os.path.join(CANDIDATES_DIR, 'processed'), CANDIDATES_DIR]:
                            p = os.path.join(sdir, src)
                            if os.path.exists(p):
                                return p
            except Exception:
                pass
    
    display_name = None
    if variant_id:
        v_meta = os.path.join(PLANTS_DIR, plant_name, variant_id, 'metadata.json')
        if os.path.exists(v_meta):
            try:
                with open(v_meta, 'r', encoding='utf-8') as f:
                    m = json.load(f)
                    display_name = m.get('display_name')
            except Exception:
                pass

    num_part = plant_name.replace('plant_', '')
    search_dirs = [os.path.join(CANDIDATES_DIR, 'processed'), CANDIDATES_DIR]
    
    for sdir in search_dirs:
        if not os.path.exists(sdir):
            continue
        files = os.listdir(sdir)
        # 1. Exact match with plant_name
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                if plant_name in f:
                    return os.path.join(sdir, f)
        # 2. Match with display_name
        if display_name:
            for f in files:
                if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                    if display_name in f:
                        return os.path.join(sdir, f)
        # 3. Match numeric part e.g. plant_0084 -> _0084_ or _84_
        if num_part.isdigit():
            target_num_padded = f'_{num_part}_'
            target_num_int = f'_{int(num_part)}_'
            for f in files:
                if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                    if target_num_padded in f or target_num_int in f:
                        return os.path.join(sdir, f)
                        
    # 4. Check legacy / v_uncut demo plants
    demo_uncut = {
        'banana': os.path.join(PROJECT_ROOT, 'src', 'assets', 'plants', 'banana', 'v_uncut', 'plant_banana.png'),
        'lily': os.path.join(PROJECT_ROOT, 'src', 'assets', 'plants', 'lily', 'v_uncut', 'plant_lily.png'),
        'mushroom': os.path.join(PROJECT_ROOT, 'src', 'assets', 'plants', 'mushroom', 'v_uncut', 'plant_mushroom.png'),
        'bamboo': os.path.join(PROJECT_ROOT, 'legacy', 'plant_bamboo.png'),
        'bonsai': os.path.join(PROJECT_ROOT, 'legacy', 'plant_bonsai.png'),
        'fern': os.path.join(PROJECT_ROOT, 'legacy', 'plant_fern.png'),
    }
    if plant_name in demo_uncut and os.path.exists(demo_uncut[plant_name]):
        return demo_uncut[plant_name]

    return None

def run_build_asset(input_path, plant_name, variant_id, display_name=None):
    # Try direct import first if Pillow is installed in Python environment
    try:
        import sys
        tools_path = os.path.join(PROJECT_ROOT, 'tools')
        if tools_path not in sys.path:
            sys.path.insert(0, tools_path)
        import build_plant_asset
        out_dir = os.path.join(PLANTS_DIR, plant_name, variant_id)
        if build_plant_asset.build_asset(input_path, plant_name, variant_id, out_dir, PROJECT_ROOT, display_name):
            return True
    except Exception as e:
        print(f"Direct build_asset failed ({e}), falling back to subprocess...")
    
    build_script = os.path.join(PROJECT_ROOT, 'tools', 'build_plant_asset.py')
    cmd = ["uv", "run", "python", build_script, input_path, plant_name, str(variant_id)]
    if display_name:
        cmd.append(display_name)
    print(f"⚙️ 正在执行切片脚本: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=os.path.join(PROJECT_ROOT, 'tools'), capture_output=True, text=True)
    if result.returncode == 0:
        return True
    
    # Try fallback to python3 if uv has sandbox or path issues
    cmd2 = ["python3", build_script, input_path, plant_name, str(variant_id)]
    if display_name:
        cmd2.append(display_name)
    result2 = subprocess.run(cmd2, cwd=os.path.join(PROJECT_ROOT, 'tools'), capture_output=True, text=True)
    if result2.returncode == 0:
        return True
        
    raise Exception(f"切片脚本执行失败:\n[uv]: {result.stderr}\n[python3]: {result2.stderr}")


def _safe_read_json(path: str):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _resolve_video36_python_cmd(args_list: list[str]) -> list[str]:
    pipeline_script = os.path.join(PROJECT_ROOT, 'tools', 'plant_video36_pipeline.py')
    has_deps = True
    for mod in ('google.genai', 'PIL', 'numpy', 'scipy', 'imageio'):
        try:
            __import__(mod)
        except Exception:
            has_deps = False
            break
    if has_deps:
        return [sys.executable, pipeline_script, *args_list]
    return [
        'uv',
        'run',
        '--offline',
        '--with',
        'google-genai>=2.30.1',
        '--with',
        'pillow',
        '--with',
        'numpy',
        '--with',
        'scipy',
        '--with',
        'imageio[ffmpeg]',
        'python',
        pipeline_script,
        *args_list,
    ]


def _resolve_video36_file_path(plant_id: str, filename: str) -> str | None:
    if not _is_safe_path_segment(plant_id) or not _is_safe_path_segment(filename):
        return None
    if filename not in VIDEO36_FILE_ALLOWLIST:
        return None
    candidates = [
        (TURNTABLE_LAB_DIR, os.path.join(TURNTABLE_LAB_DIR, f'{plant_id}_video36', filename)),
        (VIDEO36_RUNS_DIR, os.path.join(VIDEO36_RUNS_DIR, plant_id, filename)),
    ]
    for base_dir, cand in candidates:
        real_base = os.path.realpath(base_dir)
        real_cand = os.path.realpath(cand)
        if real_cand.startswith(real_base + os.sep) and os.path.isfile(real_cand):
            return real_cand
    return None


def _serialize_video36_job(job: dict) -> dict:
    return {
        'job_id': job['job_id'],
        'plant_id': job['plant_id'],
        'stages': job['stages'],
        'mode': job['mode'],
        'key_mode': job['key_mode'],
        'status': job['status'],
        'started_at': job['started_at'],
        'ended_at': job['ended_at'],
        'returncode': job['returncode'],
        'log_tail': list(job['logs'][-80:]),
    }


def _collect_video36_list() -> dict:
    manifest_data = _safe_read_json(MANIFEST_JSON_PATH) or {}
    manifest_plants = {}
    for rec in manifest_data.get('plants') or []:
        if not isinstance(rec, dict):
            continue
        pid = rec.get('name') or rec.get('id')
        if pid and rec.get('enabled', True):
            manifest_plants[str(pid)] = rec

    index_data = _safe_read_json(VIDEO36_INDEX_PATH) or {}
    published_ids = [
        str(sp.get('plant_id'))
        for sp in (index_data.get('species') or [])
        if isinstance(sp, dict) and sp.get('plant_id')
    ]
    published_set = set(published_ids)

    all_ids = set(manifest_plants.keys())
    if os.path.isdir(VIDEO36_RUNS_DIR):
        for entry in os.listdir(VIDEO36_RUNS_DIR):
            if _is_safe_path_segment(entry) and os.path.isdir(os.path.join(VIDEO36_RUNS_DIR, entry)):
                all_ids.add(entry)
    if os.path.isdir(TURNTABLE_LAB_DIR):
        for entry in os.listdir(TURNTABLE_LAB_DIR):
            if entry.endswith('_video36') and '_opt' not in entry and os.path.isdir(os.path.join(TURNTABLE_LAB_DIR, entry)):
                pid = entry[:-len('_video36')]
                if _is_safe_path_segment(pid):
                    all_ids.add(pid)

    with VIDEO36_JOBS_LOCK:
        active_by_pid = {}
        latest_by_pid = {}
        for job in VIDEO36_JOBS.values():
            pid = job['plant_id']
            if job['status'] == 'running':
                active_by_pid[pid] = _serialize_video36_job(job)
            prev = latest_by_pid.get(pid)
            if prev is None or job['started_at'] >= prev['started_at']:
                latest_by_pid[pid] = _serialize_video36_job(job)

    items = []
    for pid in sorted(all_ids):
        rec = manifest_plants.get(pid, {})
        ai = rec.get('ai_analysis') if isinstance(rec.get('ai_analysis'), dict) else {}
        prep_meta = _safe_read_json(os.path.join(VIDEO36_RUNS_DIR, pid, 'prep_meta.json'))
        video_summary = _safe_read_json(os.path.join(VIDEO36_RUNS_DIR, pid, 'video_run_summary.json'))
        flow_prompts = _safe_read_json(os.path.join(VIDEO36_RUNS_DIR, pid, 'flow_prompts.json'))
        if flow_prompts is None and prep_meta is not None:
            try:
                import plant_video36_pipeline

                flow_prompts = plant_video36_pipeline.build_flow_prompts(prep_meta)
            except Exception:
                flow_prompts = None

        run_dir_pid = os.path.join(VIDEO36_RUNS_DIR, pid)
        mp4_slots = {
            slot: os.path.isfile(os.path.join(run_dir_pid, fname))
            for slot, fname in VIDEO36_MP4_SLOTS.items()
        }

        atlas_dir = os.path.join(TURNTABLE_LAB_DIR, f'{pid}_video36')
        atlas_meta = _safe_read_json(os.path.join(atlas_dir, 'metadata.json'))
        atlas_webp = os.path.join(atlas_dir, 'atlas_36.webp')

        has_atlas = atlas_meta is not None and os.path.isfile(atlas_webp)
        has_prep = prep_meta is not None
        has_video = video_summary is not None or (mp4_slots['half_a'] and mp4_slots['half_b']) or mp4_slots['single_360']
        published = bool(has_atlas and atlas_meta.get('published') is not False and pid in published_set)

        files_map = {}
        for fname in sorted(VIDEO36_FILE_ALLOWLIST):
            if _resolve_video36_file_path(pid, fname) is not None:
                files_map[fname] = f'/api/video36/file/{pid}/{fname}'

        thumb_url = ''
        layers = rec.get('layer_files') or []
        if layers and rec.get('path_prefix'):
            green = next((l for l in layers if l.get('slot') == 'green'), layers[0])
            if green and green.get('file'):
                thumb_url = f"/assets/{rec['path_prefix'].lstrip('/')}/{green['file']}"

        display_name = (
            (atlas_meta or {}).get('display_name')
            or (prep_meta or {}).get('display_name')
            or rec.get('display_name')
            or pid
        )
        label_ascii = (
            (atlas_meta or {}).get('label_ascii')
            or (prep_meta or {}).get('label_ascii')
            or pid
        )
        plant_type = (
            (atlas_meta or {}).get('plant_type')
            or (prep_meta or {}).get('plant_type')
            or ai.get('plant_type')
            or 'shrub'
        )
        items.append(
            {
                'plant_id': pid,
                'display_name': display_name,
                'label_ascii': label_ascii,
                'plant_type': plant_type,
                'thumb_url': thumb_url,
                'has_prep': has_prep,
                'has_video': has_video,
                'has_atlas': has_atlas,
                'published': published,
                'prep_meta': prep_meta,
                'video_summary': video_summary,
                'flow_prompts': flow_prompts,
                'mp4_slots': mp4_slots,
                'atlas_meta': atlas_meta,
                'files': files_map,
                'active_job': active_by_pid.get(pid) or latest_by_pid.get(pid),
            }
        )

    items.sort(key=lambda x: (0 if x['has_atlas'] else (1 if x['has_prep'] else 2), x['plant_id']))
    return {
        'updated_at': index_data.get('updated_at'),
        'published_ids': published_ids,
        'items': items,
    }


def _video36_job_worker(job_id: str, cmd: list[str], cwd: str):
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if proc.stdout is not None:
            for line in proc.stdout:
                clean_line = line.rstrip('\r\n')
                with VIDEO36_JOBS_LOCK:
                    job = VIDEO36_JOBS.get(job_id)
                    if job is not None:
                        job['logs'].append(clean_line)
                        if len(job['logs']) > 400:
                            del job['logs'][:-400]
        rc = proc.wait()
        with VIDEO36_JOBS_LOCK:
            job = VIDEO36_JOBS.get(job_id)
            if job is not None:
                job['returncode'] = int(rc)
                job['ended_at'] = time.time()
                job['status'] = 'succeeded' if rc == 0 else 'failed'
    except Exception as exc:
        with VIDEO36_JOBS_LOCK:
            job = VIDEO36_JOBS.get(job_id)
            if job is not None:
                job['logs'].append(f'ERROR: {exc}')
                job['returncode'] = -1
                job['ended_at'] = time.time()
                job['status'] = 'failed'


def _start_video36_job(
    plant_id: str,
    stages: str,
    mode: str,
    key_mode: str = 'auto',
    force: bool = False,
    mirror_q0_for_270: bool = False,
    draft: bool = False,
) -> dict:
    if not _is_safe_path_segment(plant_id):
        raise ValueError('Invalid plant_id')
    if stages not in ('prep', 'video', 'atlas', 'all'):
        raise ValueError('Invalid stages')
    if mode not in ('2half', '4quarter', 'single360'):
        raise ValueError('Invalid mode')
    if mode == 'single360' and stages in ('video', 'all'):
        raise ValueError('single360 mode requires uploading single_360.mp4 then running atlas')
    if key_mode not in ('auto', 'pockets', 'strict_pockets', 'border_only'):
        raise ValueError('Invalid key_mode')

    with VIDEO36_JOBS_LOCK:
        for existing in VIDEO36_JOBS.values():
            if existing['plant_id'] == plant_id and existing['status'] == 'running':
                raise RuntimeError(f'Job already running for {plant_id}: {existing["job_id"]}')

        job_id = f'{plant_id}_{int(time.time() * 1000)}'
        args_list = [
            '--plant-id',
            plant_id,
            '--stages',
            stages,
            '--mode',
            mode,
            '--key-mode',
            key_mode,
        ]
        if force:
            args_list.append('--force')
        if draft:
            args_list.append('--draft')
        if mirror_q0_for_270 and mode == '4quarter':
            args_list.append('--mirror-q0-for-270')

        cmd = _resolve_video36_python_cmd(args_list)
        job = {
            'job_id': job_id,
            'plant_id': plant_id,
            'stages': stages,
            'mode': mode,
            'key_mode': key_mode,
            'status': 'running',
            'started_at': time.time(),
            'ended_at': None,
            'returncode': None,
            'logs': [f'$ {" ".join(cmd)}'],
        }
        VIDEO36_JOBS[job_id] = job

    t = threading.Thread(
        target=_video36_job_worker,
        args=(job_id, cmd, os.path.join(PROJECT_ROOT, 'tools')),
        daemon=True,
    )
    t.start()
    return _serialize_video36_job(job)


def _publish_video36_asset(plant_id: str, published: bool) -> dict:
    if not _is_safe_path_segment(plant_id):
        raise ValueError('Invalid plant_id')
    atlas_dir = os.path.join(TURNTABLE_LAB_DIR, f'{plant_id}_video36')
    meta_path = os.path.join(atlas_dir, 'metadata.json')
    if not os.path.isfile(meta_path):
        raise FileNotFoundError(f'Missing metadata.json for {plant_id}')
    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    meta['published'] = bool(published)
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    import plant_video36_pipeline

    out_path = plant_video36_pipeline.rebuild_video36_index(
        lab_root=Path(TURNTABLE_LAB_DIR),
        manifest_path=Path(MANIFEST_JSON_PATH),
    )
    idx_data = _safe_read_json(str(out_path)) or {}
    return {
        'status': 'success',
        'plant_id': plant_id,
        'published': bool(published),
        'index_species_count': len(idx_data.get('species') or []),
    }


def _save_uploaded_video36_mp4(
    plant_id: str,
    slot: str,
    raw_bytes: bytes,
    auto_atlas: bool = False,
    key_mode: str = 'auto',
) -> dict:
    if not _is_safe_path_segment(plant_id):
        raise ValueError('Invalid plant_id')
    if slot not in VIDEO36_MP4_SLOTS:
        raise ValueError(f'Invalid MP4 slot: {slot}')
    if not raw_bytes:
        raise ValueError('Empty upload body')
    if len(raw_bytes) > MAX_VIDEO36_MP4_BYTES:
        raise ValueError('Uploaded file exceeds 120 MB limit')

    runs_real = os.path.realpath(VIDEO36_RUNS_DIR)
    target_dir = os.path.join(VIDEO36_RUNS_DIR, plant_id)
    os.makedirs(target_dir, exist_ok=True)
    real_dir = os.path.realpath(target_dir)
    if real_dir != runs_real and not real_dir.startswith(runs_real + os.sep):
        raise ValueError('Unsafe upload directory')

    filename = VIDEO36_MP4_SLOTS[slot]
    out_path = os.path.join(real_dir, filename)
    with open(out_path, 'wb') as f:
        f.write(raw_bytes)

    mp4_slots = {
        s: os.path.isfile(os.path.join(real_dir, fn))
        for s, fn in VIDEO36_MP4_SLOTS.items()
    }
    if slot == 'single_360':
        ready_for_atlas = mp4_slots['single_360']
        rec_mode = 'single360'
    else:
        ready_for_atlas = mp4_slots['half_a'] and mp4_slots['half_b']
        rec_mode = '2half'

    auto_job = None
    if auto_atlas and ready_for_atlas:
        auto_job = _start_video36_job(
            plant_id=plant_id,
            stages='atlas',
            mode=rec_mode,
            key_mode=key_mode,
            force=True,
            draft=True,
        )

    return {
        'status': 'success',
        'plant_id': plant_id,
        'slot': slot,
        'filename': filename,
        'bytes': len(raw_bytes),
        'mp4_slots': mp4_slots,
        'ready_for_atlas': ready_for_atlas,
        'recommended_mode': rec_mode,
        'auto_job': auto_job,
    }


HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Psy Garden - 造物工坊 V2</title>
    <style>
        body {
            background-color: #0d1117;
            color: #c9d1d9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 40px;
            display: flex;
            justify-content: center;
        }
        .layout {
            display: flex;
            gap: 30px;
            width: 100%;
            max-width: 1400px;
        }
        .panel {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        h1 {
            margin-top: 0;
            color: #58a6ff;
            font-size: 24px;
            border-bottom: 1px solid #30363d;
            padding-bottom: 15px;
        }
        .subtitle { font-size: 14px; color: #8b949e; margin-bottom: 20px; }
        
        .gallery {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
            gap: 15px;
            overflow-y: auto;
            flex: 1;
            min-height: 400px;
            align-content: start;
        }
        .gallery::-webkit-scrollbar { width: 8px; }
        .gallery::-webkit-scrollbar-track { background: #0d1117; border-radius: 4px; }
        .gallery::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }
        
        .card {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 10px;
            display: flex;
            flex-direction: column;
            align-items: center;
            transition: border-color 0.2s;
            position: relative;
        }
        .card:hover { border-color: #58a6ff; }
        .card.confirmed {
            border-color: #f2cc60 !important;
            box-shadow: 0 0 16px rgba(242, 204, 96, 0.45);
            background: linear-gradient(180deg, rgba(242, 204, 96, 0.08) 0%, #0d1117 100%);
        }
        .card.confirmed .img-container {
            box-shadow: 0 0 10px rgba(242, 204, 96, 0.3);
        }
        .confirmed-star {
            position: absolute;
            top: 6px;
            right: 6px;
            background: rgba(242, 204, 96, 0.25);
            color: #f2cc60;
            border: 1px solid #f2cc60;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 700;
            padding: 1px 6px;
            pointer-events: none;
            z-index: 2;
        }
        .card-close-btn {
            position: absolute;
            top: 6px;
            right: 6px;
            width: 22px;
            height: 22px;
            border-radius: 50%;
            background: rgba(22, 27, 34, 0.85);
            border: 1px solid #30363d;
            color: #8b949e;
            font-size: 14px;
            line-height: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            z-index: 10;
            transition: all 0.2s ease;
            opacity: 0;
            padding: 0;
        }
        .card:hover .card-close-btn, .card-close-btn:focus {
            opacity: 1;
        }
        .card-close-btn:hover {
            background: #da3633;
            color: #ffffff;
            border-color: #f85149;
            transform: scale(1.1);
        }
        .layer-badge-btn {
            font-size: 11px;
            color: #58a6ff;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 3px 10px;
            margin-top: 6px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            transition: all 0.2s ease;
            user-select: none;
        }
        .layer-badge-btn:hover {
            background: #1f6feb22;
            border-color: #58a6ff;
            color: #79c0ff;
            transform: scale(1.05);
        }
        
        .img-container {
            width: 100%;
            aspect-ratio: 1;
            margin-bottom: 10px;
            background: #000;
            border-radius: 4px;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }
        .img-container img {
            position: absolute;
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }
        
        /* 真彩图层：保留原图 RGB 自然色相与消隐后的 alpha 通道 */
        .layer-red, .layer-green, .layer-blue {
            /* 保持原色呈现 */
        }
        
        .card.selected-asset {
            border-color: #388bfd !important;
            box-shadow: 0 0 14px rgba(56, 139, 253, 0.45) !important;
        }
        .card.card-disabled {
            opacity: 0.48;
            border-style: dashed;
            border-color: #6e7681;
        }
        .card.card-disabled:hover {
            opacity: 0.78;
        }
        .badge-disabled {
            background: rgba(218, 54, 51, 0.22);
            color: #ff7b72;
            border: 1px solid rgba(248, 81, 73, 0.45);
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 10px;
            display: inline-block;
        }
        .card-disabled-badge {
            position: absolute;
            top: 7px;
            right: 32px;
            z-index: 5;
            pointer-events: none;
        }
        .filter-pills {
            display: inline-flex;
            gap: 4px;
            background: #0d1117;
            padding: 3px;
            border-radius: 8px;
            border: 1px solid #30363d;
        }
        .filter-pill {
            flex: none;
            white-space: nowrap;
            background: transparent;
            color: #8b949e;
            border: none;
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .filter-pill:hover {
            background: #161b22;
            color: #c9d1d9;
        }
        .filter-pill.active {
            background: #21262d;
            color: #58a6ff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        }
        .btn-disable-batch {
            background: #6e7681;
            color: #fff;
            border: 1px solid rgba(248, 81, 73, 0.35);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-weight: bold;
            transition: all 0.2s;
        }
        .btn-disable-batch:hover {
            background: #b62324;
        }
        .btn-enable-batch {
            background: #1f6feb;
            color: #fff;
            border: 1px solid rgba(88, 166, 255, 0.35);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-weight: bold;
            transition: all 0.2s;
        }
        .btn-enable-batch:hover {
            background: #388bfd;
        }
        .btn-toggle-disabled {
            background: #6e7681;
            color: #fff;
            border: 1px solid rgba(255,255,255,0.12);
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }
        .btn-toggle-disabled:hover {
            background: #8b949e;
        }
        .btn-toggle-disabled.is-disabled-state {
            background: #238636;
        }
        .btn-toggle-disabled.is-disabled-state:hover {
            background: #2ea043;
        }
        .card-status-badge {
            position: absolute;
            top: 7px;
            left: 7px;
            z-index: 5;
            pointer-events: none;
        }
        .badge-truecolor {
            background: rgba(35, 134, 54, 0.2);
            color: #3fb950;
            border: 1px solid rgba(63, 185, 80, 0.4);
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 10px;
            display: inline-block;
        }
        .badge-grayscale {
            background: rgba(139, 148, 158, 0.15);
            color: #8b949e;
            border: 1px solid rgba(139, 148, 158, 0.3);
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 10px;
            display: inline-block;
        }
        
        .btn-reslice {
            background: #238636;
            color: #fff;
            border: 1px solid rgba(255,255,255,0.1);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-weight: bold;
            transition: all 0.2s;
        }
        .btn-reslice:hover {
            background: #2ea043;
            transform: scale(1.02);
        }
        .btn-reslice:disabled {
            background: #30363d;
            color: #8b949e;
            cursor: not-allowed;
            transform: none;
        }

        .btn-reslice-secondary {
            background: #21262d;
            color: #7ee787;
            border: 1px solid #238636;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-weight: 500;
            transition: all 0.2s;
        }
        .btn-reslice-secondary:hover {
            background: #23863622;
            border-color: #3fb950;
            color: #aff5b4;
            transform: scale(1.02);
        }
        .btn-reslice-secondary:disabled {
            background: #30363d;
            color: #8b949e;
            cursor: not-allowed;
            transform: none;
        }

        @keyframes spin-anim {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .img-spinner-overlay {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(13, 17, 23, 0.78);
            backdrop-filter: blur(2px);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 8px;
            z-index: 25;
            border-radius: 4px;
            pointer-events: none;
        }
        .spinner-circle {
            width: 28px;
            height: 28px;
            border: 3px solid rgba(88, 166, 255, 0.25);
            border-top: 3px solid #58a6ff;
            border-radius: 50%;
            animation: spin-anim 0.8s linear infinite;
        }
        .spinner-mini {
            display: inline-block;
            width: 12px;
            height: 12px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-top: 2px solid #ffffff;
            border-radius: 50%;
            animation: spin-anim 0.8s linear infinite;
            vertical-align: -1px;
            margin-right: 6px;
        }
        .spinner-text {
            font-size: 11px;
            color: #58a6ff;
            font-weight: 600;
            letter-spacing: 0.5px;
        }
        .reslicing-locked .card {
            pointer-events: none !important;
            cursor: not-allowed !important;
            user-select: none !important;
            filter: brightness(0.9);
        }
        .card.is-upgrading {
            border-color: #58a6ff !important;
            box-shadow: 0 0 16px rgba(88, 166, 255, 0.4) !important;
        }

        .layer-control-card {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 10px 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 6px;
        }
        .layer-control-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .slider-row {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 11px;
            color: #8b949e;
        }
        .slider-row label {
            width: 52px;
            flex: none;
        }
        .slider-row input[type="range"] {
            flex: 1;
            height: 4px;
            accent-color: #58a6ff;
            cursor: pointer;
        }
        .slider-row .slider-val {
            width: 42px;
            text-align: right;
            color: #c9d1d9;
            font-family: monospace;
            font-size: 11px;
        }
        .btn-reset-filter {
            background: #21262d;
            color: #8b949e;
            border: 1px solid #30363d;
            border-radius: 4px;
            padding: 2px 6px;
            font-size: 11px;
            cursor: pointer;
        }
        .btn-reset-filter:hover {
            background: #30363d;
            color: #c9d1d9;
        }
        
        .card .title {
            font-size: 13px;
            font-weight: bold;
            color: #c9d1d9;
            margin-bottom: 5px;
            word-break: break-all;
            text-align: center;
        }
        .card .subtitle {
            font-size: 11px;
            color: #8b949e;
            margin: 0;
        }
        
        .action-row {
            display: flex;
            gap: 8px;
            width: 100%;
            margin-top: 10px;
        }
        
        button {
            padding: 8px;
            background-color: #238636;
            color: #fff;
            border: none;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
            cursor: pointer;
            transition: background-color 0.2s;
        }
        .action-row button {
            flex: 1;
        }
        button:hover { background-color: #2ea043; }
        button:disabled { background-color: #30363d; color: #8b949e; cursor: not-allowed; }
        
        .btn-danger { background-color: #da3633; }
        .btn-danger:hover { background-color: #f85149; }
        .btn-restore { background-color: #1f6feb; }
        .btn-restore:hover { background-color: #388bfd; }
        
        .tab-header {
            display: inline-flex;
            gap: 4px;
            background: #0d1117;
            padding: 4px;
            border-radius: 8px;
            border: 1px solid #30363d;
        }
        .tab-btn {
            flex: none;
            white-space: nowrap;
            background: transparent;
            color: #8b949e;
            border: none;
            border-radius: 6px;
            padding: 6px 14px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s;
            line-height: 1.4;
        }
        .tab-btn:hover {
            background: #161b22;
            color: #c9d1d9;
        }
        .tab-btn.active {
            background: #21262d;
            color: #58a6ff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        }
        .badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: #30363d;
            color: #8b949e;
            border-radius: 10px;
            padding: 1px 7px;
            font-size: 11px;
            font-weight: 600;
        }
        .tab-btn.active .badge {
            background: #388bfd26;
            color: #58a6ff;
        }
        
        .empty-state {
            color: #8b949e; font-size: 14px; grid-column: 1/-1; text-align: center; margin-top: 50px;
        }
        
        /* Modal General */
        .modal-overlay {
            display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.85); z-index: 100;
            align-items: center; justify-content: center;
            backdrop-filter: blur(4px);
        }
        .modal-content {
            background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 30px; width: 400px;
        }
        input[type="text"] {
            width: 100%; padding: 10px; background: #0d1117; border: 1px solid #30363d;
            color: #c9d1d9; border-radius: 6px; box-sizing: border-box; margin-top: 10px;
        }

        /* Layer Inspector Modal */
        .layer-modal-content {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 24px;
            width: 960px;
            max-width: 95vw;
            max-height: 88vh;
            display: flex;
            flex-direction: column;
            box-shadow: 0 16px 48px rgba(0,0,0,0.8);
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #30363d;
            padding-bottom: 12px;
            margin-bottom: 16px;
        }
        .modal-close-btn {
            background: transparent;
            border: none;
            color: #8b949e;
            font-size: 24px;
            line-height: 1;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 4px;
        }
        .modal-close-btn:hover {
            color: #fff;
            background: #30363d;
        }
        .layer-modal-body {
            display: flex;
            gap: 24px;
            overflow-y: auto;
            flex: 1;
        }
        .layer-composite-pane {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .layer-slices-pane {
            flex: 1.4;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .pane-title {
            font-size: 13px;
            font-weight: 600;
            color: #c9d1d9;
        }
        .checkerboard-box {
            width: 100%;
            aspect-ratio: 1;
            background-color: #05070a;
            background-image: linear-gradient(45deg, #11161d 25%, transparent 25%),
                              linear-gradient(-45deg, #11161d 25%, transparent 25%),
                              linear-gradient(45deg, transparent 75%, #11161d 75%),
                              linear-gradient(-45deg, transparent 75%, #11161d 75%);
            background-size: 16px 16px;
            background-position: 0 0, 0 8px, 8px -8px, -8px 0px;
            border: 1px solid #30363d;
            border-radius: 8px;
            position: relative;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .checkerboard-box img {
            position: absolute;
            max-width: 90%;
            max-height: 90%;
            object-fit: contain;
            transition: opacity 0.15s;
        }
        .slices-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
            gap: 12px;
            overflow-y: auto;
            padding-right: 4px;
        }
        .slice-card {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 8px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 6px;
        }
        .slice-img-wrap {
            width: 100%;
            aspect-ratio: 1;
            background: #000;
            border-radius: 4px;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }
        .slice-img-wrap img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }
        .slice-tag {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 600;
        }
        .tag-red { background: #da363322; color: #ff7b72; border: 1px solid #da363355; }
        .tag-green { background: #23863622; color: #7ee787; border: 1px solid #23863655; }
        .tag-blue { background: #1f6feb22; color: #79c0ff; border: 1px solid #1f6feb55; }
        .tag-default { background: #30363d; color: #c9d1d9; }

        /* 360° Turntable Workbench Badges & Native Modal Dialog */
        .badge-v36 {
            background: rgba(242, 204, 96, 0.22);
            color: #f2cc60;
            border: 1px solid rgba(242, 204, 96, 0.55);
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 10px;
            display: inline-block;
            font-weight: 600;
        }
        .btn-v36-open {
            background: rgba(242, 204, 96, 0.14);
            color: #f2cc60;
            border: 1px solid rgba(242, 204, 96, 0.4);
            border-radius: 12px;
            font-size: 11px;
            padding: 3px 10px;
            cursor: pointer;
            margin-top: 4px;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .btn-v36-open:hover {
            background: rgba(242, 204, 96, 0.28);
            border-color: #f2cc60;
        }

        dialog#video36Dialog {
            margin: auto;
            padding: 22px;
            width: min(980px, 95vw);
            max-height: 90dvh;
            background: #161b22;
            color: #c9d1d9;
            border: 1px solid #30363d;
            border-radius: 12px;
            box-shadow: 0 20px 56px rgba(0, 0, 0, 0.85);
            flex-direction: column;
            gap: 14px;
            opacity: 0;
            transition-property: opacity, transform, display, overlay;
            transition-duration: 0.26s;
            transition-timing-function: ease-out;
            transition-behavior: allow-discrete;
        }
        dialog#video36Dialog:modal {
            overscroll-behavior: contain;
        }
        dialog#video36Dialog::backdrop {
            background-color: rgba(0, 0, 0, 0);
            overflow: hidden;
            overscroll-behavior: contain;
            transition-property: background-color, display, overlay;
            transition-duration: 0.26s;
            transition-timing-function: ease-out;
            transition-behavior: allow-discrete;
        }
        @media (prefers-reduced-motion: no-preference) {
            dialog#video36Dialog {
                transform: scale(0.95);
            }
        }
        @media (prefers-reduced-motion: reduce) {
            dialog#video36Dialog,
            dialog#video36Dialog::backdrop {
                transition-duration: 0.1s;
            }
        }
        dialog#video36Dialog[open]:where(:not([data-closing])) {
            display: flex;
            opacity: 1;
            @starting-style {
                opacity: 0;
            }
            @media (prefers-reduced-motion: no-preference) {
                transform: scale(1);
                @starting-style {
                    transform: scale(0.95);
                }
            }
            &::backdrop {
                background-color: rgba(0, 0, 0, 0.78);
                @starting-style {
                    background-color: rgba(0, 0, 0, 0);
                }
            }
        }
        .v36-body {
            display: flex;
            gap: 20px;
            overflow-y: auto;
            flex: 1;
            min-height: 0;
        }
        .v36-left {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        .v36-right {
            flex: 1.25;
            display: flex;
            flex-direction: column;
            gap: 10px;
            min-width: 0;
        }
        .v36-stage-tabs {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }
        .v36-tab-btn {
            background: #0d1117;
            color: #8b949e;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 5px 10px;
            font-size: 11px;
            cursor: pointer;
        }
        .v36-tab-btn.active {
            background: #21262d;
            color: #f2cc60;
            border-color: #f2cc60;
        }
        .v36-preview-frame {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            min-height: 290px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 10px;
            gap: 8px;
        }
        .v36-preview-frame img {
            max-width: 100%;
            max-height: 300px;
            border-radius: 6px;
            object-fit: contain;
        }
        .v36-spin-controls {
            display: flex;
            align-items: center;
            gap: 10px;
            width: 100%;
            font-size: 12px;
        }
        .v36-spin-controls input[type="range"] {
            flex: 1;
            accent-color: #f2cc60;
        }
        .v36-metrics-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
        }
        .v36-metric-card {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 8px;
            font-size: 11px;
        }
        .v36-metric-card .lbl { color: #8b949e; }
        .v36-metric-card .val { color: #c9d1d9; font-weight: 700; font-size: 13px; margin-top: 2px; font-family: monospace; }
        .v36-config-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 10px;
            font-size: 12px;
        }
        .v36-config-grid select {
            width: 100%;
            background: #161b22;
            color: #c9d1d9;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 5px 8px;
            margin-top: 4px;
        }
        .v36-run-row {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .v36-flow-box {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            font-size: 12px;
        }
        .v36-flow-row {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            align-items: center;
        }
        .v36-flow-btn {
            background: #161b22;
            color: #c9d1d9;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 4px 9px;
            font-size: 11px;
            cursor: pointer;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .v36-flow-btn:hover {
            border-color: #58a6ff;
            color: #ffffff;
        }
        .v36-flow-btn.is-ready {
            border-color: rgba(62, 180, 95, 0.7);
            color: #7ee787;
        }
        .v36-log-box {
            background: #05070a;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 8px 10px;
            font-family: monospace;
            font-size: 11px;
            line-height: 1.45;
            color: #8b949e;
            height: 150px;
            overflow-y: auto;
            white-space: pre-wrap;
            margin: 0;
        }
    </style>
</head>
<body>
    <div class="layout">
        <!-- Left Panel: Candidates & Trash -->
        <div class="panel">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #30363d; padding-bottom:12px; margin-bottom:15px;">
                <div class="tab-header">
                    <button id="tabCandidatesBtn" class="tab-btn active" onclick="switchLeftTab('candidates')">📥 备选审批区 <span id="candidateCount" class="badge">0</span></button>
                    <button id="tabTrashBtn" class="tab-btn" onclick="switchLeftTab('trash')">🗑️ 回收区 <span id="trashCount" class="badge">0</span></button>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span id="confirmedCandidateBadge" class="badge" style="background:#f2cc6022; color:#f2cc60; border:1px solid #f2cc6044;">已确认: <span id="confirmedCount">0</span></span>
                    <button id="emptyTrashBtn" class="btn-danger" style="display:none; padding:6px 12px; flex:none; font-size:12px;" onclick="purgeAllTrash()">🧹 一键清空</button>
                </div>
            </div>
            <p class="subtitle" id="leftSubtitle">单击植物图片可切换【已确认】（金色高亮）；后台持久化记录，自动化脚本可精准读取处理。</p>
            <div class="gallery" id="candidatesGallery">
                <div class="empty-state">正在加载...</div>
            </div>
            <div class="gallery" id="trashGallery" style="display:none;">
                <div class="empty-state">项目回收区为空</div>
            </div>
        </div>
        
        <!-- Right Panel: Gallery -->
        <div class="panel">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #30363d; padding-bottom:12px; margin-bottom:15px; flex-wrap:wrap; gap:8px;">
                <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                    <h1 style="border:none; margin:0; padding:0; font-size:20px;">📦 资产画廊 (Asset Gallery)</h1>
                    <span class="badge" id="assetsTotalCount">0 启用 / 0 资产</span>
                    <div class="filter-pills" id="assetFilterPills">
                        <button type="button" class="filter-pill active" data-filter="all" onclick="setAssetFilter('all')">全部</button>
                        <button type="button" class="filter-pill" data-filter="enabled" onclick="setAssetFilter('enabled')">✅ 使用中</button>
                        <button type="button" class="filter-pill" data-filter="disabled" onclick="setAssetFilter('disabled')">🚫 不使用</button>
                        <button type="button" class="filter-pill" data-filter="video36" onclick="setAssetFilter('video36')">🔄 360° 转盘</button>
                    </div>
                </div>
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    <button id="batchDisableBtn" class="btn-disable-batch" style="display:none;" onclick="setSelectedAssetsEnabled(false)">🚫 设为不使用 (<span id="selectedDisableCount">0</span>)</button>
                    <button id="batchEnableBtn" class="btn-enable-batch" style="display:none;" onclick="setSelectedAssetsEnabled(true)">✅ 恢复使用 (<span id="selectedEnableCount">0</span>)</button>
                    <button id="batchResliceBtn" class="btn-reslice" style="display:none; padding:6px 14px; font-size:12px;" onclick="resliceSelectedGrayscaleAssets()">🎨 升级真彩 (<span id="selectedGrayscaleCount">0</span>)</button>
                </div>
            </div>
            <p class="subtitle">已切片入库资产；单击卡片图片可选中，点击图层标牌可拆解检视各通道并实时调色。</p>
            <div class="gallery" id="assetsGallery">
                <div class="empty-state">正在加载...</div>
            </div>
        </div>
    </div>
    
    <!-- Modal: Process -->
    <div id="processModal" class="modal-overlay">
        <div class="modal-content" style="position:relative;">
            <div id="processModalSpinner" class="img-spinner-overlay" style="display:none; border-radius:8px;">
                <div class="spinner-circle"></div>
                <span class="spinner-text">正在智能切片打包入库...</span>
            </div>
            <h2 style="margin-top:0; color:#58a6ff;">入库打包</h2>
            <p style="color:#8b949e; font-size:13px; margin:0 0 4px 0;">1. 英文资产代号 (必填，唯一标识):</p>
            <input type="text" id="plantNameInput" placeholder="例如: bamboo / glowing_mushroom" />
            <p style="color:#8b949e; font-size:13px; margin:14px 0 4px 0;">2. 中文植物别名 (选填，展示名):</p>
            <input type="text" id="plantTitleInput" placeholder="例如: 翠竹 / 幽光蘑菇" />
            <input type="hidden" id="candidateFilename" />
            
            <div style="display:flex; gap:10px; margin-top:20px;">
                <button id="cancelBtn" style="background:#21262d; flex:1;">取消</button>
                <button id="confirmBtn" style="flex:1;">自动切片并入库</button>
            </div>
        </div>
    </div>

    <!-- Modal: Layer Inspector -->
    <div id="layerModal" class="modal-overlay">
        <div class="layer-modal-content">
            <div class="modal-header">
                <div>
                    <h2 id="layerModalTitle" style="margin:0; color:#58a6ff; font-size:18px;">🌱 植物图层检视</h2>
                    <div id="layerModalSubtitle" style="color:#8b949e; font-size:12px; margin-top:4px;"></div>
                </div>
                <div style="display:flex; align-items:center; gap:10px;">
                    <button id="modalOpenVideo36Btn" class="btn-v36-open" style="font-size:12px; padding:6px 12px; margin-top:0;">🔄 36F工坊</button>
                    <button id="modalResliceAssetBtn" class="btn-reslice">🎨 重新切片(升级真彩)</button>
                    <button id="modalToggleEnabledBtn" class="btn-toggle-disabled" style="padding:6px 12px; border-radius:6px; font-size:12px;">🚫 设为不使用</button>
                    <button id="modalRemoveAssetBtn" class="btn-danger" style="padding:6px 12px; border-radius:6px; font-size:12px;">移出资产</button>
                    <button class="modal-close-btn" onclick="closeLayerModal()">&times;</button>
                </div>
            </div>
            <div class="layer-modal-body">
                <!-- Left: Composite Preview & Controls -->
                <div class="layer-composite-pane">
                    <div class="pane-title">🎨 实时合成预览 & 相对色相微调</div>
                    <div class="checkerboard-box" id="compositePreviewBox">
                        <!-- Layer images injected dynamically -->
                    </div>
                    <div style="margin-top:4px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                            <span style="font-size:12px; font-weight:bold; color:#8b949e;">分层显隐 & 色相调色盘:</span>
                            <button class="btn-reset-filter" onclick="resetAllLayerFilters()">↺ 重置全部通道</button>
                        </div>
                        <div id="layerToggleList" style="display:flex; flex-direction:column; gap:6px;"></div>
                    </div>
                    <div style="margin-top:4px;">
                        <div style="font-size:11px; color:#8b949e; margin-bottom:4px;">实时 CSS 滤镜输出 (点击可复制):</div>
                        <div id="combinedCssCode" style="background:#0d1117; border:1px solid #30363d; border-radius:6px; padding:6px 10px; font-family:monospace; font-size:11px; color:#58a6ff; cursor:pointer;" onclick="copyCombinedCss()" title="点击复制 CSS 代码">/* 保持原生色彩 */</div>
                    </div>
                </div>
                <!-- Right: Individual Slices -->
                <div class="layer-slices-pane">
                    <div class="pane-title">🧩 各图层切片展开 (点击“🔗 查看原图”可直接新窗口打开原PNG)</div>
                    <div class="slices-grid" id="layerSlicesGrid">
                        <!-- Slices injected dynamically -->
                    </div>
                    <!-- AI Botanical Analysis Card -->
                    <div id="layerAiAnalysisCard" style="margin-top:16px;"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal: 360° Video Turntable Workbench (native modal dialog) -->
    <dialog id="video36Dialog" closedby="any" aria-labelledby="video36DialogTitle">
        <div class="modal-header" style="margin-bottom:0;">
            <div>
                <h2 id="video36DialogTitle" style="margin:0; color:#f2cc60; font-size:18px;">🔄 360° 立体转盘工坊</h2>
                <div id="v36Subtitle" style="color:#8b949e; font-size:12px; margin-top:4px;"></div>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
                <button type="button" id="v36PublishBtn" class="btn-reslice" onclick="toggleVideo36Publish()">🌐 发布到花园 36F</button>
                <button type="button" id="v36CloseBtn" class="modal-close-btn" aria-label="关闭 360° 工坊">&times;</button>
            </div>
        </div>
        <div class="v36-body">
            <div class="v36-left">
                <div class="v36-stage-tabs" id="v36ViewTabs">
                    <button type="button" class="v36-tab-btn active" data-view="turntable" onclick="setVideo36PreviewMode('turntable')">🔄 360° 动态转盘</button>
                    <button type="button" class="v36-tab-btn" data-view="iou" onclick="setVideo36PreviewMode('iou')">🪞 0°/180° 轮廓锁</button>
                    <button type="button" class="v36-tab-btn" data-view="sheet" onclick="setVideo36PreviewMode('sheet')">🧩 36帧拼版</button>
                    <button type="button" class="v36-tab-btn" data-view="raw" onclick="setVideo36PreviewMode('raw')">🎬 原视频抽帧</button>
                </div>
                <div class="v36-preview-frame" id="v36PreviewFrame">
                    <canvas id="v36Canvas" width="260" height="260" style="border-radius:6px; background:#6f664b;"></canvas>
                    <img id="v36StaticImg" style="display:none;" alt="360° 阶段预览" />
                    <div id="v36SpinControls" class="v36-spin-controls">
                        <button type="button" id="v36SpinPlayBtn" class="v36-tab-btn" onclick="toggleVideo36AutoSpin()">⏸ 暂停</button>
                        <input type="range" id="v36AngleSlider" min="0" max="350" step="10" value="0" oninput="onVideo36SliderChange(this.value)" />
                        <span id="v36AngleReadout" style="font-family:monospace; color:#f2cc60; min-width:56px; text-align:right;">F00 000°</span>
                    </div>
                    <label style="display:flex; align-items:center; gap:6px; font-size:12px; color:#8b949e; cursor:pointer; user-select:none;">
                        <input type="checkbox" id="v36ShowPivotChk" checked onchange="drawVideo36Frame()" />
                        <span>🎯 显示根部枢轴红点 & 占地椭圆</span>
                    </label>
                </div>
            </div>
            <div class="v36-right">
                <div class="v36-metrics-grid" id="v36MetricsGrid"></div>
                <div class="v36-config-grid">
                    <div>
                        <label style="color:#8b949e;">旋转模式 (--mode)</label>
                        <select id="v36ModeSelect">
                            <option value="2half">2half (常规 0→180→360 双半圈)</option>
                            <option value="single360">single360 (单段 360° Flow 旋转)</option>
                            <option value="4quarter">4quarter (宽幅四象限防压扁)</option>
                        </select>
                    </div>
                    <div>
                        <label style="color:#8b949e;">抠图策略 (--key-mode)</label>
                        <select id="v36KeyModeSelect">
                            <option value="auto">auto (自动识别白花/暗腔)</option>
                            <option value="pockets">pockets (穿透树冠镂空)</option>
                            <option value="strict_pockets">strict_pockets (保护白色花瓣)</option>
                            <option value="border_only">border_only (仅外圈)</option>
                        </select>
                    </div>
                    <label style="display:flex; align-items:center; gap:6px; cursor:pointer;">
                        <input type="checkbox" id="v36Mirror270Chk" />
                        <span>4quarter 镜像 Q0 导引 270°</span>
                    </label>
                    <label style="display:flex; align-items:center; gap:6px; cursor:pointer;">
                        <input type="checkbox" id="v36ForceChk" />
                        <span>强制覆盖重算 (--force)</span>
                    </label>
                </div>
                <div class="v36-flow-box">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
                        <span style="font-weight:600; color:#58a6ff;">🎬 Google Flow 外部投喂 & MP4 回流</span>
                        <label style="display:flex; align-items:center; gap:5px; font-size:11px; color:#8b949e; cursor:pointer;">
                            <input type="checkbox" id="v36AutoAtlasChk" checked />
                            <span>传齐后自动切片 Atlas (草稿态)</span>
                        </label>
                    </div>
                    <div class="v36-flow-row">
                        <a id="v36DlFrontBtn" class="v36-flow-btn" download="000_front.png" href="#">⬇ 0° 正面图</a>
                        <a id="v36DlRearBtn" class="v36-flow-btn" download="180_rear.png" href="#">⬇ 180° 背面图</a>
                        <button type="button" class="v36-flow-btn" onclick="copyFlowPrompt('half_a')">📋 复制 Half A 提示词</button>
                        <button type="button" class="v36-flow-btn" onclick="copyFlowPrompt('half_b')">📋 复制 Half B 提示词</button>
                        <button type="button" class="v36-flow-btn" onclick="copyFlowPrompt('single_360')">📋 复制 单段 360° 提示词</button>
                    </div>
                    <div class="v36-flow-row">
                        <label class="v36-flow-btn" id="v36LblHalfA" style="cursor:pointer;">
                            ⬆ Half A (.mp4) <span id="v36SlotHalfA">未上传</span>
                            <input type="file" accept="video/mp4" style="display:none;" onchange="uploadVideo36Mp4('half_a', this)" />
                        </label>
                        <label class="v36-flow-btn" id="v36LblHalfB" style="cursor:pointer;">
                            ⬆ Half B (.mp4) <span id="v36SlotHalfB">未上传</span>
                            <input type="file" accept="video/mp4" style="display:none;" onchange="uploadVideo36Mp4('half_b', this)" />
                        </label>
                        <label class="v36-flow-btn" id="v36LblSingle360" style="cursor:pointer;">
                            ⬆ 单段 360° (.mp4) <span id="v36SlotSingle360">未上传</span>
                            <input type="file" accept="video/mp4" style="display:none;" onchange="uploadVideo36Mp4('single_360', this)" />
                        </label>
                    </div>
                </div>
                <div class="v36-run-row">
                    <button type="button" class="btn-restore" onclick="runVideo36Stage('prep')">1. Prep (备图+轮廓锁)</button>
                    <button type="button" class="btn-restore" onclick="runVideo36Stage('video')">2. Video (生成视频)</button>
                    <button type="button" class="btn-restore" onclick="runVideo36Stage('atlas')">3. Atlas (切片+枢轴锁)</button>
                    <button type="button" class="btn-reslice" onclick="runVideo36Stage('all')">🚀 一键 All</button>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:12px; color:#8b949e;">实时执行日志 & 状态: <strong id="v36JobStatusText" style="color:#c9d1d9;">空闲</strong></span>
                </div>
                <pre id="v36LogBox" class="v36-log-box">等待选择操作阶段...</pre>
            </div>
        </div>
    </dialog>

    <script>
        let currentLeftTab = 'candidates';
        let confirmedCandidates = {};
        let selectedAssets = {};
        let allAssets = [];
        let video36ById = {};
        let currentAssetFilter = 'all';
        let activeModalVariant = null;
        let modalLayerFilters = [];
        let isReslicingInProgress = false;

        function setReslicingState(inProgress, targetKeys = []) {
            isReslicingInProgress = inProgress;
            const aGal = document.getElementById('assetsGallery');
            if (aGal) {
                if (inProgress) {
                    aGal.classList.add('reslicing-locked');
                } else {
                    aGal.classList.remove('reslicing-locked');
                }
            }

            if (!inProgress) {
                document.querySelectorAll('.img-spinner-overlay').forEach(el => el.remove());
                document.querySelectorAll('.card.is-upgrading').forEach(el => el.classList.remove('is-upgrading'));
                return;
            }

            targetKeys.forEach(key => {
                const card = document.querySelector(`.card[data-asset-key="${key}"]`);
                if (card) {
                    card.classList.add('is-upgrading');
                    const imgWrap = card.querySelector('.img-container');
                    if (imgWrap && !imgWrap.querySelector('.img-spinner-overlay')) {
                        const overlay = document.createElement('div');
                        overlay.className = 'img-spinner-overlay';
                        overlay.innerHTML = `
                            <div class="spinner-circle"></div>
                            <span class="spinner-text">真彩升级中...</span>
                        `;
                        imgWrap.appendChild(overlay);
                    }
                }
            });
        }

        function toggleSelectAssetCard(plantName, variantId, isTrueColor, title, card) {
            if (isReslicingInProgress) return;
            const key = `${plantName}__${variantId}`;
            if (selectedAssets[key]) {
                delete selectedAssets[key];
                card.classList.remove('selected-asset');
            } else {
                selectedAssets[key] = {
                    plant_name: plantName,
                    variant_id: variantId,
                    true_color: isTrueColor,
                    title: title
                };
                card.classList.add('selected-asset');
            }
            updateSelectedAssetsUI();
        }

        function updateSelectedAssetsUI() {
            const allSelected = Object.values(selectedAssets);
            const grayscaleSelected = allSelected.filter(a => !a.true_color);
            const countEl = document.getElementById('selectedGrayscaleCount');
            const batchBtn = document.getElementById('batchResliceBtn');
            if (countEl) countEl.innerText = grayscaleSelected.length;
            if (batchBtn) {
                batchBtn.style.display = grayscaleSelected.length > 0 ? 'inline-flex' : 'none';
            }

            const disableBtn = document.getElementById('batchDisableBtn');
            const enableBtn = document.getElementById('batchEnableBtn');
            const disableCountEl = document.getElementById('selectedDisableCount');
            const enableCountEl = document.getElementById('selectedEnableCount');
            if (disableCountEl) disableCountEl.innerText = allSelected.length;
            if (enableCountEl) enableCountEl.innerText = allSelected.length;
            if (disableBtn) disableBtn.style.display = allSelected.length > 0 ? 'inline-flex' : 'none';
            if (enableBtn) enableBtn.style.display = allSelected.length > 0 ? 'inline-flex' : 'none';

            document.querySelectorAll('.card[data-asset-key]').forEach(card => {
                const key = card.getAttribute('data-asset-key');
                if (selectedAssets[key]) {
                    card.classList.add('selected-asset');
                } else {
                    card.classList.remove('selected-asset');
                }
            });
        }

        function setAssetFilter(filter) {
            currentAssetFilter = filter || 'all';
            document.querySelectorAll('#assetFilterPills .filter-pill').forEach(btn => {
                if (btn.getAttribute('data-filter') === currentAssetFilter) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
            renderAssetsGallery();
        }

        function setSelectedAssetsEnabled(enabled) {
            if (isReslicingInProgress) return;
            const items = Object.values(selectedAssets).map(a => ({
                plant_name: a.plant_name,
                variant_id: a.variant_id
            }));
            if (items.length === 0) return;

            const disableBtn = document.getElementById('batchDisableBtn');
            const enableBtn = document.getElementById('batchEnableBtn');
            if (disableBtn) disableBtn.disabled = true;
            if (enableBtn) enableBtn.disabled = true;

            fetch('/api/set_assets_enabled', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ items: items, enabled: !!enabled })
            }).then(res => res.json()).then(res => {
                if (res.status === 'success') {
                    selectedAssets = {};
                    updateSelectedAssetsUI();
                    loadAssets();
                } else {
                    alert('❌ 批量更新状态失败: ' + (res.error || '未知错误'));
                }
            }).catch(err => {
                alert('网络错误: ' + err);
            }).finally(() => {
                if (disableBtn) disableBtn.disabled = false;
                if (enableBtn) enableBtn.disabled = false;
            });
        }

        function resliceSelectedGrayscaleAssets() {
            if (isReslicingInProgress) return;
            const grayscaleItems = Object.values(selectedAssets).filter(a => !a.true_color);
            if (grayscaleItems.length === 0) return;
            if (!confirm(`确定将选中的 ${grayscaleItems.length} 个灰度资产升级为真彩切片吗？\n（已是真彩的资产会自动跳过）`)) {
                return;
            }
            const btn = document.getElementById('batchResliceBtn');
            const origHtml = btn.innerHTML;
            btn.innerText = `⏳ 正在升级 0/${grayscaleItems.length}...`;
            btn.disabled = true;

            const targetKeys = grayscaleItems.map(item => `${item.plant_name}__${item.variant_id}`);
            setReslicingState(true, targetKeys);

            fetch('/api/reslice_batch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ items: grayscaleItems })
            }).then(res => res.json()).then(res => {
                if (res.status === 'success') {
                    alert(`🎉 升级完成！成功: ${res.succeeded} 个，失败: ${res.failed} 个。`);
                    window.location.reload();
                } else {
                    alert('❌ 批量升级失败: ' + (res.error || ''));
                    setReslicingState(false);
                }
            }).catch(err => {
                alert('网络错误: ' + err);
                setReslicingState(false);
            })
            .finally(() => {
                btn.innerHTML = origHtml;
                btn.disabled = false;
                updateSelectedAssetsUI();
            });
        }

        function resliceSingleAsset(plantName, variantId, titleText) {
            if (isReslicingInProgress) return;
            if (!confirm(`确定要将 [${titleText}] (Variant: ${variantId}) 升级切片为真彩模式吗？\n系统将自动匹配原始图像，保留 RGB 色彩并执行边缘去白边消隐。`)) {
                return;
            }
            const btn = document.getElementById('modalResliceAssetBtn');
            if (btn) {
                btn.innerText = '正在真彩切片...';
                btn.disabled = true;
            }

            const assetKey = `${plantName}__${variantId}`;
            setReslicingState(true, [assetKey]);

            const modalBox = document.getElementById('compositePreviewBox');
            if (modalBox && !modalBox.querySelector('.img-spinner-overlay')) {
                const overlay = document.createElement('div');
                overlay.className = 'img-spinner-overlay';
                overlay.innerHTML = `
                    <div class="spinner-circle"></div>
                    <span class="spinner-text">真彩升级中...</span>
                `;
                modalBox.appendChild(overlay);
            }

            fetch('/api/reslice_asset', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ plant_name: plantName, variant_id: variantId })
            }).then(res => res.json()).then(res => {
                if (res.status === 'success') {
                    alert('🎉 升级成功！已转为真彩高保真切片。');
                    window.location.reload();
                } else {
                    alert('❌ 升级失败: ' + (res.error || '未知错误'));
                    setReslicingState(false);
                }
            }).catch(err => {
                alert('网络错误: ' + err);
                setReslicingState(false);
            })
            .finally(() => {
                if (btn) {
                    btn.disabled = false;
                }
            });
        }

        function switchLeftTab(tab) {
            currentLeftTab = tab;
            const cGal = document.getElementById('candidatesGallery');
            const tGal = document.getElementById('trashGallery');
            const cBtn = document.getElementById('tabCandidatesBtn');
            const tBtn = document.getElementById('tabTrashBtn');
            const emptyBtn = document.getElementById('emptyTrashBtn');
            const confBadge = document.getElementById('confirmedCandidateBadge');
            const subtitle = document.getElementById('leftSubtitle');

            if (tab === 'candidates') {
                cGal.style.display = 'grid';
                tGal.style.display = 'none';
                cBtn.classList.add('active');
                tBtn.classList.remove('active');
                emptyBtn.style.display = 'none';
                if (confBadge) confBadge.style.display = 'inline-flex';
                subtitle.innerText = '单击植物图片可切换【已确认】（金色高亮）；后台持久化记录，自动化脚本可精准读取处理。';
            } else {
                cGal.style.display = 'none';
                tGal.style.display = 'grid';
                cBtn.classList.remove('active');
                tBtn.classList.add('active');
                emptyBtn.style.display = 'block';
                if (confBadge) confBadge.style.display = 'none';
                subtitle.innerText = '已移入回收区的草稿，可点击恢复重新待审，或彻底抹除。';
            }
        }

        function loadData() {
            // Load Candidates & Confirmed concurrently
            Promise.all([
                fetch('/api/candidates').then(res => res.json()),
                fetch('/api/confirmed_candidates').then(res => res.json())
            ]).then(([candidates, confirmed]) => {
                confirmedCandidates = confirmed || {};
                document.getElementById('candidateCount').innerText = candidates.length;
                document.getElementById('confirmedCount').innerText = Object.keys(confirmedCandidates).length;
                
                const cGal = document.getElementById('candidatesGallery');
                cGal.innerHTML = '';
                if (candidates.length === 0) {
                    cGal.innerHTML = '<div class="empty-state">暂无备选图，前往聊天框输入 /plant 召唤 Agent 画图</div>';
                    return;
                }
                candidates.forEach(img => {
                    const isConfirmed = !!confirmedCandidates[img];
                    const card = document.createElement('div');
                    card.className = 'card' + (isConfirmed ? ' confirmed' : '');
                    card.style.cursor = 'pointer';
                    card.title = '单击切换【已确认】选中状态';
                    card.innerHTML = `
                        <div class="confirmed-star" style="display:${isConfirmed ? 'block' : 'none'};">★ 已确认</div>
                        <div class="img-container"><img src="/tools/candidates/${img}" /></div>
                        <div class="title">${img}</div>
                        <div class="action-row">
                            <button onclick="event.stopPropagation(); openModal('${img}')">入库</button>
                            <button class="btn-danger" onclick="event.stopPropagation(); deleteCandidate('${img}')">删除</button>
                        </div>
                    `;
                    card.onclick = () => toggleConfirmCandidate(img, card);
                    cGal.appendChild(card);
                });
            }).catch(err => {
                console.error("加载备选图失败:", err);
            });

            // Load Trash
            fetch('/api/trash')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('trashCount').innerText = data.length;
                    const tGal = document.getElementById('trashGallery');
                    tGal.innerHTML = '';
                    if (data.length === 0) {
                        tGal.innerHTML = '<div class="empty-state">项目回收区为空</div>';
                        return;
                    }
                    data.forEach(img => {
                        const card = document.createElement('div');
                        card.className = 'card';
                        card.innerHTML = `
                            <div class="img-container"><img src="/tools/trash/${img}" /></div>
                            <div class="title">${img}</div>
                            <div class="action-row">
                                <button class="btn-restore" onclick="restoreTrash('${img}')">恢复</button>
                                <button class="btn-danger" onclick="purgeTrash('${img}')">彻底抹除</button>
                            </div>
                        `;
                        tGal.appendChild(card);
                    });
                });
                
            // Load Assets
            loadAssets();
        }

        function loadVideo36List() {
            return fetch('/api/video36/list')
                .then(res => res.json())
                .then(data => {
                    video36ById = {};
                    const items = Array.isArray(data && data.items) ? data.items : [];
                    items.forEach(item => {
                        if (item && item.plant_id) {
                            video36ById[item.plant_id] = item;
                        }
                    });
                    renderAssetsGallery();
                    if (activeVideo36PlantId && video36ById[activeVideo36PlantId]) {
                        renderVideo36DialogContent(video36ById[activeVideo36PlantId]);
                    }
                }).catch(err => {
                    console.error("加载 360° 转盘列表失败:", err);
                });
        }

        function loadAssets() {
            return Promise.all([
                fetch('/api/assets').then(res => res.json()),
                fetch('/api/video36/list').then(res => res.json()).catch(() => ({ items: [] })),
            ]).then(([assets, v36Data]) => {
                allAssets = Array.isArray(assets) ? assets : [];
                video36ById = {};
                const v36Items = Array.isArray(v36Data && v36Data.items) ? v36Data.items : [];
                v36Items.forEach(item => {
                    if (item && item.plant_id) video36ById[item.plant_id] = item;
                });
                const enabledCount = allAssets.filter(v => v.enabled !== false).length;
                document.getElementById('assetsTotalCount').innerText = `${enabledCount} 启用 / ${allAssets.length} 资产`;
                renderAssetsGallery();
                if (allAssets.some(v => v.status === 'ai_analyzing')) {
                    startAiStatusPolling();
                }
            }).catch(err => {
                console.error("加载资产失败:", err);
            });
        }

        function renderAssetsGallery() {
            const aGal = document.getElementById('assetsGallery');
            aGal.innerHTML = '';
            if (allAssets.length === 0) {
                aGal.innerHTML = '<div class="empty-state">资产库为空</div>';
                updateSelectedAssetsUI();
                return;
            }

            const filteredAssets = allAssets.filter(variant => {
                const isDisabled = (variant.enabled === false);
                const v36 = video36ById[variant.plant_name];
                if (currentAssetFilter === 'enabled') return !isDisabled;
                if (currentAssetFilter === 'disabled') return isDisabled;
                if (currentAssetFilter === 'video36') return Boolean(v36 && (v36.has_atlas || v36.has_prep || v36.has_video));
                return true;
            });

            if (filteredAssets.length === 0) {
                aGal.innerHTML = '<div class="empty-state">当前筛选条件下无资产</div>';
                updateSelectedAssetsUI();
                return;
            }

            filteredAssets.forEach(variant => {
                const key = `${variant.plant_name}__${variant.variant_id}`;
                const isSelected = !!selectedAssets[key];
                const isDisabled = (variant.enabled === false);
                const card = document.createElement('div');
                card.className = 'card' + (isSelected ? ' selected-asset' : '') + (isDisabled ? ' card-disabled' : '');
                card.setAttribute('data-asset-key', key);
                card.style.cursor = 'pointer';

                let imagesHtml = '';
                let colorIndex = 0;
                const fallbackColors = ['layer-red', 'layer-green', 'layer-blue'];
                variant.layers.forEach(layer => {
                    let cssClass = '';
                    if (layer.includes('red') || layer.includes('flower') || layer.includes('cap') || layer.includes('leaves')) {
                        cssClass = 'layer-red';
                    } else if (layer.includes('green') || layer.includes('stem') || layer.includes('trunk')) {
                        cssClass = 'layer-green';
                    } else if (layer.includes('blue') || layer.includes('accent')) {
                        cssClass = 'layer-blue';
                    } else {
                        cssClass = fallbackColors[colorIndex % 3];
                        colorIndex++;
                    }

                    imagesHtml += `<img src="/assets/plants/${variant.path_prefix}/${layer}" class="${cssClass}" />`;
                });

                const hasAlias = variant.display_name && variant.display_name !== variant.plant_name;
                const titleHtml = hasAlias
                    ? `${variant.display_name} <span style="font-size:11px; color:#8b949e; font-weight:normal;">(${variant.plant_name})</span>`
                    : variant.plant_name;

                const variantDataStr = JSON.stringify(variant).replace(/"/g, '&quot;');
                const displayTitleStr = hasAlias ? `${variant.display_name} (${variant.plant_name})` : variant.plant_name;
                const isAnalyzing = (variant.status === 'ai_analyzing');
                let colorBadgeHtml = "";
                if (isAnalyzing) {
                    colorBadgeHtml = `<span class="badge-grayscale card-status-badge" style="background:#f39c12; color:#fff;" title="植物生物学 AI 分析中...">🤖 AI分析中...</span>`;
                } else {
                    colorBadgeHtml = variant.true_color
                        ? `<span class="badge-truecolor card-status-badge" title="真彩保留 RGB + 边缘消隐">✨ 真彩</span>`
                        : `<span class="badge-grayscale card-status-badge" title="旧版灰度提取">⚪ 灰度</span>`;
                }
                const disabledBadgeHtml = isDisabled
                    ? `<span class="badge-disabled card-disabled-badge" title="已在花园中停用，仅在造物工坊保留">🚫 不使用</span>`
                    : '';
                const v36Info = video36ById[variant.plant_name];
                const prepIouVal = v36Info && v36Info.prep_meta ? Number(v36Info.prep_meta.mirror_silhouette_iou) : null;
                const v36TagHtml = (v36Info && v36Info.has_atlas)
                    ? `<span class="badge-v36" style="margin-top:4px;" title="已生成 36 帧转盘 (${v36Info.published ? '已发布到花园' : '未发布'})">${v36Info.published ? '🔄 36F 已发布' : '🔄 36F 未发布'}</span>`
                    : ((v36Info && v36Info.has_prep)
                        ? `<span class="badge-v36" style="margin-top:4px; opacity:0.88; ${prepIouVal !== null && prepIouVal >= 0.78 ? 'color:#7ee787; border-color:rgba(62,180,95,0.5);' : ''}">🪞 Prep${prepIouVal !== null ? ' IoU ' + prepIouVal.toFixed(2) : ''}</span>`
                        : ((v36Info && v36Info.has_video) ? `<span class="badge-v36" style="margin-top:4px; opacity:0.75;">⏳ 36F 半成品</span>` : ''));
                const safePlantIdArg = String(variant.plant_name).replace(/'/g, "");

                card.innerHTML = `
                    ${colorBadgeHtml}
                    ${disabledBadgeHtml}
                    <button class="card-close-btn" title="移出资产并召回原图" onclick="event.stopPropagation(); removeAsset('${variant.plant_name}', '${variant.variant_id}', '${displayTitleStr.replace(/'/g, "\\'")}')">&times;</button>
                    <div class="img-container">${imagesHtml}</div>
                    <div class="title" style="margin-top:2px;">${titleHtml}</div>
                    <div class="subtitle" style="margin-top:2px;">Variant: ${variant.variant_id}</div>
                    ${v36TagHtml}
                    <div style="margin-top:6px; display:flex; flex-direction:column; align-items:center; gap:4px;">
                        <span class="layer-badge-btn" onclick="event.stopPropagation(); openLayerModal(${variantDataStr})">
                            🧩 ${variant.layers.length} 个独立图层 ↗
                        </span>
                        <button type="button" class="btn-v36-open" onclick="event.stopPropagation(); openVideo36Workbench('${safePlantIdArg}')">
                            🔄 36F工坊
                        </button>
                    </div>
                `;

                card.onclick = () => {
                    if (isAnalyzing) {
                        openLayerModal(variant);
                    } else {
                        toggleSelectAssetCard(variant.plant_name, variant.variant_id, !!variant.true_color, displayTitleStr, card);
                    }
                };
                aGal.appendChild(card);
            });
            updateSelectedAssetsUI();
        }
        
        function toggleConfirmCandidate(filename, card) {
            fetch('/api/toggle_confirm_candidate', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ filename: filename })
            }).then(res => res.json()).then(res => {
                if (res.status === 'success') {
                    confirmedCandidates = res.confirmed || {};
                    const isConfirmed = res.is_confirmed;
                    if (isConfirmed) {
                        card.classList.add('confirmed');
                    } else {
                        card.classList.remove('confirmed');
                    }
                    const star = card.querySelector('.confirmed-star');
                    if (star) star.style.display = isConfirmed ? 'block' : 'none';
                    document.getElementById('confirmedCount').innerText = Object.keys(confirmedCandidates).length;
                } else {
                    alert('切换确认状态失败: ' + (res.error || ''));
                }
            }).catch(err => alert('网络错误: ' + err));
        }

        function updateModalToggleEnabledBtn(variant) {
            const toggleBtn = document.getElementById('modalToggleEnabledBtn');
            if (!toggleBtn || !variant) return;
            const isDisabled = (variant.enabled === false);
            if (isDisabled) {
                toggleBtn.innerText = '✅ 恢复使用';
                toggleBtn.classList.add('is-disabled-state');
                toggleBtn.title = '恢复此植物资产到花园中';
                toggleBtn.onclick = () => toggleSingleAssetEnabled(variant, true);
            } else {
                toggleBtn.innerText = '🚫 设为不使用';
                toggleBtn.classList.remove('is-disabled-state');
                toggleBtn.title = '在花园中隐藏此植物资产（保留源码目录）';
                toggleBtn.onclick = () => toggleSingleAssetEnabled(variant, false);
            }
        }

        function toggleSingleAssetEnabled(variant, nextEnabled) {
            if (!variant) return;
            const toggleBtn = document.getElementById('modalToggleEnabledBtn');
            if (toggleBtn) toggleBtn.disabled = true;

            fetch('/api/set_assets_enabled', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    items: [{ plant_name: variant.plant_name, variant_id: variant.variant_id }],
                    enabled: !!nextEnabled
                })
            }).then(res => res.json()).then(res => {
                if (res.status === 'success') {
                    variant.enabled = !!nextEnabled;
                    if (activeModalVariant &&
                        activeModalVariant.plant_name === variant.plant_name &&
                        activeModalVariant.variant_id === variant.variant_id) {
                        activeModalVariant.enabled = !!nextEnabled;
                        updateModalToggleEnabledBtn(activeModalVariant);
                        const hasAlias = activeModalVariant.display_name && activeModalVariant.display_name !== activeModalVariant.plant_name;
                        const titleText = hasAlias ? `${activeModalVariant.display_name} (${activeModalVariant.plant_name})` : activeModalVariant.plant_name;
                        const colorTag = activeModalVariant.true_color ? '✨ 真彩高保真' : '⚪ 传统灰度';
                        const disabledTag = activeModalVariant.enabled === false ? ' [🚫 不使用]' : '';
                        document.getElementById('layerModalTitle').innerText = `🌱 ${titleText} · Variant: ${activeModalVariant.variant_id} [${colorTag}]${disabledTag}`;
                    }
                    loadAssets();
                } else {
                    alert('❌ 切换启用状态失败: ' + (res.error || '未知错误'));
                }
            }).catch(err => {
                alert('网络错误: ' + err);
            }).finally(() => {
                if (toggleBtn) toggleBtn.disabled = false;
            });
        }

        function openLayerModal(variant) {
            activeModalVariant = variant;
            const hasAlias = variant.display_name && variant.display_name !== variant.plant_name;
            const titleText = hasAlias ? `${variant.display_name} (${variant.plant_name})` : variant.plant_name;
            const colorTag = variant.true_color ? '✨ 真彩高保真' : '⚪ 传统灰度';
            const disabledTag = variant.enabled === false ? ' [🚫 不使用]' : '';
            document.getElementById('layerModalTitle').innerText = `🌱 ${titleText} · Variant: ${variant.variant_id} [${colorTag}]${disabledTag}`;
            document.getElementById('layerModalSubtitle').innerText = `路径: assets/plants/${variant.path_prefix} | 共 ${variant.layers.length} 个独立分层切片`;

            const resliceBtn = document.getElementById('modalResliceAssetBtn');
            if (resliceBtn) {
                if (variant.true_color) {
                    resliceBtn.style.display = 'none';
                } else {
                    resliceBtn.style.display = 'inline-flex';
                    resliceBtn.innerHTML = '🎨 升级真彩切片';
                    resliceBtn.className = 'btn-reslice';
                    resliceBtn.title = '使用真实色彩保留与边缘消隐去白边算法重新切片';
                    resliceBtn.onclick = () => resliceSingleAsset(variant.plant_name, variant.variant_id, titleText);
                }
            }

            updateModalToggleEnabledBtn(variant);

            const removeBtn = document.getElementById('modalRemoveAssetBtn');
            if (removeBtn) {
                removeBtn.onclick = () => {
                    closeLayerModal();
                    removeAsset(variant.plant_name, variant.variant_id, titleText);
                };
            }

            const v36ModalBtn = document.getElementById('modalOpenVideo36Btn');
            if (v36ModalBtn) {
                v36ModalBtn.onclick = () => {
                    closeLayerModal();
                    openVideo36Workbench(variant.plant_name);
                };
            }

            const compositeBox = document.getElementById('compositePreviewBox');
            compositeBox.innerHTML = '';
            const toggleList = document.getElementById('layerToggleList');
            toggleList.innerHTML = '';
            const slicesGrid = document.getElementById('layerSlicesGrid');
            slicesGrid.innerHTML = '';

            const fallbackColors = ['layer-red', 'layer-green', 'layer-blue'];
            const fallbackTags = ['tag-red', 'tag-green', 'tag-blue'];
            const fallbackLabels = ['红通道', '绿通道', '蓝通道'];

            modalLayerFilters = variant.layers.map(() => ({ visible: true, hue: 0, sat: 100, bri: 100 }));

            variant.layers.forEach((layer, idx) => {
                let cssClass = '';
                let tagClass = 'tag-default';
                let label = '分层切片';

                if (layer.includes('red') || layer.includes('flower') || layer.includes('cap') || layer.includes('leaves')) {
                    cssClass = 'layer-red';
                    tagClass = 'tag-red';
                    label = '红通道';
                } else if (layer.includes('green') || layer.includes('stem') || layer.includes('trunk')) {
                    cssClass = 'layer-green';
                    tagClass = 'tag-green';
                    label = '绿通道';
                } else if (layer.includes('blue') || layer.includes('accent')) {
                    cssClass = 'layer-blue';
                    tagClass = 'tag-blue';
                    label = '蓝通道';
                } else {
                    cssClass = fallbackColors[idx % 3];
                    tagClass = fallbackTags[idx % 3];
                    label = fallbackLabels[idx % 3];
                }

                const layerImgSrc = `/assets/plants/${variant.path_prefix}/${layer}`;

                // 1. Composite Layer Image
                const compImg = document.createElement('img');
                compImg.src = layerImgSrc;
                compImg.className = cssClass;
                compImg.id = `comp-layer-${idx}`;
                compositeBox.appendChild(compImg);

                // 2. Interactive Layer Control Card
                const ctrlCard = document.createElement('div');
                ctrlCard.className = 'layer-control-card';
                ctrlCard.innerHTML = `
                    <div class="layer-control-header">
                        <label style="display:flex; align-items:center; gap:8px; cursor:pointer; user-select:none;">
                            <input type="checkbox" checked id="layer-vis-${idx}" onchange="toggleLayerVisibility(${idx}, this.checked)" style="accent-color:#1f6feb; cursor:pointer;" />
                            <span class="slice-tag ${tagClass}">${layer}</span>
                            <span style="color:#c9d1d9; font-size:12px; font-weight:600;">${label}</span>
                        </label>
                        <button class="btn-reset-filter" onclick="resetSingleLayerFilter(${idx})" title="重置此图层色相与明度">↺ 重置</button>
                    </div>
                    <div class="slider-row">
                        <label>色相偏移</label>
                        <input type="range" min="0" max="360" value="0" step="5" id="slider-hue-${idx}" oninput="setLayerFilterValue(${idx}, 'hue', this.value, 'val-hue-${idx}')" />
                        <span class="slider-val" id="val-hue-${idx}">0°</span>
                    </div>
                    <div class="slider-row">
                        <label>饱和度</label>
                        <input type="range" min="0" max="200" value="100" step="5" id="slider-sat-${idx}" oninput="setLayerFilterValue(${idx}, 'sat', this.value, 'val-sat-${idx}')" />
                        <span class="slider-val" id="val-sat-${idx}">100%</span>
                    </div>
                    <div class="slider-row">
                        <label>明度</label>
                        <input type="range" min="50" max="150" value="100" step="5" id="slider-bri-${idx}" oninput="setLayerFilterValue(${idx}, 'bri', this.value, 'val-bri-${idx}')" />
                        <span class="slider-val" id="val-bri-${idx}">100%</span>
                    </div>
                `;
                toggleList.appendChild(ctrlCard);

                // 3. Isolated Slice Card
                const sliceCard = document.createElement('div');
                sliceCard.className = 'slice-card';
                sliceCard.innerHTML = `
                    <div class="slice-img-wrap">
                        <img src="${layerImgSrc}" />
                    </div>
                    <div style="font-size:11px; font-weight:bold; color:#c9d1d9; word-break:break-all; text-align:center;">${layer}</div>
                    <span class="slice-tag ${tagClass}">${label}</span>
                    <a href="${layerImgSrc}" target="_blank" style="color:#58a6ff; font-size:11px; text-decoration:none; margin-top:2px;">🔗 查看原图</a>
                `;
                slicesGrid.appendChild(sliceCard);
            });

            updateCombinedCssBox();
            renderAiAnalysisCard(variant);
            document.getElementById('layerModal').style.display = 'flex';
        }

        function renderAiAnalysisCard(variant) {
            const container = document.getElementById('layerAiAnalysisCard');
            if (!container) return;
            if (!variant) {
                container.innerHTML = '';
                return;
            }

            // Footprint info HTML
            let fpBlock = '';
            if (variant.root_width) {
                const rw = variant.root_width;
                const ratioStr = variant.root_ratio ? `${(variant.root_ratio * 100).toFixed(1)}%` : (variant.width ? `${(rw / variant.width * 100).toFixed(1)}%` : '');
                const ellStr = variant.ground_ellipse ? `${variant.ground_ellipse[0]} × ${variant.ground_ellipse[1]} px` : `${rw} × ${Math.round(rw * 0.55)} px`;
                fpBlock = `
                    <div style="margin-top:10px; background:#0d1117; padding:8px 10px; border-radius:6px; border:1px solid #21262d;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="color:#8b949e; font-size:11px;">🌱 根部占地宽度:</span>
                            <span style="color:#d29922; font-weight:bold; font-size:12px;">${rw}px ${ratioStr ? `<span style="color:#8b949e; font-size:10px; font-weight:normal;">(冠幅占比 ${ratioStr})</span>` : ''}</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px;">
                            <span style="color:#8b949e; font-size:11px;">🪐 2.5D 地表椭圆投影:</span>
                            <span style="color:#7ee787; font-size:11px; font-family:monospace;">${ellStr}</span>
                        </div>
                    </div>
                `;
            }

            if (variant.status === 'ai_analyzing') {
                container.innerHTML = `
                    <div style="background:#161b22; border:1px solid #f39c12; border-radius:8px; padding:12px 14px;">
                        <div style="display:flex; align-items:center; gap:8px; color:#f39c12; font-size:13px; font-weight:600;">
                            <span class="spinner-mini" style="border-top-color:#f39c12; width:14px; height:14px;"></span>
                            <span>🤖 植物生物学 AI 分析中...</span>
                        </div>
                        <div style="color:#8b949e; font-size:12px; margin-top:6px; line-height:1.4;">
                            后台正在通过 OpenAI 兼容接口测算真实植物学生态高度与 2.5D 映射范围，测算完成后将自动就绪并在此展示。
                        </div>
                        ${fpBlock}
                    </div>
                `;
            } else if (variant.ai_analysis && (variant.ai_analysis.status === 'success' || variant.ai_analysis.botanical_height_m)) {
                const ai = variant.ai_analysis;
                const botH = ai.botanical_height_m ? `${ai.botanical_height_m[0]}m ~ ${ai.botanical_height_m[1]}m` : '未知';
                const gameH = ai.suggested_game_height_px ? `${ai.suggested_game_height_px[0]}px ~ ${ai.suggested_game_height_px[1]}px` : '未知';
                const sciName = ai.scientific_name ? `<i>(${ai.scientific_name})</i>` : '';
                const typeMap = { 'tree': '🌳 乔木', 'shrub': '🌿 灌木', 'flower': '🌸 花卉', 'groundcover': '🌱 地被' };
                const habitMap = { 'slender': '直立挺拔', 'canopy': '伞冠展开', 'bush': '丛生茂密', 'carpet': '铺地蔓延' };
                const pType = typeMap[ai.plant_type] || ai.plant_type || '自然植被';
                const pHabit = habitMap[ai.growth_habit] || ai.growth_habit || '自然形态';

                container.innerHTML = `
                    <div style="background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px 14px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                            <span style="font-weight:bold; color:#58a6ff; font-size:13px;">🤖 植物生物学 AI 测算结果:</span>
                            <span style="font-size:11px; color:#8b949e;">${sciName}</span>
                        </div>
                        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:10px;">
                            <div style="background:#0d1117; padding:8px 10px; border-radius:6px; border:1px solid #21262d;">
                                <div style="color:#8b949e; font-size:11px;">真实生物学生长高度:</div>
                                <div style="color:#7ee787; font-weight:bold; font-size:13px; margin-top:2px;">${botH}</div>
                            </div>
                            <div style="background:#0d1117; padding:8px 10px; border-radius:6px; border:1px solid #21262d;">
                                <div style="color:#8b949e; font-size:11px;">2.5D 游戏映射高度:</div>
                                <div style="color:#58a6ff; font-weight:bold; font-size:13px; margin-top:2px;">${gameH}</div>
                            </div>
                        </div>
                        <div style="font-size:12px; color:#c9d1d9; line-height:1.5;">
                            <span style="display:inline-block; background:#21262d; padding:2px 8px; border-radius:12px; font-size:11px; margin-right:6px; color:#58a6ff;">${pType}</span>
                            <span style="display:inline-block; background:#21262d; padding:2px 8px; border-radius:12px; font-size:11px; color:#8b949e;">${pHabit}</span>
                            <div style="margin-top:6px; color:#8b949e; font-size:12px;">${ai.brief || ''}</div>
                        </div>
                        ${fpBlock}
                    </div>
                `;
            } else if (variant.ai_analysis && variant.ai_analysis.status === 'failed') {
                container.innerHTML = `
                    <div style="background:#161b22; border:1px solid #8b2020; border-radius:8px; padding:10px 14px;">
                        <span style="color:#ff7b72; font-size:12px; font-weight:bold;">⚠️ AI 测算未完成:</span>
                        <span style="color:#8b949e; font-size:12px; margin-left:6px;">${variant.ai_analysis.brief || '网络或配置原因'}</span>
                        ${fpBlock}
                    </div>
                `;
            } else {
                container.innerHTML = `
                    <div style="background:#161b22; border:1px solid #21262d; border-radius:8px; padding:10px 14px; color:#8b949e; font-size:12px;">
                        ℹ️ 暂无生物学 AI 测算数据
                        ${fpBlock}
                    </div>
                `;
            }
        }

        let aiPollingTimer = null;
        function startAiStatusPolling() {
            if (aiPollingTimer) return;
            aiPollingTimer = setInterval(() => {
                fetch('/api/assets')
                    .then(res => res.json())
                    .then(assets => {
                        let stillAnalyzing = false;
                        assets.forEach(variant => {
                            if (variant.status === 'ai_analyzing') {
                                stillAnalyzing = true;
                            }
                        });

                        // If active modal is open for a variant, check if its status changed
                        if (activeModalVariant) {
                            const updated = assets.find(v => v.plant_name === activeModalVariant.plant_name && v.variant_id === activeModalVariant.variant_id);
                            if (updated) {
                                if (updated.status !== activeModalVariant.status || JSON.stringify(updated.ai_analysis) !== JSON.stringify(activeModalVariant.ai_analysis)) {
                                    activeModalVariant = updated;
                                    renderAiAnalysisCard(updated);
                                }
                            }
                        }

                        // Update badges on existing asset cards
                        assets.forEach(variant => {
                            const key = `${variant.plant_name}__${variant.variant_id}`;
                            const card = document.querySelector(`.card[data-asset-key="${key}"]`);
                            if (card) {
                                const badge = card.querySelector('.card-status-badge');
                                if (badge) {
                                    if (variant.status === 'ai_analyzing') {
                                        badge.className = 'badge-grayscale card-status-badge';
                                        badge.style.background = '#f39c12';
                                        badge.style.color = '#fff';
                                        badge.innerText = '🤖 AI分析中...';
                                    } else {
                                        badge.className = variant.true_color ? 'badge-truecolor card-status-badge' : 'badge-grayscale card-status-badge';
                                        badge.style.background = '';
                                        badge.style.color = '';
                                        badge.innerText = variant.true_color ? '✨ 真彩' : '⚪ 灰度';
                                    }
                                }
                            }
                        });

                        const hasPlaceholder = !!document.querySelector('.is-slicing-placeholder');
                        if (!hasPlaceholder && !stillAnalyzing) {
                            clearInterval(aiPollingTimer);
                            aiPollingTimer = null;
                        }
                    })
                    .catch(() => {});
            }, 2500);
        }

        function toggleLayerVisibility(idx, isChecked) {
            modalLayerFilters[idx].visible = isChecked;
            applyLayerFilters(idx);
        }

        function setLayerFilterValue(idx, key, val, textId) {
            modalLayerFilters[idx][key] = parseInt(val, 10);
            const valEl = document.getElementById(textId);
            if (valEl) valEl.innerText = (key === 'hue') ? `${val}°` : `${val}%`;
            applyLayerFilters(idx);
        }

        function resetSingleLayerFilter(idx) {
            modalLayerFilters[idx] = { visible: true, hue: 0, sat: 100, bri: 100 };
            const vis = document.getElementById(`layer-vis-${idx}`);
            if (vis) vis.checked = true;
            const sHue = document.getElementById(`slider-hue-${idx}`);
            if (sHue) sHue.value = 0;
            const sSat = document.getElementById(`slider-sat-${idx}`);
            if (sSat) sSat.value = 100;
            const sBri = document.getElementById(`slider-bri-${idx}`);
            if (sBri) sBri.value = 100;
            const vHue = document.getElementById(`val-hue-${idx}`);
            if (vHue) vHue.innerText = '0°';
            const vSat = document.getElementById(`val-sat-${idx}`);
            if (vSat) vSat.innerText = '100%';
            const vBri = document.getElementById(`val-bri-${idx}`);
            if (vBri) vBri.innerText = '100%';
            applyLayerFilters(idx);
        }

        function resetAllLayerFilters() {
            if (!modalLayerFilters) return;
            modalLayerFilters.forEach((_, idx) => resetSingleLayerFilter(idx));
        }

        function applyLayerFilters(idx) {
            const f = modalLayerFilters[idx];
            const img = document.getElementById(`comp-layer-${idx}`);
            if (!img) return;
            img.style.opacity = f.visible ? '1' : '0';
            const parts = [];
            if (f.hue !== 0) parts.push(`hue-rotate(${f.hue}deg)`);
            if (f.sat !== 100) parts.push(`saturate(${f.sat}%)`);
            if (f.bri !== 100) parts.push(`brightness(${f.bri}%)`);
            img.style.filter = parts.length > 0 ? parts.join(' ') : 'none';
            updateCombinedCssBox();
        }

        function updateCombinedCssBox() {
            if (!activeModalVariant) return;
            const cssBox = document.getElementById('combinedCssCode');
            if (!cssBox) return;
            const rules = [];
            activeModalVariant.layers.forEach((layer, idx) => {
                const f = modalLayerFilters[idx];
                const parts = [];
                if (f.hue !== 0) parts.push(`hue-rotate(${f.hue}deg)`);
                if (f.sat !== 100) parts.push(`saturate(${f.sat}%)`);
                if (f.bri !== 100) parts.push(`brightness(${f.bri}%)`);
                if (!f.visible) parts.push('opacity(0)');
                if (parts.length > 0) {
                    rules.push(`.plant-sprite.plant-${activeModalVariant.plant_name}-v${activeModalVariant.variant_id} .${layer.replace('.png', '')} { filter: ${parts.join(' ')}; }`);
                }
            });
            if (rules.length === 0) {
                cssBox.innerText = '/* 当前所有通道保持原生色彩 (filter: none) */';
            } else {
                cssBox.innerText = rules.join('\\n');
            }
        }

        function copyCombinedCss() {
            const cssBox = document.getElementById('combinedCssCode');
            if (!cssBox) return;
            navigator.clipboard.writeText(cssBox.innerText).then(() => {
                const originalText = cssBox.innerText;
                cssBox.innerText = '✅ 已成功复制 CSS 滤镜到剪贴板！';
                setTimeout(() => {
                    cssBox.innerText = originalText;
                }, 1500);
            }).catch(err => alert('复制失败: ' + err));
        }

        function closeLayerModal() {
            document.getElementById('layerModal').style.display = 'none';
        }

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                closeLayerModal();
                document.getElementById('processModal').style.display = 'none';
            }
        });
        
        function openModal(filename) {
            document.getElementById('candidateFilename').value = filename;
            document.getElementById('plantNameInput').value = '';
            document.getElementById('plantTitleInput').value = '';
            const spinnerOverlay = document.getElementById('processModalSpinner');
            if (spinnerOverlay) spinnerOverlay.style.display = 'none';
            const btn = document.getElementById('confirmBtn');
            if (btn) {
                btn.innerText = '自动切片并入库';
                btn.disabled = false;
            }
            const cancelBtn = document.getElementById('cancelBtn');
            if (cancelBtn) cancelBtn.disabled = false;
            document.getElementById('processModal').style.display = 'flex';
            document.getElementById('plantNameInput').focus();
        }
        
        
        function removeAsset(plantName, variantId, displayName) {
            if (confirm(`确定要移出资产 [${displayName}] 并召回原图到待审区吗？\n注意：这将从物理目录及样式表中彻底删除该资产变体。`)) {
                fetch('/api/remove_asset', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ plant_name: plantName, variant_id: variantId })
                }).then(res => res.json()).then(res => {
                    if(res.status === 'success') {
                        loadData();
                    } else {
                        alert('移出资产失败: ' + (res.error || ''));
                    }
                }).catch(err => alert('网络错误: ' + err));
            }
        }

        function deleteCandidate(filename) {
            fetch('/api/delete_candidate', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ filename: filename })
            }).then(res => res.json()).then(res => {
                if(res.status === 'success') {
                    loadData();
                } else {
                    alert('移入回收区失败: ' + (res.error || ''));
                }
            });
        }

        function restoreTrash(filename) {
            fetch('/api/restore_trash', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ filename: filename })
            }).then(res => res.json()).then(res => {
                if(res.status === 'success') {
                    loadData();
                } else {
                    alert('恢复失败: ' + (res.error || ''));
                }
            });
        }

        function purgeTrash(filename) {
            fetch('/api/purge_trash', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ filename: filename })
            }).then(res => res.json()).then(res => {
                if(res.status === 'success') {
                    loadData();
                } else {
                    alert('彻底抹除失败: ' + (res.error || ''));
                }
            });
        }

        function purgeAllTrash() {
            fetch('/api/purge_trash', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ all: true })
            }).then(res => res.json()).then(res => {
                if(res.status === 'success') {
                    loadData();
                } else {
                    alert('清空回收区失败: ' + (res.error || ''));
                }
            });
        }
        
        document.getElementById('cancelBtn').onclick = () => {
            document.getElementById('processModal').style.display = 'none';
        }
        
        // Quick Enter key support
        document.getElementById('plantNameInput').addEventListener('keypress', function (e) {
            if (e.key === 'Enter') {
                document.getElementById('confirmBtn').click();
            }
        });
        document.getElementById('plantTitleInput').addEventListener('keypress', function (e) {
            if (e.key === 'Enter') {
                document.getElementById('confirmBtn').click();
            }
        });
        
        document.getElementById('confirmBtn').onclick = () => {
            const name = document.getElementById('plantNameInput').value.trim();
            const displayName = document.getElementById('plantTitleInput').value.trim();
            const filename = document.getElementById('candidateFilename').value;
            if(!name) return alert('请输入植物英文代号');
            
            // 1. 立即关闭入库弹窗
            document.getElementById('processModal').style.display = 'none';

            // 2. 立即从左侧候选池中隐藏当前候选卡片
            const candidateCard = Array.from(document.querySelectorAll('#candidatesGallery .card')).find(c => {
                const title = c.querySelector('.title');
                return title && title.innerText.trim() === filename;
            });
            if (candidateCard) {
                candidateCard.style.display = 'none';
            }

            // 3. 立即在右侧资产画廊插入灰色不可用占位卡片【切图中】
            const aGal = document.getElementById('assetsGallery');
            const placeholderCard = document.createElement('div');
            placeholderCard.className = 'card is-slicing-placeholder';
            placeholderCard.id = `card-processing-${name}`;
            placeholderCard.style.cssText = 'opacity:0.6; filter:grayscale(0.85); cursor:not-allowed; position:relative;';
            const displayTitleStr = displayName ? `${displayName} (${name})` : name;
            placeholderCard.innerHTML = `
                <span class="badge-grayscale card-status-badge" style="background:#555; color:#eee;">⏳ 切图中...</span>
                <div class="img-container">
                    <img src="/tools/candidates/${filename}" />
                    <div class="img-spinner-overlay" style="display:flex;">
                        <div class="spinner-circle"></div>
                        <span class="spinner-text">智能切片中...</span>
                    </div>
                </div>
                <div class="title" style="margin-top:2px;">${displayTitleStr}</div>
                <div class="subtitle" style="margin-top:2px;">Variant: --</div>
                <div style="margin-top:6px;">
                    <span class="layer-badge-btn" style="opacity:0.5; pointer-events:none;">⏳ 正在切片...</span>
                </div>
            `;
            if (aGal.firstChild) {
                aGal.insertBefore(placeholderCard, aGal.firstChild);
            } else {
                aGal.appendChild(placeholderCard);
            }

            // 4. 发起切图请求 (Phase 1)
            fetch('/api/process', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: name, display_name: displayName, filename: filename })
            }).then(res => res.json()).then(res => {
                if (res.status === 'success') {
                    // 切图成功，此时进入半可用状态 (AI分析中)
                    loadData();
                    startAiStatusPolling();
                } else {
                    alert('处理失败: ' + res.error);
                    if (placeholderCard) placeholderCard.remove();
                    if (candidateCard) candidateCard.style.display = '';
                }
            }).catch(err => {
                alert('网络错误: ' + err);
                if (placeholderCard) placeholderCard.remove();
                if (candidateCard) candidateCard.style.display = '';
            });
        }
        
        let activeVideo36PlantId = null;
        let v36PreviewMode = 'turntable';
        let v36AtlasImage = null;
        let v36CurrentMeta = null;
        let v36AngleDeg = 0;
        let v36AutoSpin = true;
        let v36SpinTimer = null;
        let v36JobPollTimer = null;
        let v36CurrentJobId = null;

        let _v36SupportsDisplayTransition;
        function v36CanTransitionDisplay() {
            if (_v36SupportsDisplayTransition !== undefined) return _v36SupportsDisplayTransition;
            if (!window.CSS || !window.CSS.supports || !window.CSS.supports('transition-behavior', 'allow-discrete') || !document.body) {
                return false;
            }
            const probe = document.createElement('div');
            probe.style.cssText = 'transition: display 1s allow-discrete !important; display: block;';
            document.body.appendChild(probe);
            getComputedStyle(probe).display;
            probe.style.display = 'none';
            _v36SupportsDisplayTransition = getComputedStyle(probe).display === 'block';
            probe.remove();
            return _v36SupportsDisplayTransition;
        }
        function v36SupportsTopLayerExit() {
            return Boolean(window.CSS && window.CSS.supports && window.CSS.supports('overlay', 'auto') && v36CanTransitionDisplay());
        }
        async function closeTopLayerDialog(dialogEl) {
            if (!dialogEl || !dialogEl.open) return;
            if (!v36SupportsTopLayerExit()) {
                dialogEl.setAttribute('data-closing', '');
                const animations = dialogEl.getAnimations ? dialogEl.getAnimations({ subtree: true }) : [];
                if (animations.length > 0) {
                    await Promise.race([
                        Promise.allSettled(animations.map(a => a.finished)),
                        new Promise(r => setTimeout(r, 1500)),
                    ]);
                }
                if (!dialogEl.hasAttribute('data-closing')) return;
                dialogEl.removeAttribute('data-closing');
            }
            dialogEl.close();
        }

        const v36DialogEl = document.getElementById('video36Dialog');
        if (v36DialogEl) {
            if (!('closedBy' in HTMLDialogElement.prototype)) {
                const isOutsideDialog = (e, dlg) => {
                    const r = dlg.getBoundingClientRect();
                    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
                };
                let pressedOutside = false;
                v36DialogEl.addEventListener('pointerdown', e => {
                    pressedOutside = isOutsideDialog(e, v36DialogEl);
                });
                v36DialogEl.addEventListener('pointerup', e => {
                    if (pressedOutside && isOutsideDialog(e, v36DialogEl)) {
                        closeTopLayerDialog(v36DialogEl);
                    }
                });
            }
            v36DialogEl.addEventListener('cancel', e => {
                if (e.cancelable) {
                    e.preventDefault();
                    closeTopLayerDialog(v36DialogEl);
                }
            });
            v36DialogEl.addEventListener('close', () => {
                v36DialogEl.returnValue = '';
                stopVideo36SpinTimer();
                stopVideo36JobPoll();
            });
        }
        const v36CloseBtn = document.getElementById('v36CloseBtn');
        if (v36CloseBtn && v36DialogEl) {
            v36CloseBtn.addEventListener('click', () => closeTopLayerDialog(v36DialogEl));
        }

        function stopVideo36SpinTimer() {
            if (v36SpinTimer) {
                clearInterval(v36SpinTimer);
                v36SpinTimer = null;
            }
        }
        function startVideo36SpinTimer() {
            stopVideo36SpinTimer();
            if (!v36AutoSpin || v36PreviewMode !== 'turntable') return;
            v36SpinTimer = setInterval(() => {
                v36AngleDeg = (v36AngleDeg + 10) % 360;
                const slider = document.getElementById('v36AngleSlider');
                if (slider) slider.value = String(v36AngleDeg);
                drawVideo36Frame();
            }, 130);
        }
        function stopVideo36JobPoll() {
            if (v36JobPollTimer) {
                clearInterval(v36JobPollTimer);
                v36JobPollTimer = null;
            }
        }

        function drawVideo36Frame() {
            const canvas = document.getElementById('v36Canvas');
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            const cw = canvas.width;
            const ch = canvas.height;
            ctx.fillStyle = '#6f664b';
            ctx.fillRect(0, 0, cw, ch);

            const frameIdx = Math.round(v36AngleDeg / 10) % 36;
            const readout = document.getElementById('v36AngleReadout');
            if (readout) {
                readout.innerText = `F${String(frameIdx).padStart(2, '0')} ${String(frameIdx * 10).padStart(3, '0')}°`;
            }

            if (!v36AtlasImage || !v36AtlasImage.complete || v36AtlasImage.naturalWidth <= 0) {
                ctx.fillStyle = '#c9d1d9';
                ctx.font = '12px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('尚无 36F 图集，可先运行左侧/右侧管线生成', cw / 2, ch / 2);
                return;
            }

            const hf = (v36CurrentMeta && v36CurrentMeta.hframes) || 6;
            const vf = (v36CurrentMeta && v36CurrentMeta.vframes) || 6;
            const cellW = v36AtlasImage.naturalWidth / hf;
            const cellH = v36AtlasImage.naturalHeight / vf;
            const col = frameIdx % hf;
            const row = Math.floor(frameIdx / hf);

            ctx.drawImage(v36AtlasImage, col * cellW, row * cellH, cellW, cellH, 0, 0, cw, ch);

            const showPivot = document.getElementById('v36ShowPivotChk')?.checked;
            if (showPivot) {
                const anchor = (v36CurrentMeta && v36CurrentMeta.root_anchor_px) || [cellW * 0.5, cellH - 8];
                const ax = (anchor[0] / cellW) * cw;
                const ay = (anchor[1] / cellH) * ch;

                ctx.strokeStyle = 'rgba(165, 195, 145, 0.75)';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(12, ay);
                ctx.lineTo(cw - 12, ay);
                ctx.stroke();

                const rwPx = ((v36CurrentMeta && v36CurrentMeta.root_width) || 32) * (cw / 1024);
                ctx.strokeStyle = 'rgba(130, 236, 158, 0.85)';
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.ellipse(ax, ay, Math.max(8, rwPx * 0.5), Math.max(5, rwPx * 0.36), 0, 0, Math.PI * 2);
                ctx.stroke();

                ctx.fillStyle = '#eb3232';
                ctx.beginPath();
                ctx.arc(ax, ay, 4, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        function setVideo36PreviewMode(mode) {
            v36PreviewMode = mode || 'turntable';
            document.querySelectorAll('#v36ViewTabs .v36-tab-btn').forEach(btn => {
                btn.classList.toggle('active', btn.getAttribute('data-view') === v36PreviewMode);
            });
            const canvas = document.getElementById('v36Canvas');
            const spinCtrl = document.getElementById('v36SpinControls');
            const staticImg = document.getElementById('v36StaticImg');
            const item = video36ById[activeVideo36PlantId] || {};
            const files = item.files || {};
            const ts = Date.now();

            if (v36PreviewMode === 'turntable') {
                canvas.style.display = 'block';
                spinCtrl.style.display = 'flex';
                staticImg.style.display = 'none';
                drawVideo36Frame();
                startVideo36SpinTimer();
                return;
            }
            stopVideo36SpinTimer();
            canvas.style.display = 'none';
            spinCtrl.style.display = 'none';
            staticImg.style.display = 'block';

            let targetUrl = '';
            if (v36PreviewMode === 'iou') {
                targetUrl = files['ref_4views_check.jpg'] || files['ref_000_vs_180_check.jpg'] || '';
            } else if (v36PreviewMode === 'sheet') {
                targetUrl = files['video_36_contact_sheet.jpg'] || '';
            } else if (v36PreviewMode === 'raw') {
                targetUrl = files['raw_preview_36.jpg'] || '';
            }
            if (targetUrl) {
                staticImg.src = targetUrl + '?t=' + ts;
            } else {
                staticImg.removeAttribute('src');
                staticImg.alt = '当前阶段尚未产出预览图';
            }
        }

        function toggleVideo36AutoSpin() {
            v36AutoSpin = !v36AutoSpin;
            const btn = document.getElementById('v36SpinPlayBtn');
            if (btn) btn.innerText = v36AutoSpin ? '⏸ 暂停' : '▶ 自动旋转';
            if (v36AutoSpin) startVideo36SpinTimer();
            else stopVideo36SpinTimer();
        }
        function onVideo36SliderChange(val) {
            v36AutoSpin = false;
            const btn = document.getElementById('v36SpinPlayBtn');
            if (btn) btn.innerText = '▶ 自动旋转';
            stopVideo36SpinTimer();
            v36AngleDeg = parseInt(val, 10) || 0;
            drawVideo36Frame();
        }

        function renderVideo36DialogContent(item) {
            if (!item) return;
            const titleEl = document.getElementById('video36DialogTitle');
            const subEl = document.getElementById('v36Subtitle');
            titleEl.innerText = `🔄 ${item.display_name || item.plant_id} (${item.plant_id}) · 360° 转盘工坊`;
            subEl.innerText = `物种类型: ${item.plant_type || 'shrub'} | 阶段进度: Prep=${item.has_prep ? '✓' : '-'} · Video=${item.has_video ? '✓' : '-'} · Atlas=${item.has_atlas ? '✓' : '-'}`;

            const prep = item.prep_meta || {};
            const atlas = item.atlas_meta || {};
            const pt = atlas.pivot_tracking || {};
            v36CurrentMeta = atlas;

            const modeSel = document.getElementById('v36ModeSelect');
            if (prep.mode && (prep.mode === '2half' || prep.mode === '4quarter')) {
                modeSel.value = prep.mode;
            }

            const iou = prep.mirror_silhouette_iou;
            const iouText = iou !== undefined ? `${Number(iou).toFixed(4)} ${iou >= 0.78 ? '✓' : '⚠ <0.78'}` : '--';
            const iouColor = iou === undefined ? '#c9d1d9' : (iou >= 0.78 ? '#7ee787' : '#f85149');
            const spikeText = atlas.cut_spike_ratio !== undefined ? `${atlas.cut_spike_ratio}×` : '--';
            const pivotErr = pt.rx_med_err_px !== undefined ? `${pt.rx_med_err_px} / ${pt.ry_med_err_px} px` : '--';
            const anchorText = atlas.root_anchor_px ? `[${atlas.root_anchor_px[0]}, ${atlas.root_anchor_px[1]}]` : '--';
            const webpKb = atlas.webp_bytes ? `${Math.round(atlas.webp_bytes / 1024)} KB` : '--';
            const modeLabel = pt.modes ? Object.keys(pt.modes).map(k => `${k}:${pt.modes[k]}`).join(' ') : (prep.mode || '--');

            document.getElementById('v36MetricsGrid').innerHTML = `
                <div class="v36-metric-card"><div class="lbl">镜像轮廓 IoU</div><div class="val" style="color:${iouColor}">${iouText}</div></div>
                <div class="v36-metric-card"><div class="lbl">最大跳变 Spike</div><div class="val">${spikeText}</div></div>
                <div class="v36-metric-card"><div class="lbl">WebP 体积</div><div class="val">${webpKb}</div></div>
                <div class="v36-metric-card"><div class="lbl">枢轴误差 (rx/ry)</div><div class="val">${pivotErr}</div></div>
                <div class="v36-metric-card"><div class="lbl">单元格锚点 (px)</div><div class="val">${anchorText}</div></div>
                <div class="v36-metric-card"><div class="lbl">定位模式</div><div class="val">${modeLabel}</div></div>
            `;

            const pubBtn = document.getElementById('v36PublishBtn');
            pubBtn.disabled = !item.has_atlas;
            if (!item.has_atlas) {
                pubBtn.innerText = '📦 尚未生成 Atlas';
                pubBtn.className = 'btn-reslice-secondary';
            } else if (item.published) {
                pubBtn.innerText = '✅ 已发布庭院 (点击下架)';
                pubBtn.className = 'btn-toggle-disabled is-disabled-state';
                pubBtn.style.padding = '6px 12px';
                pubBtn.style.borderRadius = '6px';
            } else {
                pubBtn.innerText = '🌐 发布到花园 36F';
                pubBtn.className = 'btn-reslice';
            }

            const slots = item.mp4_slots || {};
            const files = item.files || {};
            const dlFront = document.getElementById('v36DlFrontBtn');
            const dlRear = document.getElementById('v36DlRearBtn');
            if (dlFront) {
                dlFront.href = files['ref_000.png'] || '#';
                dlFront.download = `${item.plant_id}_000_front.png`;
                dlFront.style.opacity = files['ref_000.png'] ? '1' : '0.45';
                dlFront.style.pointerEvents = files['ref_000.png'] ? 'auto' : 'none';
            }
            if (dlRear) {
                dlRear.href = files['ref_180.png'] || '#';
                dlRear.download = `${item.plant_id}_180_rear.png`;
                dlRear.style.opacity = files['ref_180.png'] ? '1' : '0.45';
                dlRear.style.pointerEvents = files['ref_180.png'] ? 'auto' : 'none';
            }
            const updateSlotBadge = (lblId, txtId, ok) => {
                const lbl = document.getElementById(lblId);
                const sp = document.getElementById(txtId);
                if (sp) sp.innerText = ok ? '✓ 已上传' : '未上传';
                if (lbl) lbl.classList.toggle('is-ready', Boolean(ok));
            };
            updateSlotBadge('v36LblHalfA', 'v36SlotHalfA', slots.half_a);
            updateSlotBadge('v36LblHalfB', 'v36SlotHalfB', slots.half_b);
            updateSlotBadge('v36LblSingle360', 'v36SlotSingle360', slots.single_360);

            const atlasUrl = (item.files && item.files['atlas_36.webp']) || '';
            if (atlasUrl) {
                const img = new Image();
                img.onload = () => {
                    v36AtlasImage = img;
                    drawVideo36Frame();
                };
                img.src = atlasUrl + '?t=' + Date.now();
            } else {
                v36AtlasImage = null;
                drawVideo36Frame();
            }

            if (item.active_job) {
                renderVideo36JobStatus(item.active_job);
                if (item.active_job.status === 'running') {
                    startVideo36JobPoll(item.active_job.job_id);
                }
            }
            setVideo36PreviewMode(v36PreviewMode);
        }

        function renderVideo36JobStatus(job) {
            const stEl = document.getElementById('v36JobStatusText');
            const logEl = document.getElementById('v36LogBox');
            if (stEl) {
                const map = { running: '⏳ 运行中...', succeeded: '✅ 已完成', failed: '❌ 失败' };
                stEl.innerText = `${map[job.status] || job.status} (${job.stages} / ${job.mode})`;
                stEl.style.color = job.status === 'succeeded' ? '#7ee787' : (job.status === 'failed' ? '#f85149' : '#f2cc60');
            }
            if (logEl && Array.isArray(job.log_tail)) {
                logEl.innerText = job.log_tail.join('\\n') || '(无日志输出)';
                logEl.scrollTop = logEl.scrollHeight;
            }
        }

        function startVideo36JobPoll(jobId) {
            stopVideo36JobPoll();
            v36CurrentJobId = jobId;
            v36JobPollTimer = setInterval(() => {
                fetch(`/api/video36/jobs/${encodeURIComponent(jobId)}`)
                    .then(r => r.json())
                    .then(job => {
                        renderVideo36JobStatus(job);
                        if (job.status !== 'running') {
                            stopVideo36JobPoll();
                            loadVideo36List();
                        }
                    })
                    .catch(() => {});
            }, 1500);
        }

        function openVideo36Workbench(plantId) {
            activeVideo36PlantId = plantId;
            v36AngleDeg = 0;
            v36AutoSpin = true;
            const btn = document.getElementById('v36SpinPlayBtn');
            if (btn) btn.innerText = '⏸ 暂停';
            const stEl = document.getElementById('v36JobStatusText');
            if (stEl) {
                stEl.innerText = '空闲';
                stEl.style.color = '#c9d1d9';
            }
            document.getElementById('v36LogBox').innerText = '就绪，请选择阶段或一键生成...';

            const assetFallback = allAssets.find(a => a.plant_name === plantId);
            const item = video36ById[plantId] || {
                plant_id: plantId,
                display_name: (assetFallback && assetFallback.display_name) || plantId,
                plant_type: (assetFallback && assetFallback.ai_analysis && assetFallback.ai_analysis.plant_type) || 'shrub',
                has_prep: false,
                has_video: false,
                has_atlas: false,
                published: false,
                files: {},
            };
            renderVideo36DialogContent(item);
            loadVideo36List();
            if (v36DialogEl && !v36DialogEl.open) {
                v36DialogEl.removeAttribute('data-closing');
                v36DialogEl.showModal();
            }
            startVideo36SpinTimer();
        }

        function runVideo36Stage(stages) {
            if (!activeVideo36PlantId) return;
            const mode = document.getElementById('v36ModeSelect').value;
            const keyMode = document.getElementById('v36KeyModeSelect').value;
            const mirror270 = document.getElementById('v36Mirror270Chk').checked;
            const force = document.getElementById('v36ForceChk').checked;

            fetch('/api/video36/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    plant_id: activeVideo36PlantId,
                    stages: stages,
                    mode: mode,
                    key_mode: keyMode,
                    mirror_q0_for_270: mirror270,
                    force: force,
                }),
            }).then(res => res.json().then(body => ({ ok: res.ok, body }))).then(({ ok, body }) => {
                if (!ok) {
                    alert('启动失败: ' + (body.error || '未知错误'));
                    return;
                }
                renderVideo36JobStatus(body);
                startVideo36JobPoll(body.job_id);
            }).catch(err => alert('网络错误: ' + err));
        }

        function copyFlowPrompt(slot) {
            if (!activeVideo36PlantId) return;
            const item = video36ById[activeVideo36PlantId];
            const prompts = (item && item.flow_prompts) || {};
            const txt = prompts[slot];
            if (!txt) {
                alert('请先运行 Stage 1 Prep 生成首尾图与专属提示词！');
                return;
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(txt).then(() => {
                    const stEl = document.getElementById('v36JobStatusText');
                    if (stEl) {
                        stEl.innerText = `📋 已复制 ${slot} 提示词`;
                        stEl.style.color = '#7ee787';
                    }
                }).catch(() => prompt('请手动复制提示词:', txt));
            } else {
                prompt('请手动复制提示词:', txt);
            }
        }

        function uploadVideo36Mp4(slot, inputEl) {
            if (!activeVideo36PlantId || !inputEl || !inputEl.files || !inputEl.files[0]) return;
            const file = inputEl.files[0];
            const autoAtlas = document.getElementById('v36AutoAtlasChk') && document.getElementById('v36AutoAtlasChk').checked ? '1' : '0';
            const keyMode = (document.getElementById('v36KeyModeSelect') && document.getElementById('v36KeyModeSelect').value) || 'auto';
            const url = `/api/video36/upload_mp4?plant_id=${encodeURIComponent(activeVideo36PlantId)}&slot=${encodeURIComponent(slot)}&auto_atlas=${autoAtlas}&key_mode=${encodeURIComponent(keyMode)}`;
            fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'video/mp4'},
                body: file,
            }).then(res => res.json().then(body => ({ ok: res.ok, body }))).then(({ ok, body }) => {
                inputEl.value = '';
                if (!ok) {
                    alert('上传 MP4 失败: ' + (body.error || '未知错误'));
                    return;
                }
                loadVideo36List();
                if (body.auto_job && body.auto_job.job_id) {
                    renderVideo36JobStatus(body.auto_job);
                    startVideo36JobPoll(body.auto_job.job_id);
                } else {
                    const stEl = document.getElementById('v36JobStatusText');
                    if (stEl) {
                        stEl.innerText = body.ready_for_atlas
                            ? `✅ ${slot} 上传完成，已满足 Atlas 切片条件`
                            : `✅ ${slot} 上传完成，等待另一段配对 MP4`;
                        stEl.style.color = '#7ee787';
                    }
                }
            }).catch(err => {
                inputEl.value = '';
                alert('上传失败: ' + err);
            });
        }

        function toggleVideo36Publish() {
            if (!activeVideo36PlantId) return;
            const item = video36ById[activeVideo36PlantId];
            if (!item || !item.has_atlas) return;
            const nextPublished = !item.published;
            fetch('/api/video36/publish', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ plant_id: activeVideo36PlantId, published: nextPublished }),
            }).then(res => res.json()).then(res => {
                if (res.status === 'success') {
                    loadVideo36List();
                } else {
                    alert('切换发布状态失败: ' + (res.error || ''));
                }
            }).catch(err => alert('网络错误: ' + err));
        }

        loadData();
    </script>
</body>
</html>
"""

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send_json(self, status_code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path).path
        unquoted_path = urllib.parse.unquote(parsed_path)

        if parsed_path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
            
        elif parsed_path == '/api/candidates':
            os.makedirs(CANDIDATES_DIR, exist_ok=True)
            files = [
                f for f in os.listdir(CANDIDATES_DIR)
                if f.lower().endswith(('.png', '.jpg', '.jpeg'))
                and not f.startswith('.')
                and os.path.exists(os.path.join(CANDIDATES_DIR, f))
            ]
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(files).encode('utf-8'))

        elif parsed_path == '/api/trash':
            os.makedirs(TRASH_DIR, exist_ok=True)
            files = [f for f in os.listdir(TRASH_DIR) if f.lower().endswith(('.png', '.jpg', '.jpeg')) and not f.startswith('.')]
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(files).encode('utf-8'))
            
        elif parsed_path in ('/api/confirmed_candidates', '/api/confirmed_plants'):
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(get_confirmed_candidates()).encode('utf-8'))

        elif parsed_path == '/api/video36/list':
            self._send_json(200, _collect_video36_list())

        elif parsed_path == '/api/video36/jobs':
            with VIDEO36_JOBS_LOCK:
                jobs = [_serialize_video36_job(j) for j in VIDEO36_JOBS.values()]
            jobs.sort(key=lambda j: j['started_at'], reverse=True)
            self._send_json(200, {'jobs': jobs})

        elif parsed_path.startswith('/api/video36/jobs/'):
            job_id = unquoted_path[len('/api/video36/jobs/'):]
            if not _is_safe_path_segment(job_id):
                self._send_json(400, {'status': 'error', 'error': 'Invalid job_id'})
                return
            with VIDEO36_JOBS_LOCK:
                job = VIDEO36_JOBS.get(job_id)
                serialized = _serialize_video36_job(job) if job is not None else None
            if serialized is None:
                self._send_json(404, {'status': 'error', 'error': 'Job not found'})
            else:
                self._send_json(200, serialized)

        elif unquoted_path.startswith('/api/video36/file/'):
            rel = unquoted_path[len('/api/video36/file/'):]
            parts = rel.split('/')
            if len(parts) != 2 or not _is_safe_path_segment(parts[0]) or not _is_safe_path_segment(parts[1]) or parts[1] not in VIDEO36_FILE_ALLOWLIST:
                self._send_json(400, {'status': 'error', 'error': 'Invalid video36 file path'})
                return
            file_path = _resolve_video36_file_path(parts[0], parts[1])
            if file_path is None:
                self._send_json(404, {'status': 'error', 'error': 'File not found'})
                return
            self._serve_file(file_path)

        elif parsed_path == '/api/assets':
            os.makedirs(PLANTS_DIR, exist_ok=True)
            variants = []
            for plant_name in os.listdir(PLANTS_DIR):
                plant_path = os.path.join(PLANTS_DIR, plant_name)
                if os.path.isdir(plant_path):
                    has_subdirs = False
                    # Check for explicit variant subdirectories
                    for item in os.listdir(plant_path):
                        item_path = os.path.join(plant_path, item)
                        if os.path.isdir(item_path):
                            has_subdirs = True
                            entry = _load_asset_variant_entry(
                                plant_name, item, item_path, f'{plant_name}/{item}'
                            )
                            if entry is not None:
                                variants.append(entry)

                    # If no subdirs, treat the plant_path itself as the container (Legacy compatibility)
                    if not has_subdirs:
                        entry = _load_asset_variant_entry(
                            plant_name, 'default', plant_path, plant_name
                        )
                        if entry is not None:
                            variants.append(entry)
            
            # Sort by plant name, then variant_id descending
            variants.sort(key=lambda x: (x['plant_name'], x['variant_id']), reverse=True)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(variants).encode('utf-8'))
            
        elif parsed_path.startswith('/tools/candidates/'):
            filename = parsed_path.split('/')[-1]
            file_path = os.path.join(CANDIDATES_DIR, filename)
            self._serve_file(file_path)

        elif parsed_path.startswith('/tools/trash/'):
            filename = parsed_path.split('/')[-1]
            file_path = os.path.join(TRASH_DIR, filename)
            self._serve_file(file_path)
            
        elif parsed_path.startswith('/assets/'):
            file_path = os.path.join(PROJECT_ROOT, 'src', parsed_path.lstrip('/'))
            self._serve_file(file_path)
                
        else:
            self.send_response(404)
            self.end_headers()
            
    def _serve_file(self, file_path):
        try:
            file_path = urllib.parse.unquote(file_path)
        except Exception:
            pass

        if os.path.exists(file_path) and os.path.isfile(file_path):
            self.send_response(200)
            low = file_path.lower()
            if low.endswith('.png'):
                self.send_header('Content-type', 'image/png')
            elif low.endswith(('.jpg', '.jpeg')):
                self.send_header('Content-type', 'image/jpeg')
            elif low.endswith('.webp'):
                self.send_header('Content-type', 'image/webp')
            elif low.endswith('.mp4'):
                self.send_header('Content-type', 'video/mp4')
            elif low.endswith('.json'):
                self.send_header('Content-type', 'application/json; charset=utf-8')
            elif low.endswith('.txt'):
                self.send_header('Content-type', 'text/plain; charset=utf-8')
            self.end_headers()
            with open(file_path, 'rb') as f:
                try:
                    self.wfile.write(f.read())
                except (BrokenPipeError, ConnectionResetError):
                    pass
        else:
            self.send_response(404)
            self.end_headers()
            
    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path).path

        if parsed_path == '/api/video36/run':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('Invalid JSON object')
                job = _start_video36_job(
                    plant_id=str(data.get('plant_id', '')),
                    stages=str(data.get('stages', 'all')),
                    mode=str(data.get('mode', '2half')),
                    key_mode=str(data.get('key_mode', 'auto')),
                    force=bool(data.get('force', False)),
                    mirror_q0_for_270=bool(data.get('mirror_q0_for_270', False)),
                )
                self._send_json(202, {'status': 'accepted', **job})
            except ValueError as ve:
                self._send_json(400, {'status': 'error', 'error': str(ve)})
            except RuntimeError as re_err:
                self._send_json(409, {'status': 'error', 'error': str(re_err)})
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {'status': 'error', 'error': str(e)})
            return

        if parsed_path == '/api/video36/publish':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                if not isinstance(data, dict) or 'published' not in data:
                    raise ValueError('Invalid publish payload')
                res = _publish_video36_asset(
                    plant_id=str(data.get('plant_id', '')),
                    published=bool(data.get('published')),
                )
                self._send_json(200, res)
            except ValueError as ve:
                self._send_json(400, {'status': 'error', 'error': str(ve)})
            except FileNotFoundError as fnf:
                self._send_json(404, {'status': 'error', 'error': str(fnf)})
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {'status': 'error', 'error': str(e)})
            return

        if parsed_path == '/api/video36/upload_mp4':
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                self._send_json(400, {'status': 'error', 'error': 'Empty upload body'})
                return
            if content_length > MAX_VIDEO36_MP4_BYTES:
                self._send_json(413, {'status': 'error', 'error': 'File exceeds 120 MB limit'})
                return
            raw_bytes = self.rfile.read(content_length)
            try:
                res = _save_uploaded_video36_mp4(
                    plant_id=str((qs.get('plant_id') or [''])[0]),
                    slot=str((qs.get('slot') or [''])[0]),
                    raw_bytes=raw_bytes,
                    auto_atlas=str((qs.get('auto_atlas') or ['0'])[0]).lower() in ('1', 'true', 'yes'),
                    key_mode=str((qs.get('key_mode') or ['auto'])[0]),
                )
                self._send_json(200, res)
            except ValueError as ve:
                self._send_json(400, {'status': 'error', 'error': str(ve)})
            except RuntimeError as re_err:
                self._send_json(409, {'status': 'error', 'error': str(re_err)})
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {'status': 'error', 'error': str(e)})
            return

        if parsed_path == '/api/delete_candidate':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                filename = data.get('filename')
                input_path = os.path.join(CANDIDATES_DIR, filename)
                os.makedirs(TRASH_DIR, exist_ok=True)
                trash_path = os.path.join(TRASH_DIR, filename)
                
                if os.path.exists(input_path):
                    shutil.move(input_path, trash_path)
                    
                # Clean from confirmed candidates if present
                conf = get_confirmed_candidates()
                if filename in conf:
                    del conf[filename]
                    save_confirmed_candidates(conf)
                    
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))

        elif self.path == '/api/restore_trash':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                filename = data.get('filename')
                trash_path = os.path.join(TRASH_DIR, filename)
                os.makedirs(CANDIDATES_DIR, exist_ok=True)
                candidate_path = os.path.join(CANDIDATES_DIR, filename)
                
                if os.path.exists(trash_path):
                    shutil.move(trash_path, candidate_path)
                    
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))

        elif self.path == '/api/purge_trash':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                purge_all = data.get('all', False)
                filename = data.get('filename')
                
                if purge_all:
                    if os.path.exists(TRASH_DIR):
                        for f in os.listdir(TRASH_DIR):
                            fp = os.path.join(TRASH_DIR, f)
                            if os.path.isfile(fp) and not f.startswith('.'):
                                os.remove(fp)
                elif filename:
                    target_path = os.path.join(TRASH_DIR, filename)
                    if os.path.exists(target_path):
                        os.remove(target_path)
                        
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))

        elif self.path in ('/api/toggle_confirm_candidate', '/api/toggle_confirm_plant'):
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                filename = data.get('filename') or data.get('key')
                if not filename:
                    raise Exception("Missing candidate filename")
                
                confirmed_data = get_confirmed_candidates()
                if filename in confirmed_data:
                    del confirmed_data[filename]
                    is_confirmed = False
                else:
                    confirmed_data[filename] = {
                        "filename": filename,
                        "confirmed_at": int(time.time())
                    }
                    is_confirmed = True
                
                save_confirmed_candidates(confirmed_data)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "success",
                    "is_confirmed": is_confirmed,
                    "confirmed": confirmed_data
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))


        elif self.path == '/api/set_assets_enabled':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)

            try:
                data = json.loads(post_data.decode('utf-8'))
                if not isinstance(data, dict) or not isinstance(data.get('items'), list) or 'enabled' not in data:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "error": "Invalid request payload"}).encode('utf-8'))
                    return

                enabled = bool(data['enabled'])
                plants_real_root = os.path.realpath(PLANTS_DIR)
                validated_targets = []

                for item in data['items']:
                    if not isinstance(item, dict):
                        self.send_response(400)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "error", "error": "Invalid item entry"}).encode('utf-8'))
                        return

                    plant_name = item.get('plant_name')
                    variant_id = item.get('variant_id')
                    if not _is_safe_path_segment(plant_name) or not _is_safe_path_segment(variant_id):
                        self.send_response(400)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "error", "error": "Unsafe plant_name or variant_id"}).encode('utf-8'))
                        return

                    if variant_id == 'default':
                        variant_dir = os.path.join(PLANTS_DIR, plant_name)
                    else:
                        variant_dir = os.path.join(PLANTS_DIR, plant_name, variant_id)

                    real_variant = os.path.realpath(variant_dir)
                    if real_variant != plants_real_root and not real_variant.startswith(plants_real_root + os.sep):
                        self.send_response(400)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "error", "error": "Path escapes plants directory"}).encode('utf-8'))
                        return

                    validated_targets.append((plant_name, variant_id, variant_dir))

                updated_count = 0
                for plant_name, variant_id, variant_dir in validated_targets:
                    if not os.path.isdir(variant_dir):
                        continue
                    meta_file = os.path.join(variant_dir, 'metadata.json')
                    mdata = {}
                    if os.path.exists(meta_file):
                        try:
                            with open(meta_file, 'r', encoding='utf-8') as mf:
                                loaded = json.load(mf)
                                if isinstance(loaded, dict):
                                    mdata = loaded
                        except Exception:
                            mdata = {}
                    if not mdata:
                        mdata = {'name': plant_name, 'variant_id': variant_id}
                    mdata['enabled'] = enabled
                    with open(meta_file, 'w', encoding='utf-8') as mf:
                        json.dump(mdata, mf, ensure_ascii=False, indent=4)
                    updated_count += 1

                refresh_plant_manifest()

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "success",
                    "updated": updated_count,
                    "enabled": enabled
                }).encode('utf-8'))
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))

        elif self.path == '/api/remove_asset':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                plant_name = data.get('plant_name')
                variant_id = data.get('variant_id')
                
                if not plant_name or not variant_id:
                    raise Exception("Missing plant_name or variant_id")
                
                # 1. Recall original image
                recalled = False
                processed_dir = os.path.join(CANDIDATES_DIR, 'processed')
                if os.path.exists(processed_dir):
                    for f in os.listdir(processed_dir):
                        if plant_name in f:
                            src_path = os.path.join(processed_dir, f)
                            if os.path.isfile(src_path) or os.path.islink(src_path):
                                dst_path = os.path.join(CANDIDATES_DIR, f)
                                shutil.move(src_path, dst_path)
                                recalled = True
                
                # 2. Delete asset directory
                variant_dir = os.path.join(PLANTS_DIR, plant_name, variant_id)
                if os.path.exists(variant_dir):
                    shutil.rmtree(variant_dir)
                
                plant_dir = os.path.join(PLANTS_DIR, plant_name)
                if os.path.exists(plant_dir) and not os.listdir(plant_dir):
                    os.rmdir(plant_dir)
                    
                # 3. Clean CSS
                css_path = os.path.join(PROJECT_ROOT, 'src', 'styles.css')
                if os.path.exists(css_path):
                    with open(css_path, 'r', encoding='utf-8') as f:
                        css_content = f.read()
                    
                    pattern = r"\n*/\* CSS Output for " + re.escape(plant_name) + r" [^\n]*Variant: " + re.escape(variant_id) + r"\)[^\n]*\n(?:\.plant-sprite\.plant-" + re.escape(plant_name) + r"-v" + re.escape(variant_id) + r"[^\n]*\n*)+"
                    new_css_content = re.sub(pattern, "\n", css_content)
                    
                    with open(css_path, 'w', encoding='utf-8') as f:
                        f.write(new_css_content)

                refresh_plant_manifest()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "recalled_candidate": recalled}).encode('utf-8'))
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))

        elif self.path == '/api/reslice_asset':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                plant_name = data.get('plant_name')
                variant_id = data.get('variant_id')
                if not plant_name or not variant_id:
                    raise Exception("缺少 plant_name 或 variant_id")
                
                src_path = find_source_image_for_asset(plant_name, variant_id)
                if not src_path or not os.path.exists(src_path):
                    raise Exception(f"未找到植物 [{plant_name}] 的原始图像，无法执行重切。")
                
                display_name = plant_name
                meta_file = os.path.join(PLANTS_DIR, plant_name, variant_id, 'metadata.json')
                if os.path.exists(meta_file):
                    try:
                        with open(meta_file, 'r', encoding='utf-8') as mf:
                            mdata = json.load(mf)
                            display_name = mdata.get('display_name', plant_name)
                    except Exception:
                        pass
                
                if not run_build_asset(src_path, plant_name, variant_id, display_name):
                    raise Exception("真彩切片生成失败")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "success",
                    "plant_name": plant_name,
                    "variant_id": variant_id,
                    "source_image": os.path.basename(src_path)
                }).encode('utf-8'))
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))

        elif self.path == '/api/reslice_batch':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                items = data.get('items', [])
                if not items:
                    raise Exception("未指定要重切的植物资产列表")
                
                succeeded = 0
                failed = 0
                errors = []
                
                for item in items:
                    p_name = item.get('plant_name')
                    v_id = item.get('variant_id')
                    try:
                        src_path = find_source_image_for_asset(p_name, v_id)
                        if not src_path or not os.path.exists(src_path):
                            raise Exception(f"未找到原图: {p_name}")
                        
                        display_name = p_name
                        meta_file = os.path.join(PLANTS_DIR, p_name, v_id, 'metadata.json')
                        if os.path.exists(meta_file):
                            try:
                                with open(meta_file, 'r', encoding='utf-8') as mf:
                                    mdata = json.load(mf)
                                    display_name = mdata.get('display_name', p_name)
                            except Exception:
                                pass
                        
                        if run_build_asset(src_path, p_name, v_id, display_name):
                            succeeded += 1
                        else:
                            failed += 1
                            errors.append(f"{p_name}: 构建失败")
                    except Exception as err:
                        failed += 1
                        errors.append(f"{p_name}: {str(err)}")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "success",
                    "total": len(items),
                    "succeeded": succeeded,
                    "failed": failed,
                    "errors": errors
                }).encode('utf-8'))
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))

        elif self.path == '/api/process':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                plant_name = data.get('name')
                display_name = data.get('display_name', '').strip()
                filename = data.get('filename')
                
                input_path = os.path.join(CANDIDATES_DIR, filename)
                if not os.path.exists(input_path):
                    raise Exception("Candidate image not found.")
                
                build_script = os.path.join(PROJECT_ROOT, 'tools', 'build_plant_asset.py')
                cmd = ["uv", "run", "python", build_script, input_path, plant_name, "auto"]
                if display_name:
                    cmd.append(display_name)
                print(f"\n⚙️ 正在执行打包脚本 (Phase 1): {' '.join(cmd)}")
                
                result = subprocess.run(cmd, cwd=os.path.join(PROJECT_ROOT, 'tools'), capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"✅ 处理成功！归档备选图: {filename}")
                    processed_dir = os.path.join(CANDIDATES_DIR, 'processed')
                    os.makedirs(processed_dir, exist_ok=True)
                    shutil.move(input_path, os.path.join(processed_dir, filename))
                    
                    # Clean from confirmed candidates if present
                    conf = get_confirmed_candidates()
                    if filename in conf:
                        del conf[filename]
                        save_confirmed_candidates(conf)

                    # Refresh manifest immediately after slicing
                    refresh_plant_manifest()

                    # Find the newest variant folder under plant_name
                    plant_dir = os.path.join(PLANTS_DIR, plant_name)
                    if os.path.exists(plant_dir):
                        subdirs = [os.path.join(plant_dir, d) for d in os.listdir(plant_dir) if os.path.isdir(os.path.join(plant_dir, d))]
                        if subdirs:
                            newest_dir = max(subdirs, key=lambda d: os.path.getmtime(d))
                            meta_file = os.path.join(newest_dir, 'metadata.json')

                            def run_ai_analysis(meta_path, p_name, d_name):
                                print(f"\n🤖 开始 AI 分析: {p_name} ({d_name})")
                                try:
                                    analysis = ai_botanist.analyze_plant_botany(p_name, d_name)
                                    print(f"✅ AI 分析完成: {analysis.get('status')}")
                                    if os.path.exists(meta_path):
                                        with open(meta_path, 'r', encoding='utf-8') as f:
                                            m = json.load(f)
                                        m['status'] = 'ready'
                                        m['ai_analysis'] = analysis
                                        with open(meta_path, 'w', encoding='utf-8') as f:
                                            json.dump(m, f, ensure_ascii=False, indent=4)
                                except Exception as e:
                                    print(f"❌ AI 分析异常: {e}")
                                    if os.path.exists(meta_path):
                                        try:
                                            with open(meta_path, 'r', encoding='utf-8') as f:
                                                m = json.load(f)
                                            m['status'] = 'ready'
                                            m['ai_analysis'] = {"status": "failed", "brief": f"Error: {e}"}
                                            with open(meta_path, 'w', encoding='utf-8') as f:
                                                json.dump(m, f, ensure_ascii=False, indent=4)
                                        except:
                                            pass
                                finally:
                                    refresh_plant_manifest()

                            threading.Thread(target=run_ai_analysis, args=(meta_file, plant_name, display_name), daemon=True).start()
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                else:
                    print(f"❌ 脚本执行失败:\n{result.stderr}")
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "error": result.stderr}).encode('utf-8'))
                    
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))

class ThreadingStudioServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    handler = RequestHandler
    with ThreadingStudioServer(("", PORT), handler) as httpd:
        print(f"\\n🌱 Botanist Studio V2 (造物审查终端) 已启动！")
        print(f"🌐 请在浏览器中打开: http://localhost:{PORT}")
        print(f"⚠️ 按 Ctrl+C 停止服务\\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\\n🛑 正在安全释放 {PORT} 端口...")
            httpd.server_close()
            print("✅ 服务已完全停止。")
