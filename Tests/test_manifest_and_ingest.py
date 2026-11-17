import importlib.util
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.request

from PIL import Image, ImageDraw

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


def _load_module(module_name: str, draft_filename: str, post_merge_filename: str):
    draft_path = os.path.join(TOOLS_DIR, draft_filename)
    post_path = os.path.join(TOOLS_DIR, post_merge_filename)
    target_path = draft_path if os.path.exists(draft_path) else post_path
    spec = importlib.util.spec_from_file_location(module_name, target_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load draft modules and alias post-merge names so cross-module imports resolve cleanly pre-merge
generate_plant_manifest = _load_module(
    "generate_plant_manifest",
    "generate_plant_manifest_task_batch_ingest_all_candidates_and_manifest_028.py",
    "generate_plant_manifest.py",
)
build_plant_asset = _load_module(
    "build_plant_asset",
    "build_plant_asset_task_batch_ingest_all_candidates_and_manifest_028.py",
    "build_plant_asset.py",
)
batch_ingest = _load_module(
    "batch_ingest_all_candidates",
    "batch_ingest_all_candidates_task_batch_ingest_all_candidates_and_manifest_028.py",
    "batch_ingest_all_candidates.py",
)
botanist_studio = _load_module(
    "botanist_studio_draft",
    "botanist_studio_task_batch_ingest_all_candidates_and_manifest_028.py",
    "botanist_studio.py",
)


def _create_dummy_plant_image(path: str, size=(120, 160)):
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Green stem at the bottom
    cx = size[0] // 2
    draw.rectangle([cx - 8, size[1] // 2, cx + 8, size[1] - 10], fill=(30, 160, 40))
    # Red flower cap at the top
    draw.ellipse([cx - 30, 15, cx + 30, size[1] // 2 + 10], fill=(210, 40, 40))
    # Blue accent in center
    draw.ellipse([cx - 10, 35, cx + 10, 55], fill=(40, 80, 220))
    img.save(path)


def _create_rgba_png(path: str, size=(80, 100), stem_width=14):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = size[0] // 2
    half_w = stem_width // 2
    draw.rectangle([cx - half_w, 20, cx - half_w + stem_width - 1, size[1] - 1], fill=(30, 180, 50, 255))
    img.save(path)


class TestManifestAndIngest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="psy_garden_test_")
        self.plants_dir = os.path.join(self.tmpdir, "src", "assets", "plants")
        self.candidates_dir = os.path.join(self.tmpdir, "tools", "candidates")
        os.makedirs(self.plants_dir, exist_ok=True)
        os.makedirs(self.candidates_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generate_manifest_schema_slots_dedup_and_fallbacks(self):
        # 1. plant_0000 with two variants: '01' and '1791079136' -> '01' must win
        v01 = os.path.join(self.plants_dir, "plant_0000", "01")
        vlong = os.path.join(self.plants_dir, "plant_0000", "1791079136")
        os.makedirs(v01)
        os.makedirs(vlong)
        _create_rgba_png(os.path.join(v01, "layer_red.png"), size=(100, 120))
        _create_rgba_png(os.path.join(v01, "layer_green.png"), size=(100, 120))
        _create_rgba_png(os.path.join(v01, "layer_blue.png"), size=(100, 120))
        with open(os.path.join(v01, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "name": "plant_0000",
                    "display_name": "孔雀竹芋",
                    "variant_id": "01",
                    "width": 100,
                    "height": 120,
                    "root_width": 20,
                    "root_ratio": 0.2,
                    "ground_ellipse": [20, 11],
                    "true_color": True,
                    "ai_analysis": {"status": "success", "plant_type": "groundcover"},
                },
                f,
            )

        _create_rgba_png(os.path.join(vlong, "layer_green.png"), size=(100, 120))
        with open(os.path.join(vlong, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "plant_0000", "display_name": "孔雀竹芋", "true_color": True}, f)

        # 2. bamboo/v_rgb with multi_* filenames + hidden dot file
        vbamboo = os.path.join(self.plants_dir, "bamboo", "v_rgb")
        os.makedirs(vbamboo)
        _create_rgba_png(os.path.join(vbamboo, "plant_bamboo_multi_blue.png"), size=(60, 80))
        _create_rgba_png(os.path.join(vbamboo, "plant_bamboo_multi_red.png"), size=(60, 80))
        _create_rgba_png(os.path.join(vbamboo, "plant_bamboo_multi_green.png"), size=(60, 80))
        _create_rgba_png(os.path.join(vbamboo, ".hidden_layer_green.png"), size=(60, 80))
        with open(os.path.join(vbamboo, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "bamboo", "display_name": "竹子", "true_color": True}, f)

        # 3. lily with v_uncut (skip), v_semantic (skip), and v_rgb (missing metadata dimensions -> PIL fallback)
        os.makedirs(os.path.join(self.plants_dir, "lily", "v_uncut"))
        _create_rgba_png(os.path.join(self.plants_dir, "lily", "v_uncut", "plant_lily.png"))
        os.makedirs(os.path.join(self.plants_dir, "lily", "v_semantic"))
        _create_rgba_png(os.path.join(self.plants_dir, "lily", "v_semantic", "plant_lily_multi_stem.png"))

        vlily = os.path.join(self.plants_dir, "lily", "v_rgb")
        os.makedirs(vlily)
        _create_rgba_png(os.path.join(vlily, "plant_lily_multi_stem.png"), size=(100, 140))
        _create_rgba_png(os.path.join(vlily, "plant_lily_multi_flower.png"), size=(100, 140))
        _create_rgba_png(os.path.join(vlily, "plant_lily_multi_accent.png"), size=(100, 140))

        manifest = generate_plant_manifest.generate_manifest(plants_dir=self.plants_dir)
        manifest_path = os.path.join(self.plants_dir, "manifest.json")
        self.assertTrue(os.path.isfile(manifest_path))
        self.assertEqual(manifest["total_species"], 3)
        self.assertEqual([p["name"] for p in manifest["plants"]], ["bamboo", "lily", "plant_0000"])

        bamboo_entry, lily_entry, p0_entry = manifest["plants"]
        self.assertEqual(p0_entry["id"], "plant_0000__01")
        self.assertEqual(p0_entry["variant_id"], "01")
        self.assertEqual(p0_entry["path_prefix"], "assets/plants/plant_0000/01")

        self.assertEqual([lf["slot"] for lf in bamboo_entry["layer_files"]], ["green", "red", "blue"])
        self.assertTrue(all(not lf["file"].startswith(".") for lf in bamboo_entry["layer_files"]))

        self.assertEqual(lily_entry["variant_id"], "v_rgb")
        self.assertEqual(lily_entry["width"], 100)
        self.assertEqual(lily_entry["height"], 140)
        self.assertEqual(lily_entry["root_width"], 15)  # max(10, int(100 * 0.15))
        self.assertEqual(lily_entry["root_ratio"], 0.15)
        self.assertEqual(lily_entry["ground_ellipse"], [15, 8])
        self.assertEqual([lf["slot"] for lf in lily_entry["layer_files"]], ["green", "red", "blue"])

    def test_build_asset_inject_css_false_and_footprint(self):
        styles_path = os.path.join(self.tmpdir, "src", "styles.css")
        os.makedirs(os.path.dirname(styles_path), exist_ok=True)
        with open(styles_path, "w", encoding="utf-8") as f:
            f.write("/* SENTINEL STYLES */\n")

        src_img = os.path.join(self.candidates_dir, "candidate_plant_0005_羅漢松.jpg")
        _create_dummy_plant_image(src_img, size=(120, 160))
        out_dir = os.path.join(self.plants_dir, "plant_0005", "01")

        ok = build_plant_asset.build_asset(
            input_path=src_img,
            plant_name="plant_0005",
            variant_id="01",
            output_dir=out_dir,
            project_root=self.tmpdir,
            display_name="羅漢松",
            inject_css=False,
        )
        self.assertTrue(ok)
        with open(styles_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "/* SENTINEL STYLES */\n")

        meta_path = os.path.join(out_dir, "metadata.json")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["display_name"], "羅漢松")
        self.assertTrue(meta["true_color"])
        self.assertGreater(meta["root_width"], 0)
        self.assertAlmostEqual(meta["root_ratio"], round(meta["root_width"] / meta["width"], 3))
        self.assertEqual(
            meta["ground_ellipse"],
            [meta["root_width"], max(4, int(round(meta["root_width"] * 0.55)))],
        )

    def test_batch_ingest_dedup_symlinks_placeholders_and_skip_existing(self):
        # 1. Broken symlink
        os.symlink("missing_target.jpg", os.path.join(self.candidates_dir, "candidate_plant_0164_灰幹蘇鐵.jpg"))

        # 2. Wikipedia placeholders + stray User:Hung Ting directory
        placeholder_real = os.path.join(self.candidates_dir, "candidate_plant_plant_0020_User_Hung_Ting_條目建立中-01.jpg")
        _create_dummy_plant_image(placeholder_real)
        os.symlink(
            "candidate_plant_plant_0020_User_Hung_Ting_條目建立中-01.jpg",
            os.path.join(self.candidates_dir, "candidate_plant_0020_User_Hung_Ting_條目建立中-01.jpg"),
        )
        os.makedirs(os.path.join(self.candidates_dir, "candidate_plant_0113_User:Hung Ting"))

        # 3. Valid candidate pair (regular file + relative symlink)
        real_0002 = os.path.join(self.candidates_dir, "candidate_plant_plant_0002_軟枝黃蟬.jpg")
        _create_dummy_plant_image(real_0002)
        os.symlink(
            "candidate_plant_plant_0002_軟枝黃蟬.jpg",
            os.path.join(self.candidates_dir, "candidate_plant_0002_軟枝黃蟬.jpg"),
        )

        # 4. zen_bamboo_<slug>_<ts>.jpg
        zen_file = os.path.join(self.candidates_dir, "zen_bamboo_cluster_green_1790953794720.jpg")
        _create_dummy_plant_image(zen_file)

        # 5. Existing true_color=True asset (plant_0009/01) missing footprint & AI
        existing_dir = os.path.join(self.plants_dir, "plant_0009", "01")
        os.makedirs(existing_dir)
        _create_rgba_png(os.path.join(existing_dir, "layer_green.png"), size=(80, 100), stem_width=14)
        _create_rgba_png(os.path.join(existing_dir, "layer_red.png"), size=(80, 100), stem_width=14)
        with open(os.path.join(existing_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "name": "plant_0009",
                    "display_name": "酒瓶兰",
                    "variant_id": "01",
                    "true_color": True,
                    "status": "ai_analyzing",
                    "ai_analysis": None,
                },
                f,
            )
        _create_dummy_plant_image(os.path.join(self.candidates_dir, "candidate_plant_0009_酒瓶兰.jpg"))

        # 6. Confirmed registries
        conf_cand_file = os.path.join(self.tmpdir, "tools", "confirmed_candidates.json")
        conf_plants_file = os.path.join(self.tmpdir, "tools", "confirmed_plants.json")
        conf_payload = {
            "candidate_plant_0002_軟枝黃蟬.jpg": {"filename": "candidate_plant_0002_軟枝黃蟬.jpg"},
            "unrelated_kept.jpg": {"filename": "unrelated_kept.jpg"},
        }
        for cf in (conf_cand_file, conf_plants_file):
            with open(cf, "w", encoding="utf-8") as f:
                json.dump(conf_payload, f, ensure_ascii=False)

        call_counts = {}

        def fake_ai(plant_name, display_name=None):
            call_counts[plant_name] = call_counts.get(plant_name, 0) + 1
            # Trigger transient error once on plant_0002 to verify retry-once behavior
            if plant_name == "plant_0002" and call_counts[plant_name] == 1:
                return {"status": "failed", "brief": "transient timeout"}
            return {
                "status": "success",
                "zh_name": display_name or plant_name,
                "scientific_name": f"Testus {plant_name}",
                "botanical_height_m": [0.5, 1.5],
                "plant_type": "shrub",
                "growth_habit": "bush",
                "suggested_game_height_px": [80, 140],
                "brief": "测试植物",
            }

        with mock.patch.object(batch_ingest, "analyze_plant_botany", side_effect=fake_ai):
            summary = batch_ingest.run_batch_ingest(
                candidates_dir=self.candidates_dir,
                plants_dir=self.plants_dir,
                project_root=self.tmpdir,
                confirmed_candidates_file=conf_cand_file,
                confirmed_plants_file=conf_plants_file,
                slice_workers=2,
                ai_workers=2,
                dry_run=False,
                force_reslice=False,
            )

        self.assertEqual(summary["broken_symlinks_removed"], 1)
        self.assertEqual(summary["placeholders_moved"], 3)
        self.assertEqual(summary["unique_species"], 3)
        self.assertEqual(summary["skipped_existing"], 1)
        self.assertEqual(summary["sliced_ok"], 2)
        self.assertEqual(summary["sliced_failed"], 0)
        self.assertEqual(summary["ai_ok"], 3)
        self.assertEqual(summary["ai_failed"], 0)
        self.assertEqual(summary["manifest_total_species"], 3)

        # Retry happened for plant_0002
        self.assertEqual(call_counts["plant_0002"], 2)

        # No broken symlinks remain anywhere in candidates_dir or processed/
        for root_dir in (self.candidates_dir, os.path.join(self.candidates_dir, "processed")):
            for fname in os.listdir(root_dir):
                fpath = os.path.join(root_dir, fname)
                if os.path.islink(fpath):
                    self.assertTrue(os.path.exists(fpath), f"Broken symlink found: {fpath}")

        # Real files archived; relative symlink unlinked
        processed_files = set(os.listdir(os.path.join(self.candidates_dir, "processed")))
        self.assertIn("candidate_plant_plant_0002_軟枝黃蟬.jpg", processed_files)
        self.assertIn("zen_bamboo_cluster_green_1790953794720.jpg", processed_files)
        self.assertIn("candidate_plant_0009_酒瓶兰.jpg", processed_files)
        self.assertNotIn("candidate_plant_0002_軟枝黃蟬.jpg", processed_files)

        # Existing plant_0009 got footprint & AI backfilled without reslicing
        with open(os.path.join(existing_dir, "metadata.json"), "r", encoding="utf-8") as f:
            m9 = json.load(f)
        self.assertEqual(m9["root_width"], 14)
        self.assertEqual(m9["status"], "ready")
        self.assertEqual(m9["ai_analysis"]["status"], "success")

        # zen_bamboo display_name
        with open(os.path.join(self.plants_dir, "zen_bamboo_cluster_green", "01", "metadata.json"), "r", encoding="utf-8") as f:
            mzen = json.load(f)
        self.assertEqual(mzen["display_name"], "禅意翠竹(cluster_green)")

        # Confirmed registries cleaned
        for cf in (conf_cand_file, conf_plants_file):
            with open(cf, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            self.assertNotIn("candidate_plant_0002_軟枝黃蟬.jpg", cdata)
            self.assertIn("unrelated_kept.jpg", cdata)

        # Manifest generated
        with open(os.path.join(self.plants_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["total_species"], 3)

    def test_batch_ingest_dry_run_makes_no_changes(self):
        broken_link = os.path.join(self.candidates_dir, "candidate_plant_0164_灰幹蘇鐵.jpg")
        os.symlink("missing_target.jpg", broken_link)
        cand = os.path.join(self.candidates_dir, "candidate_plant_0002_軟枝黃蟬.jpg")
        _create_dummy_plant_image(cand)

        summary = batch_ingest.run_batch_ingest(
            candidates_dir=self.candidates_dir,
            plants_dir=self.plants_dir,
            project_root=self.tmpdir,
            dry_run=True,
        )
        self.assertEqual(summary["broken_symlinks_removed"], 1)
        self.assertEqual(summary["sliced_ok"], 1)
        self.assertTrue(os.path.islink(broken_link))
        self.assertTrue(os.path.exists(cand))
        self.assertFalse(os.path.exists(os.path.join(self.plants_dir, "manifest.json")))

    def test_botanist_studio_candidates_filters_broken_symlinks_and_refreshes_manifest(self):
        valid_cand = os.path.join(self.candidates_dir, "candidate_plant_0002_軟枝黃蟬.jpg")
        _create_dummy_plant_image(valid_cand)
        os.symlink("missing.jpg", os.path.join(self.candidates_dir, "candidate_plant_0164_灰幹蘇鐵.jpg"))

        vdir = os.path.join(self.plants_dir, "plant_0099", "01")
        os.makedirs(vdir)
        _create_rgba_png(os.path.join(vdir, "layer_green.png"))
        with open(os.path.join(vdir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "plant_0099", "display_name": "测试植物", "true_color": True}, f)

        with (
            mock.patch.object(botanist_studio, "CANDIDATES_DIR", self.candidates_dir),
            mock.patch.object(botanist_studio, "PLANTS_DIR", self.plants_dir),
            mock.patch.object(botanist_studio, "PROJECT_ROOT", self.tmpdir),
        ):
            socketserver.TCPServer.allow_reuse_address = True
            server = socketserver.TCPServer(("127.0.0.1", 0), botanist_studio.RequestHandler)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/candidates") as resp:
                    cands = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(cands, ["candidate_plant_0002_軟枝黃蟬.jpg"])

                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/remove_asset",
                    data=json.dumps({"plant_name": "plant_0099", "variant_id": "01"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(res["status"], "success")
                manifest_path = os.path.join(self.plants_dir, "manifest.json")
                self.assertTrue(os.path.isfile(manifest_path))
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                self.assertEqual(manifest["total_species"], 0)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
