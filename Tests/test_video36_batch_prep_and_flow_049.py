import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
import urllib.error
import urllib.request

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import batch_video36_prep as bvp  # noqa: E402
import botanist_studio  # noqa: E402
import build_video_turntable_atlas as bvta  # noqa: E402
import plant_video36_pipeline as pvp  # noqa: E402


def _make_plant_png(path: Path, size=(256, 256), width=80, color=(40, 150, 70, 255)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = size[0] // 2
    draw.rectangle([cx - width // 2, 24, cx + width // 2, size[1] - 16], fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _make_16x9_white_plant(cx_offset=0, box_w=120, add_corner_watermark=False):
    arr = np.full((360, 640, 3), 255, dtype=np.uint8)
    cx = 320 + cx_offset
    arr[40:330, cx - box_w // 2 : cx + box_w // 2] = (35, 145, 65)
    if add_corner_watermark:
        arr[320:355, 560:635] = (40, 40, 40)
    return arr


class TestVideo36BatchPrepAndFlow049(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="psy_garden_v36_batch_049_")
        self.tmp_path = Path(self.tmpdir)
        self.plants_dir = self.tmp_path / "src" / "assets" / "plants"
        self.lab_dir = self.tmp_path / "src" / "assets" / "turntable_lab"
        self.runs_dir = self.tmp_path / "tools" / "candidates" / "video36_runs"
        self.export_dir = self.tmp_path / "tools" / "candidates" / "flow_export"
        self.processed_dir = self.tmp_path / "tools" / "candidates" / "processed"
        for d in (self.plants_dir, self.lab_dir, self.runs_dir, self.export_dir, self.processed_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.manifest_path = self.plants_dir / "manifest.json"
        _make_plant_png(self.plants_dir / "plant_8001" / "01" / "layer_green.png", width=84)
        _make_plant_png(self.plants_dir / "plant_8002" / "01" / "layer_green.png", width=84)
        self.manifest_path.write_text(
            json.dumps(
                {
                    "total_species": 2,
                    "plants": [
                        {
                            "name": "plant_8001",
                            "display_name": "测试银杏",
                            "enabled": True,
                            "true_color": True,
                            "width": 256,
                            "height": 256,
                            "root_width": 24,
                            "path_prefix": "assets/plants/plant_8001/01",
                            "layer_files": [{"slot": "green", "file": "layer_green.png"}],
                            "ai_analysis": {
                                "scientific_name": "Ginkgo biloba",
                                "plant_type": "tree",
                                "suggested_game_height_px": [180, 210],
                            },
                        },
                        {
                            "name": "plant_8002",
                            "display_name": "测试含羞草",
                            "enabled": True,
                            "true_color": True,
                            "width": 256,
                            "height": 256,
                            "root_width": 28,
                            "path_prefix": "assets/plants/plant_8002/01",
                            "layer_files": [{"slot": "green", "file": "layer_green.png"}],
                            "ai_analysis": {
                                "scientific_name": "Mimosa pudica",
                                "plant_type": "groundcover",
                                "suggested_game_height_px": [50, 75],
                            },
                        },
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mask_16x9_corner_watermark(self):
        frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        frame[120:620, 500:780] = (30, 150, 60)  # Centered plant
        frame[645:710, 1110:1270] = (20, 20, 20)  # Synthetic bottom-right Veo watermark
        cleaned = bvta._mask_16x9_corner_watermark(frame)
        self.assertTrue(np.all(cleaned[645:710, 1110:1270] == 255))
        self.assertTrue(np.all(cleaned[120:620, 500:780] == (30, 150, 60)))

        sq = np.full((720, 720, 3), 255, dtype=np.uint8)
        sq[645:710, 630:710] = (20, 20, 20)
        sq_out = bvta._mask_16x9_corner_watermark(sq)
        self.assertTrue(np.all(sq_out[645:710, 630:710] == (20, 20, 20)))

    def test_single360_atlas_draft_and_watermark_clean(self):
        from types import SimpleNamespace

        run_dir = self.runs_dir / "plant_8001"
        run_dir.mkdir(parents=True, exist_ok=True)
        single_mp4 = run_dir / "single_360.mp4"
        single_mp4.write_bytes(b"fake_mp4_header")

        frames = np.stack(
            [
                _make_16x9_white_plant(box_w=100 + (i % 5) * 6, add_corner_watermark=True)
                for i in range(18)
            ],
            axis=0,
        )

        prep_meta = {
            "plant_id": "plant_8001",
            "mode": "2half",
            "display_name": "测试银杏",
            "label_ascii": "Ginkgo",
            "scientific_name": "Ginkgo biloba",
            "plant_type": "tree",
            "resolved_key_mode": "pockets",
            "root_width": 24.0,
            "gallery_height_px": 195.0,
        }
        args = argparse.Namespace(
            plant_id="plant_8001",
            mode="single360",
            key_mode="auto",
            draft=True,
            reverse_first_half=False,
            reverse_second_half=False,
        )

        fake_iio_v3 = SimpleNamespace(imread=mock.MagicMock(return_value=frames))
        fake_iio = SimpleNamespace(v3=fake_iio_v3)

        with (
            mock.patch.dict(sys.modules, {"imageio": fake_iio, "imageio.v3": fake_iio_v3}),
            mock.patch.object(pvp, "LAB_ROOT", self.lab_dir),
            mock.patch.object(pvp, "MANIFEST_PATH", self.manifest_path),
        ):
            meta = pvp.run_atlas_stage(args, prep_meta, run_dir)

        self.assertFalse(meta["published"])
        self.assertFalse(meta["stitched"])
        atlas_webp = self.lab_dir / "plant_8001_video36" / "atlas_36.webp"
        self.assertTrue(atlas_webp.is_file())
        with Image.open(atlas_webp) as im:
            self.assertEqual(im.size, (1320, 1320))
            self.assertEqual(im.mode, "RGBA")

        idx_data = json.loads((self.lab_dir / "video36_index.json").read_text(encoding="utf-8"))
        self.assertEqual(idx_data["species"], [])

    def test_batch_prep_tier1_list_keep_best_retry_and_flow_bundle(self):
        tier1_ids = bvp.parse_list_file(TOOLS_DIR / "video36_tier1_batch.txt")
        self.assertEqual(len(tier1_ids), 16)
        self.assertEqual(len(set(tier1_ids)), 16)

        calls = {"plant_8001": 0, "plant_8002": 0}

        def fake_synthesize_rear(front_sq_path: Path, out_raw_path: Path, species_desc: str, model: str, timeout: int):
            pid = out_raw_path.parent.name
            calls[pid] = calls.get(pid, 0) + 1
            if pid == "plant_8001":
                if calls[pid] == 1:
                    # Low-IoU initial rear silhouette (~0.25) -> triggers retry
                    _make_plant_png(out_raw_path, size=(256, 256), width=26, color=(35, 130, 55, 255))
                else:
                    # Retry succeeds with matched silhouette (IoU > 0.90)
                    _make_plant_png(out_raw_path, size=(256, 256), width=84, color=(35, 130, 55, 255))
            else:
                if calls[pid] == 1:
                    # Attempt 1 moderate mismatch (~0.55)
                    _make_plant_png(out_raw_path, size=(256, 256), width=48, color=(35, 130, 55, 255))
                else:
                    # Retry degrades (~0.18) -> must keep attempt 1
                    _make_plant_png(out_raw_path, size=(256, 256), width=16, color=(35, 130, 55, 255))

        args = bvp.build_arg_parser().parse_args(
            [
                "--plant-ids",
                "plant_8001,plant_8002",
                "--workers",
                "2",
                "--min-iou",
                "0.78",
                "--max-retries",
                "1",
                "--no-skip-existing",
                "--export-flow-dir",
                str(self.export_dir),
            ]
        )

        with (
            mock.patch.object(pvp, "REPO_ROOT", self.tmp_path),
            mock.patch.object(pvp, "MANIFEST_PATH", self.manifest_path),
            mock.patch.object(pvp, "RUNS_ROOT", self.runs_dir),
            mock.patch.object(pvp, "LAB_ROOT", self.lab_dir),
            mock.patch.object(pvp, "CANDIDATES_PROCESSED_DIR", self.processed_dir),
            mock.patch.object(pvp, "_ensure_api_key_silently", return_value=None),
            mock.patch.object(pvp, "_synthesize_rear_image", side_effect=fake_synthesize_rear),
        ):
            summary = bvp.run_batch(args)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["pass"], 1)
        self.assertEqual(summary["review"], 1)
        by_id = {r["plant_id"]: r for r in summary["results"]}
        self.assertGreaterEqual(by_id["plant_8001"]["mirror_silhouette_iou"], 0.85)
        self.assertEqual(by_id["plant_8001"]["qc_status"], "PASS")
        self.assertGreater(by_id["plant_8002"]["mirror_silhouette_iou"], 0.45)
        self.assertEqual(by_id["plant_8002"]["qc_status"], "REVIEW")

        bundle_8001 = Path(by_id["plant_8001"]["flow_bundle"])
        for req in ("000_front.png", "180_rear.png", "check_000_vs_180.jpg", "prompts.txt"):
            self.assertTrue((bundle_8001 / req).is_file(), f"Missing {req} in Flow bundle")

        self.assertTrue((self.runs_dir / "prep_batch_report.json").is_file())
        self.assertTrue((self.runs_dir / "prep_batch_report.md").is_file())
        self.assertTrue((self.runs_dir / "prep_batch_contact_sheet.jpg").is_file())

    def test_studio_flow_prompts_and_upload_mp4(self):
        run_dir = self.runs_dir / "plant_8001"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "prep_meta.json").write_text(
            json.dumps(
                {
                    "plant_id": "plant_8001",
                    "display_name": "测试银杏",
                    "label_ascii": "Ginkgo",
                    "scientific_name": "Ginkgo biloba",
                    "plant_type": "tree",
                    "mirror_silhouette_iou": 0.91,
                    "resolved_key_mode": "pockets",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        index_path = self.lab_dir / "video36_index.json"
        index_path.write_text(json.dumps({"updated_at": 1700000000, "species": []}), encoding="utf-8")

        fake_jobs = {}
        with (
            mock.patch.object(botanist_studio, "PROJECT_ROOT", self.tmpdir),
            mock.patch.object(botanist_studio, "PLANTS_DIR", str(self.plants_dir)),
            mock.patch.object(botanist_studio, "MANIFEST_JSON_PATH", str(self.manifest_path)),
            mock.patch.object(botanist_studio, "VIDEO36_RUNS_DIR", str(self.runs_dir)),
            mock.patch.object(botanist_studio, "TURNTABLE_LAB_DIR", str(self.lab_dir)),
            mock.patch.object(botanist_studio, "VIDEO36_INDEX_PATH", str(index_path)),
            mock.patch.object(botanist_studio, "VIDEO36_JOBS", fake_jobs),
        ):
            server = botanist_studio.ThreadingStudioServer(
                ("127.0.0.1", 0), botanist_studio.RequestHandler
            )
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{port}"
            try:
                with urllib.request.urlopen(f"{base_url}/") as resp:
                    html = resp.read().decode("utf-8")
                for marker in ("v36AutoAtlasChk", "copyFlowPrompt", "uploadVideo36Mp4", "single360"):
                    self.assertIn(marker, html)

                # Upload single_360 MP4
                req_up = urllib.request.Request(
                    f"{base_url}/api/video36/upload_mp4?plant_id=plant_8001&slot=single_360&auto_atlas=0",
                    data=b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64,
                    headers={"Content-Type": "video/mp4"},
                    method="POST",
                )
                with urllib.request.urlopen(req_up) as resp:
                    self.assertEqual(resp.status, 200)
                    up_res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(up_res["status"], "success")
                self.assertTrue(up_res["ready_for_atlas"])
                self.assertTrue((run_dir / "single_360.mp4").is_file())

                # Invalid slot -> 400
                req_bad = urllib.request.Request(
                    f"{base_url}/api/video36/upload_mp4?plant_id=plant_8001&slot=invalid_slot",
                    data=b"1234",
                    headers={"Content-Type": "video/mp4"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req_bad)
                self.assertEqual(ctx.exception.code, 400)

                # List endpoint exposes flow_prompts & mp4_slots
                with urllib.request.urlopen(f"{base_url}/api/video36/list") as resp:
                    v36_list = json.loads(resp.read().decode("utf-8"))
                by_id = {it["plant_id"]: it for it in v36_list["items"]}
                item = by_id["plant_8001"]
                self.assertTrue(item["mp4_slots"]["single_360"])
                self.assertFalse(item["mp4_slots"]["half_a"])
                self.assertIn("half_a", item["flow_prompts"])
                self.assertIn("single_360", item["flow_prompts"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
