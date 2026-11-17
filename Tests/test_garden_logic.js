const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC_DIR = path.join(__dirname, '..', 'src');
const draftJsPath = path.join(SRC_DIR, 'garden_task_garden_25d_dynamic_height_and_footprint_scene_029.js');
const postJsPath = path.join(SRC_DIR, 'garden.js');
const garden = require(fs.existsSync(draftJsPath) ? draftJsPath : postJsPath);

function makeSeededRng(seed = 42) {
    let s = seed >>> 0;
    return () => {
        s = (1664525 * s + 1013904223) >>> 0;
        return s / 0x100000000;
    };
}

function testDynamicHeightSamplingBounds() {
    // 1. Clamping out-of-range AI suggestions to [28, 230]
    const extremePlant = {
        width: 200,
        height: 400,
        root_width: 40,
        ai_analysis: { suggested_game_height_px: [10, 350] },
    };
    const [clampedMin, clampedMax] = garden.getHeightRange(extremePlant);
    assert.strictEqual(clampedMin, 28, 'minPx should clamp to 28');
    assert.strictEqual(clampedMax, 230, 'maxPx should clamp to 230');

    // 2. Fallback [70, 130] when ai_analysis is absent
    const noAiPlant = { width: 180, height: 250 };
    assert.deepStrictEqual(garden.getHeightRange(noAiPlant), [70, 130]);

    // 3. Sampling bounds across min/max rng
    const samplePlant = {
        width: 221,
        height: 250,
        root_width: 20,
        ai_analysis: { suggested_game_height_px: [180, 220] },
    };
    const atMin = garden.samplePlantDimensions(samplePlant, () => 0);
    const atMax = garden.samplePlantDimensions(samplePlant, () => 0.999999);
    assert.strictEqual(atMin.targetH, 180);
    assert.strictEqual(atMax.targetH, 220);

    const rng = makeSeededRng(12345);
    for (let i = 0; i < 50; i += 1) {
        const d = garden.samplePlantDimensions(samplePlant, rng);
        assert.ok(d.targetH >= 180 && d.targetH <= 220, `sampled targetH ${d.targetH} out of range`);
    }
}

function testAspectRatioAndGroundEllipseScaling() {
    const plant = {
        width: 400,
        height: 200,
        root_width: 100,
        ai_analysis: { suggested_game_height_px: [100, 100] },
    };
    const dims = garden.samplePlantDimensions(plant, () => 0.5);
    // targetH = 100, scaleFactor = 100 / 200 = 0.5
    assert.strictEqual(dims.targetH, 100);
    assert.strictEqual(dims.scaleFactor, 0.5);
    assert.strictEqual(dims.targetW, 200, 'width should preserve 2:1 aspect ratio');
    assert.strictEqual(dims.fpW, 50, 'fpW should scale root_width by scaleFactor');
    assert.strictEqual(dims.fpH, Math.round(50 * 0.55), 'fpH should be 0.55 * fpW');
    assert.strictEqual(dims.fpH, 28);

    // Minimum floor checks (fpW >= 10, fpH >= 6, targetW >= 12)
    const tinyPlant = {
        width: 40,
        height: 200,
        root_width: 12,
        ai_analysis: { suggested_game_height_px: [28, 28] },
    };
    const tinyDims = garden.samplePlantDimensions(tinyPlant, () => 0);
    assert.strictEqual(tinyDims.targetH, 28);
    assert.ok(tinyDims.targetW >= 12, 'targetW floor >= 12');
    assert.strictEqual(tinyDims.fpW, 10, 'fpW floor >= 10');
    assert.strictEqual(tinyDims.fpH, 6, 'fpH floor >= 6');
}

function testEllipticalCollisionAndCanopyOverlap() {
    const worldSize = { width: 2880, height: 1800 };
    // Place a wide-canopy tree at (40%, 40%) -> (1152px, 720px), well away from incense (1440, 900)
    const tree = {
        instanceId: 'tree-1',
        xPx: 1152,
        yPx: 720,
        targetW: 200,
        targetH: 220,
        fpW: 20,
        fpH: 11,
    };

    // Overlapping root: a = (20+16)/2 + 8 = 26, b = (11+9)/2 + 6 = 16
    const overlappingRoot = { xPx: 1152 + 20, yPx: 720 + 5, fpW: 16, fpH: 9 };
    const col1 = garden.checkFootprintCollision(overlappingRoot, [tree], worldSize);
    assert.strictEqual(col1.collides, true, 'must reject overlapping ground footprint ellipses');
    assert.strictEqual(col1.reason, 'plant');

    // Canopy overhang allowed! Groundcover root at dx=28px (> a=26px) is clear on the ground,
    // even though visually the tree's 200px wide canopy overhangs it.
    const underCanopyFlower = { xPx: 1152 + 28, yPx: 720, targetW: 50, targetH: 45, fpW: 16, fpH: 9 };
    const col2 = garden.checkFootprintCollision(underCanopyFlower, [tree], worldSize);
    assert.strictEqual(col2.collides, false, 'must allow canopy overlap when ground footprints do not collide');

    // Verify vertical semi-axis b is tighter than horizontal semi-axis a (2.5D squash)
    const verticalClear = { xPx: 1152, yPx: 720 + 17, fpW: 16, fpH: 9 }; // dy=17 > b=16
    const col3 = garden.checkFootprintCollision(verticalClear, [tree], worldSize);
    assert.strictEqual(col3.collides, false, 'dy > b on x=0 should clear ellipse');
}

