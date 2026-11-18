import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GODOT_BIN = "/usr/local/bin/godot4"
TASK_SUFFIX = "_task_godot_persp_037"


def _pick(rel_draft: str, rel_merged: str) -> Path:
    d = REPO_ROOT / rel_draft
    return d if d.exists() else (REPO_ROOT / rel_merged)


PLANT_PATH = _pick(f"godot/Plant{TASK_SUFFIX}.gd", "godot/Plant.gd")
GARDEN_PATH = _pick(f"godot/Garden{TASK_SUFFIX}.gd", "godot/Garden.gd")


VERIFY_GDSCRIPT = r"""extends SceneTree

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

	# (a) get_depth_scale curve checks
	if abs(garden.get_depth_scale(140.0) - 0.75) > 0.001:
		push_error("VERIFY_FAIL: get_depth_scale(140.0) expected 0.75, got %f" % garden.get_depth_scale(140.0))
		quit(1)
		return
	if abs(garden.get_depth_scale(490.0) - 1.0) > 0.001:
		push_error("VERIFY_FAIL: get_depth_scale(490.0) expected 1.0, got %f" % garden.get_depth_scale(490.0))
		quit(1)
		return
	if abs(garden.get_depth_scale(840.0) - 1.25) > 0.001:
		push_error("VERIFY_FAIL: get_depth_scale(840.0) expected 1.25, got %f" % garden.get_depth_scale(840.0))
		quit(1)
		return
	if abs(garden.get_depth_scale(840.0, 0.0) - 1.0) > 0.001:
		push_error("VERIFY_FAIL: get_depth_scale(840.0, 0.0) expected 1.0, got %f" % garden.get_depth_scale(840.0, 0.0))
		quit(1)
		return

	# (b) Synthetic Plant comparison at y=200 vs y=800
	var img = Image.create(100, 200, false, Image.FORMAT_RGBA8)
	img.fill(Color(0, 0, 0, 0))
	for y in range(20, 181):
		for x in range(30, 51):
			img.set_pixel(x, y, Color(0.2, 0.8, 0.3, 1.0))
	var synth_tex = ImageTexture.create_from_image(img)

	var plant_scene = load("res://Plant.tscn")
	var p_far = plant_scene.instantiate()
	p_far.skip_auto_load = true
	p_far.global_position = Vector2(500.0, 200.0)
	get_root().add_child(p_far)
	p_far.setup_with_textures("synth_far", synth_tex, synth_tex, null, 100.0, 20.0)
	p_far.set_perspective_scale(garden.get_depth_scale(200.0))

	var p_near = plant_scene.instantiate()
	p_near.skip_auto_load = true
	p_near.global_position = Vector2(500.0, 800.0)
	get_root().add_child(p_near)
	p_near.setup_with_textures("synth_near", synth_tex, synth_tex, null, 100.0, 20.0)
	p_near.set_perspective_scale(garden.get_depth_scale(800.0))

	if not (p_near.visual_height > p_far.visual_height):
		push_error("VERIFY_FAIL: Expected near visual_height > far visual_height (%f vs %f)" % [p_near.visual_height, p_far.visual_height])
		quit(1)
		return

	var expected_ratio = garden.get_depth_scale(800.0) / garden.get_depth_scale(200.0)
	var actual_ratio = p_near.visual_height / p_far.visual_height
	if abs(actual_ratio - expected_ratio) > 0.001:
		push_error("VERIFY_FAIL: Height ratio mismatch: expected %f, got %f" % [expected_ratio, actual_ratio])
		quit(1)
		return

	for pair in [[p_far, 200.0], [p_near, 800.0]]:
		var p = pair[0]
		var y_val = pair[1]
		var ds = garden.get_depth_scale(y_val)
		var expected_s = (100.0 / 160.0) * ds
		if p.sprite_green.scale.distance_to(Vector2(expected_s, expected_s)) > 0.001:
			push_error("VERIFY_FAIL: sprite_green scale mismatch at y=%f: %s" % [y_val, str(p.sprite_green.scale)])
			quit(1)
			return
		if p.sun_shadow.scale.distance_to(Vector2(expected_s, expected_s)) > 0.001:
			push_error("VERIFY_FAIL: sun_shadow scale mismatch at y=%f: %s" % [y_val, str(p.sun_shadow.scale)])
			quit(1)
			return
		var expected_fp_w = max(10.0, 20.0 * expected_s)
		if abs(p.fp_w - expected_fp_w) > 0.001:
			push_error("VERIFY_FAIL: fp_w mismatch at y=%f: expected %f, got %f" % [y_val, expected_fp_w, p.fp_w])
			quit(1)
			return
		var expected_cs = clampf(expected_fp_w / 24.0, 0.45, 2.6)
		if p.contact_shadow.scale.distance_to(Vector2(expected_cs, expected_cs)) > 0.001:
			push_error("VERIFY_FAIL: contact_shadow scale mismatch at y=%f: %s" % [y_val, str(p.contact_shadow.scale)])
			quit(1)
			return
		if p.sun_shadow.offset.distance_to(-p.root_anchor) > 0.001:
			push_error("VERIFY_FAIL: sun_shadow offset changed at y=%f: %s" % [y_val, str(p.sun_shadow.offset)])
			quit(1)
			return

	if not (p_near.fp_w > p_far.fp_w) or not (p_near.contact_shadow.scale.x > p_far.contact_shadow.scale.x):
		push_error("VERIFY_FAIL: Expected near fp_w and contact_shadow scale > far")
		quit(1)
		return

	# (c) Garden manifest load and scaled footprint overlap checks
	if garden.manifest_records.size() != 383:
		push_error("VERIFY_FAIL: Expected 383 manifest_records, got %d" % garden.manifest_records.size())
		quit(1)
		return
	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active_plants, got %d" % garden.active_plants.size())
		quit(1)
		return

	var incense_pos = Vector2(720.0, 450.0)
	for i in range(garden.active_plants.size()):
		var p = garden.active_plants[i]
		var pos = p.global_position
		if pos.x < 290.0 and pos.y < 640.0:
			push_error("VERIFY_FAIL: Plant %d inside HUD exclusion zone: %s" % [i, str(pos)])
			quit(1)
			return
		if pos.distance_to(incense_pos) < 65.0:
			push_error("VERIFY_FAIL: Plant %d too close to IncenseBurner: %s" % [i, str(pos)])
			quit(1)
			return
		var expected_ds = garden.get_depth_scale(pos.y)
		if abs(p.perspective_scale - expected_ds) > 0.001:
			push_error("VERIFY_FAIL: Active plant %d perspective_scale mismatch: %f vs %f" % [i, p.perspective_scale, expected_ds])
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

	# (d) Live HUD Perspective Depth slider checks
	if garden.perspective_label == null or garden.perspective_slider == null:
		push_error("VERIFY_FAIL: Missing perspective_label or perspective_slider")
		quit(1)
		return
	if garden.perspective_label.text != "Perspective Depth (1.00x)":
		push_error("VERIFY_FAIL: Unexpected initial perspective_label text: %s" % garden.perspective_label.text)
		quit(1)
		return
	if garden.perspective_label.get_index() != garden.lantern_slider.get_index() + 1:
		push_error("VERIFY_FAIL: perspective_label not placed right after lantern_slider")
		quit(1)
		return
	if garden.perspective_slider.get_index() != garden.lantern_slider.get_index() + 2:
		push_error("VERIFY_FAIL: perspective_slider not placed right after perspective_label")
		quit(1)
		return

	garden.perspective_slider.value = 0.0
	if abs(garden.perspective_strength - 0.0) > 0.001:
		push_error("VERIFY_FAIL: Expected perspective_strength 0.0 after slider=0.0")
		quit(1)
		return
	if garden.perspective_label.text != "Perspective Depth (0.00x)":
		push_error("VERIFY_FAIL: Expected Perspective Depth (0.00x), got %s" % garden.perspective_label.text)
		quit(1)
		return
	for p in garden.active_plants:
		if abs(p.perspective_scale - 1.0) > 0.001:
			push_error("VERIFY_FAIL: Expected perspective_scale 1.0 when slider=0.0, got %f" % p.perspective_scale)
			quit(1)
			return

	garden.perspective_slider.value = 1.25
	if abs(garden.perspective_strength - 1.25) > 0.001:
		push_error("VERIFY_FAIL: Expected perspective_strength 1.25 after slider=1.25")
		quit(1)
		return
	if garden.perspective_label.text != "Perspective Depth (1.25x)":
		push_error("VERIFY_FAIL: Expected Perspective Depth (1.25x), got %s" % garden.perspective_label.text)
		quit(1)
		return
	var min_y_p = garden.active_plants[0]
	var max_y_p = garden.active_plants[0]
	for p in garden.active_plants:
		var exp_s = garden.get_depth_scale(p.global_position.y)
		if abs(p.perspective_scale - exp_s) > 0.001:
			push_error("VERIFY_FAIL: Expected perspective_scale %f at slider=1.25, got %f" % [exp_s, p.perspective_scale])
			quit(1)
			return
		if p.global_position.y < min_y_p.global_position.y:
			min_y_p = p
		if p.global_position.y > max_y_p.global_position.y:
			max_y_p = p
	if max_y_p.global_position.y > min_y_p.global_position.y and not (max_y_p.perspective_scale > min_y_p.perspective_scale):
		push_error("VERIFY_FAIL: Expected near plant scale > far plant scale at slider=1.25")
		quit(1)
		return

	# (e) generate_demo_layout() places 12 demo plants with matching depth scale
	garden.generate_demo_layout()
	if garden.active_plants.size() != 12:
		push_error("VERIFY_FAIL: Expected 12 demo plants, got %d" % garden.active_plants.size())
		quit(1)
		return
	for p in garden.active_plants:
		var exp_ds = garden.get_depth_scale(p.global_position.y)
		if abs(p.perspective_scale - exp_ds) > 0.001:
			push_error("VERIFY_FAIL: Demo plant perspective_scale mismatch: %f vs %f" % [p.perspective_scale, exp_ds])
			quit(1)
			return

	print("VERIFY_OK: Godot 2.5D perspective depth scaling, HUD slider, manifest scatter, and demo layout passed!")
	quit(0)
"""


