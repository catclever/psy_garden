import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GODOT_BIN = "/usr/local/bin/godot4"
TASK_SUFFIX = "_task_godot_cam_038"


def _pick(rel_draft: str, rel_merged: str) -> Path:
    d = REPO_ROOT / rel_draft
    return d if d.exists() else (REPO_ROOT / rel_merged)


SHADER_PATH = _pick(f"godot/plant_foliage{TASK_SUFFIX}.gdshader", "godot/plant_foliage.gdshader")
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

	# (a) screen_to_courtyard <-> project_courtyard_point roundtrip < 0.01px
	var sample_pts = [
		Vector2(720.0, 450.0),
		Vector2(420.0, 260.0),
		Vector2(1080.0, 690.0),
		Vector2(540.0, 740.0),
	]
	for ang in [0.0, 35.0, -72.0, 145.0]:
		garden.orbit_angle_deg = ang
		for pt in sample_pts:
			var c_pt: Vector2 = garden.screen_to_courtyard(pt)
			var back: Vector2 = garden.project_courtyard_point(c_pt)["screen_pos"]
			var err: float = pt.distance_to(back)
			if err >= 0.01:
				push_error("VERIFY_FAIL: Roundtrip error %f >= 0.01 at ang=%f pt=%s" % [err, ang, str(pt)])
				quit(1)
				return
	garden.orbit_angle_deg = 0.0

	# (b) Default camera_pitch_ratio == 0.76, ZenGround at z_index == -80,
	#     spawned garden plants pitch == 0.76 while standalone Plant defaults to 0.55
	if abs(garden.camera_pitch_ratio - 0.76) > 0.001:
		push_error("VERIFY_FAIL: Expected default camera_pitch_ratio == 0.76, got %f" % garden.camera_pitch_ratio)
		quit(1)
		return

	var zen_ground = garden.get_node_or_null("ZenGround")
	if not zen_ground:
		push_error("VERIFY_FAIL: Missing ZenGround node on Garden")
		quit(1)
		return
	if zen_ground.z_index != -80:
		push_error("VERIFY_FAIL: Expected ZenGround.z_index == -80, got %d" % zen_ground.z_index)
		quit(1)
		return

	if garden.active_plants.is_empty():
		push_error("VERIFY_FAIL: Expected active_plants to be populated")
		quit(1)
		return

	for i in range(garden.active_plants.size()):
		var p = garden.active_plants[i]
		if abs(p.ground_pitch_ratio - 0.76) > 0.001:
			push_error("VERIFY_FAIL: Garden plant %d ground_pitch_ratio expected 0.76, got %f" % [i, p.ground_pitch_ratio])
			quit(1)
			return
		var gsp = p.get_node_or_null("GroundShadowPlane")
		if not gsp or abs(gsp.scale.y - 0.76) > 0.001:
			push_error("VERIFY_FAIL: Garden plant %d GroundShadowPlane.scale.y expected 0.76" % i)
			quit(1)
			return

	var plant_scene = load("res://Plant.tscn")
	var standalone = plant_scene.instantiate()
	standalone.skip_auto_load = true
	get_root().add_child(standalone)
	if abs(standalone.ground_pitch_ratio - 0.55) > 0.001:
		push_error("VERIFY_FAIL: Standalone Plant ground_pitch_ratio expected 0.55, got %f" % standalone.ground_pitch_ratio)
		quit(1)
		return
	var sgsp = standalone.get_node_or_null("GroundShadowPlane")
	if not sgsp or abs(sgsp.scale.y - 0.55) > 0.001:
		push_error("VERIFY_FAIL: Standalone Plant GroundShadowPlane.scale.y expected 0.55")
		quit(1)
		return
	standalone.queue_free()

	# Check HUD index compatibility for TEST_037
	if garden.perspective_label.get_index() != garden.lantern_slider.get_index() + 1:
		push_error("VERIFY_FAIL: perspective_label index shifted")
		quit(1)
		return
	if garden.perspective_slider.get_index() != garden.lantern_slider.get_index() + 2:
		push_error("VERIFY_FAIL: perspective_slider index shifted")
		quit(1)
		return

	# (c) Orbiting target_orbit_deg = 180.0 and calling _process(1.5)
	var rear_plant = null
	for p in garden.active_plants:
		if p.global_position.y < 420.0:
			rear_plant = p
			break
	if not rear_plant:
		push_error("VERIFY_FAIL: Could not find a rear plant with y < 420.0")
		quit(1)
		return

	var old_y: float = rear_plant.global_position.y
	var old_scale: float = rear_plant.perspective_scale
	garden.target_orbit_deg = 180.0
	garden._process(1.5)

	if not (rear_plant.global_position.y > 450.0):
		push_error("VERIFY_FAIL: Expected rear plant (was y=%f) to orbit to front y > 450, got y=%f" % [old_y, rear_plant.global_position.y])
		quit(1)
		return
	var expected_orbited_scale: float = garden.get_depth_scale(rear_plant.global_position.y)
	if abs(rear_plant.perspective_scale - expected_orbited_scale) > 0.001:
		push_error("VERIFY_FAIL: Orbited perspective_scale mismatch: %f vs %f" % [rear_plant.perspective_scale, expected_orbited_scale])
		quit(1)
		return
	if not (rear_plant.perspective_scale > old_scale):
		push_error("VERIFY_FAIL: Expected orbited rear->front plant scale to increase (%f -> %f)" % [old_scale, rear_plant.perspective_scale])
		quit(1)
		return

	# Reset orbit cleanly before next checks
	garden._set_orbit_target(0.0, true)

	# (d) Directional lantern-only X-Ray test: only plants strictly in front of lantern fade
	garden.clear_garden()
	var img = Image.create(100, 200, false, Image.FORMAT_RGBA8)
	img.fill(Color(0, 0, 0, 0))
	for y in range(20, 181):
		for x in range(25, 76):
			img.set_pixel(x, y, Color(0.2, 0.8, 0.3, 1.0))
	var synth_tex = ImageTexture.create_from_image(img)

	var _spawn_synth = func(name_id: String, pos: Vector2, target_h: float, root_w: float):
		var sp = plant_scene.instantiate()
		sp.skip_auto_load = true
		sp.global_position = pos
		sp.ground_pitch_ratio = garden.camera_pitch_ratio
		sp.courtyard_pos = garden.screen_to_courtyard(pos)
		sp.has_courtyard_pos = true
		garden.y_sort.add_child(sp)
		garden.active_plants.append(sp)
		sp.setup_with_textures(name_id, synth_tex, synth_tex, null, target_h, root_w)
		return sp

	var p_behind = _spawn_synth.call("synth_behind", Vector2(700.0, 380.0), 160.0, 24.0)
	var p_at = _spawn_synth.call("synth_at", Vector2(700.0, 440.0), 60.0, 18.0)
	var p_front = _spawn_synth.call("synth_front", Vector2(700.0, 530.0), 180.0, 30.0)
	var p_far_away = _spawn_synth.call("synth_far_away", Vector2(1120.0, 530.0), 180.0, 30.0)

	garden._test_mouse_override = Vector2(700.0, 440.0)
	garden._process(0.6)
	garden._process(0.6)

	if p_front.current_xray_alpha > 0.40:
		push_error("VERIFY_FAIL: Expected front-of-lantern plant current_xray_alpha <= 0.40, got %f" % p_front.current_xray_alpha)
		quit(1)
		return
	if p_behind.current_xray_alpha < 0.99:
		push_error("VERIFY_FAIL: Expected behind-lantern plant to stay solid >= 0.99, got %f" % p_behind.current_xray_alpha)
		quit(1)
		return
	if p_at.current_xray_alpha < 0.99:
		push_error("VERIFY_FAIL: Expected at-lantern plant to stay solid >= 0.99, got %f" % p_at.current_xray_alpha)
		quit(1)
		return
	if p_far_away.current_xray_alpha < 0.99:
		push_error("VERIFY_FAIL: Expected away-from-lantern overlapping plant to stay solid >= 0.99, got %f" % p_far_away.current_xray_alpha)
		quit(1)
		return

	# Moving lantern in front of p_front must restore p_front to solid 1.0
	garden._test_mouse_override = Vector2(700.0, 580.0)
	garden._process(0.6)
	garden._process(0.6)
	if p_front.current_xray_alpha < 0.99:
		push_error("VERIFY_FAIL: Expected p_front to become solid >= 0.99 when lantern moves in front of it, got %f" % p_front.current_xray_alpha)
		quit(1)
		return

	# Put lantern back behind p_front, confirm fade, then toggle X-Ray OFF to restore solid 1.0
	garden._test_mouse_override = Vector2(700.0, 440.0)
	garden._process(0.6)
	garden._process(0.6)
	if p_front.current_xray_alpha > 0.40:
		push_error("VERIFY_FAIL: Expected p_front <= 0.40 before toggle OFF, got %f" % p_front.current_xray_alpha)
		quit(1)
		return

	garden._on_toggle_xray_pressed()
	garden._process(0.6)
	garden._process(0.6)
	if p_front.current_xray_alpha < 0.99:
		push_error("VERIFY_FAIL: Expected front plant current_xray_alpha >= 0.99 after X-Ray toggle OFF, got %f" % p_front.current_xray_alpha)
		quit(1)
		return

	garden._test_mouse_override = Vector2(-99999.0, -99999.0)

	# (e) Adjusting pitch_slider.value = 0.62 updates camera_pitch_ratio and GroundShadowPlane.scale.y
	if not garden.pitch_slider:
		push_error("VERIFY_FAIL: Missing pitch_slider on Garden")
		quit(1)
		return
	garden.pitch_slider.value = 0.62
	if abs(garden.camera_pitch_ratio - 0.62) > 0.001:
		push_error("VERIFY_FAIL: Expected camera_pitch_ratio == 0.62 after pitch_slider=0.62, got %f" % garden.camera_pitch_ratio)
		quit(1)
		return
	for i in range(garden.active_plants.size()):
		var p = garden.active_plants[i]
		if abs(p.ground_pitch_ratio - 0.62) > 0.001:
			push_error("VERIFY_FAIL: Plant %d ground_pitch_ratio expected 0.62, got %f" % [i, p.ground_pitch_ratio])
			quit(1)
			return
		var gsp = p.get_node_or_null("GroundShadowPlane")
		if not gsp or abs(gsp.scale.y - 0.62) > 0.001:
			push_error("VERIFY_FAIL: Plant %d GroundShadowPlane.scale.y expected 0.62, got %s" % [i, str(gsp.scale.y if gsp else -1.0)])
			quit(1)
			return

	print("VERIFY_OK: Godot 2.5D courtyard pitch, zen ground, orbit, and dynamic X-Ray passed!")
	quit(0)