function testIncenseShrineExclusion() {
    const worldSize = { width: 2880, height: 1800 };
    // Center is (1440, 900)
    const tooClose = { leftPct: 50.5, topPct: 50.5, fpW: 14, fpH: 8 }; // ~17px away
    const resClose = garden.checkFootprintCollision(tooClose, [], worldSize);
    assert.strictEqual(resClose.collides, true);
    assert.strictEqual(resClose.reason, 'incense');

    const safeSpot = { xPx: 1440 + 60, yPx: 900, fpW: 14, fpH: 8 }; // 60px >= 55px
    const resSafe = garden.checkFootprintCollision(safeSpot, [], worldSize);
    assert.strictEqual(resSafe.collides, false);
}

function testIsometricDepthSorting() {
    const back = garden.computeZIndex(25, 25);   // 500
    const mid = garden.computeZIndex(50, 50);    // 1000
    const front = garden.computeZIndex(75, 75);  // 1500
    const sideA = garden.computeZIndex(30, 60);  // 900
    const sideB = garden.computeZIndex(45, 55);  // 1000

    assert.strictEqual(back, 500);
    assert.strictEqual(mid, 1000);
    assert.strictEqual(front, 1500);
    assert.ok(back < sideA && sideA < sideB && sideB < front);
}

function testEcologicalScatterAndProjection() {
    const rng = makeSeededRng(2026);
    const placed = garden.planEcologicalScatter(garden.FALLBACK_PLANTS, 55, { width: 2880, height: 1800 }, rng);
    assert.strictEqual(placed.length, 55, 'should place all 55 ecological specimens');

    // Verify all 4 ecological plant_type buckets are represented
    const typesSeen = new Set(placed.map((p) => p.plant.ai_analysis.plant_type));
    assert.ok(typesSeen.has('tree') && typesSeen.has('shrub') && typesSeen.has('flower') && typesSeen.has('groundcover'));

    // Verify zero pairwise root collisions and zero incense collisions
    for (let i = 0; i < placed.length; i += 1) {
        const others = placed.slice(0, i);
        const res = garden.checkFootprintCollision(placed[i], others, { width: 2880, height: 1800 });
        assert.strictEqual(res.collides, false, `placed specimen ${i} should not collide`);
        const distPct = Math.hypot(placed[i].leftPct - 50, placed[i].topPct - 50);
        assert.ok(distPct <= 26.001, 'specimen should stay within central 26% garden ellipse');
    }

    // Verify screenToWorldPct center maps back to (50%, 50%)
    const centerPct = garden.screenToWorldPct(
        720,
        450,
        { left: 0, top: 0, width: 1440, height: 900 },
        { width: 2880, height: 1800 },
        1200
    );
    assert.ok(Math.abs(centerPct.leftPct - 50) < 1e-6);
    assert.ok(Math.abs(centerPct.topPct - 50) < 1e-6);
}

function testDraftHtmlAndCssContracts() {
    const draftHtmlPath = path.join(SRC_DIR, 'index_task_garden_25d_dynamic_height_and_footprint_scene_029.html');
    const postHtmlPath = path.join(SRC_DIR, 'index.html');
    const draftCssPath = path.join(SRC_DIR, 'styles_task_garden_25d_dynamic_height_and_footprint_scene_029.css');
    const postCssPath = path.join(SRC_DIR, 'styles.css');
    const htmlPath = fs.existsSync(draftHtmlPath) ? draftHtmlPath : postHtmlPath;
    const cssPath = fs.existsSync(draftCssPath) ? draftCssPath : postCssPath;
    const jsPath = fs.existsSync(draftJsPath) ? draftJsPath : postJsPath;
    const html = fs.readFileSync(htmlPath, 'utf8');
    const css = fs.readFileSync(cssPath, 'utf8');
    const js = fs.readFileSync(jsPath, 'utf8');

    assert.ok(html.includes('<script src="garden.js"></script>'), 'HTML should reference garden.js');
    assert.ok(html.includes('class="world-3d-container"'), 'HTML should preserve .world-3d-container');
    assert.ok(html.includes('id="plant-inspector-card"'), 'HTML should include #plant-inspector-card');
    assert.ok(html.includes('id="catalog-drawer"'), 'HTML should include #catalog-drawer');

    assert.ok(css.includes('rotateX(60deg) rotateZ(45deg)'), 'CSS should preserve pure CSS 3D plane');
    assert.ok(css.includes('translate(-50%, 0) rotateZ(-45deg) rotateX(-60deg)'), 'CSS should preserve counter-rotated sprite');
    assert.ok(css.includes('.catalog-card:nth-child(n + 15)'), 'CSS should defer below-the-fold catalog cards');
    assert.ok(css.includes('content-visibility: auto;'), 'CSS should apply content-visibility: auto');
    assert.ok(css.includes('contain-intrinsic-size: auto none auto 72px;'), 'CSS should apply contain-intrinsic-size');

    assert.ok(js.includes("btn.type = 'button'") && js.includes("btn.className = 'catalog-card'"), 'JS should render semantic button.catalog-card elements');
    assert.ok(!js.includes('THREE.'), 'Must NOT use Three.js');
}

function main() {
    testDynamicHeightSamplingBounds();
    testAspectRatioAndGroundEllipseScaling();
    testEllipticalCollisionAndCanopyOverlap();
    testIncenseShrineExclusion();
    testIsometricDepthSorting();
    testEcologicalScatterAndProjection();
    testDraftHtmlAndCssContracts();
    console.log('ALL GARDEN 2.5D LOGIC TESTS PASSED (7/7)');
}

main();