class TestGodotPerspectiveDepth037(unittest.TestCase):
    def test_scripts_exist_and_strict_ascii(self):
        for path in [PLANT_PATH, GARDEN_PATH]:
            self.assertTrue(path.exists(), f"Missing script: {path}")
            content = path.read_text(encoding="utf-8")
            non_ascii = [(idx, ch) for idx, ch in enumerate(content) if ord(ch) >= 128]
            self.assertEqual(
                non_ascii,
                [],
                f"Non-ASCII characters found in {path.name}: {non_ascii[:10]}",
            )

    def test_godot_perspective_depth_headless_verify(self):
        self.assertTrue(os.path.exists(GODOT_BIN), f"Godot 4 binary not found at {GODOT_BIN}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            tmp_godot = tmp_root / "godot"
            shutil.copytree(
                REPO_ROOT / "godot",
                tmp_godot,
                ignore=shutil.ignore_patterns("*_task_*"),
            )
            shutil.copy2(PLANT_PATH, tmp_godot / "Plant.gd")
            shutil.copy2(GARDEN_PATH, tmp_godot / "Garden.gd")

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
            boot_output = res_boot.stdout + "\n" + res_boot.stderr
            self.assertEqual(res_boot.returncode, 0, f"Godot headless boot failed:\n{boot_output}")
            for bad_token in ["SCRIPT ERROR", "Parse Error", "Failed to load script", "ERROR:"]:
                self.assertNotIn(bad_token, boot_output, f"Unexpected Godot boot error ({bad_token}):\n{boot_output}")

            verify_path = tmp_godot / "test_verify_persp_037.gd"
            verify_path.write_text(VERIFY_GDSCRIPT, encoding="utf-8")

            res_verify = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--script", "res://test_verify_persp_037.gd"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            verify_output = res_verify.stdout + "\n" + res_verify.stderr
            self.assertEqual(res_verify.returncode, 0, f"Godot verify script failed:\n{verify_output}")
            self.assertIn("VERIFY_OK:", verify_output, f"Missing VERIFY_OK in output:\n{verify_output}")
            for bad_token in ["SCRIPT ERROR", "Parse Error", "VERIFY_FAIL", "ERROR:"]:
                self.assertNotIn(bad_token, verify_output, f"Unexpected Godot verify error ({bad_token}):\n{verify_output}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
