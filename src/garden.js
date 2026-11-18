/**
 * Psy Garden 2.5D Dynamic Height & Elliptical Footprint Engine
 * Preserves pure CSS 3D plane (.world-3d-container) + counter-rotated .plant-sprite billboards.
 */

const HEIGHT_CLAMP_MIN = 28;
const HEIGHT_CLAMP_MAX = 230;
const DEFAULT_HEIGHT_RANGE = [70, 130];
const DEFAULT_WORLD_WIDTH = 2880;
const DEFAULT_WORLD_HEIGHT = 1800;
const INCENSE_EXCLUSION_RADIUS_PX = 55;
const ELLIPSE_PADDING_X = 8;
const ELLIPSE_PADDING_Y = 6;
const FOOTPRINT_ASPECT_RATIO = 0.55;
const GARDEN_SCATTER_RADIUS_PCT = 26;
const MAX_PLACEMENT_ATTEMPTS = 45;
const DEFAULT_PLANT_COUNT = 55;

const PLANT_TYPE_BUCKETS = ['tree', 'shrub', 'flower', 'groundcover'];
const PLANT_TYPE_LABELS = {
    tree: '乔木',
    shrub: '灌木',
    flower: '花卉',
    groundcover: '地被',
};
const GROWTH_HABIT_LABELS = {
    canopy: '伞冠型',
    bush: '丛生型',
    slender: '直立修长',
    carpet: '地被铺展',
};
const NEON_TINTS = ['tint-cyan', 'tint-magenta', 'tint-emerald', 'tint-gold'];
const COLOR_MODE_ORDER = ['natural', 'gold', 'neon'];
const COLOR_MODE_LABELS = {
    natural: '🎨 色彩: 原生真彩',
    gold: '🎨 色彩: 金色回想',
    neon: '🎨 色彩: 霓虹幻彩',
};

/**
 * Clean 6-plant fallback dataset used when assets/plants/manifest.json is unavailable.
 */
const FALLBACK_PLANTS = [
    {
        id: 'banana__v_rgb',
        name: 'banana',
        display_name: '芭蕉',
        variant_id: 'v_rgb',
        path_prefix: 'assets/plants/banana/v_rgb',
        width: 1024,
        height: 1024,
        root_width: 95,
        root_ratio: 0.093,
        ground_ellipse: [95, 52],
        true_color: true,
        layer_files: [
            { slot: 'green', file: 'plant_banana_multi_green.png' },
            { slot: 'red', file: 'plant_banana_multi_red.png' },
            { slot: 'blue', file: 'plant_banana_multi_blue.png' },
        ],
        ai_analysis: {
            zh_name: '芭蕉',
            scientific_name: 'Musa basjoo',
            botanical_height_m: [2.0, 4.0],
            plant_type: 'shrub',
            growth_habit: 'canopy',
            suggested_game_height_px: [130, 220],
            brief: '芭蕉为大型多年生草本，具粗壮假茎和宽大长椭圆形叶片，叶色翠绿，呈伞状丛生。',
        },
    },
    {
        id: 'bamboo__v_rgb',
        name: 'bamboo',
        display_name: '竹子',
        variant_id: 'v_rgb',
        path_prefix: 'assets/plants/bamboo/v_rgb',
        width: 1024,
        height: 1024,
        root_width: 54,
        root_ratio: 0.053,
        ground_ellipse: [54, 30],
        true_color: true,
        layer_files: [
            { slot: 'green', file: 'plant_bamboo_multi_green.png' },
            { slot: 'red', file: 'plant_bamboo_multi_red.png' },
            { slot: 'blue', file: 'plant_bamboo_multi_blue.png' },
        ],
        ai_analysis: {
            zh_name: '竹子',
            scientific_name: 'Bambusoideae',
            botanical_height_m: [0.3, 30.0],
            plant_type: 'shrub',
            growth_habit: 'slender',
            suggested_game_height_px: [35, 220],
            brief: '竹子为禾本科竹亚科常绿植物，具木质化中空竹秆、明显节间和披针形叶片。',
        },
    },
    {
        id: 'mushroom__v_rgb',
        name: 'mushroom',
        display_name: '蘑菇',
        variant_id: 'v_rgb',
        path_prefix: 'assets/plants/mushroom/v_rgb',
        width: 1024,
        height: 1024,
        root_width: 93,
        root_ratio: 0.091,
        ground_ellipse: [93, 51],
        true_color: true,
        layer_files: [
            { slot: 'green', file: 'plant_mushroom_multi_green.png' },
            { slot: 'red', file: 'plant_mushroom_multi_red.png' },
            { slot: 'blue', file: 'plant_mushroom_multi_blue.png' },
        ],
        ai_analysis: {
            zh_name: '蘑菇',
            scientific_name: 'Agaricus bisporus',
            botanical_height_m: [0.05, 0.15],
            plant_type: 'groundcover',
            growth_habit: 'carpet',
            suggested_game_height_px: [35, 40],
            brief: '蘑菇是一种真菌，具有菌盖和菌柄，通常生长在腐殖质丰富的土壤或堆肥上。',
        },
    },
    {
        id: 'fern__v_rgb',
        name: 'fern',
        display_name: '蕨类',
        variant_id: 'v_rgb',
        path_prefix: 'assets/plants/fern/v_rgb',
        width: 1024,
        height: 1024,
        root_width: 61,
        root_ratio: 0.06,
        ground_ellipse: [61, 34],
        true_color: true,
        layer_files: [
            { slot: 'green', file: 'plant_fern_multi_green.png' },
            { slot: 'red', file: 'plant_fern_multi_red.png' },
            { slot: 'blue', file: 'plant_fern_multi_blue.png' },
        ],
        ai_analysis: {
            zh_name: '蕨类',
            scientific_name: 'Pteridophyta',
            botanical_height_m: [0.1, 1.5],
            plant_type: 'groundcover',
            growth_habit: 'carpet',
            suggested_game_height_px: [28, 120],
            brief: '蕨类植物具有羽状复叶、孢子囊群和根状茎，多呈丛生或地毯状生长。',
        },
    },
    {
        id: 'bonsai__v_rgb',
        name: 'bonsai',
        display_name: '盆景松',
        variant_id: 'v_rgb',
        path_prefix: 'assets/plants/bonsai/v_rgb',
        width: 1024,
        height: 1024,
        root_width: 138,
        root_ratio: 0.135,
        ground_ellipse: [138, 76],
        true_color: true,
        layer_files: [
            { slot: 'green', file: 'plant_bonsai_multi_green.png' },
            { slot: 'red', file: 'plant_bonsai_multi_red.png' },
            { slot: 'blue', file: 'plant_bonsai_multi_blue.png' },
        ],
        ai_analysis: {
            zh_name: '盆景松',
            scientific_name: 'Pinus parviflora',
            botanical_height_m: [0.3, 1.5],
            plant_type: 'tree',
            growth_habit: 'canopy',
            suggested_game_height_px: [35, 100],
            brief: '盆景松为常绿针叶小乔木，针叶簇生、树冠紧凑，常经修剪造型呈盘曲优雅的盆景姿态。',
        },
    },
    {
        id: 'lily__v_rgb',
        name: 'lily',
        display_name: '百合',
        variant_id: 'v_rgb',
        path_prefix: 'assets/plants/lily/v_rgb',
        width: 1024,
        height: 1024,
        root_width: 28,
        root_ratio: 0.027,
        ground_ellipse: [28, 15],
        true_color: true,
        layer_files: [
            { slot: 'green', file: 'plant_lily_multi_green.png' },
            { slot: 'red', file: 'plant_lily_multi_red.png' },
            { slot: 'blue', file: 'plant_lily_multi_blue.png' },
        ],
        ai_analysis: {
            zh_name: '百合',
            scientific_name: 'Lilium spp.',
            botanical_height_m: [0.3, 1.5],
            plant_type: 'flower',
            growth_habit: 'slender',
            suggested_game_height_px: [35, 140],
            brief: '百合为具鳞茎的多年生草本，茎直立不分枝，叶披针形互生或轮生，顶生大型漏斗状花，花被片六枚，色彩丰富。',
        },
    },
];

