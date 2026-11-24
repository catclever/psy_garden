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

GARDEN_PATH = REPO_ROOT / "godot" / "Garden.gd"
PLANT_PATH = REPO_ROOT / "godot" / "Plant.gd"
LIGHT_DEMO_HTML = REPO_ROOT / "src" / "light_demo.html"
LAB_DIR = REPO_ROOT / "src" / "assets" / "turntable_lab"
GALLERY_INDEX_PATH = LAB_DIR / "video36_index.json"


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

	if garden.manifest_records.size() != 383:
		push_error("VERIFY_FAIL: Expected 383 manifest_records on startup, got %d" % garden.manifest_records.size())
		quit(1)
		return
	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active_plants on startup, got %d" % garden.active_plants.size())
		quit(1)
		return

	var vbox = garden.get_node_or_null("HUD/MarginContainer/VBoxContainer")
	if not vbox:
		push_error("VERIFY_FAIL: Missing HUD VBoxContainer")
		quit(1)
		return
	var btn_video = vbox.get_node_or_null("BtnVideoLab")
	var btn_gal = vbox.get_node_or_null("BtnVideoGallery")
	if not btn_video or not btn_gal:
		push_error("VERIFY_FAIL: Missing BtnVideoLab or BtnVideoGallery in HUD")
		quit(1)
		return
	if abs(btn_gal.custom_minimum_size.y - 24.0) > 0.1:
		push_error("VERIFY_FAIL: Expected BtnVideoGallery height == 24, got %f" % btn_gal.custom_minimum_size.y)
		quit(1)
		return
	if btn_gal.get_index() <= btn_video.get_index() or btn_gal.get_index() >= garden.fleet_status_label.get_index():
		push_error("VERIFY_FAIL: BtnVideoGallery must sit between BtnVideoLab and FleetStatusLabel")
		quit(1)
		return

	garden.enter_video_gallery()
	await process_frame
	await process_frame

	if not garden.video_gallery_active:
		push_error("VERIFY_FAIL: Expected video_gallery_active == true")
		quit(1)
		return
	if garden.active_plants.size() < 4:
		push_error("VERIFY_FAIL: Expected >= 4 gallery plants, got %d" % garden.active_plants.size())
		quit(1)
		return

	var seen_ids := {}
	var seen_positions := []
	for p in garden.active_plants:
		if not p.is_video_atlas:
			push_error("VERIFY_FAIL: Expected all gallery plants to have is_video_atlas == true")
			quit(1)
			return
		if p.sprite_green.hframes != 6 or p.sprite_green.vframes != 6:
			push_error("VERIFY_FAIL: Expected 6x6 sprite_green on gallery plant %s" % p.plant_id)
			quit(1)
			return
		if p.sun_shadow.hframes != 6 or p.sun_shadow.vframes != 6:
			push_error("VERIFY_FAIL: Expected 6x6 sun_shadow on gallery plant %s" % p.plant_id)
			quit(1)
			return
		seen_ids[p.plant_id] = true
		for prev_pos in seen_positions:
			if prev_pos.distance_to(p.courtyard_pos) < 50.0:
				push_error("VERIFY_FAIL: Overlapping gallery courtyard slots for %s" % p.plant_id)
				quit(1)
				return
		seen_positions.append(p.courtyard_pos)

	for req_id in ["plant_0000", "plant_0003", "plant_0005", "plant_0177"]:
		if not seen_ids.has(req_id):
			push_error("VERIFY_FAIL: Missing species %s in gallery" % req_id)
			quit(1)
			return

	var test_angles = [
		[0.0, 0],
		[90.0, 9],
		[180.0, 18],
		[270.0, 27]
	]
	for pair in test_angles:
		var ang_deg: float = pair[0]
		var expected_f: int = pair[1]
		garden._set_orbit_target(ang_deg, true)
		garden._process(0.05)

		var expected_row: float = float(int(floor(float(expected_f) / 6.0)))
		var expected_uv := Vector2(expected_row / 6.0, (expected_row + 1.0) / 6.0)
		for p in garden.active_plants:
			if p.current_video_frame != expected_f:
				push_error("VERIFY_FAIL: %s at orbit=%f expected frame %d, got %d" % [p.plant_id, ang_deg, expected_f, p.current_video_frame])
				quit(1)
				return
			if p.sprite_green.frame != expected_f or p.sun_shadow.frame != expected_f:
				push_error("VERIFY_FAIL: Sprite/shadow frame mismatch on %s at orbit=%f" % [p.plant_id, ang_deg])
				quit(1)
				return
			var g_uv: Vector2 = (p.sprite_green.material as ShaderMaterial).get_shader_parameter("uv_y_range")
			var s_uv: Vector2 = (p.sun_shadow.material as ShaderMaterial).get_shader_parameter("uv_y_range")
			if g_uv.distance_to(expected_uv) > 0.001 or s_uv.distance_to(expected_uv) > 0.001:
				push_error("VERIFY_FAIL: uv_y_range mismatch on %s at frame %d" % [p.plant_id, expected_f])
				quit(1)
				return

	# Mutual exclusion with Video 36F Lab
	garden.enter_video_turntable_lab()
	await process_frame
	await process_frame

	if garden.video_gallery_active or not garden.video_lab_active:
		push_error("VERIFY_FAIL: Mutual exclusion failed entering Video 36F Lab from Gallery")
		quit(1)
		return
	if btn_gal.text != "Video 36F Gallery: OFF":
		push_error("VERIFY_FAIL: Expected BtnVideoGallery OFF after switching to Video Lab, got %s" % btn_gal.text)
		quit(1)
		return
	if garden.active_plants.size() != 4:
		push_error("VERIFY_FAIL: Expected 4 Calathea PK plants in Video Lab, got %d" % garden.active_plants.size())
		quit(1)
		return

	# Return to gallery then exit to random 36-plant garden
	garden.enter_video_gallery()
	await process_frame
	if garden.video_lab_active or not garden.video_gallery_active or btn_video.text != "Video 36F Lab: OFF":
		push_error("VERIFY_FAIL: Failed returning from Video Lab to Gallery")
		quit(1)
		return

	garden.exit_video_gallery(true)
	await process_frame
	if garden.video_gallery_active:
		push_error("VERIFY_FAIL: Expected video_gallery_active == false after exit")
		quit(1)
		return
	if garden.active_plants.size() != 36:
		push_error("VERIFY_FAIL: Expected 36 active_plants after exiting gallery, got %d" % garden.active_plants.size())
		quit(1)
		return

	print("VERIFY_OK: Multi-species Video 36F Gallery verified!")
	quit(0)
