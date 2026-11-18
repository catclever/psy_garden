import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
SRC_DIR = REPO_ROOT / "src"
GODOT_BIN = "/usr/local/bin/godot4"
TASK_SUFFIX = "_task_stem_anchor_034"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _load_module(module_name: str, draft_filename: str, merged_filename: str):
    draft_path = TOOLS_DIR / draft_filename
    merged_path = TOOLS_DIR / merged_filename
    target = draft_path if draft_path.exists() else merged_path
    spec = importlib.util.spec_from_file_location(module_name, str(target))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


calibrator = _load_module(
    "root_anchor_calibrator",
    "root_anchor_calibrator.py",
    "root_anchor_calibrator.py",
)
build_plant_asset = _load_module(
    "build_plant_asset_034",
    f"build_plant_asset{TASK_SUFFIX}.py",
    "build_plant_asset.py",
)
generate_plant_manifest = _load_module(
    "generate_plant_manifest_034",
    f"generate_plant_manifest{TASK_SUFFIX}.py",
    "generate_plant_manifest.py",
)
recalibrate_cli = _load_module(
    "recalibrate_and_ingest_grounded",
    "recalibrate_and_ingest_grounded.py",
    "recalibrate_and_ingest_grounded.py",
)


def _pick_path(rel_draft: str, rel_merged: str) -> Path:
    d = REPO_ROOT / rel_draft
    return d if d.exists() else (REPO_ROOT / rel_merged)


GARDEN_JS_PATH = _pick_path(f"src/garden{TASK_SUFFIX}.js", "src/garden.js")
PLANT_GD_PATH = _pick_path(f"godot/Plant{TASK_SUFFIX}.gd", "godot/Plant.gd")
GARDEN_GD_PATH = _pick_path(f"godot/Garden{TASK_SUFFIX}.gd", "godot/Garden.gd")


def _make_upright_stem_png(path: str, size=(80, 100), stem_width=14):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = size[0] // 2
    half_w = stem_width // 2
    draw.rectangle([cx - half_w, 20, cx - half_w + stem_width - 1, size[1] - 1], fill=(30, 180, 50, 255))
    img.save(path)
    return img


def _make_white_bg_plant_image(path: str, size=(160, 200)):
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    cx = size[0] // 2
    # Woody brown stem down to y=165
    draw.rectangle([cx - 6, 90, cx + 5, 165], fill=(120, 75, 45))
    # Canopy
    draw.ellipse([cx - 45, 20, cx + 45, 110], fill=(35, 165, 50))
    # Drooping outer green leaf reaching y=195 on far right
    draw.polygon([(cx + 30, 95), (cx + 55, 195), (cx + 42, 195), (cx + 22, 105)], fill=(40, 175, 55))
    img.save(path)


