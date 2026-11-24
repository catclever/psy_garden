import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
import urllib.error
import urllib.request

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import botanist_studio  # noqa: E402


class _FakeStdout:
    def __init__(self, lines):
        self._iter = iter(lines)

    def __iter__(self):
        return self._iter


class _FakePopen:
    def __init__(self, lines, returncode=0, delay=0.12):
        self.stdout = _FakeStdout(lines)
        self._returncode = returncode
        self._delay = delay

    def wait(self):
        time.sleep(self._delay)
        return self._returncode


class TestBotanistStudioVideo36048(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="psy_garden_studio_v36_")
        self.tools_dir = os.path.join(self.tmpdir, "tools")
        self.candidates_dir = os.path.join(self.tools_dir, "candidates")
        self.runs_dir = os.path.join(self.candidates_dir, "video36_runs")
        self.plants_dir = os.path.join(self.tmpdir, "src", "assets", "plants")
        self.lab_dir = os.path.join(self.tmpdir, "src", "assets", "turntable_lab")
        os.makedirs(self.candidates_dir, exist_ok=True)
        os.makedirs(self.runs_dir, exist_ok=True)
        os.makedirs(self.plants_dir, exist_ok=True)
        os.makedirs(self.lab_dir, exist_ok=True)

        self.manifest_path = os.path.join(self.plants_dir, "manifest.json")
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "total_species": 1,
                    "plants": [
                        {
                            "name": "plant_9999",
                            "display_name": "测试景天",
                            "enabled": True,
                            "true_color": True,
                            "path_prefix": "assets/plants/plant_9999/01",
                            "layer_files": [{"slot": "green", "file": "layer_green.png"}],
                            "ai_analysis": {
                                "plant_type": "flower",
                                "suggested_game_height_px": [80, 120],
                            },
                        }
                    ],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        self.plant_v36_dir = os.path.join(self.lab_dir, "plant_9999_video36")
        os.makedirs(self.plant_v36_dir, exist_ok=True)
        atlas_path = os.path.join(self.plant_v36_dir, "atlas_36.webp")
        Image.new("RGBA", (1320, 1320), (40, 160, 80, 255)).save(atlas_path, "WEBP")

        run_dir = os.path.join(self.runs_dir, "plant_9999")
        os.makedirs(run_dir, exist_ok=True)
        Image.new("RGB", (240, 240), (255, 255, 255)).save(
            os.path.join(run_dir, "raw_preview_36.jpg"), "JPEG"
        )
        with open(os.path.join(run_dir, "prep_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"plant_id": "plant_9999", "display_name": "测试景天", "ref_view_count": 4}, f)
        with open(os.path.join(run_dir, "video_run_summary.json"), "w", encoding="utf-8") as f:
            json.dump({"plant_id": "plant_9999", "mode": "2half", "frames": 36}, f)

        self.meta_path = os.path.join(self.plant_v36_dir, "metadata.json")
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "plant_id": "plant_9999",
                    "display_name": "测试景天",
                    "label_ascii": "SedumTest",
                    "plant_type": "flower",
                    "path_prefix": "assets/turntable_lab/plant_9999_video36",
                    "atlas_file": "atlas_36.webp",
                    "hframes": 6,
                    "vframes": 6,
                    "frame_count": 36,
                    "root_anchor_px": [110.0, 200.0],
                    "crown_h": 182.0,
                    "root_width": 22.0,
                    "webp_bytes": os.path.getsize(atlas_path),
                    "published": False,
                    "pivot_tracking": {
                        "modes": {"woody_shaft": 36},
                        "rx_med_err_px": 1.2,
                        "ry_med_err_px": 1.5,
                        "root_x_iqr": 2.4,
                    },
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        self.index_path = os.path.join(self.lab_dir, "video36_index.json")
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump({"updated_at": 1700000000, "species": []}, f)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_video36_workbench_html_and_endpoints(self):
        fake_jobs = {}

        def fake_popen(cmd, **kwargs):
            return _FakePopen(
                [
                    "[STAGE 1/3] prep completed",
                    "[STAGE 2/3] video completed",
                    "[STAGE 3/3] atlas_36.webp built",
                ],
                returncode=0,
                delay=0.15,
            )

        with (
            mock.patch.object(botanist_studio, "PROJECT_ROOT", self.tmpdir),
            mock.patch.object(botanist_studio, "CANDIDATES_DIR", self.candidates_dir),
            mock.patch.object(botanist_studio, "PLANTS_DIR", self.plants_dir),
            mock.patch.object(botanist_studio, "MANIFEST_JSON_PATH", self.manifest_path),
            mock.patch.object(botanist_studio, "VIDEO36_RUNS_DIR", self.runs_dir),
            mock.patch.object(botanist_studio, "TURNTABLE_LAB_DIR", self.lab_dir),
            mock.patch.object(botanist_studio, "VIDEO36_INDEX_PATH", self.index_path),
            mock.patch.object(botanist_studio, "VIDEO36_JOBS", fake_jobs),
            mock.patch.object(botanist_studio.subprocess, "Popen", side_effect=fake_popen),
        ):
            server = botanist_studio.ThreadingStudioServer(
                ("127.0.0.1", 0), botanist_studio.RequestHandler
            )
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{port}"
            try:
                # 1. GET / HTML workbench markup & CSS/JS hygiene
                with urllib.request.urlopen(f"{base_url}/") as resp:
                    self.assertEqual(resp.status, 200)
                    html = resp.read().decode("utf-8")
                for marker in (
                    '<dialog id="video36Dialog" closedby="any"',
                    "🔄 360° 转盘",
                    "overscroll-behavior: contain",
                    "@starting-style",
                    "transition-behavior: allow-discrete",
                    "modalOpenVideo36Btn",
                ):
                    self.assertIn(marker, html, f"Missing Studio UI marker: {marker}")
                self.assertEqual(
                    html.count("let activeVideo36PlantId = null;"),
                    1,
                    "Expected exactly 1 workbench JS block in Botanist Studio HTML",
                )

                # 2. GET /api/video36/list
                with urllib.request.urlopen(f"{base_url}/api/video36/list") as resp:
                    self.assertEqual(resp.status, 200)
                    v36_list = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(v36_list["published_ids"], [])
                items = {it["plant_id"]: it for it in v36_list["items"]}
                self.assertIn("plant_9999", items)
                item = items["plant_9999"]
                self.assertTrue(item["has_atlas"])
                self.assertTrue(item["has_prep"])
                self.assertTrue(item["has_video"])
                self.assertFalse(item["published"])
                self.assertEqual(
                    item["atlas_meta"]["pivot_tracking"]["rx_med_err_px"], 1.2
                )
                self.assertIn("atlas_36.webp", item["files"])
                self.assertIn("raw_preview_36.jpg", item["files"])

                # 3. GET /api/video36/file/<plant_id>/<filename>
                with urllib.request.urlopen(
                    f"{base_url}/api/video36/file/plant_9999/atlas_36.webp"
                ) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers.get("Content-Type"), "image/webp")
                    self.assertGreater(len(resp.read()), 1000)

                for bad_path in (
                    "/api/video36/file/..%2Fetc/atlas_36.webp",
                    "/api/video36/file/plant_9999/metadata.json",
                ):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(f"{base_url}{bad_path}")
                    self.assertEqual(ctx.exception.code, 400)

                # 4. POST /api/video36/publish -> toggle published=True then False (non-destructive)
                req_pub = urllib.request.Request(
                    f"{base_url}/api/video36/publish",
                    data=json.dumps({"plant_id": "plant_9999", "published": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_pub) as resp:
                    pub_res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(pub_res["status"], "success")
                self.assertTrue(pub_res["published"])
                self.assertEqual(pub_res["index_species_count"], 1)

                with open(self.index_path, "r", encoding="utf-8") as f:
                    idx_after_pub = json.load(f)
                self.assertEqual([s["plant_id"] for s in idx_after_pub["species"]], ["plant_9999"])

                req_unpub = urllib.request.Request(
                    f"{base_url}/api/video36/publish",
                    data=json.dumps({"plant_id": "plant_9999", "published": False}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_unpub) as resp:
                    unpub_res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(unpub_res["status"], "success")
                self.assertFalse(unpub_res["published"])
                self.assertEqual(unpub_res["index_species_count"], 0)
                self.assertTrue(
                    os.path.isfile(os.path.join(self.plant_v36_dir, "atlas_36.webp")),
                    "Unpublish must be non-destructive and keep atlas_36.webp on disk",
                )

                # 5. POST /api/video36/run -> 202 accepted, duplicate -> 409 conflict, poll -> succeeded
                req_run = urllib.request.Request(
                    f"{base_url}/api/video36/run",
                    data=json.dumps(
                        {"plant_id": "plant_9999", "stages": "atlas", "mode": "2half", "key_mode": "auto"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_run) as resp:
                    self.assertEqual(resp.status, 202)
                    run_res = json.loads(resp.read().decode("utf-8"))
                job_id = run_res["job_id"]
                self.assertEqual(run_res["status"], "running")

                # Duplicate job for same plant_id must return 409 while still running
                req_dup = urllib.request.Request(
                    f"{base_url}/api/video36/run",
                    data=json.dumps({"plant_id": "plant_9999", "stages": "atlas", "mode": "2half"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req_dup)
                self.assertEqual(ctx.exception.code, 409)

                # Poll until succeeded
                final_job = None
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    with urllib.request.urlopen(f"{base_url}/api/video36/jobs/{job_id}") as resp:
                        self.assertEqual(resp.status, 200)
                        final_job = json.loads(resp.read().decode("utf-8"))
                    if final_job["status"] != "running":
                        break
                    time.sleep(0.04)

                self.assertIsNotNone(final_job)
                self.assertEqual(final_job["status"], "succeeded")
                self.assertEqual(final_job["returncode"], 0)
                joined_logs = "\n".join(final_job["log_tail"])
                self.assertIn("[STAGE 3/3] atlas_36.webp built", joined_logs)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
