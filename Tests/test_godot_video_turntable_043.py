import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
GODOT_BIN = "/usr/local/bin/godot4"

FOLIAGE_SHADER_PATH = REPO_ROOT / "godot" / "plant_foliage.gdshader"
SHADOW_SHADER_PATH = REPO_ROOT / "godot" / "ink_shadow.gdshader"
PLANT_PATH = REPO_ROOT / "godot" / "Plant.gd"
GARDEN_PATH = REPO_ROOT / "godot" / "Garden.gd"
VIDEO_LAB_DIR = REPO_ROOT / "src" / "assets" / "turntable_lab" / "plant_0000_video36"
ATLAS_WEBP_PATH = VIDEO_LAB_DIR / "atlas_36.webp"
META_JSON_PATH = VIDEO_LAB_DIR / "metadata.json"
CONTACT_SHEET_PATH = VIDEO_LAB_DIR / "video_36_contact_sheet.jpg"


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

	# 1. Default startup loads 383 manifest_records and 36 active_plants; HUD has BtnVideoLab
	if garden.manifest_records.size() != 383:
		push_error("VERIFY_FAIL: Expected 383 manifest_records on default startup, got %d" % garden.manifest_records.size())
		quit(1)
		return
	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active_plants on default startup, got %d" % garden.active_plants.size())
		quit(1)
		return

	var vbox = garden.get_node_or_null("HUD/MarginContainer/VBoxContainer")
	if not vbox:
		push_error("VERIFY_FAIL: Missing HUD VBoxContainer")
		quit(1)
		return
	var btn_video = vbox.get_node_or_null("BtnVideoLab")
	if not btn_video:
		push_error("VERIFY_FAIL: Missing BtnVideoLab in HUD VBoxContainer")
		quit(1)
		return
	if abs(btn_video.custom_minimum_size.y - 24.0) > 0.1:
		push_error("VERIFY_FAIL: Expected BtnVideoLab custom_minimum_size.y == 24, got %f" % btn_video.custom_minimum_size.y)
		quit(1)
		return
	if btn_video.get_index() >= garden.fleet_status_label.get_index():
		push_error("VERIFY_FAIL: BtnVideoLab must be placed before FleetStatusLabel")
		quit(1)
		return

	# 2. enter_video_turntable_lab() spawns 4 plants (1 Legacy, 1 8-Way, 2 Video 36F)
	garden.enter_video_turntable_lab()
	await process_frame
	await process_frame

	if not garden.video_lab_active:
		push_error("VERIFY_FAIL: Expected video_lab_active == true")
		quit(1)
		return
	if garden.active_plants.size() != 4:
		push_error("VERIFY_FAIL: Expected 4 plants in video turntable lab, got %d" % garden.active_plants.size())
		quit(1)
		return

	var legacy_count: int = 0
	var turntable_8_count: int = 0
	var video_front = null
	var video_back = null

	for p in garden.active_plants:
		if p.is_video_atlas:
			if abs(p.base_yaw_deg) < 0.01:
				video_front = p
			elif abs(p.base_yaw_deg - 180.0) < 0.01:
				video_back = p
		elif p.is_turntable:
			turntable_8_count += 1
		else:
			legacy_count += 1

	if legacy_count != 1 or turntable_8_count != 1 or video_front == null or video_back == null:
		push_error("VERIFY_FAIL: Expected 1 legacy, 1 8-way, 2 video (yaw=0 & yaw=180), got %d/%d" % [legacy_count, turntable_8_count])
		quit(1)
		return

	if video_front.sprite_green.hframes != 6 or video_front.sprite_green.vframes != 6:
		push_error("VERIFY_FAIL: Expected 6x6 hframes/vframes on video_front.sprite_green")
		quit(1)
		return
	if video_front.sun_shadow.hframes != 6 or video_front.sun_shadow.vframes != 6:
		push_error("VERIFY_FAIL: Expected 6x6 hframes/vframes on video_front.sun_shadow")
		quit(1)
		return

	# 3. Orbiting 0..350 step 10 hits all 36 frames cleanly, keeps shadow + uv_y_range synced, and keeps back offset by 18 frames
	var seen_frames := {}
	for step_idx in range(36):
		var ang_deg: float = float(step_idx * 10)
		garden._set_orbit_target(ang_deg, true)
		garden._process(0.05)

		var f_idx: int = video_front.current_video_frame
		seen_frames[f_idx] = true
		if f_idx != step_idx:
			push_error("VERIFY_FAIL: At orbit=%f expected frame %d, got %d" % [ang_deg, step_idx, f_idx])
			quit(1)
			return
		if video_front.sprite_green.frame != f_idx or video_front.sun_shadow.frame != f_idx:
			push_error("VERIFY_FAIL: Sprite/shadow frame mismatch at orbit=%f (%d vs %d vs %d)" % [ang_deg, f_idx, video_front.sprite_green.frame, video_front.sun_shadow.frame])
			quit(1)
			return

		var expected_row: float = float(int(floor(float(f_idx) / 6.0)))
		var expected_uv := Vector2(expected_row / 6.0, (expected_row + 1.0) / 6.0)
		var g_uv: Vector2 = (video_front.sprite_green.material as ShaderMaterial).get_shader_parameter("uv_y_range")
		var s_uv: Vector2 = (video_front.sun_shadow.material as ShaderMaterial).get_shader_parameter("uv_y_range")
		if g_uv.distance_to(expected_uv) > 0.001 or s_uv.distance_to(expected_uv) > 0.001:
			push_error("VERIFY_FAIL: uv_y_range mismatch at frame %d: g=%s s=%s expected=%s" % [f_idx, str(g_uv), str(s_uv), str(expected_uv)])
			quit(1)
			return

		var expected_back: int = (f_idx + 18) % 36
		if video_back.current_video_frame != expected_back:
			push_error("VERIFY_FAIL: Back yaw=180 expected frame %d, got %d" % [expected_back, video_back.current_video_frame])
			quit(1)
			return

	if seen_frames.size() != 36:
		push_error("VERIFY_FAIL: Expected all 36 frames visited, got %d" % seen_frames.size())
		quit(1)
		return

	# 4. Fixed-depth courtyard (0,0) video plant has < 0.05px height variance and locked offsets across all 36 angles
	var fixed_v = load("res://Plant.tscn").instantiate()
	fixed_v.skip_auto_load = true
	fixed_v.global_position = garden.project_courtyard_point(Vector2.ZERO)["screen_pos"]
	fixed_v.perspective_scale = garden.project_courtyard_point(Vector2.ZERO)["depth_scale"]
	garden.y_sort.add_child(fixed_v)
	fixed_v.setup_video_atlas_turntable(
		"plant_0000",
		video_front.video_atlas_tex,
		6,
		6,
		36,
		Vector2(110.0, 212.0),
		188.0,
		150.0,
		22.0
	)

	var min_vh: float = 99999.0
	var max_vh: float = -99999.0
	for step_idx in range(36):
		fixed_v.update_turntable_orbit(float(step_idx * 10), 0.016, true)
		min_vh = min(min_vh, fixed_v.visual_height)
		max_vh = max(max_vh, fixed_v.visual_height)
		if fixed_v.sprite_green.offset.distance_to(-fixed_v.root_anchor) > 0.01:
			push_error("VERIFY_FAIL: fixed_v sprite_green.offset != -root_anchor at step %d" % step_idx)
			quit(1)
			return
		if fixed_v.sun_shadow.offset.distance_to(-fixed_v.root_anchor) > 0.01:
			push_error("VERIFY_FAIL: fixed_v sun_shadow.offset != -root_anchor at step %d" % step_idx)
			quit(1)
			return
	if (max_vh - min_vh) >= 0.05:
		push_error("VERIFY_FAIL: Fixed-depth video plant visual_height variance >= 0.05px: %f" % (max_vh - min_vh))
		quit(1)
		return
	fixed_v.queue_free()

	# 5. exit_video_turntable_lab(true) restores 36 active_plants
	garden.exit_video_turntable_lab(true)
	if garden.video_lab_active:
		push_error("VERIFY_FAIL: Expected video_lab_active == false after exit")
		quit(1)
		return
	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active_plants after exiting video lab, got %d" % garden.active_plants.size())
		quit(1)
		return

	print("VERIFY_OK: Godot 36-Frame Video Turntable Atlas Lab verified!")
	quit(0)