"""


class TestGodotVideoGallery045(unittest.TestCase):
    def test_strict_ascii_and_cache_buster_and_styles(self):
        for path in [GARDEN_PATH, PLANT_PATH]:
            content = path.read_text(encoding="utf-8")
            non_ascii = [(idx, ch) for idx, ch in enumerate(content) if ord(ch) >= 128]
            self.assertEqual(non_ascii, [], f"Non-ASCII found in {path.name}: {non_ascii[:10]}")

        garden_src = GARDEN_PATH.read_text(encoding="utf-8")
        self.assertIn('const LAB_ASSET_VER: String = "047"', garden_src)

        demo_html = LIGHT_DEMO_HTML.read_text(encoding="utf-8")
        self.assertIn("v=047", demo_html, "Expected cache buster v=047 in light_demo.html")
        self.assertIn('"047"', demo_html, 'Expected q.set("v", "047") in light_demo.html')

        res = subprocess.run(
            ["git", "diff", "--name-only", "src/styles.css", "src/assets/plants/manifest.json", "tools/root_anchor_calibrator.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, f"git diff failed: {res.stderr}")
        self.assertEqual(res.stdout.strip(), "", f"Protected files modified: {res.stdout}")

    def test_video36_index_and_four_atlases(self):
        self.assertTrue(GALLERY_INDEX_PATH.exists(), f"Missing {GALLERY_INDEX_PATH}")
        idx_data = json.loads(GALLERY_INDEX_PATH.read_text(encoding="utf-8"))
        species = idx_data.get("species", [])
        self.assertGreaterEqual(len(species), 4, f"Expected >= 4 species, got {len(species)}")

        by_id = {sp["plant_id"]: sp for sp in species}
        for req_id in ("plant_0000", "plant_0003", "plant_0005", "plant_0177"):
            self.assertIn(req_id, by_id, f"Missing {req_id} in video36_index.json")
            sp = by_id[req_id]
            atlas_dir = LAB_DIR / f"{req_id}_video36"
            meta_path = atlas_dir / "metadata.json"
            atlas_path = atlas_dir / sp.get("atlas_file", "atlas_36.webp")
            self.assertTrue(meta_path.exists(), f"Missing {meta_path}")
            self.assertTrue(atlas_path.exists(), f"Missing {atlas_path}")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("hframes"), 6)
            self.assertEqual(meta.get("vframes"), 6)
            self.assertEqual(meta.get("frame_count"), 36)
            self.assertGreater(int(sp.get("webp_bytes", 0)), 50000)

            if req_id in ("plant_0003", "plant_0005", "plant_0177"):
                pt = meta.get("pivot_tracking")
                self.assertIsInstance(pt, dict, f"Missing pivot_tracking in {meta_path}")
                if req_id == "plant_0005":
                    self.assertEqual(pt.get("modes", {}).get("woody_shaft", 0), 36)
                    self.assertLess(float(pt.get("rx_med_err_px", 99.0)), 8.0)
                    self.assertLess(float(pt.get("ry_med_err_px", 99.0)), 10.0)
                    anchor_px = meta.get("root_anchor_px", [110.0, 212.0])
                    self.assertLess(float(anchor_px[1]), 212.0)

            with Image.open(atlas_path) as im:
                self.assertEqual(im.size, (1320, 1320))
                self.assertEqual(im.mode, "RGBA")
                if req_id == "plant_0005":
                    alpha_band = im.getchannel("A")
                    for row_idx in range(6):
                        bot_y = (row_idx + 1) * 220 - 1
                        row_strip = list(alpha_band.crop((0, bot_y, 1320, bot_y + 1)).getdata())
                        clipped_px = sum(1 for a in row_strip if a > 8)
                        self.assertEqual(
                            clipped_px,
                            0,
                            f"Found {clipped_px} bottom-clipped pixels on plant_0005 row {row_idx} (y={bot_y})",
                        )

    def test_godot_video_gallery_headless_verify(self):
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

            verify_path = tmp_godot / "test_verify_video_gallery_045.gd"
            verify_path.write_text(VERIFY_GDSCRIPT, encoding="utf-8")

            res_verify = subprocess.run(
                [GODOT_BIN, "--headless", "--path", str(tmp_godot), "--script", "res://test_verify_video_gallery_045.gd"],
                capture_output=True,
                text=True,
                env=env,
                timeout=45,
            )
            verify_output = res_verify.stdout + "\n" + res_verify.stderr
            self.assertEqual(res_verify.returncode, 0, f"Godot gallery verify failed:\n{verify_output}")
            self.assertIn("VERIFY_OK:", verify_output, f"Missing VERIFY_OK in output:\n{verify_output}")
            for bad_token in ["SCRIPT ERROR", "Parse Error", "VERIFY_FAIL", "ERROR:"]:
                self.assertNotIn(bad_token, verify_output, f"Unexpected Godot verify error ({bad_token}):\n{verify_output}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
