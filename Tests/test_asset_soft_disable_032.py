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
import urllib.error
import urllib.request

from PIL import Image, ImageDraw

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

TASK_SUFFIX = "_task_asset_soft_disable_toggle_and_hide_legacy_plants_032"


def _load_module(module_name: str, draft_filename: str, post_merge_filename: str):
    draft_path = os.path.join(TOOLS_DIR, draft_filename)
    post_path = os.path.join(TOOLS_DIR, post_merge_filename)
    target_path = draft_path if os.path.exists(draft_path) else post_path
    spec = importlib.util.spec_from_file_location(module_name, target_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


generate_plant_manifest = _load_module(
    "generate_plant_manifest",
    f"generate_plant_manifest{TASK_SUFFIX}.py",
    "generate_plant_manifest.py",
)
botanist_studio = _load_module(
    "botanist_studio_soft_disable",
    f"botanist_studio{TASK_SUFFIX}.py",
    "botanist_studio.py",
)
set_legacy_plants_disabled = _load_module(
    "set_legacy_plants_disabled",
    f"set_legacy_plants_disabled{TASK_SUFFIX}.py",
    "set_legacy_plants_disabled.py",
)


def _create_rgba_png(path: str, size=(64, 80)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = size[0] // 2
    draw.rectangle([cx - 6, 16, cx + 6, size[1] - 1], fill=(30, 180, 50, 255))
    img.save(path)


class TestAssetSoftDisable(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="psy_garden_soft_disable_")
        self.plants_dir = os.path.join(self.tmpdir, "src", "assets", "plants")
        self.candidates_dir = os.path.join(self.tmpdir, "tools", "candidates")
        os.makedirs(self.plants_dir, exist_ok=True)
        os.makedirs(self.candidates_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_variant(self, plant_name: str, variant_id: str, metadata: dict | None = None):
        vdir = os.path.join(self.plants_dir, plant_name, variant_id)
        os.makedirs(vdir, exist_ok=True)
        _create_rgba_png(os.path.join(vdir, "layer_green.png"))
        _create_rgba_png(os.path.join(vdir, "layer_red.png"))
        if metadata is not None:
            with open(os.path.join(vdir, "metadata.json"), "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        return vdir

    def test_generate_manifest_excludes_disabled_variants(self):
        # Species 1: enabled explicitly True
        self._make_variant(
            "plant_0001",
            "01",
            {"name": "plant_0001", "display_name": "白鹤芋", "true_color": True, "enabled": True},
        )
        # Species 2: enabled omitted (defaults to enabled)
        self._make_variant(
            "plant_0002",
            "01",
            {"name": "plant_0002", "display_name": "龟背竹", "true_color": True},
        )
        # Species 3: disabled only variant -> excluded from manifest
        self._make_variant(
            "plant_0003",
            "01",
            {"name": "plant_0003", "display_name": "测试草", "true_color": True, "enabled": False},
        )
        # Species 4: disabled '01' (would normally outrank) + enabled '02' -> '02' kept
        self._make_variant(
            "plant_0004",
            "01",
            {"name": "plant_0004", "display_name": "天堂鸟", "true_color": True, "enabled": False},
        )
        self._make_variant(
            "plant_0004",
            "02",
            {"name": "plant_0004", "display_name": "天堂鸟", "true_color": False, "enabled": True},
        )

        manifest = generate_plant_manifest.generate_manifest(plants_dir=self.plants_dir)
        self.assertEqual(manifest["total_species"], 3)
        by_name = {item["name"]: item for item in manifest["plants"]}
        self.assertIn("plant_0001", by_name)
        self.assertIn("plant_0002", by_name)
        self.assertNotIn("plant_0003", by_name)
        self.assertIn("plant_0004", by_name)
        self.assertEqual(by_name["plant_0004"]["variant_id"], "02")

    def test_studio_api_set_assets_enabled_and_get_assets(self):
        v1 = self._make_variant(
            "plant_0010",
            "01",
            {"name": "plant_0010", "display_name": "虎尾兰", "true_color": True},
        )
        # Legacy flat plant directory (no variant subdir -> variant_id == 'default')
        flat_dir = os.path.join(self.plants_dir, "legacy_flat")
        os.makedirs(flat_dir, exist_ok=True)
        _create_rgba_png(os.path.join(flat_dir, "layer_green.png"))
        with open(os.path.join(flat_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "legacy_flat", "display_name": "旧版植物"}, f)

        with (
            mock.patch.object(botanist_studio, "CANDIDATES_DIR", self.candidates_dir),
            mock.patch.object(botanist_studio, "PLANTS_DIR", self.plants_dir),
            mock.patch.object(botanist_studio, "PROJECT_ROOT", self.tmpdir),
        ):
            socketserver.TCPServer.allow_reuse_address = True
            server = socketserver.TCPServer(("127.0.0.1", 0), botanist_studio.RequestHandler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{port}"

                # 1. Initial GET /api/assets returns enabled=True for both
                with urllib.request.urlopen(f"{base_url}/api/assets") as resp:
                    assets = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(assets), 2)
                by_key = {(a["plant_name"], a["variant_id"]): a for a in assets}
                self.assertTrue(by_key[("plant_0010", "01")]["enabled"])
                self.assertTrue(by_key[("legacy_flat", "default")]["enabled"])

                # 2. POST /api/set_assets_enabled -> disable plant_0010/01 & legacy_flat/default
                req_disable = urllib.request.Request(
                    f"{base_url}/api/set_assets_enabled",
                    data=json.dumps({
                        "items": [
                            {"plant_name": "plant_0010", "variant_id": "01"},
                            {"plant_name": "legacy_flat", "variant_id": "default"},
                        ],
                        "enabled": False,
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_disable) as resp:
                    res_disable = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(res_disable["status"], "success")
                self.assertEqual(res_disable["updated"], 2)
                self.assertFalse(res_disable["enabled"])

                with open(os.path.join(v1, "metadata.json"), "r", encoding="utf-8") as f:
                    self.assertIs(json.load(f)["enabled"], False)
                with open(os.path.join(flat_dir, "metadata.json"), "r", encoding="utf-8") as f:
                    self.assertIs(json.load(f)["enabled"], False)

                manifest_path = os.path.join(self.plants_dir, "manifest.json")
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                self.assertEqual(manifest["total_species"], 0)

                # GET /api/assets still lists disabled assets with enabled=False
                with urllib.request.urlopen(f"{base_url}/api/assets") as resp:
                    assets_after = json.loads(resp.read().decode("utf-8"))
                by_key_after = {(a["plant_name"], a["variant_id"]): a for a in assets_after}
                self.assertIs(by_key_after[("plant_0010", "01")]["enabled"], False)
                self.assertIs(by_key_after[("legacy_flat", "default")]["enabled"], False)

                # 3. POST /api/set_assets_enabled -> re-enable plant_0010/01
                req_enable = urllib.request.Request(
                    f"{base_url}/api/set_assets_enabled",
                    data=json.dumps({
                        "items": [{"plant_name": "plant_0010", "variant_id": "01"}],
                        "enabled": True,
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_enable) as resp:
                    res_enable = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(res_enable["status"], "success")
                self.assertEqual(res_enable["updated"], 1)
                self.assertTrue(res_enable["enabled"])

                with open(os.path.join(v1, "metadata.json"), "r", encoding="utf-8") as f:
                    self.assertIs(json.load(f)["enabled"], True)
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_re = json.load(f)
                self.assertEqual(manifest_re["total_species"], 1)
                self.assertEqual(manifest_re["plants"][0]["name"], "plant_0010")

                # 4. Path safety check rejects '..' and '/'
                for bad_item in (
                    {"plant_name": "../etc", "variant_id": "01"},
                    {"plant_name": "plant_0010", "variant_id": "01/../../bad"},
                ):
                    req_bad = urllib.request.Request(
                        f"{base_url}/api/set_assets_enabled",
                        data=json.dumps({"items": [bad_item], "enabled": False}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(req_bad)
                    self.assertEqual(ctx.exception.code, 400)

                # 5. HTML page includes expected UI elements
                with urllib.request.urlopen(f"{base_url}/") as resp:
                    html = resp.read().decode("utf-8")
                for marker in (
                    "assetsTotalCount",
                    "assetFilterPills",
                    "batchDisableBtn",
                    "batchEnableBtn",
                    "card-disabled",
                    "modalToggleEnabledBtn",
                    "setSelectedAssetsEnabled",
                ):
                    self.assertIn(marker, html)
            finally:
                server.shutdown()
                server.server_close()

    def test_set_legacy_plants_disabled_preserves_pngs_and_is_idempotent(self):
        legacy_dirs = []
        for sp in set_legacy_plants_disabled.LEGACY_SPECIES:
            vdir = self._make_variant(sp, "v_rgb", {"name": sp, "display_name": sp})
            legacy_dirs.append(vdir)
        # Add extra variant on lily to ensure all variants are disabled
        lily_uncut = self._make_variant("lily", "v_semantic", {"name": "lily", "display_name": "lily"})
        legacy_dirs.append(lily_uncut)

        # plant_0000 has both legacy timestamp variant and modern '01' variant
        p0_legacy = self._make_variant(
            "plant_0000", "1791079136", {"name": "plant_0000", "display_name": "孔雀竹芋"}
        )
        p0_modern = self._make_variant(
            "plant_0000", "01", {"name": "plant_0000", "display_name": "孔雀竹芋", "true_color": True}
        )
        # Another modern species
        p5_modern = self._make_variant(
            "plant_0005", "01", {"name": "plant_0005", "display_name": "琴叶榕", "true_color": True}
        )

        # First run
        summary1 = set_legacy_plants_disabled.disable_legacy_plants(plants_dir=self.plants_dir)
        self.assertEqual(summary1["total_species"], 2)  # plant_0000 + plant_0005

        # All legacy variants disabled, PNG files preserved
        for d in legacy_dirs + [p0_legacy]:
            self.assertTrue(os.path.isfile(os.path.join(d, "layer_green.png")))
            self.assertTrue(os.path.isfile(os.path.join(d, "layer_red.png")))
            with open(os.path.join(d, "metadata.json"), "r", encoding="utf-8") as f:
                mdata = json.load(f)
            self.assertIs(mdata["enabled"], False)

        # Modern variants remain enabled
        for d in (p0_modern, p5_modern):
            with open(os.path.join(d, "metadata.json"), "r", encoding="utf-8") as f:
                mdata = json.load(f)
            self.assertIsNot(mdata.get("enabled"), False)

        with open(os.path.join(self.plants_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["total_species"], 2)
        names = [p["name"] for p in manifest["plants"]]
        self.assertEqual(names, ["plant_0000", "plant_0005"])

        # Second run is idempotent
        summary2 = set_legacy_plants_disabled.disable_legacy_plants(plants_dir=self.plants_dir)
        self.assertEqual(summary2["total_species"], 2)
        self.assertEqual(summary2["updated_variants"], summary1["updated_variants"])


if __name__ == "__main__":
    unittest.main()