GODOT_VERIFY_SCRIPT = r"""extends SceneTree

func _init():
	var main_scene = load("res://Main.tscn")
	if not main_scene:
		push_error("VERIFY_FAIL: Could not load res://Main.tscn")
		quit(1)
		return

	var garden = main_scene.instantiate()
	get_root().add_child(garden)
	await process_frame
	await process_frame

	# 1. Synthetic image: default (-1, -1) override keeps legacy pixel root_anchor
	var img = Image.create(100, 200, false, Image.FORMAT_RGBA8)
	img.fill(Color(0, 0, 0, 0))
	for y in range(20, 181):
		for x in range(30, 51):
			img.set_pixel(x, y, Color(0.2, 0.8, 0.3, 1.0))
	var synth_tex = ImageTexture.create_from_image(img)

	var plant_scene = load("res://Plant.tscn")
	var p_default = plant_scene.instantiate()
	p_default.skip_auto_load = true
	get_root().add_child(p_default)
	p_default.setup_with_textures("synth_default", synth_tex, synth_tex, null, 80.0, 20.0)
	if abs(p_default.root_anchor.x - 40.0) > 0.01 or abs(p_default.root_anchor.y - 180.0) > 0.01:
		push_error("VERIFY_FAIL: Default root_anchor mismatch: %s" % str(p_default.root_anchor))
		quit(1)
		return

	# 2. Override anchor UV path sets exact scaled root_anchor and root_uv_y while preserving visual_height
	var p_override = plant_scene.instantiate()
	p_override.skip_auto_load = true
	get_root().add_child(p_override)
	p_override.setup_with_textures("synth_override", synth_tex, synth_tex, null, 80.0, 20.0, Vector2(0.35, 0.82))
	if abs(p_override.root_anchor.x - 35.0) > 0.01 or abs(p_override.root_anchor.y - 164.0) > 0.01:
		push_error("VERIFY_FAIL: Override root_anchor mismatch: %s" % str(p_override.root_anchor))
		quit(1)
		return
	if abs(p_override.root_uv_y - 0.82) > 0.001:
		push_error("VERIFY_FAIL: Override root_uv_y mismatch: %f" % p_override.root_uv_y)
		quit(1)
		return
	if abs(p_override.visual_height - 80.0) > 0.01:
		push_error("VERIFY_FAIL: Override visual_height mismatch: %f" % p_override.visual_height)
		quit(1)
		return

	# 3. Garden.spawn_manifest_plant passes rec["root_anchor"] through to Plant
	var dummy_key = "assets/plants/synth_rec/01/layer_green.png"
	garden.texture_cache[dummy_key] = synth_tex
	var rec = {
		"id": "synth_rec__01",
		"name": "synth_rec",
		"path_prefix": "assets/plants/synth_rec/01",
		"width": 100,
		"height": 200,
		"root_width": 20,
		"root_anchor": [0.42, 0.78],
		"layer_files": [{"slot": "green", "file": "layer_green.png"}],
	}
	garden.spawn_manifest_plant(rec, Vector2(600, 400), 80.0, 20.0, 10.0, 6.0)
	var spawned = garden.active_plants[garden.active_plants.size() - 1]
	if abs(spawned.root_anchor.x - 42.0) > 0.01 or abs(spawned.root_anchor.y - 156.0) > 0.01:
		push_error("VERIFY_FAIL: Spawned manifest plant root_anchor mismatch: %s" % str(spawned.root_anchor))
		quit(1)
		return

	print("VERIFY_OK: stem_anchor_034 Plant and Garden root_anchor override checks passed!")
	quit(0)
"""


