import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GODOT_BIN = "/usr/local/bin/godot4"
TASK_SUFFIX = "_task_merge_godot_lighting_and_connect_manifest_plants_033"

def _pick(rel_draft: str, rel_merged: str) -> Path:
    d = ROOT / rel_draft
    return d if d.exists() else (ROOT / rel_merged)


PLANT_DRAFT = _pick(f"godot/Plant{TASK_SUFFIX}.gd", "godot/Plant.gd")
GARDEN_DRAFT = _pick(f"godot/Garden{TASK_SUFFIX}.gd", "godot/Garden.gd")
INDEX_DRAFT = _pick(f"src/index{TASK_SUFFIX}.html", "src/index.html")
LIGHT_DEMO_DRAFT = _pick(f"src/light_demo{TASK_SUFFIX}.html", "src/light_demo.html")


VERIFY_GDSCRIPT = r"""extends SceneTree

func _init():
	var main_scene = load("res://Main.tscn")
	if not main_scene:
		push_error("VERIFY_FAIL: Could not load res://Main.tscn")
		quit(1)
		return

	var garden = main_scene.instantiate()
	get_root().add_child(garden)

	# Allow deferred _load_manifest() to run
	await process_frame
	await process_frame

	if garden.manifest_records.size() != 383:
		push_error("VERIFY_FAIL: Expected 383 manifest_records, got %d" % garden.manifest_records.size())
		quit(1)
		return

	if garden.fleet_status_label == null or garden.fleet_status_label.text != "Fleet: 383 species loaded":
		push_error("VERIFY_FAIL: Unexpected fleet_status_label: %s" % str(garden.fleet_status_label.text if garden.fleet_status_label else "null"))
		quit(1)
		return

	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active plants from stratified scatter, got %d" % garden.active_plants.size())
		quit(1)
		return

	var incense_pos = Vector2(720.0, 450.0)
	for i in range(garden.active_plants.size()):
		var p = garden.active_plants[i]
		var pos = p.global_position
		if pos.x < 290.0 and pos.y < 600.0:
			push_error("VERIFY_FAIL: Plant %d placed inside HUD exclusion zone: %s" % [i, str(pos)])
			quit(1)
			return
		if pos.distance_to(incense_pos) < 65.0:
			push_error("VERIFY_FAIL: Plant %d too close to IncenseBurner: %s" % [i, str(pos)])
			quit(1)
			return
		if not p.show_footprint_ring:
			push_error("VERIFY_FAIL: Plant %d show_footprint_ring should default true" % i)
			quit(1)
			return
		for j in range(i + 1, garden.active_plants.size()):
			var q = garden.active_plants[j]
			var dx = pos.x - q.global_position.x
			var dy = pos.y - q.global_position.y
			var a = (p.fp_w + q.fp_w) * 0.5 + 12.0
			var b = (p.fp_h + q.fp_h) * 0.5 + 8.0
			if ((dx / a) * (dx / a) + (dy / b) * (dy / b)) < 1.0:
				push_error("VERIFY_FAIL: Plants %d and %d overlap in 2.5D ellipse!" % [i, j])
				quit(1)
				return

	# Verify footprint toggle button
	garden._on_toggle_footprints_pressed()
	if garden.btn_toggle_footprints.text != "Footprints: OFF":
		push_error("VERIFY_FAIL: Expected Footprints: OFF")
		quit(1)
		return
	for p in garden.active_plants:
		if p.show_footprint_ring:
			push_error("VERIFY_FAIL: Plant ring should be false after toggle OFF")
			quit(1)
			return

	garden._on_toggle_footprints_pressed()
	if garden.btn_toggle_footprints.text != "Footprints: ON":
		push_error("VERIFY_FAIL: Expected Footprints: ON")
		quit(1)
		return

	# Verify exact root calibration and uniform scaling on synthetic PNG/ImageTexture
	var img = Image.create(100, 200, false, Image.FORMAT_RGBA8)
	img.fill(Color(0, 0, 0, 0))
	for y in range(20, 181):
		for x in range(30, 51):
			img.set_pixel(x, y, Color(0.2, 0.8, 0.3, 1.0))
	var synth_tex = ImageTexture.create_from_image(img)

	var plant_scene = load("res://Plant.tscn")
	var test_plant = plant_scene.instantiate()
	test_plant.skip_auto_load = true
	get_root().add_child(test_plant)
	test_plant.setup_with_textures("synth_test", synth_tex, synth_tex, null, 80.0, 20.0)

	# raw_h = 180 - 20 = 160, s = 80 / 160 = 0.5
	if abs(test_plant.root_anchor.x - 40.0) > 0.01 or abs(test_plant.root_anchor.y - 180.0) > 0.01:
		push_error("VERIFY_FAIL: Unexpected root_anchor: %s" % str(test_plant.root_anchor))
		quit(1)
		return
	if abs(test_plant.visual_height - 80.0) > 0.01:
		push_error("VERIFY_FAIL: Expected visual_height 80.0, got %f" % test_plant.visual_height)
		quit(1)
		return
	if abs(test_plant.fp_w - 10.0) > 0.01 or abs(test_plant.fp_h - 6.0) > 0.01:
		push_error("VERIFY_FAIL: Expected fp_w=10, fp_h=6, got %f, %f" % [test_plant.fp_w, test_plant.fp_h])
		quit(1)
		return
	if test_plant.sprite_green.scale.distance_to(Vector2(0.5, 0.5)) > 0.001:
		push_error("VERIFY_FAIL: sprite_green scale mismatch: %s" % str(test_plant.sprite_green.scale))
		quit(1)
		return
	if test_plant.sun_shadow.scale.distance_to(Vector2(0.5, 0.5)) > 0.001:
		push_error("VERIFY_FAIL: sun_shadow scale mismatch: %s" % str(test_plant.sun_shadow.scale))
		quit(1)
		return
	if test_plant.sun_shadow.offset.distance_to(-test_plant.root_anchor) > 0.001:
		push_error("VERIFY_FAIL: sun_shadow offset mismatch: %s" % str(test_plant.sun_shadow.offset))
		quit(1)
		return
	if test_plant.sprite_blue.visible:
		push_error("VERIFY_FAIL: sprite_blue should be invisible when tex_blue is null")
		quit(1)
		return

	# Verify PNG buffer decode path + pre-bundled demo layout fallback
	var png_buf = img.save_png_to_buffer()
	var decoded = Image.new()
	if decoded.load_png_from_buffer(png_buf) != OK:
		push_error("VERIFY_FAIL: load_png_from_buffer failed")
		quit(1)
		return

	garden.generate_demo_layout()
	if garden.active_plants.size() != 12:
		push_error("VERIFY_FAIL: Expected 12 demo plants, got %d" % garden.active_plants.size())
		quit(1)
		return
	var demo_p = garden.active_plants[0]
	if demo_p.sun_shadow.offset.distance_to(-demo_p.root_anchor) > 0.001:
		push_error("VERIFY_FAIL: Demo plant sun_shadow offset mismatch")
		quit(1)
		return

	print("VERIFY_OK: 383 manifest plants, 36 stratified scatter, 2.5D collision, footprint toggle, and root calibration all passed!")
	quit(0)
"""