function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

/**
 * Extract and clamp [minPx, maxPx] from plant.ai_analysis.suggested_game_height_px.
 */
function getHeightRange(plant) {
    const raw = plant && plant.ai_analysis && plant.ai_analysis.suggested_game_height_px;
    let rawMin = DEFAULT_HEIGHT_RANGE[0];
    let rawMax = DEFAULT_HEIGHT_RANGE[1];
    if (Array.isArray(raw) && raw.length >= 2) {
        const a = Number(raw[0]);
        const b = Number(raw[1]);
        if (Number.isFinite(a) && Number.isFinite(b)) {
            rawMin = Math.min(a, b);
            rawMax = Math.max(a, b);
        }
    }
    const minPx = clamp(Math.round(rawMin), HEIGHT_CLAMP_MIN, HEIGHT_CLAMP_MAX);
    const maxPx = Math.max(minPx, clamp(Math.round(rawMax), HEIGHT_CLAMP_MIN, HEIGHT_CLAMP_MAX));
    return [minPx, maxPx];
}

/**
 * Extract and clamp normalized [ax, ay] stem-base root anchor UV from plant.root_anchor.
 */
function getRootAnchorUV(plant) {
    const raw = plant && plant.root_anchor;
    if (Array.isArray(raw) && raw.length >= 2) {
        const rx = raw[0];
        const ry = raw[1];
        const validX = typeof rx === 'number' || (typeof rx === 'string' && rx.trim() !== '');
        const validY = typeof ry === 'number' || (typeof ry === 'string' && ry.trim() !== '');
        if (validX && validY) {
            const ax = Number(rx);
            const ay = Number(ry);
            if (Number.isFinite(ax) && Number.isFinite(ay)) {
                return [clamp(ax, 0.05, 0.95), clamp(ay, 0.60, 1.0)];
            }
        }
    }
    return [0.5, 1.0];
}

/**
 * Sample botanical height in px, derive aspect-ratio-preserved sprite size and 0.55 elliptical stem footprint.
 */
