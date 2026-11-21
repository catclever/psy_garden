import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GODOT_BIN = "/usr/local/bin/godot4"

SHADER_PATH = REPO_ROOT / "godot" / "plant_foliage.gdshader"
PLANT_PATH = REPO_ROOT / "godot" / "Plant.gd"
GARDEN_PATH = REPO_ROOT / "godot" / "Garden.gd"
LAB_DIR = REPO_ROOT / "src" / "assets" / "turntable_lab"
INDEX_PATH = LAB_DIR / "index.json"
EXPECTED_ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]


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

	# 1. Default startup still loads 383 manifest_records and 36 active_plants
	if garden.manifest_records.size() != 383:
		push_error("VERIFY_FAIL: Expected 383 manifest_records on default startup, got %d" % garden.manifest_records.size())
		quit(1)
		return
	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active_plants on default startup, got %d" % garden.active_plants.size())
		quit(1)
		return

	# Check HUD compact turntable controls are placed before FleetStatusLabel with custom_minimum_size.y == 24
	var vbox = garden.get_node_or_null("HUD/MarginContainer/VBoxContainer")
	if not vbox:
		push_error("VERIFY_FAIL: Missing HUD VBoxContainer")
		quit(1)
		return
	var btn_lab = vbox.get_node_or_null("BtnTurntableLab")
	var ctrl_row = vbox.get_node_or_null("TurntableCtrlRow")
	if not btn_lab or not ctrl_row:
		push_error("VERIFY_FAIL: Missing BtnTurntableLab or TurntableCtrlRow")
		quit(1)
		return
	if abs(btn_lab.custom_minimum_size.y - 24.0) > 0.1 or abs(ctrl_row.custom_minimum_size.y - 24.0) > 0.1:
		push_error("VERIFY_FAIL: Expected turntable HUD buttons custom_minimum_size.y == 24")
		quit(1)
		return
	if btn_lab.get_index() >= garden.fleet_status_label.get_index() or ctrl_row.get_index() >= garden.fleet_status_label.get_index():
		push_error("VERIFY_FAIL: Turntable HUD buttons must be placed before FleetStatusLabel")
		quit(1)
		return

	# 2. enter_turntable_lab() spawns 6 plants (2 Legacy, 4 3A Turntable) and loads all 8 frames
	garden.enter_turntable_lab()
	await process_frame
	await process_frame

	if not garden.turntable_lab_active:
		push_error("VERIFY_FAIL: Expected turntable_lab_active == true")
		quit(1)
		return
	if garden.active_plants.size() != 6:
		push_error("VERIFY_FAIL: Expected 6 plants in turntable lab, got %d" % garden.active_plants.size())
		quit(1)
		return

	var legacy_plants: Array = []
	var turntable_plants: Array = []
	var right_yaw0_plants: Array = []
	for p in garden.active_plants:
		if p.is_turntable:
			turntable_plants.append(p)
			if abs(p.base_yaw_deg) < 0.01:
				right_yaw0_plants.append(p)
			if p.turntable_frames.size() != 8:
				push_error("VERIFY_FAIL: Expected 8 turntable frames on %s, got %d" % [p.plant_id, p.turntable_frames.size()])
				quit(1)
				return
		else:
			legacy_plants.append(p)

	if legacy_plants.size() != 2 or turntable_plants.size() != 4 or right_yaw0_plants.size() != 2:
		push_error("VERIFY_FAIL: Expected 2 legacy, 4 turntable (2 yaw=0), got %d/%d/%d" % [legacy_plants.size(), turntable_plants.size(), right_yaw0_plants.size()])
		quit(1)
		return

	# 3. Orbiting 0 -> 45 -> 90 -> 180 -> -90 switches right-side yaw=0 3A current_sector_deg
	var legacy_ref = legacy_plants[0]
	var legacy_initial_tex = legacy_ref.sprite_green.texture
	var right_cal = null
	for rp in right_yaw0_plants:
		if rp.plant_id == "plant_0000":
			right_cal = rp
			break
	if not right_cal:
		push_error("VERIFY_FAIL: Could not find right-side yaw=0 Calathea plant_0000")
		quit(1)
		return

	var orbit_seq = [
		[0.0, 0],
		[45.0, 45],
		[90.0, 90],
		[180.0, 180],
		[-90.0, 270],
	]
	var prev_foliage_tex = null
	var prev_shadow_tex = null
	var distinct_foliage := {}
	var distinct_shadows := {}

	for pair in orbit_seq:
		var ang_deg: float = pair[0]
		var exp_sector: int = pair[1]
		garden._set_orbit_target(ang_deg, true)
		garden._process(0.2)
		if right_cal.current_sector_deg != exp_sector:
			push_error("VERIFY_FAIL: At orbit=%f expected current_sector_deg=%d, got %d" % [ang_deg, exp_sector, right_cal.current_sector_deg])
			quit(1)
			return
		var ftex = right_cal.sprite_green.texture
		var stex = right_cal.sun_shadow.texture
		if not ftex or not stex:
			push_error("VERIFY_FAIL: Missing foliage or shadow texture at sector %d" % exp_sector)
			quit(1)
			return
		if prev_foliage_tex != null and ftex == prev_foliage_tex:
			push_error("VERIFY_FAIL: sprite_green.texture did not change across sector %d" % exp_sector)
			quit(1)
			return
		if prev_shadow_tex != null and stex == prev_shadow_tex:
			push_error("VERIFY_FAIL: sun_shadow.texture did not change across sector %d" % exp_sector)
			quit(1)
			return
		prev_foliage_tex = ftex
		prev_shadow_tex = stex
		distinct_foliage[ftex] = true
		distinct_shadows[stex] = true
		if legacy_ref.sprite_green.texture != legacy_initial_tex:
			push_error("VERIFY_FAIL: Legacy plant texture changed during orbit!")
			quit(1)
			return

	if distinct_foliage.size() != 5 or distinct_shadows.size() != 5:
		push_error("VERIFY_FAIL: Expected 5 distinct textures across 0/45/90/180/270, got %d/%d" % [distinct_foliage.size(), distinct_shadows.size()])
		quit(1)
		return

	# 4. Fixed-depth courtyard (0,0) turntable plant visual_height variance across all 8 angles < 1.0px
	#    and sprite_green.offset == -root_anchor
	var fixed_p = load("res://Plant.tscn").instantiate()
	fixed_p.skip_auto_load = true
	fixed_p.desired_height_px = 150.0
	fixed_p.meta_root_width = 22.0
	fixed_p.global_position = garden.project_courtyard_point(Vector2.ZERO)["screen_pos"]
	fixed_p.perspective_scale = garden.project_courtyard_point(Vector2.ZERO)["depth_scale"]
	garden.y_sort.add_child(fixed_p)

	for ang_key in right_cal.turntable_frames.keys():
		var fr = right_cal.turntable_frames[ang_key]
		var uv = Vector2(fr["root_anchor"].x / float(max(fr["width"], 1)), fr["root_anchor"].y / float(max(fr["height"], 1)))
		fixed_p.register_turntable_frame(int(ang_key), fr["tex_red"], fr["tex_green"], fr["tex_blue"], uv, fr["crown_h"])

	var min_vh: float = 99999.0
	var max_vh: float = -99999.0
	for ang in [0, 45, 90, 135, 180, 225, 270, 315]:
		fixed_p.update_turntable_orbit(float(ang), 0.0, true)
		min_vh = min(min_vh, fixed_p.visual_height)
		max_vh = max(max_vh, fixed_p.visual_height)
		if fixed_p.sprite_green.offset.distance_to(-fixed_p.root_anchor) > 0.01:
			push_error("VERIFY_FAIL: sprite_green.offset != -root_anchor at angle %d" % ang)
			quit(1)
			return
		if fixed_p.sun_shadow.offset.distance_to(-fixed_p.root_anchor) > 0.01:
			push_error("VERIFY_FAIL: sun_shadow.offset != -root_anchor at angle %d" % ang)
			quit(1)
			return

	if (max_vh - min_vh) >= 1.0:
		push_error("VERIFY_FAIL: Fixed-depth visual_height variance >= 1.0px: %f (%f..%f)" % [max_vh - min_vh, min_vh, max_vh])
		quit(1)
		return
	fixed_p.queue_free()

	# 5. Switching to 4-Way mode quantizes orbit=45 deg to 0 or 90 (never 45)
	garden._set_orbit_target(45.0, true)
	garden._on_turntable_dirs_pressed()
	if garden.turntable_sector_count != 4:
		push_error("VERIFY_FAIL: Expected turntable_sector_count == 4 after toggle")
		quit(1)
		return
	if right_cal.current_sector_deg != 0 and right_cal.current_sector_deg != 90:
		push_error("VERIFY_FAIL: Expected 4-Way orbit=45 to quantize to 0 or 90, got %d" % right_cal.current_sector_deg)
		quit(1)
		return

	# Switch back to 8-Way and cycle Cross-Fade / Pop-Flip / Crisp Snap
	garden._on_turntable_dirs_pressed()
	for _cycle in range(4):
		garden._on_turntable_transition_pressed()
		garden._set_orbit_target(0.0, true)
		garden._process(0.05)
		garden._set_orbit_target(90.0, true)
		garden._process(0.05)
		garden._process(0.15)

	# Exit lab cleanly back to garden
	garden.exit_turntable_lab(true)
	if garden.turntable_lab_active:
		push_error("VERIFY_FAIL: Expected turntable_lab_active == false after exit")
		quit(1)
		return
	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active_plants after exiting lab, got %d" % garden.active_plants.size())
		quit(1)
		return

	print("VERIFY_OK: Godot 3A Multi-Angle Turntable Lab verified!")
	quit(0)
