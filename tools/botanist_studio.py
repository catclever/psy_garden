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

PORT = int(os.environ.get("PORT", 18087))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATES_DIR = os.path.join(PROJECT_ROOT, 'tools', 'candidates')
TRASH_DIR = os.path.join(PROJECT_ROOT, 'tools', 'trash')
PLANTS_DIR = os.path.join(PROJECT_ROOT, 'src', 'assets', 'plants')
CONFIRMED_CANDIDATES_FILE = os.path.join(PROJECT_ROOT, 'tools', 'confirmed_candidates.json')
CONFIRMED_PLANTS_FILE = os.path.join(PROJECT_ROOT, 'tools', 'confirmed_plants.json')

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
                <div style="display:flex; align-items:center; gap:10px;">
                    <h1 style="border:none; margin:0; padding:0; font-size:20px;">📦 资产画廊 (Asset Gallery)</h1>
                    <span class="badge" id="assetsTotalCount">0 资产</span>
                </div>
                <div>
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
                    <button id="modalResliceAssetBtn" class="btn-reslice">🎨 重新切片(升级真彩)</button>
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

    <script>
        let currentLeftTab = 'candidates';
        let confirmedCandidates = {};
        let selectedAssets = {};
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
            document.querySelectorAll('.card[data-asset-key]').forEach(card => {
                const key = card.getAttribute('data-asset-key');
                if (selectedAssets[key]) {
                    card.classList.add('selected-asset');
                } else {
                    card.classList.remove('selected-asset');
                }
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
            fetch('/api/assets')
                .then(res => res.json())
                .then(assets => {
                    document.getElementById('assetsTotalCount').innerText = `${assets.length} 资产`;
                    
                    const aGal = document.getElementById('assetsGallery');
                    aGal.innerHTML = '';
                    if (assets.length === 0) {
                        aGal.innerHTML = '<div class="empty-state">资产库为空</div>';
                        return;
                    }
                    
                    assets.forEach(variant => {
                        const key = `${variant.plant_name}__${variant.variant_id}`;
                        const isSelected = !!selectedAssets[key];
                        const card = document.createElement('div');
                        card.className = 'card' + (isSelected ? ' selected-asset' : '');
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

                        card.innerHTML = `
                            ${colorBadgeHtml}
                            <button class="card-close-btn" title="移出资产并召回原图" onclick="event.stopPropagation(); removeAsset('${variant.plant_name}', '${variant.variant_id}', '${displayTitleStr.replace(/'/g, "\\'")}')">&times;</button>
                            <div class="img-container">${imagesHtml}</div>
                            <div class="title" style="margin-top:2px;">${titleHtml}</div>
                            <div class="subtitle" style="margin-top:2px;">Variant: ${variant.variant_id}</div>
                            <div style="margin-top:6px;">
                                <span class="layer-badge-btn" onclick="event.stopPropagation(); openLayerModal(${variantDataStr})">
                                    🧩 ${variant.layers.length} 个独立图层 ↗
                                </span>
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
                    if (assets.some(v => v.status === 'ai_analyzing')) {
                        startAiStatusPolling();
                    }
                }).catch(err => {
                    console.error("加载资产失败:", err);
                });
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

        function openLayerModal(variant) {
            activeModalVariant = variant;
            const hasAlias = variant.display_name && variant.display_name !== variant.plant_name;
            const titleText = hasAlias ? `${variant.display_name} (${variant.plant_name})` : variant.plant_name;
            const colorTag = variant.true_color ? '✨ 真彩高保真' : '⚪ 传统灰度';
            document.getElementById('layerModalTitle').innerText = `🌱 ${titleText} · Variant: ${variant.variant_id} [${colorTag}]`;
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

            const removeBtn = document.getElementById('modalRemoveAssetBtn');
            if (removeBtn) {
                removeBtn.onclick = () => {
                    closeLayerModal();
                    removeAsset(variant.plant_name, variant.variant_id, titleText);
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
        
        loadData();
    </script>
</body>
</html>
"""

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
            
        elif self.path == '/api/candidates':
            os.makedirs(CANDIDATES_DIR, exist_ok=True)
            files = [f for f in os.listdir(CANDIDATES_DIR) if f.endswith(('.png', '.jpg', '.jpeg'))]
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(files).encode('utf-8'))

        elif self.path == '/api/trash':
            os.makedirs(TRASH_DIR, exist_ok=True)
            files = [f for f in os.listdir(TRASH_DIR) if f.lower().endswith(('.png', '.jpg', '.jpeg')) and not f.startswith('.')]
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(files).encode('utf-8'))
            
        elif self.path in ('/api/confirmed_candidates', '/api/confirmed_plants'):
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(get_confirmed_candidates()).encode('utf-8'))

        elif self.path == '/api/assets':
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
                            layers = [f for f in os.listdir(item_path) if f.endswith('.png')]
                            if layers:
                                display_name = plant_name
                                true_color = False
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
                                            display_name = mdata.get('display_name') or mdata.get('name') or plant_name
                                            true_color = bool(mdata.get('true_color', False))
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
                                variants.append({
                                    'plant_name': plant_name,
                                    'display_name': display_name,
                                    'variant_id': item,
                                    'layers': layers,
                                    'true_color': true_color,
                                    'source_image': source_image,
                                    'status': status,
                                    'ai_analysis': ai_analysis,
                                    'root_width': root_width,
                                    'root_ratio': root_ratio,
                                    'ground_ellipse': ground_ellipse,
                                    'width': width,
                                    'height': height,
                                    'path_prefix': f'{plant_name}/{item}'
                                })
                    
                    # If no subdirs, treat the plant_path itself as the container (Legacy compatibility)
                    if not has_subdirs:
                        layers = [f for f in os.listdir(plant_path) if f.endswith('.png')]
                        if layers:
                            display_name = plant_name
                            true_color = False
                            source_image = None
                            status = 'ready'
                            ai_analysis = None
                            root_width = None
                            root_ratio = None
                            ground_ellipse = None
                            width = None
                            height = None
                            meta_file = os.path.join(plant_path, 'metadata.json')
                            if os.path.exists(meta_file):
                                try:
                                    with open(meta_file, 'r', encoding='utf-8') as mf:
                                        mdata = json.load(mf)
                                        display_name = mdata.get('display_name') or mdata.get('name') or plant_name
                                        true_color = bool(mdata.get('true_color', False))
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
                            variants.append({
                                'plant_name': plant_name,
                                'display_name': display_name,
                                'variant_id': 'default',
                                'layers': layers,
                                'true_color': true_color,
                                'source_image': source_image,
                                'status': status,
                                'ai_analysis': ai_analysis,
                                'root_width': root_width,
                                'root_ratio': root_ratio,
                                'ground_ellipse': ground_ellipse,
                                'width': width,
                                'height': height,
                                'path_prefix': plant_name
                            })
            
            # Sort by plant name, then variant_id descending
            variants.sort(key=lambda x: (x['plant_name'], x['variant_id']), reverse=True)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(variants).encode('utf-8'))
            
        elif self.path.startswith('/tools/candidates/'):
            filename = self.path.split('/')[-1]
            file_path = os.path.join(CANDIDATES_DIR, filename)
            self._serve_file(file_path)

        elif self.path.startswith('/tools/trash/'):
            filename = self.path.split('/')[-1]
            file_path = os.path.join(TRASH_DIR, filename)
            self._serve_file(file_path)
            
        elif self.path.startswith('/assets/'):
            file_path = os.path.join(PROJECT_ROOT, 'src', self.path.lstrip('/'))
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
            if file_path.lower().endswith('.png'):
                self.send_header('Content-type', 'image/png')
            elif file_path.lower().endswith(('.jpg', '.jpeg')):
                self.send_header('Content-type', 'image/jpeg')
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
        if self.path == '/api/delete_candidate':
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

if __name__ == "__main__":
    handler = RequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"\\n🌱 Botanist Studio V2 (造物审查终端) 已启动！")
        print(f"🌐 请在浏览器中打开: http://localhost:{PORT}")
        print(f"⚠️ 按 Ctrl+C 停止服务\\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\\n🛑 正在安全释放 {PORT} 端口...")
            httpd.server_close()
            print("✅ 服务已完全停止。")
