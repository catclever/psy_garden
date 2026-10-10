import http.server
import socketserver
import json
import os
import glob
import subprocess
import traceback
import urllib.parse
import shutil
import time

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
        
        .layer-red { filter: sepia(1) saturate(5) hue-rotate(-50deg); }
        .layer-green { filter: sepia(1) saturate(5) hue-rotate(50deg); }
        .layer-blue { filter: sepia(1) saturate(5) hue-rotate(180deg); }
        
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
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #30363d; padding-bottom:12px; margin-bottom:15px;">
                <h1 style="border:none; margin:0; padding:0; font-size:20px;">📦 资产画廊 (Asset Gallery)</h1>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span class="badge" id="assetsTotalCount">0 资产</span>
                </div>
            </div>
            <p class="subtitle">已切片入库资产；点击图层标牌可拆解检视各独立分层。</p>
            <div class="gallery" id="assetsGallery">
                <div class="empty-state">正在加载...</div>
            </div>
        </div>
    </div>
    
    <!-- Modal: Process -->
    <div id="processModal" class="modal-overlay">
        <div class="modal-content">
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
                <button class="modal-close-btn" onclick="closeLayerModal()">&times;</button>
            </div>
            <div class="layer-modal-body">
                <!-- Left: Composite Preview & Controls -->
                <div class="layer-composite-pane">
                    <div class="pane-title">🎨 实时合成预览</div>
                    <div class="checkerboard-box" id="compositePreviewBox">
                        <!-- Layer images injected dynamically -->
                    </div>
                    <div style="margin-top:4px;">
                        <div style="font-size:12px; font-weight:bold; color:#8b949e; margin-bottom:8px;">图层显隐开关:</div>
                        <div id="layerToggleList" style="display:flex; flex-direction:column; gap:6px;"></div>
                    </div>
                </div>
                <!-- Right: Individual Slices -->
                <div class="layer-slices-pane">
                    <div class="pane-title">🧩 各图层切片展开 (点击“🔗 查看原图”可直接新窗口打开原PNG)</div>
                    <div class="slices-grid" id="layerSlicesGrid">
                        <!-- Slices injected dynamically -->
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentLeftTab = 'candidates';
        let confirmedCandidates = {};

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
                        const card = document.createElement('div');
                        card.className = 'card';
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

                        card.innerHTML = `
                            <div class="img-container">${imagesHtml}</div>
                            <div class="title" style="margin-top:2px;">${titleHtml}</div>
                            <div class="subtitle" style="margin-top:2px;">Variant: ${variant.variant_id}</div>
                            <div style="margin-top:6px;">
                                <span class="layer-badge-btn" onclick="event.stopPropagation(); openLayerModal(${variantDataStr})">
                                    🧩 ${variant.layers.length} 个独立图层 ↗
                                </span>
                            </div>
                        `;
                        
                        card.onclick = () => openLayerModal(variant);
                        aGal.appendChild(card);
                    });
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
            const hasAlias = variant.display_name && variant.display_name !== variant.plant_name;
            const titleText = hasAlias ? `${variant.display_name} (${variant.plant_name})` : variant.plant_name;
            document.getElementById('layerModalTitle').innerText = `🌱 ${titleText} · Variant: ${variant.variant_id}`;
            document.getElementById('layerModalSubtitle').innerText = `路径: assets/plants/${variant.path_prefix} | 共 ${variant.layers.length} 个独立分层切片`;

            const compositeBox = document.getElementById('compositePreviewBox');
            compositeBox.innerHTML = '';
            const toggleList = document.getElementById('layerToggleList');
            toggleList.innerHTML = '';
            const slicesGrid = document.getElementById('layerSlicesGrid');
            slicesGrid.innerHTML = '';

            const fallbackColors = ['layer-red', 'layer-green', 'layer-blue'];
            const fallbackTags = ['tag-red', 'tag-green', 'tag-blue'];
            const fallbackLabels = ['红通道 (花叶/顶盖)', '绿通道 (茎枝/主干)', '蓝通道 (细节点缀)'];

            variant.layers.forEach((layer, idx) => {
                let cssClass = '';
                let tagClass = 'tag-default';
                let label = '分层切片';

                if (layer.includes('red') || layer.includes('flower') || layer.includes('cap') || layer.includes('leaves')) {
                    cssClass = 'layer-red';
                    tagClass = 'tag-red';
                    label = '红通道 (花叶/顶盖)';
                } else if (layer.includes('green') || layer.includes('stem') || layer.includes('trunk')) {
                    cssClass = 'layer-green';
                    tagClass = 'tag-green';
                    label = '绿通道 (茎枝/主干)';
                } else if (layer.includes('blue') || layer.includes('accent')) {
                    cssClass = 'layer-blue';
                    tagClass = 'tag-blue';
                    label = '蓝通道 (细节点缀)';
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

                // 2. Toggle row
                const toggleItem = document.createElement('label');
                toggleItem.style.cssText = 'display:flex; align-items:center; gap:8px; font-size:12px; color:#c9d1d9; cursor:pointer; background:#0d1117; padding:6px 10px; border-radius:6px; border:1px solid #30363d; user-select:none;';
                toggleItem.innerHTML = `
                    <input type="checkbox" checked onchange="document.getElementById('comp-layer-${idx}').style.opacity = this.checked ? '1' : '0'" style="accent-color:#1f6feb; cursor:pointer;" />
                    <span class="slice-tag ${tagClass}">${layer}</span>
                    <span style="color:#8b949e; font-size:11px;">${label}</span>
                `;
                toggleList.appendChild(toggleItem);

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

            document.getElementById('layerModal').style.display = 'flex';
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
            document.getElementById('processModal').style.display = 'flex';
            document.getElementById('plantNameInput').focus();
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
            
            const btn = document.getElementById('confirmBtn');
            btn.innerText = '处理中...';
            btn.disabled = true;
            
            fetch('/api/process', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: name, display_name: displayName, filename: filename })
            }).then(res => res.json()).then(res => {
                if(res.status === 'success') {
                    document.getElementById('processModal').style.display = 'none';
                    loadData(); // reload
                } else {
                    alert('处理失败: ' + res.error);
                }
            }).catch(err => alert('网络错误: ' + err))
            .finally(() => {
                btn.innerText = '自动切片并入库';
                btn.disabled = false;
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
                                meta_file = os.path.join(item_path, 'metadata.json')
                                if os.path.exists(meta_file):
                                    try:
                                        with open(meta_file, 'r', encoding='utf-8') as mf:
                                            mdata = json.load(mf)
                                            display_name = mdata.get('display_name') or mdata.get('name') or plant_name
                                    except Exception:
                                        pass
                                variants.append({
                                    'plant_name': plant_name,
                                    'display_name': display_name,
                                    'variant_id': item,
                                    'layers': layers,
                                    'path_prefix': f'{plant_name}/{item}'
                                })
                    
                    # If no subdirs, treat the plant_path itself as the container (Legacy compatibility)
                    if not has_subdirs:
                        layers = [f for f in os.listdir(plant_path) if f.endswith('.png')]
                        if layers:
                            display_name = plant_name
                            meta_file = os.path.join(plant_path, 'metadata.json')
                            if os.path.exists(meta_file):
                                try:
                                    with open(meta_file, 'r', encoding='utf-8') as mf:
                                        mdata = json.load(mf)
                                        display_name = mdata.get('display_name') or mdata.get('name') or plant_name
                                except Exception:
                                    pass
                            variants.append({
                                'plant_name': plant_name,
                                'display_name': display_name,
                                'variant_id': 'default',
                                'layers': layers,
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
                self.wfile.write(f.read())
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
                print(f"\n⚙️ 正在执行打包脚本: {' '.join(cmd)}")
                
                result = subprocess.run(cmd, cwd=os.path.join(PROJECT_ROOT, 'tools'), capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"✅ 处理成功！清理备选图: {filename}")
                    os.remove(input_path)
                    
                    # Clean from confirmed candidates if present
                    conf = get_confirmed_candidates()
                    if filename in conf:
                        del conf[filename]
                        save_confirmed_candidates(conf)
                    
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