class TestGodotManifestIntegration033(unittest.TestCase):
    def test_draft_files_exist_and_no_cjk_in_gdscript(self):
        for path in [PLANT_DRAFT, GARDEN_DRAFT, INDEX_DRAFT, LIGHT_DEMO_DRAFT]:
            self.assertTrue(path.exists(), f"Missing draft file: {path}")

        cjk_re = re.compile(r"[\u4e00-\u9fff]")
        for gd_path in [PLANT_DRAFT, GARDEN_DRAFT]:
            content = gd_path.read_text(encoding="utf-8")
            matches = cjk_re.findall(content)
            self.assertEqual(
                matches,
                [],
                f"Godot WebGL font does not support CJK glyphs! Found {matches} in {gd_path.name}",
            )

    def test_html_cross_navigation_contracts(self):
        index_html = INDEX_DRAFT.read_text(encoding="utf-8")
        self.assertIn("id=\"btn-godot-lighting\"", index_html)
        self.assertIn("href=\"light_demo.html\"", index_html)
        self.assertIn("🏮 切换 Godot 2.5D 光影版", index_html)

        light_html = LIGHT_DEMO_DRAFT.read_text(encoding="utf-8")
        self.assertIn("godot_web/index.html", light_html)
        self.assertIn("href=\"index.html\"", light_html)
        self.assertIn("🌿 返回 DOM 图鉴手植版 · 383种", light_html)
        self.assertIn("🏮 Godot 2.5D 动态光影舱 · 移动鼠标体验手电筒单向量柔影与树冠遮挡", light_html)

    def test_godot_headless_compile_and_manifest_integration(self):
        self.assertTrue(os.path.exists(GODOT_BIN), f"Godot 4 binary not found at {GODOT_BIN}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            tmp_godot = tmp_root / "godot"
            shutil.copytree(
                ROOT / "godot",
                tmp_godot,
                ignore=shutil.ignore_patterns("*_task_*"),
            )
            # Install draft scripts as target scripts inside temporary copy only
            shutil.copy2(PLANT_DRAFT, tmp_godot / "Plant.gd")
            shutil.copy2(GARDEN_DRAFT, tmp_godot / "Garden.gd")

            # Link src/assets so ../src/assets/plants/manifest.json resolves identically
            tmp_src = tmp_root / "src"
            tmp_src.mkdir(parents=True, exist_ok=True)
            os.symlink(ROOT / "src" / "assets", tmp_src / "assets")

            env = os.environ.copy()
            env["GODOT_SILENCE_ROOT_WARNING"] = "1"

            # 1. Standard headless boot + quit check (verifies Main.tscn, Plant.gd, Garden.gd parse & _ready)
            res_boot = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--quit"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            boot_output = res_boot.stdout + "\n" + res_boot.stderr
            self.assertEqual(res_boot.returncode, 0, f"Godot headless boot failed:\n{boot_output}")
            for bad_token in ["SCRIPT ERROR", "Parse Error", "Failed to load script", "ERROR:"]:
                self.assertNotIn(bad_token, boot_output, f"Unexpected Godot error ({bad_token}):\n{boot_output}")

            # 2. Deep integration verification script inside temporary copy
            verify_path = tmp_godot / "test_verify.gd"
            verify_path.write_text(VERIFY_GDSCRIPT, encoding="utf-8")

            res_verify = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--script", "res://test_verify.gd"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            verify_output = res_verify.stdout + "\n" + res_verify.stderr
            self.assertEqual(res_verify.returncode, 0, f"Godot verify script failed:\n{verify_output}")
            self.assertIn("VERIFY_OK:", verify_output, f"Missing VERIFY_OK in output:\n{verify_output}")
            for bad_token in ["SCRIPT ERROR", "Parse Error", "VERIFY_FAIL", "ERROR:"]:
                self.assertNotIn(bad_token, verify_output, f"Unexpected Godot error ({bad_token}):\n{verify_output}")

    def test_existing_js_garden_logic_still_passes(self):
        res = subprocess.run(
            ["node", str(ROOT / "Tests" / "test_garden_logic.js")],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, f"test_garden_logic.js failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("ALL GARDEN 2.5D LOGIC TESTS PASSED", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