class TestStemAnchorAndRegenIngest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="psy_garden_034_")
        self.plants_dir = os.path.join(self.tmpdir, "src", "assets", "plants")
        self.processed_dir = os.path.join(self.tmpdir, "tools", "candidates", "processed")
        os.makedirs(self.plants_dir, exist_ok=True)
        os.makedirs(self.processed_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_calibrate_woody_stem_ignores_lower_drooping_green_leaf(self):
        W, H = 160, 200
        red_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        green_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        r_draw = ImageDraw.Draw(red_img)
        g_draw = ImageDraw.Draw(green_img)

        # Canopy spanning x=30..130, y=20..100
        g_draw.ellipse([30, 20, 130, 100], fill=(35, 165, 50, 255))
        # Woody trunk/stem in red_img at x=75..84, y=90..160
        r_draw.rectangle([75, 90, 84, 160], fill=(125, 80, 50, 255))
        # Tiny isolated 2px speckle far below at y=185 (separated by >6 empty rows)
        r_draw.point((80, 185), fill=(125, 80, 50, 255))
        r_draw.point((81, 185), fill=(125, 80, 50, 255))
        # Drooping outer green leaf reaching bottom y=195 on the right side
        g_draw.polygon([(115, 95), (138, 195), (128, 195), (108, 105)], fill=(40, 175, 55, 255))

        res = calibrator.calibrate_root_anchor(red_img, green_img, None)
        self.assertEqual(res["root_y"], 160)
        self.assertGreaterEqual(res["root_x"], 74.0)
        self.assertLessEqual(res["root_x"], 85.0)
        self.assertAlmostEqual(res["root_anchor"][0], round(res["root_x"] / W, 4), places=4)
        self.assertAlmostEqual(res["root_anchor"][1], round(160 / H, 4), places=4)
        self.assertEqual(res["root_width"], 10)

    def test_calibrate_non_woody_clump_ignores_thin_drooping_blade(self):
        W, H = 160, 200
        red_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        green_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        g_draw = ImageDraw.Draw(green_img)

        # Dense central clump from y=30 to y=140, width ~40px (x=60..100)
        g_draw.ellipse([55, 30, 105, 142], fill=(40, 175, 60, 255))
        # Single thin 1-2px drooping blade extending down to y=195
        g_draw.line([(80, 140), (82, 195)], fill=(40, 175, 60, 255), width=2)

        res = calibrator.calibrate_root_anchor(red_img, green_img, None)
        self.assertLessEqual(res["root_y"], 145)
        self.assertGreaterEqual(res["root_y"], 135)
        self.assertAlmostEqual(res["root_anchor"][1], round(res["root_y"] / H, 4), places=4)
        self.assertGreaterEqual(res["root_width"], 8)

    def test_calibrate_upright_14px_stem_keeps_bottom_y_and_width_14(self):
        stem_path = os.path.join(self.tmpdir, "upright.png")
        green_img = _make_upright_stem_png(stem_path, size=(80, 100), stem_width=14)
        red_img = Image.new("RGBA", (80, 100), (0, 0, 0, 0))

        res = calibrator.calibrate_root_anchor(red_img, green_img, None)
        self.assertEqual(res["root_y"], 99)
        self.assertEqual(res["root_width"], 14)
        self.assertEqual(res["ground_ellipse"], [14, 8])

    def test_build_asset_and_manifest_root_anchor_roundtrip(self):
        src_img = os.path.join(self.tmpdir, "test_woody.png")
        _make_white_bg_plant_image(src_img, size=(160, 200))
        out_dir = os.path.join(self.plants_dir, "plant_0901", "01")

        ok = build_plant_asset.build_asset(
            src_img,
            "plant_0901",
            "01",
            out_dir,
            self.tmpdir,
            display_name="测试木本",
            inject_css=False,
        )
        self.assertTrue(ok)
        with open(os.path.join(out_dir, "metadata.json"), "r", encoding="utf-8") as f:
            meta = json.load(f)
        self.assertIn("root_anchor", meta)
        self.assertIn("root_x", meta)
        self.assertIn("root_y", meta)
        self.assertEqual(len(meta["root_anchor"]), 2)
        self.assertLess(meta["root_anchor"][1], 0.95)

        # Also add a plant without root_anchor to verify [0.5, 1.0] default in manifest
        no_anchor_dir = os.path.join(self.plants_dir, "plant_0902", "01")
        os.makedirs(no_anchor_dir, exist_ok=True)
        _make_upright_stem_png(os.path.join(no_anchor_dir, "layer_green.png"))
        with open(os.path.join(no_anchor_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "plant_0902", "display_name": "默认锚点", "true_color": True}, f)

        manifest = generate_plant_manifest.generate_manifest(plants_dir=self.plants_dir)
        by_name = {p["name"]: p for p in manifest["plants"]}
        self.assertEqual(by_name["plant_0901"]["root_anchor"], meta["root_anchor"])
        self.assertEqual(by_name["plant_0902"]["root_anchor"], [0.5, 1.0])

    def test_ingest_regen_dir_disables_old_variants_and_preserves_ai(self):
        # Setup plant_0071 with existing '01' (has full ai_analysis) and '02'
        v01 = os.path.join(self.plants_dir, "plant_0071", "01")
        v02 = os.path.join(self.plants_dir, "plant_0071", "02")
        os.makedirs(v01)
        os.makedirs(v02)
        _make_upright_stem_png(os.path.join(v01, "layer_green.png"))
        _make_upright_stem_png(os.path.join(v02, "layer_green.png"))

        orig_ai = {
            "zh_name": "鵝鑾鼻燈籠草",
            "scientific_name": "Kalanchoe garambiensis",
            "plant_type": "groundcover",
            "suggested_game_height_px": [35, 70],
            "brief": "原始AI描述",
            "status": "success",
        }
        with open(os.path.join(v01, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "name": "plant_0071",
                    "display_name": "鵝鑾鼻燈籠草",
                    "variant_id": "01",
                    "true_color": True,
                    "enabled": True,
                    "ai_analysis": orig_ai,
                },
                f,
                ensure_ascii=False,
            )
        with open(os.path.join(v02, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "name": "plant_0071",
                    "display_name": "鵝鑾鼻燈籠草",
                    "variant_id": "02",
                    "true_color": True,
                    "enabled": True,
                    "ai_analysis": None,
                },
                f,
                ensure_ascii=False,
            )

        regen_dir = os.path.join(self.tmpdir, "regen_inputs")
        os.makedirs(regen_dir)
        regen_img = os.path.join(regen_dir, "plant_0071.png")
        _make_white_bg_plant_image(regen_img, size=(160, 200))

        species_json = os.path.join(self.tmpdir, "species.json")
        with open(species_json, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "plant_id": "plant_0071",
                        "zh_name": "鵝鑾鼻燈籠草",
                        "scientific_name": "Kalanchoe garambiensis",
                        "plant_type": "groundcover",
                        "category": "A_moderate",
                    }
                ],
                f,
                ensure_ascii=False,
            )

        summary = recalibrate_cli.ingest_regen_dir(
            regen_dir=regen_dir,
            species_json_path=species_json,
            plants_dir=self.plants_dir,
            processed_dir=self.processed_dir,
            project_root=self.tmpdir,
        )
        self.assertEqual(summary["ingested"], 1)
        self.assertEqual(summary["variants_disabled"], 2)
        self.assertEqual(summary["total_species"], 1)

        with open(os.path.join(v01, "metadata.json"), "r", encoding="utf-8") as f:
            self.assertIs(json.load(f)["enabled"], False)
        with open(os.path.join(v02, "metadata.json"), "r", encoding="utf-8") as f:
            self.assertIs(json.load(f)["enabled"], False)

        v03 = os.path.join(self.plants_dir, "plant_0071", "03")
        self.assertTrue(os.path.isdir(v03))
        with open(os.path.join(v03, "metadata.json"), "r", encoding="utf-8") as f:
            m03 = json.load(f)
        self.assertIs(m03["enabled"], True)
        self.assertEqual(m03["status"], "ready")
        self.assertEqual(m03["display_name"], "鵝鑾鼻燈籠草")
        self.assertEqual(m03["ai_analysis"]["brief"], "原始AI描述")
        self.assertIn("root_anchor", m03)

        archived = os.path.join(self.processed_dir, "candidate_plant_0071_鵝鑾鼻燈籠草_grounded.png")
        self.assertTrue(os.path.isfile(archived))

        with open(os.path.join(self.plants_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["plants"][0]["variant_id"], "03")
        self.assertEqual(manifest["plants"][0]["root_anchor"], m03["root_anchor"])

    def test_recalibrate_all_updates_enabled_and_skips_disabled(self):
        v_en = os.path.join(self.plants_dir, "plant_0100", "01")
        v_dis = os.path.join(self.plants_dir, "plant_0101", "01")
        os.makedirs(v_en)
        os.makedirs(v_dis)
        _make_upright_stem_png(os.path.join(v_en, "layer_green.png"), size=(80, 100), stem_width=14)
        _make_upright_stem_png(os.path.join(v_dis, "layer_green.png"), size=(80, 100), stem_width=14)

        with open(os.path.join(v_en, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "plant_0100", "display_name": "启用植物", "true_color": True}, f)
        with open(os.path.join(v_dis, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "plant_0101", "display_name": "禁用植物", "enabled": False}, f)

        summary = recalibrate_cli.recalibrate_all(plants_dir=self.plants_dir)
        self.assertEqual(summary["updated_variants"], 1)
        self.assertEqual(summary["skipped_disabled"], 1)
        self.assertEqual(summary["total_species"], 1)

        with open(os.path.join(v_en, "metadata.json"), "r", encoding="utf-8") as f:
            m_en = json.load(f)
        self.assertEqual(m_en["width"], 80)
        self.assertEqual(m_en["height"], 100)
        self.assertEqual(m_en["root_y"], 99)
        self.assertEqual(m_en["root_width"], 14)
        self.assertIn("root_anchor", m_en)

        with open(os.path.join(v_dis, "metadata.json"), "r", encoding="utf-8") as f:
            m_dis = json.load(f)
        self.assertNotIn("root_anchor", m_dis)

    def test_js_root_anchor_uv_and_sprite_layer_transform(self):
        script = f"""
const assert = require('assert');
function makeEl(tag) {{
    const el = {{
        tagName: tag.toUpperCase(),
        className: '',
        dataset: {{}},
        style: {{}},
        children: [],
        attrs: {{}},
        setAttribute(k, v) {{ this.attrs[k] = v; }},
        addEventListener() {{}},
        appendChild(child) {{ this.children.push(child); return child; }},
    }};
    return el;
}}
global.document = {{
    readyState: 'loading',
    addEventListener() {{}},
    createElement: makeEl,
    getElementById() {{ return null; }},
    querySelectorAll() {{ return []; }},
}};
global.window = {{}};
const garden = require({json.dumps(str(GARDEN_JS_PATH))});

assert.deepStrictEqual(garden.getRootAnchorUV(null), [0.5, 1.0]);
assert.deepStrictEqual(garden.getRootAnchorUV({{}}), [0.5, 1.0]);
assert.deepStrictEqual(garden.getRootAnchorUV({{ root_anchor: [0.01, 0.40] }}), [0.05, 0.60]);
assert.deepStrictEqual(garden.getRootAnchorUV({{ root_anchor: [0.99, 1.25] }}), [0.95, 1.0]);
assert.deepStrictEqual(garden.getRootAnchorUV({{ root_anchor: [0.40, 0.82] }}), [0.40, 0.82]);

const nodeShifted = global.window.createPlantNodeElement({{
    instanceId: 'p-1',
    plant: {{
        name: 'plant_0048',
        display_name: '莲叶桐',
        path_prefix: 'assets/plants/plant_0048/01',
        root_anchor: [0.40, 0.82],
        root_ratio: 0.1,
        layer_files: [
            {{ slot: 'green', file: 'layer_green.png' }},
            {{ slot: 'red', file: 'layer_red.png' }},
        ],
        ai_analysis: {{ plant_type: 'tree', zh_name: '莲叶桐' }},
    }},
    leftPct: 50,
    topPct: 50,
    zIndex: 1000,
    targetW: 100,
    targetH: 120,
    fpW: 14,
    fpH: 8,
}}, 0);
const sprite = nodeShifted.children.find((c) => c.className === 'plant-sprite');
const layers = sprite.children.filter((c) => c.className.startsWith('sprite-layer'));
assert.strictEqual(layers.length, 2);
for (const l of layers) {{
    assert.strictEqual(l.style.transform, 'translate(10%, 18%)');
}}

const nodeDefault = global.window.createPlantNodeElement({{
    instanceId: 'p-2',
    plant: {{
        name: 'plant_0001',
        path_prefix: 'assets/plants/plant_0001/01',
        root_anchor: [0.5, 1.0],
        layer_files: [{{ slot: 'green', file: 'layer_green.png' }}],
    }},
    leftPct: 50,
    topPct: 50,
    zIndex: 1000,
    targetW: 80,
    targetH: 100,
    fpW: 12,
    fpH: 7,
}}, 1);
const spriteDef = nodeDefault.children.find((c) => c.className === 'plant-sprite');
const layerDef = spriteDef.children.find((c) => c.className.startsWith('sprite-layer'));
assert.strictEqual(layerDef.style.transform, undefined);

console.log('JS_STEM_ANCHOR_OK');
"""
        res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
        self.assertEqual(res.returncode, 0, f"Node check failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("JS_STEM_ANCHOR_OK", res.stdout)

    def test_godot_drafts_ascii_only_and_headless_verify(self):
        cjk_re = re.compile(r"[\u4e00-\u9fff]")
        for gd_path in (PLANT_GD_PATH, GARDEN_GD_PATH):
            text = gd_path.read_text(encoding="utf-8")
            self.assertEqual(cjk_re.findall(text), [], f"CJK found in {gd_path}")
            self.assertTrue(all(ord(ch) < 128 for ch in text), f"Non-ASCII found in {gd_path}")

        self.assertTrue(os.path.exists(GODOT_BIN), f"Godot 4 binary missing at {GODOT_BIN}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            tmp_godot = tmp_root / "godot"
            shutil.copytree(REPO_ROOT / "godot", tmp_godot, ignore=shutil.ignore_patterns("*_task_*"))
            shutil.copy2(PLANT_GD_PATH, tmp_godot / "Plant.gd")
            shutil.copy2(GARDEN_GD_PATH, tmp_godot / "Garden.gd")

            tmp_src = tmp_root / "src"
            tmp_src.mkdir(parents=True, exist_ok=True)
            os.symlink(REPO_ROOT / "src" / "assets", tmp_src / "assets")

            env = os.environ.copy()
            env["GODOT_SILENCE_ROOT_WARNING"] = "1"

            res_boot = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--quit"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            boot_out = res_boot.stdout + "\n" + res_boot.stderr
            self.assertEqual(res_boot.returncode, 0, f"Godot boot failed:\n{boot_out}")
            for tok in ("SCRIPT ERROR", "Parse Error", "Failed to load script", "ERROR:"):
                self.assertNotIn(tok, boot_out, f"Godot boot error ({tok}):\n{boot_out}")

            verify_gd = tmp_godot / "test_stem_anchor_034.gd"
            verify_gd.write_text(GODOT_VERIFY_SCRIPT, encoding="utf-8")

            res_verify = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--script", "res://test_stem_anchor_034.gd"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            verify_out = res_verify.stdout + "\n" + res_verify.stderr
            self.assertEqual(res_verify.returncode, 0, f"Godot verify failed:\n{verify_out}")
            self.assertIn("VERIFY_OK:", verify_out, f"Missing VERIFY_OK:\n{verify_out}")
            for tok in ("SCRIPT ERROR", "Parse Error", "VERIFY_FAIL", "ERROR:"):
                self.assertNotIn(tok, verify_out, f"Godot verify error ({tok}):\n{verify_out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