function samplePlantDimensions(plant, rng = Math.random) {
    const [minPx, maxPx] = getHeightRange(plant);
    const span = Math.max(0, maxPx - minPx);
    const targetH = clamp(Math.round(minPx + rng() * span), HEIGHT_CLAMP_MIN, HEIGHT_CLAMP_MAX);
    const srcH = plant && Number(plant.height) > 0 ? Number(plant.height) : 250;
    const srcW = plant && Number(plant.width) > 0 ? Number(plant.width) : 180;
    const scaleFactor = targetH / srcH;
    const targetW = Math.max(12, Math.round(srcW * scaleFactor));

    const fallbackRootW = (plant && Number(plant.width) > 0 ? Number(plant.width) : 160) * 0.15;
    const baseRootW = plant && Number(plant.root_width) > 0 ? Number(plant.root_width) : fallbackRootW;
    const fpW = Math.max(10, Math.round(baseRootW * scaleFactor));
    const fpH = Math.max(6, Math.round(fpW * FOOTPRINT_ASPECT_RATIO));

    return {
        targetW,
        targetH,
        scaleFactor,
        fpW,
        fpH,
        minPx,
        maxPx,
    };
}

function pctToPx(leftPct, topPct, worldW = DEFAULT_WORLD_WIDTH, worldH = DEFAULT_WORLD_HEIGHT) {
    return {
        xPx: (leftPct / 100) * worldW,
        yPx: (topPct / 100) * worldH,
    };
}

function pxToPct(xPx, yPx, worldW = DEFAULT_WORLD_WIDTH, worldH = DEFAULT_WORLD_HEIGHT) {
    return {
        leftPct: (xPx / worldW) * 100,
        topPct: (yPx / worldH) * 100,
    };
}

/**
 * Accurate isometric depth sorting for rotateX(60deg) rotateZ(45deg).
 */
function computeZIndex(leftPct, topPct) {
    return Math.floor((topPct + leftPct) * 10);
}

/**
 * Check 2.5D elliptical ground footprint collision against the central incense shrine and placed plants.
 * Canopies in the air may overhang freely; only ground footprints collide.
 */
function checkFootprintCollision(candidate, placedPlants = [], worldSize = {}) {
    const worldW = worldSize && Number(worldSize.width) > 0 ? Number(worldSize.width) : DEFAULT_WORLD_WIDTH;
    const worldH = worldSize && Number(worldSize.height) > 0 ? Number(worldSize.height) : DEFAULT_WORLD_HEIGHT;

    let xPx = candidate.xPx;
    let yPx = candidate.yPx;
    if (!Number.isFinite(xPx) || !Number.isFinite(yPx)) {
        const pt = pctToPx(candidate.leftPct, candidate.topPct, worldW, worldH);
        xPx = pt.xPx;
        yPx = pt.yPx;
    }

    const center = pctToPx(50, 50, worldW, worldH);
    const distToIncense = Math.hypot(xPx - center.xPx, yPx - center.yPx);
    if (distToIncense < INCENSE_EXCLUSION_RADIUS_PX) {
        return { collides: true, reason: 'incense', colliderId: 'incense-shrine' };
    }

    for (let i = 0; i < placedPlants.length; i += 1) {
        const other = placedPlants[i];
        let ox = other.xPx;
        let oy = other.yPx;
        if (!Number.isFinite(ox) || !Number.isFinite(oy)) {
            const opt = pctToPx(other.leftPct, other.topPct, worldW, worldH);
            ox = opt.xPx;
            oy = opt.yPx;
        }
        const dx = xPx - ox;
        const dy = yPx - oy;
        const a = (candidate.fpW + other.fpW) / 2 + ELLIPSE_PADDING_X;
        const b = (candidate.fpH + other.fpH) / 2 + ELLIPSE_PADDING_Y;
        if ((dx / a) ** 2 + (dy / b) ** 2 < 1) {
            return { collides: true, reason: 'plant', colliderId: other.instanceId || other.id || null };
        }
    }

    return { collides: false, reason: null, colliderId: null };
}

function sampleGardenPoint(rng = Math.random, radiusPct = GARDEN_SCATTER_RADIUS_PCT) {
    const r = Math.sqrt(rng()) * radiusPct;
    const theta = rng() * Math.PI * 2;
    return {
        leftPct: 50 + r * Math.cos(theta),
        topPct: 50 + r * Math.sin(theta),
    };
}

/**
 * Group plants into ecological buckets (tree, shrub, flower, groundcover) and scatter up to `count` plants.
 */
function planEcologicalScatter(plants, count = DEFAULT_PLANT_COUNT, worldSize = {}, rng = Math.random) {
    if (!Array.isArray(plants) || plants.length === 0) return [];

    const buckets = {
        tree: [],
        shrub: [],
        flower: [],
        groundcover: [],
        other: [],
    };
    for (let i = 0; i < plants.length; i += 1) {
        const p = plants[i];
        const pType = (p && p.ai_analysis && p.ai_analysis.plant_type) || 'other';
        if (buckets[pType]) {
            buckets[pType].push(p);
        } else {
            buckets.other.push(p);
        }
    }

    const activeBuckets = PLANT_TYPE_BUCKETS.concat(['other'])
        .map((key) => buckets[key])
        .filter((arr) => arr.length > 0);
    if (activeBuckets.length === 0) return [];

    const placed = [];
    for (let i = 0; i < count; i += 1) {
        const bucket = activeBuckets[i % activeBuckets.length];
        const plant = bucket[Math.floor(rng() * bucket.length)];
        const dims = samplePlantDimensions(plant, rng);

        for (let attempt = 0; attempt < MAX_PLACEMENT_ATTEMPTS; attempt += 1) {
            const pt = sampleGardenPoint(rng, GARDEN_SCATTER_RADIUS_PCT);
            const candidate = {
                leftPct: pt.leftPct,
                topPct: pt.topPct,
                fpW: dims.fpW,
                fpH: dims.fpH,
            };
            const collision = checkFootprintCollision(candidate, placed, worldSize);
            if (!collision.collides) {
                placed.push({
                    instanceId: `specimen-${i}-${attempt}`,
                    plant,
                    leftPct: pt.leftPct,
                    topPct: pt.topPct,
                    zIndex: computeZIndex(pt.leftPct, pt.topPct),
                    ...dims,
                });
                break;
            }
        }
    }
    return placed;
}