"""


class TestGodotVideoTurntable043(unittest.TestCase):
    def test_strict_ascii_and_styles_unmodified(self):
        for path in [FOLIAGE_SHADER_PATH, SHADOW_SHADER_PATH, PLANT_PATH, GARDEN_PATH]:
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

    def test_video_atlas_assets_and_metadata(self):
        self.assertTrue(ATLAS_WEBP_PATH.exists(), f"Missing {ATLAS_WEBP_PATH}")
        self.assertTrue(META_JSON_PATH.exists(), f"Missing {META_JSON_PATH}")
        self.assertTrue(CONTACT_SHEET_PATH.exists(), f"Missing {CONTACT_SHEET_PATH}")
        self.assertGreater(CONTACT_SHEET_PATH.stat().st_size, 10000)

        with Image.open(ATLAS_WEBP_PATH) as im:
            self.assertEqual(im.size, (1320, 1320))
            self.assertEqual(im.mode, "RGBA")

        meta = json.loads(META_JSON_PATH.read_text(encoding="utf-8"))
        self.assertEqual(meta.get("frame_count"), 36)
        self.assertEqual(meta.get("hframes"), 6)
        self.assertEqual(meta.get("vframes"), 6)
        self.assertEqual(meta.get("root_anchor_px"), [110.0, 212.0])
        self.assertTrue(0.85 <= float(meta.get("root_uv_y", 0.0)) <= 0.99)

    def test_godot_video_turntable_headless_verify(self):
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

            verify_path = tmp_godot / "test_verify_video_043.gd"
            verify_path.write_text(VERIFY_GDSCRIPT, encoding="utf-8")

            res_verify = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--script", "res://test_verify_video_043.gd"],
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
