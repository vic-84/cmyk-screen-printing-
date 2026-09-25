import io
import json
import unittest
import zipfile

import numpy as np
from PIL import Image

try:
    from fastapi.testclient import TestClient
    from src.web.app import app
except ImportError:  # dependencias web opcionales
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi no instalado (pip install -r requirements-web.txt)")
class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _upload(self, size=(80, 60), color=(200, 40, 40), name="arte.png"):
        buffer = io.BytesIO()
        Image.new("RGB", size, color).save(buffer, format="PNG")
        response = self.client.post("/api/upload", files={"file": (name, buffer.getvalue(), "image/png")},
                                    data={"dpi": "300"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_index_and_options(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        options = self.client.get("/api/options").json()
        self.assertIn("cmyk", options["modes"].values())
        self.assertIn("Algodón negro", options["substrates"])

    def test_mesh_advice(self):
        advice = self.client.get("/api/mesh", params={"mesh_tpi": 120, "lpi": 45}).json()
        self.assertEqual(advice["level"], "riesgo")
        self.assertIn(advice["suggested"], (29, 31))

    def test_upload_preview_and_export(self):
        uploaded = self._upload()
        settings = {"lpi": 30, "fit_to_paper": True, "registration_guides": True,
                    "paper_width_mm": 60, "paper_height_mm": 50, "white_base": True}
        preview = self.client.post("/api/preview", data={"doc_id": uploaded["id"], "settings": json.dumps(settings),
                                                         "view": "tac"}).json()
        self.assertEqual([c["id"] for c in preview["channels"]], ["W", "Y", "C", "M", "K"])
        self.assertGreater(len(preview["image"]), 100)

        exported = self.client.post("/api/export", data={"doc_id": uploaded["id"], "settings": json.dumps(settings)})
        self.assertEqual(exported.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(exported.content)) as bundle:
            names = bundle.namelist()
            with bundle.open("POSITIVO_K.png") as f:
                dpi = Image.open(f).info["dpi"]
        self.assertIn("configuracion.json", names)
        self.assertAlmostEqual(dpi[0], 300, places=0)

    def test_palette_detection_for_spot(self):
        image = np.zeros((60, 80, 3), dtype=np.uint8)
        image[:, :40] = (220, 30, 30)
        image[:, 40:] = (30, 60, 200)
        buffer = io.BytesIO()
        Image.fromarray(image).save(buffer, format="PNG")
        uploaded = self.client.post("/api/upload", files={"file": ("dos.png", buffer.getvalue(), "image/png")}).json()
        palette = self.client.post("/api/palette", data={
            "doc_id": uploaded["id"], "count": "2",
            "settings": json.dumps({"mode": "spot", "garment_rgb": [255, 255, 255]})}).json()
        self.assertEqual(len(palette["spot_colors"]), 2)

    def test_icc_profile_upload_separation_and_proof(self):
        import os
        import tempfile
        from unittest import mock
        from src.core import icc
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"SERIGRAFIA_PROFILES_DIR": folder}):
            with open(icc.find_profile("GRACoL2006_Coated1v2.icc"), "rb") as f:
                data = f.read()
            installed = self.client.post("/api/profile", files={"file": ("Marca.icc", data, "application/octet-stream")})
            self.assertEqual(installed.status_code, 200, installed.text)
            self.assertAlmostEqual(installed.json()["profile"]["tac"], 320, delta=1)
            rejected = self.client.post("/api/profile", files={"file": ("roto.icc", b"xx", "application/octet-stream")})
            self.assertEqual(rejected.status_code, 422)

            uploaded = self._upload()
            settings = json.dumps({"icc_profile": "Marca.icc", "lpi": 30})
            proof = self.client.post("/api/preview", data={"doc_id": uploaded["id"], "settings": settings, "view": "proof"})
            self.assertEqual(proof.status_code, 200, proof.text)
            exported = self.client.post("/api/export", data={"doc_id": uploaded["id"], "settings": settings})
            with zipfile.ZipFile(io.BytesIO(exported.content)) as bundle:
                config = json.loads(bundle.read("configuracion.json"))
                self.assertIn("compuesto_CMYK.tif", bundle.namelist())
        self.assertEqual(len(config["icc_profile_md5"]), 32)

    def test_errors_are_explained(self):
        response = self.client.post("/api/upload", files={"file": ("nota.txt", b"hola", "text/plain")})
        self.assertEqual(response.status_code, 415)
        self.assertIn("Formato no admitido", response.json()["detail"])
        missing = self.client.post("/api/preview", data={"doc_id": "no-existe"})
        self.assertEqual(missing.status_code, 404)
        self.assertIn("Vuelve a subirla", missing.json()["detail"])


if __name__ == "__main__":
    unittest.main()