/**
 * Analytical inverse of CSS perspective(1200px) + rotateX(60deg) rotateZ(45deg) centered in viewportRect.
 */
function screenToWorldPct(clientX, clientY, viewportRect, worldSize = {}, perspectivePx = 1200) {
    if (!viewportRect || viewportRect.width <= 0 || viewportRect.height <= 0) return null;
    const cx = viewportRect.left + viewportRect.width / 2;
    const cy = viewportRect.top + viewportRect.height / 2;
    const dx = clientX - cx;
    const dy = clientY - cy;

    const radX = (60 * Math.PI) / 180;
    const radZ = (45 * Math.PI) / 180;
    const cosX = Math.cos(radX);
    const sinX = Math.sin(radX);
    const cosZ = Math.cos(radZ);
    const sinZ = Math.sin(radZ);

    const denom = cosX * perspectivePx + dy * sinX;
    if (Math.abs(denom) < 1e-6) return null;

    const y1 = (dy * perspectivePx) / denom;
    const z2 = y1 * sinX;
    if (perspectivePx - z2 <= 1e-3) return null;

    const scale = perspectivePx / (perspectivePx - z2);
    const x1 = dx / scale;

    const u = x1 * cosZ + y1 * sinZ;
    const v = -x1 * sinZ + y1 * cosZ;

    const worldW = worldSize && Number(worldSize.width) > 0 ? Number(worldSize.width) : DEFAULT_WORLD_WIDTH;
    const worldH = worldSize && Number(worldSize.height) > 0 ? Number(worldSize.height) : DEFAULT_WORLD_HEIGHT;

    return {
        leftPct: 50 + (u / worldW) * 100,
        topPct: 50 + (v / worldH) * 100,
    };
}

function formatBotanicalMeters(botanicalHeightM) {
    if (!Array.isArray(botanicalHeightM) || botanicalHeightM.length === 0) return '';
    const minM = Number(botanicalHeightM[0]);
    const maxM = Number(botanicalHeightM[1] !== undefined ? botanicalHeightM[1] : minM);
    if (!Number.isFinite(minM)) return '';
    const mid = Number.isFinite(maxM) ? (minM + maxM) / 2 : minM;
    if (mid >= 2) {
        return `${Math.round(mid)}m`;
    }
    const rounded = Math.round(mid * 10) / 10;
    return `${rounded}m`;
}

function getPlantMetaSummary(plant) {
    const ai = (plant && plant.ai_analysis) || {};
    const zhName = ai.zh_name || (plant && (plant.display_name || plant.name)) || '未命名植物';
    const scientificName = ai.scientific_name || 'Species indet.';
    const plantType = ai.plant_type || 'shrub';
    const growthHabit = ai.growth_habit || 'bush';
    const botanicalHeightM = Array.isArray(ai.botanical_height_m) ? ai.botanical_height_m : null;
    const suggestedRange = getHeightRange(plant);
    const rootRatio = plant && Number.isFinite(Number(plant.root_ratio))
        ? Number(plant.root_ratio)
        : 0.15;
    const rootRatioPct = Math.round(rootRatio * 1000) / 10;
    const brief = ai.brief || '暂无 AI 植物形态简述。';

    return {
        zhName,
        scientificName,
        plantType,
        plantTypeLabel: PLANT_TYPE_LABELS[plantType] || plantType,
        growthHabit,
        growthHabitLabel: GROWTH_HABIT_LABELS[growthHabit] || growthHabit,
        botanicalHeightM,
        suggestedRange,
        rootRatioPct,
        brief,
    };
}

/* =========================================================================
   Browser Runtime Controller
   ========================================================================= */