"""


class TestGodotCourtyardCamera038(unittest.TestCase):
    def test_scripts_exist_and_strict_ascii(self):
        for path in [SHADER_PATH, PLANT_PATH, GARDEN_PATH]:
            self.assertTrue(path.exists(), f"Missing file: {path}")
            content = path.read_text(encoding="utf-8")
            non_ascii = [(idx, ch) for idx, ch in enumerate(content) if ord(ch) >= 128]
            self.assertEqual(
                non_ascii,
                [],
                f"Non-ASCII characters found in {path.name}: {non_ascii[:10]}",
            )

    def test_godot_courtyard_camera_headless_verify(self):
        self.assertTrue(os.path.exists(GODOT_BIN), f"Godot 4 binary not found at {GODOT_BIN}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            tmp_godot = tmp_root / "godot"
            shutil.copytree(
                REPO_ROOT / "godot",
                tmp_godot,
                ignore=shutil.ignore_patterns("*_task_*"),
            )
            shutil.copy2(SHADER_PATH, tmp_godot / "plant_foliage.gdshader")
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

            verify_path = tmp_godot / "test_verify_cam_038.gd"
            verify_path.write_text(VERIFY_GDSCRIPT, encoding="utf-8")

            res_verify = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--script", "res://test_verify_cam_038.gd"],
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
