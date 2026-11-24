import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GODOT_BIN = "/usr/local/bin/godot4"

GARDEN_PATH = REPO_ROOT / "godot" / "Garden.gd"
PLANT_PATH = REPO_ROOT / "godot" / "Plant.gd"
LIGHT_DEMO_HTML = REPO_ROOT / "src" / "light_demo.html"


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
	await process_frame

	if garden.manifest_records.size() != 383:
		push_error("VERIFY_FAIL: Expected 383 manifest_records on startup, got %d" % garden.manifest_records.size())
		quit(1)
		return
	if garden.fleet_status_label == null or garden.fleet_status_label.text != "Fleet: 383 species loaded":
		push_error("VERIFY_FAIL: Unexpected fleet_status_label: %s" % str(garden.fleet_status_label.text if garden.fleet_status_label else "null"))
		quit(1)
		return
	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active_plants on startup, got %d" % garden.active_plants.size())
		quit(1)
		return
	if garden.video36_by_id.size() < 4:
		push_error("VERIFY_FAIL: Expected >= 4 video36_by_id entries, got %d" % garden.video36_by_id.size())
		quit(1)
		return
	if garden.video_gallery_active or garden.video_lab_active or garden.turntable_lab_active:
		push_error("VERIFY_FAIL: Regular startup must not enter lab or gallery modes")
		quit(1)
		return

	var v36_plants := []
	var seen_v36 := {}
	for i in range(garden.active_plants.size()):
		var p = garden.active_plants[i]
		if p.is_video_atlas:
			v36_plants.append(p)
			seen_v36[p.plant_id] = true
			if p.sprite_green.hframes != 6 or p.sprite_green.vframes != 6:
				push_error("VERIFY_FAIL: Expected 6x6 hframes/vframes on 36F regular plant %s" % p.plant_id)
				quit(1)
				return
		for j in range(i + 1, garden.active_plants.size()):
			var q = garden.active_plants[j]
			var dx: float = p.global_position.x - q.global_position.x
			var dy: float = p.global_position.y - q.global_position.y
			var a: float = (p.fp_w + q.fp_w) * 0.5 + 12.0
			var b: float = (p.fp_h + q.fp_h) * 0.5 + 8.0
			if ((dx / a) * (dx / a) + (dy / b) * (dy / b)) < 1.0:
				push_error("VERIFY_FAIL: 2.5D overlap between %d (%s) and %d (%s)" % [i, p.plant_id, j, q.plant_id])
				quit(1)
				return

	if v36_plants.size() < 4:
		push_error("VERIFY_FAIL: Expected >= 4 regular-garden is_video_atlas plants, got %d" % v36_plants.size())
		quit(1)
		return
	for req_id in ["plant_0000", "plant_0003", "plant_0005", "plant_0177"]:
		if not seen_v36.has(req_id):
			push_error("VERIFY_FAIL: Regular 36-plant garden missing published 36F species %s" % req_id)
			quit(1)
			return

	# Verify orbit rotation advances all regular-garden 36F plants (0 -> 90 -> 180)
	garden._set_orbit_target(0.0, true)
	garden._process(0.05)
	var frames_0 := []
	for vp in v36_plants:
		frames_0.append(vp.current_video_frame)
		if vp.sprite_green.frame != vp.current_video_frame or vp.sun_shadow.frame != vp.current_video_frame:
			push_error("VERIFY_FAIL: Frame mismatch at orbit=0 on %s" % vp.plant_id)
			quit(1)
			return

	garden._set_orbit_target(90.0, true)
	garden._process(0.05)
	var frames_90 := []
	for idx in range(v36_plants.size()):
		var vp = v36_plants[idx]
		frames_90.append(vp.current_video_frame)
		if vp.current_video_frame == frames_0[idx]:
			push_error("VERIFY_FAIL: 36F plant %s did not rotate from 0 to 90 deg" % vp.plant_id)
			quit(1)
			return

	garden._set_orbit_target(180.0, true)
	garden._process(0.05)
	for idx in range(v36_plants.size()):
		var vp = v36_plants[idx]
		if vp.current_video_frame == frames_90[idx]:
			push_error("VERIFY_FAIL: 36F plant %s did not rotate from 90 to 180 deg" % vp.plant_id)
			quit(1)
			return
		if vp.sprite_green.frame != vp.current_video_frame or vp.sun_shadow.frame != vp.current_video_frame:
			push_error("VERIFY_FAIL: Sprite/shadow frame mismatch at orbit=180 on %s" % vp.plant_id)
			quit(1)
			return

	garden._set_orbit_target(0.0, true)
	garden._process(0.05)

	# Verify click hit-testing & selection ring toggle
	var first_p = garden.active_plants[0]
	var click_pt = first_p.global_position - Vector2(0.0, min(first_p.visual_height * 0.35, 22.0))
	var picked = garden.pick_plant_at_screen_pos(click_pt)
	if picked == null:
		push_error("VERIFY_FAIL: pick_plant_at_screen_pos returned null on active plant %s" % first_p.plant_id)
		quit(1)
		return
	garden._select_plant(picked, false)
	if not picked.is_selected or garden.selected_plant != picked:
		push_error("VERIFY_FAIL: Plant selection state mismatch after _select_plant")
		quit(1)
		return
	garden._select_plant(null, false)
	if picked.is_selected or garden.selected_plant != null:
		push_error("VERIFY_FAIL: Selection was not cleared properly")
		quit(1)
		return

	# Verify handle_bridge_command API contract for Route 2 integration
	var res_mode: Dictionary = garden.handle_bridge_command(JSON.stringify({
		"type": "set_planting_mode",
		"payload": {"plant_id": "plant_0005"}
	}))
	if not res_mode.get("ok", false) or garden.planting_species_id != "plant_0005":
		push_error("VERIFY_FAIL: set_planting_mode failed: %s" % str(res_mode))
		quit(1)
		return
	garden.handle_bridge_command(JSON.stringify({"type": "set_planting_mode", "payload": {"plant_id": ""}}))
	if garden.planting_species_id != "":
		push_error("VERIFY_FAIL: Clearing planting_mode failed")
		quit(1)
		return

	var res_clear: Dictionary = garden.handle_bridge_command(JSON.stringify({"type": "clear_garden", "payload": {}}))
	await process_frame
	if not res_clear.get("ok", false) or garden.active_plants.size() != 0:
		push_error("VERIFY_FAIL: clear_garden failed: %s (count=%d)" % [str(res_clear), garden.active_plants.size()])
		quit(1)
		return

	var res_spawn_36: Dictionary = garden.handle_bridge_command(JSON.stringify({
		"type": "spawn_plant",
		"payload": {
			"plant_id": "plant_0005",
			"courtyard_x": -140.0,
			"courtyard_y": 120.0,
			"golden_sentence": "Stillness shapes the pine",
			"tint_hex": "#ffd2a6"
		}
	}))
	if not res_spawn_36.get("ok", false) or res_spawn_36.get("plant_id") != "plant_0005" or not res_spawn_36.get("is_video36", false):
		push_error("VERIFY_FAIL: spawn_plant 36F failed: %s" % str(res_spawn_36))
		quit(1)
		return
	if res_spawn_36.get("golden_sentence", "") != "Stillness shapes the pine":
		push_error("VERIFY_FAIL: spawn_plant 36F lost golden_sentence: %s" % str(res_spawn_36))
		quit(1)
		return
	var spawned_36 = garden.active_plants[0]
	if not spawned_36.custom_tint_color.is_equal_approx(Color.from_string("#ffd2a6", Color.WHITE)):
		push_error("VERIFY_FAIL: spawn_plant 36F custom_tint_color mismatch")
		quit(1)
		return

	var picked_36 = garden.pick_plant_at_screen_pos(spawned_36.global_position - Vector2(0.0, 20.0))
	if picked_36 != spawned_36:
		push_error("VERIFY_FAIL: Exact pick_plant_at_screen_pos failed on isolated spawned plant")
		quit(1)
		return

	var res_spawn_static: Dictionary = garden.handle_bridge_command(JSON.stringify({
		"type": "spawn_plant",
		"payload": {
			"plant_id": "plant_0001",
			"courtyard_x": 180.0,
			"courtyard_y": 80.0,
			"golden_sentence": "Quiet moss holds the rain",
			"tint_hex": "#a8e6cf"
		}
	}))
	if not res_spawn_static.get("ok", false) or res_spawn_static.get("plant_id") != "plant_0001" or res_spawn_static.get("is_video36", true):
		push_error("VERIFY_FAIL: spawn_plant static failed: %s" % str(res_spawn_static))
		quit(1)
		return
	if res_spawn_static.get("golden_sentence", "") != "Quiet moss holds the rain":
		push_error("VERIFY_FAIL: spawn_plant static lost golden_sentence")
		quit(1)
		return
	var spawned_static = garden.active_plants[1]
	if not spawned_static.custom_tint_color.is_equal_approx(Color.from_string("#a8e6cf", Color.WHITE)):
		push_error("VERIFY_FAIL: spawn_plant static custom_tint_color mismatch")
		quit(1)
		return

	var res_orbit: Dictionary = garden.handle_bridge_command(JSON.stringify({
		"type": "set_orbit",
		"payload": {"angle_deg": 90.0, "snap": true}
	}))
	if not res_orbit.get("ok", false) or abs(garden.orbit_angle_deg - 90.0) > 0.1:
		push_error("VERIFY_FAIL: set_orbit failed: %s" % str(res_orbit))
		quit(1)
		return

	var res_weather: Dictionary = garden.handle_bridge_command(JSON.stringify({
		"type": "set_weather",
		"payload": {"preset": "night"}
	}))
	if not res_weather.get("ok", false):
		push_error("VERIFY_FAIL: set_weather failed: %s" % str(res_weather))
		quit(1)
		return

	var res_state: Dictionary = garden.handle_bridge_command(JSON.stringify({
		"type": "get_state",
		"payload": {}
	}))
	if not res_state.get("ok", false) or int(res_state.get("plant_count", 0)) != 2 or res_state.get("mode", "") != "regular":
		push_error("VERIFY_FAIL: get_state mismatch: %s" % str(res_state))
		quit(1)
		return
	if res_state.get("plants", []).size() != 2:
		push_error("VERIFY_FAIL: get_state plants array mismatch")
		quit(1)
		return

	print("VERIFY_OK: Regular courtyard 36F integration, collision check, selection, and PsyGardenBridge verified!")
	quit(0)