if (typeof document !== 'undefined') {
    const state = {
        catalogPlants: [],
        placedPlants: [],
        showFootprints: true,
        colorMode: 'natural',
        catalogOpen: false,
        activeFilterType: 'all',
        searchQuery: '',
        selectedPlantForPlanting: null,
        previewDims: null,
        specimenCounter: 0,
    };

    function getWorldSize() {
        const container = document.querySelector('.world-3d-container');
        if (container && container.clientWidth > 0 && container.clientHeight > 0) {
            return { width: container.clientWidth, height: container.clientHeight };
        }
        return { width: DEFAULT_WORLD_WIDTH, height: DEFAULT_WORLD_HEIGHT };
    }

    function getLayerTintClass(plant, slotIdx, instanceIdx) {
        if (state.colorMode === 'gold') return 'tint-gold';
        if (state.colorMode === 'neon') {
            return NEON_TINTS[(slotIdx + instanceIdx) % NEON_TINTS.length];
        }
        if (plant && plant.true_color === false) {
            return 'tint-emerald';
        }
        return '';
    }

    function applyColorModeToExistingSprites() {
        const nodes = document.querySelectorAll('#world-plants .plant-node');
        nodes.forEach((node, instanceIdx) => {
            const specimen = state.placedPlants.find((item) => item.instanceId === node.dataset.instanceId);
            const plant = specimen ? specimen.plant : null;
            const layers = node.querySelectorAll('.sprite-layer');
            layers.forEach((layerEl, slotIdx) => {
                NEON_TINTS.forEach((cls) => layerEl.classList.remove(cls));
                layerEl.classList.remove('tint-ghost');
                const tint = getLayerTintClass(plant, slotIdx, instanceIdx);
                if (tint) layerEl.classList.add(tint);
            });
        });
    }

    function createPlantNodeElement(specimen, instanceIdx) {
        const { instanceId, plant, leftPct, topPct, zIndex, targetW, targetH, fpW, fpH } = specimen;
        const meta = getPlantMetaSummary(plant);

        const node = document.createElement('div');
        node.className = 'plant-node';
        node.dataset.instanceId = instanceId;
        node.dataset.plantType = meta.plantType;
        node.tabIndex = 0;
        node.setAttribute('role', 'button');
        node.setAttribute('aria-label', `${meta.zhName} ${meta.scientificName}，高度 ${targetH} 像素`);
        node.style.left = `${leftPct}%`;
        node.style.top = `${topPct}%`;
        node.style.zIndex = String(zIndex);

        const footprint = document.createElement('div');
        footprint.className = `plant-footprint type-${meta.plantType}`;
        footprint.style.width = `${fpW}px`;
        footprint.style.height = `${fpH}px`;
        const anchorDot = document.createElement('span');
        anchorDot.className = 'plant-footprint-dot';
        anchorDot.setAttribute('aria-hidden', 'true');
        footprint.appendChild(anchorDot);
        node.appendChild(footprint);

        const sprite = document.createElement('div');
        sprite.className = 'plant-sprite';
        sprite.style.width = `${targetW}px`;
        sprite.style.height = `${targetH}px`;

        const [ax, ay] = getRootAnchorUV(plant);
        const shiftXPct = Math.round((0.5 - ax) * 10000) / 100;
        const shiftYPct = Math.round((1.0 - ay) * 10000) / 100;
        const hasAnchorShift = Math.abs(shiftXPct) > 0.05 || Math.abs(shiftYPct) > 0.05;

        const layers = Array.isArray(plant.layer_files) && plant.layer_files.length > 0
            ? plant.layer_files
            : [{ slot: 'green', file: 'layer_green.png' }];
        layers.forEach((layer, slotIdx) => {
            const layerEl = document.createElement('div');
            const tint = getLayerTintClass(plant, slotIdx, instanceIdx);
            layerEl.className = `sprite-layer layer-${layer.slot}${tint ? ` ${tint}` : ''}`;
            layerEl.style.backgroundImage = `url('${plant.path_prefix}/${layer.file}')`;
            if (hasAnchorShift) {
                layerEl.style.transform = `translate(${shiftXPct}%, ${shiftYPct}%)`;
            }
            sprite.appendChild(layerEl);
        });

        const meterStr = formatBotanicalMeters(meta.botanicalHeightM);
        const rulerTag = document.createElement('span');
        rulerTag.className = 'plant-ruler-tag';
        rulerTag.textContent = meterStr ? `${targetH}px · ${meterStr}` : `${targetH}px`;
        sprite.appendChild(rulerTag);

        node.appendChild(sprite);

        const showInspector = () => {
            if (state.selectedPlantForPlanting) return;
            document.querySelectorAll('#world-plants .plant-node.is-inspected').forEach((el) => {
                if (el !== node) el.classList.remove('is-inspected');
            });
            node.classList.add('is-inspected');
            renderInspectorCard(specimen);
        };
        const hideInspector = () => {
            node.classList.remove('is-inspected');
            const card = document.getElementById('plant-inspector-card');
            if (card) card.hidden = true;
        };

        node.addEventListener('mouseenter', showInspector);
        node.addEventListener('focus', showInspector);
        node.addEventListener('mouseleave', hideInspector);
        node.addEventListener('blur', hideInspector);

        return node;
    }

    function renderInspectorCard(specimen) {
        const card = document.getElementById('plant-inspector-card');
        if (!card || !specimen) return;
        const { plant, targetH, fpW, fpH, minPx, maxPx } = specimen;
        const meta = getPlantMetaSummary(plant);

        const botanicalText = meta.botanicalHeightM
            ? `${meta.botanicalHeightM[0]} – ${meta.botanicalHeightM[1] !== undefined ? meta.botanicalHeightM[1] : meta.botanicalHeightM[0]} m`
            : '未知';

        card.querySelector('[data-field="zh-name"]').textContent = meta.zhName;
        card.querySelector('[data-field="sci-name"]').textContent = meta.scientificName;
        const typeEl = card.querySelector('[data-field="type-tag"]');
        typeEl.textContent = meta.plantTypeLabel;
        typeEl.className = `inspector-tag type-${meta.plantType}`;
        card.querySelector('[data-field="habit-tag"]').textContent = meta.growthHabitLabel;
        card.querySelector('[data-field="botanical-height"]').textContent = botanicalText;
        card.querySelector('[data-field="sampled-height"]').textContent = `${targetH} px (AI 区间: ${minPx}~${maxPx} px)`;
        card.querySelector('[data-field="footprint-size"]').textContent = `${fpW} × ${fpH} px (根茎比 ${meta.rootRatioPct}%)`;
        card.querySelector('[data-field="brief"]').textContent = meta.brief;
        card.hidden = false;
    }

    function renderGardenDOM() {
        const gardenBed = document.getElementById('world-plants');
        if (!gardenBed) return;
        gardenBed.innerHTML = '';
        gardenBed.classList.toggle('footprints-visible', state.showFootprints);

        const frag = document.createDocumentFragment();
        state.placedPlants.forEach((specimen, idx) => {
            frag.appendChild(createPlantNodeElement(specimen, idx));
        });
        gardenBed.appendChild(frag);
    }

    function randomizeGarden(count = DEFAULT_PLANT_COUNT) {
        if (!state.catalogPlants.length) return;
        const worldSize = getWorldSize();
        state.placedPlants = planEcologicalScatter(state.catalogPlants, count, worldSize);
        state.specimenCounter = state.placedPlants.length;
        const card = document.getElementById('plant-inspector-card');
        if (card) card.hidden = true;
        renderGardenDOM();
    }

    function clearGarden() {
        state.placedPlants = [];
        const card = document.getElementById('plant-inspector-card');
        if (card) card.hidden = true;
        renderGardenDOM();
    }

    function toggleFootprintMode() {
        state.showFootprints = !state.showFootprints;
        const gardenBed = document.getElementById('world-plants');
        if (gardenBed) {
            gardenBed.classList.toggle('footprints-visible', state.showFootprints);
        }
        const btn = document.getElementById('btn-toggle-footprints');
        if (btn) {
            btn.setAttribute('aria-pressed', String(state.showFootprints));
            btn.textContent = state.showFootprints
                ? '🪐 占地圈与高度标尺: 开'
                : '🪐 占地圈与高度标尺: 关';
        }
    }

    function cycleColorMode() {
        const idx = COLOR_MODE_ORDER.indexOf(state.colorMode);
        state.colorMode = COLOR_MODE_ORDER[(idx + 1) % COLOR_MODE_ORDER.length];
        const btn = document.getElementById('btn-cycle-color');
        if (btn) {
            btn.textContent = COLOR_MODE_LABELS[state.colorMode];
        }
        applyColorModeToExistingSprites();
    }

    function updateCatalogButtonCount() {
        const btn = document.getElementById('btn-toggle-catalog');
        if (btn) {
            btn.textContent = `🌿 植物图鉴与手植 (共 ${state.catalogPlants.length} 种)`;
        }
    }

    function toggleCatalogDrawer(forceState) {
        state.catalogOpen = typeof forceState === 'boolean' ? forceState : !state.catalogOpen;
        const drawer = document.getElementById('catalog-drawer');
        const btn = document.getElementById('btn-toggle-catalog');
        if (drawer) {
            drawer.hidden = !state.catalogOpen;
            drawer.classList.toggle('is-open', state.catalogOpen);
        }
        if (btn) {
            btn.setAttribute('aria-expanded', String(state.catalogOpen));
        }
        if (state.catalogOpen) {
            const searchInput = document.getElementById('catalog-search');
            if (searchInput) searchInput.focus();
        }
    }

    function enterPlantingMode(plant) {
        state.selectedPlantForPlanting = plant;
        state.previewDims = samplePlantDimensions(plant);
        const layout = document.querySelector('.app-layout');
        if (layout) layout.classList.add('planting-mode');

        const banner = document.getElementById('planting-banner');
        const meta = getPlantMetaSummary(plant);
        if (banner) {
            banner.textContent = `🌱 手植模式: ${meta.zhName} (${state.previewDims.targetH}px) · 移动预览占地 · 点击种植 · 右键或 Esc 退出`;
            banner.hidden = false;
        }
        const preview = document.getElementById('footprint-preview');
        if (preview) {
            preview.style.width = `${state.previewDims.fpW}px`;
            preview.style.height = `${state.previewDims.fpH}px`;
            preview.hidden = false;
        }
        const card = document.getElementById('plant-inspector-card');
        if (card) card.hidden = true;
        renderCatalogGrid();
    }

    function exitPlantingMode() {
        state.selectedPlantForPlanting = null;
        state.previewDims = null;
        const layout = document.querySelector('.app-layout');
        if (layout) layout.classList.remove('planting-mode');
        const banner = document.getElementById('planting-banner');
        if (banner) banner.hidden = true;
        const preview = document.getElementById('footprint-preview');
        if (preview) preview.hidden = true;
        renderCatalogGrid();
    }

    function renderCatalogGrid() {
        const grid = document.getElementById('catalog-grid');
        if (!grid) return;
        const query = state.searchQuery.trim().toLowerCase();
        const filtered = state.catalogPlants.filter((plant) => {
            const meta = getPlantMetaSummary(plant);
            if (state.activeFilterType !== 'all' && meta.plantType !== state.activeFilterType) {
                return false;
            }
            if (!query) return true;
            return (
                meta.zhName.toLowerCase().includes(query) ||
                meta.scientificName.toLowerCase().includes(query) ||
                (plant.name && plant.name.toLowerCase().includes(query))
            );
        });

        grid.innerHTML = '';
        if (filtered.length === 0) {
            const empty = document.createElement('p');
            empty.className = 'catalog-empty';
            empty.textContent = '没有匹配的植物种类';
            grid.appendChild(empty);
            return;
        }

        const frag = document.createDocumentFragment();
        filtered.forEach((plant) => {
            const meta = getPlantMetaSummary(plant);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'catalog-card';
            if (state.selectedPlantForPlanting && state.selectedPlantForPlanting.id === plant.id) {
                btn.classList.add('is-selected');
                btn.setAttribute('aria-pressed', 'true');
            } else {
                btn.setAttribute('aria-pressed', 'false');
            }

            const thumb = document.createElement('span');
            thumb.className = 'catalog-thumb';
            thumb.setAttribute('aria-hidden', 'true');
            const thumbLayer = (Array.isArray(plant.layer_files) && plant.layer_files[0])
                ? `${plant.path_prefix}/${plant.layer_files[0].file}`
                : '';
            if (thumbLayer) {
                thumb.style.backgroundImage = `url('${thumbLayer}')`;
            }

            const info = document.createElement('span');
            info.className = 'catalog-info';

            const nameRow = document.createElement('span');
            nameRow.className = 'catalog-name-row';
            const zhSpan = document.createElement('strong');
            zhSpan.className = 'catalog-zh';
            zhSpan.textContent = meta.zhName;
            const badgeSpan = document.createElement('span');
            badgeSpan.className = `catalog-type-pill type-${meta.plantType}`;
            badgeSpan.textContent = meta.plantTypeLabel;
            nameRow.appendChild(zhSpan);
            nameRow.appendChild(badgeSpan);

            const sciSpan = document.createElement('span');
            sciSpan.className = 'catalog-sci';
            sciSpan.lang = 'la';
            sciSpan.textContent = meta.scientificName;

            const metricsSpan = document.createElement('span');
            metricsSpan.className = 'catalog-metrics';
            const mText = formatBotanicalMeters(meta.botanicalHeightM) || '-';
            metricsSpan.textContent = `${meta.suggestedRange[0]}~${meta.suggestedRange[1]}px · ~${mText}`;

            info.appendChild(nameRow);
            info.appendChild(sciSpan);
            info.appendChild(metricsSpan);

            btn.appendChild(thumb);
            btn.appendChild(info);

            btn.addEventListener('click', () => {
                if (state.selectedPlantForPlanting && state.selectedPlantForPlanting.id === plant.id) {
                    exitPlantingMode();
                } else {
                    enterPlantingMode(plant);
                }
            });

            frag.appendChild(btn);
        });
        grid.appendChild(frag);
    }

    function bindWorldPlantingEvents() {
        const layerWorld = document.querySelector('.layer-world');
        if (!layerWorld) return;

        let lastPreviewValid = false;
        let lastCoords = null;

        layerWorld.addEventListener('mousemove', (e) => {
            if (!state.selectedPlantForPlanting || !state.previewDims) return;
            const rect = layerWorld.getBoundingClientRect();
            const worldSize = getWorldSize();
            const pct = screenToWorldPct(e.clientX, e.clientY, rect, worldSize, 1200);
            const preview = document.getElementById('footprint-preview');
            if (!pct || !preview) return;

            lastCoords = pct;
            const collision = checkFootprintCollision(
                { leftPct: pct.leftPct, topPct: pct.topPct, fpW: state.previewDims.fpW, fpH: state.previewDims.fpH },
                state.placedPlants,
                worldSize
            );
            lastPreviewValid = !collision.collides;

            preview.style.left = `${pct.leftPct}%`;
            preview.style.top = `${pct.topPct}%`;
            preview.style.zIndex = String(computeZIndex(pct.leftPct, pct.topPct));
            preview.classList.toggle('is-valid', lastPreviewValid);
            preview.classList.toggle('is-collision', !lastPreviewValid);
            preview.hidden = false;
        });

        layerWorld.addEventListener('click', (e) => {
            if (!state.selectedPlantForPlanting || !state.previewDims) return;
            const rect = layerWorld.getBoundingClientRect();
            const worldSize = getWorldSize();
            const pct = screenToWorldPct(e.clientX, e.clientY, rect, worldSize, 1200) || lastCoords;
            if (!pct) return;

            const collision = checkFootprintCollision(
                { leftPct: pct.leftPct, topPct: pct.topPct, fpW: state.previewDims.fpW, fpH: state.previewDims.fpH },
                state.placedPlants,
                worldSize
            );
            if (collision.collides) return;

            const plant = state.selectedPlantForPlanting;
            const dims = state.previewDims;
            state.specimenCounter += 1;
            const specimen = {
                instanceId: `manual-${state.specimenCounter}`,
                plant,
                leftPct: pct.leftPct,
                topPct: pct.topPct,
                zIndex: computeZIndex(pct.leftPct, pct.topPct),
                ...dims,
            };
            state.placedPlants.push(specimen);

            const gardenBed = document.getElementById('world-plants');
            if (gardenBed) {
                gardenBed.appendChild(createPlantNodeElement(specimen, state.placedPlants.length - 1));
            }

            // Freshly sample height for next click
            state.previewDims = samplePlantDimensions(plant);
            const preview = document.getElementById('footprint-preview');
            if (preview) {
                preview.style.width = `${state.previewDims.fpW}px`;
                preview.style.height = `${state.previewDims.fpH}px`;
            }
            const banner = document.getElementById('planting-banner');
            const meta = getPlantMetaSummary(plant);
            if (banner) {
                banner.textContent = `🌱 手植模式: ${meta.zhName} (${state.previewDims.targetH}px) · 移动预览占地 · 点击种植 · 右键或 Esc 退出`;
            }
        });

        document.addEventListener('contextmenu', (e) => {
            if (state.selectedPlantForPlanting) {
                e.preventDefault();
                exitPlantingMode();
            }
        });
    }

    function bindHudAndCatalogControls() {
        const btnRandomize = document.getElementById('btn-randomize');
        if (btnRandomize) btnRandomize.addEventListener('click', () => randomizeGarden(DEFAULT_PLANT_COUNT));

        const btnFootprints = document.getElementById('btn-toggle-footprints');
        if (btnFootprints) btnFootprints.addEventListener('click', toggleFootprintMode);

        const btnColor = document.getElementById('btn-cycle-color');
        if (btnColor) btnColor.addEventListener('click', cycleColorMode);

        const btnCatalog = document.getElementById('btn-toggle-catalog');
        if (btnCatalog) btnCatalog.addEventListener('click', () => toggleCatalogDrawer());

        const btnCloseCatalog = document.getElementById('btn-close-catalog');
        if (btnCloseCatalog) btnCloseCatalog.addEventListener('click', () => toggleCatalogDrawer(false));

        const btnClear = document.getElementById('btn-clear-garden');
        if (btnClear) btnClear.addEventListener('click', clearGarden);

        const btnFocus = document.getElementById('btn-focus-mode');
        const focusOverlay = document.getElementById('focus-mode-ui');
        if (btnFocus && focusOverlay) {
            btnFocus.addEventListener('click', () => {
                focusOverlay.style.display = 'flex';
            });
        }
        const btnCloseFocus = document.getElementById('btn-close-focus');
        if (btnCloseFocus && focusOverlay) {
            btnCloseFocus.addEventListener('click', () => {
                focusOverlay.style.display = 'none';
            });
        }

        const searchInput = document.getElementById('catalog-search');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                state.searchQuery = e.target.value || '';
                renderCatalogGrid();
            });
        }

        const filterButtons = document.querySelectorAll('.catalog-filter-pill');
        filterButtons.forEach((pill) => {
            pill.addEventListener('click', () => {
                state.activeFilterType = pill.dataset.filterType || 'all';
                filterButtons.forEach((b) => {
                    const active = b === pill;
                    b.classList.toggle('is-active', active);
                    b.setAttribute('aria-pressed', String(active));
                });
                renderCatalogGrid();
            });
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (state.selectedPlantForPlanting) {
                    exitPlantingMode();
                } else if (focusOverlay && focusOverlay.style.display === 'flex') {
                    focusOverlay.style.display = 'none';
                } else if (state.catalogOpen) {
                    toggleCatalogDrawer(false);
                }
            }
        });
    }

    async function loadManifestOrFallback() {
        try {
            const resp = await fetch('assets/plants/manifest.json', { cache: 'no-cache' });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            if (data && Array.isArray(data.plants) && data.plants.length > 0) {
                return data.plants;
            }
        } catch (_err) {
            // Fall back cleanly to built-in 6-plant set when manifest.json is absent.
        }
        return FALLBACK_PLANTS.slice();
    }

    async function initGarden() {
        bindHudAndCatalogControls();
        bindWorldPlantingEvents();
        state.catalogPlants = await loadManifestOrFallback();
        updateCatalogButtonCount();
        renderCatalogGrid();
        randomizeGarden(DEFAULT_PLANT_COUNT);
    }

    if (typeof window !== 'undefined') {
        window.randomizeGarden = randomizeGarden;
        window.toggleFootprintMode = toggleFootprintMode;
        window.cycleColorMode = cycleColorMode;
        window.toggleCatalogDrawer = toggleCatalogDrawer;
        window.clearGarden = clearGarden;
        window.createPlantNodeElement = createPlantNodeElement;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initGarden);
    } else {
        initGarden();
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        HEIGHT_CLAMP_MIN,
        HEIGHT_CLAMP_MAX,
        DEFAULT_HEIGHT_RANGE,
        DEFAULT_WORLD_WIDTH,
        DEFAULT_WORLD_HEIGHT,
        INCENSE_EXCLUSION_RADIUS_PX,
        FOOTPRINT_ASPECT_RATIO,
        GARDEN_SCATTER_RADIUS_PCT,
        MAX_PLACEMENT_ATTEMPTS,
        DEFAULT_PLANT_COUNT,
        FALLBACK_PLANTS,
        clamp,
        getHeightRange,
        getRootAnchorUV,
        samplePlantDimensions,
        pctToPx,
        pxToPct,
        computeZIndex,
        checkFootprintCollision,
        sampleGardenPoint,
        planEcologicalScatter,
        screenToWorldPct,
        formatBotanicalMeters,
        getPlantMetaSummary,
    };
}