"""


class TestGodotTurntableLab042(unittest.TestCase):
    def test_strict_ascii_and_styles_unmodified(self):
        for path in [SHADER_PATH, PLANT_PATH, GARDEN_PATH]:
            self.assertTrue(path.exists(), f"Missing file: {path}")
            content = path.read_text(encoding="utf-8")
            non_ascii = [(idx, ch) for idx, ch in enumerate(content) if ord(ch) >= 128]
            self.assertEqual(
                non_ascii,
                [],
                f"Non-ASCII characters found in {path.name}: {non_ascii[:10]}",
            )

        res = subprocess.run(
            ["git", "diff", "--name-only", "src/styles.css"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, f"git diff failed: {res.stderr}")
        self.assertEqual(res.stdout.strip(), "", f"src/styles.css was modified: {res.stdout}")

    def test_turntable_lab_index_and_layers(self):
        self.assertTrue(INDEX_PATH.exists(), f"Missing {INDEX_PATH}")
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data.get("angles"), EXPECTED_ANGLES)
        species_by_name = {sp["name"]: sp for sp in data.get("species", [])}
        self.assertIn("plant_0000", species_by_name)
        self.assertIn("plant_0177", species_by_name)

        for pid in ("plant_0000", "plant_0177"):
            sp = species_by_name[pid]
            self.assertIn("legacy", sp)
            views = sp.get("views", {})
            for ang in EXPECTED_ANGLES:
                v = views.get(str(ang))
                self.assertIsNotNone(v, f"Missing angle {ang} for {pid}")
                ra = v.get("root_anchor")
                self.assertIsInstance(ra, list)
                self.assertEqual(len(ra), 2)
                self.assertTrue(0.05 <= ra[0] <= 0.95)
                self.assertTrue(0.60 <= ra[1] <= 1.0)
                prefix = REPO_ROOT / "src" / v["path_prefix"]
                layer_files = v.get("layer_files", [])
                self.assertGreaterEqual(len(layer_files), 2)
                for lf in layer_files:
                    fpath = prefix / lf["file"]
                    self.assertTrue(fpath.exists(), f"Missing layer PNG: {fpath}")

    def test_godot_turntable_lab_headless_verify(self):
        self.assertTrue(os.path.exists(GODOT_BIN), f"Godot 4 binary not found at {GODOT_BIN}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            tmp_godot = tmp_root / "godot"
            shutil.copytree(
                REPO_ROOT / "godot",
                tmp_godot,
                ignore=shutil.ignore_patterns("*_task_*"),
            )

            tmp_src = tmp_root / "src"
            tmp_src.mkdir(parents=True, exist_ok=True)
            os.symlink(REPO_ROOT / "src" / "assets", tmp_src / "assets")

            env = os.environ.copy()
            env["GODOT_SILENCE_ROOT_WARNING"] = "1"

            verify_path = tmp_godot / "test_verify_turntable_042.gd"
            verify_path.write_text(VERIFY_GDSCRIPT, encoding="utf-8")

            res_verify = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--script", "res://test_verify_turntable_042.gd"],
                capture_output=True,
                text=True,
                env=env,
                timeout=45,
            )
            verify_output = res_verify.stdout + "\n" + res_verify.stderr
            self.assertEqual(res_verify.returncode, 0, f"Godot verify script failed:\n{verify_output}")
            self.assertIn("VERIFY_OK:", verify_output, f"Missing VERIFY_OK in output:\n{verify_output}")
            for bad_token in ["SCRIPT ERROR", "Parse Error", "VERIFY_FAIL", "ERROR:"]:
                self.assertNotIn(bad_token, verify_output, f"Unexpected Godot verify error ({bad_token}):\n{verify_output}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