"""


class TestGodotVideo36MainGarden047(unittest.TestCase):
    def test_static_contracts_and_protected_files(self):
        for path in (GARDEN_PATH, PLANT_PATH):
            content = path.read_text(encoding="utf-8")
            non_ascii = [(idx, ch) for idx, ch in enumerate(content) if ord(ch) >= 128]
            self.assertEqual(non_ascii, [], f"Non-ASCII found in {path.name}: {non_ascii[:10]}")

        garden_src = GARDEN_PATH.read_text(encoding="utf-8")
        self.assertIn('const LAB_ASSET_VER: String = "047"', garden_src)

        demo_html = LIGHT_DEMO_HTML.read_text(encoding="utf-8")
        for marker in (
            "v=047",
            '<dialog id="catalogDrawer" class="zen-panel" popover="manual"',
            '<dialog id="plantInspector" class="zen-panel" popover="manual"',
            "window.PsyGardenBridge",
            "overscroll-behavior: contain",
            "@starting-style",
            "transition-behavior: allow-discrete",
            "🌿 返回 DOM 图鉴手植版 · 383种",
            "🏮 Godot 2.5D 动态光影舱 · 移动鼠标体验手电筒单向量柔影与树冠遮挡",
        ):
            self.assertIn(marker, demo_html, f"Missing marker in light_demo.html: {marker}")

        if (REPO_ROOT / ".git").exists():
            res = subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "src/styles.css",
                    "src/assets/plants/manifest.json",
                    "tools/root_anchor_calibrator.py",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(res.returncode, 0, f"git diff failed: {res.stderr}")
            self.assertEqual(res.stdout.strip(), "", f"Protected files modified: {res.stdout}")

    def test_godot_main_courtyard_36f_and_bridge(self):
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

            verify_path = tmp_godot / "test_verify_main_garden_047.gd"
            verify_path.write_text(VERIFY_GDSCRIPT, encoding="utf-8")

            res_verify = subprocess.run(
                [
                    GODOT_BIN,
                    "--headless",
                    "--path",
                    str(tmp_godot),
                    "--script",
                    "res://test_verify_main_garden_047.gd",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            verify_output = res_verify.stdout + "\n" + res_verify.stderr
            self.assertEqual(res_verify.returncode, 0, f"Godot main garden verify failed:\n{verify_output}")
            self.assertIn("VERIFY_OK:", verify_output, f"Missing VERIFY_OK in output:\n{verify_output}")
            for bad_token in ["SCRIPT ERROR", "Parse Error", "VERIFY_FAIL", "ERROR:"]:
                self.assertNotIn(
                    bad_token,
                    verify_output,
                    f"Unexpected Godot error ({bad_token}):\n{verify_output}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
