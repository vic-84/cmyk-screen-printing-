import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.core.image_processing import generate_white_base, prepare_image_for_processing, rotate_image
from src.core import output
from src.core.job import JobSettings
from src.core.screening import adjust_levels, halftone
from src.core.separation import render
from src.ui.main_window import SimpleHalftoneApp
from src.utils.constants import LPI_VALUES, MEASUREMENT_UNITS, POINT_SHAPES, TOTAL_INK_LIMIT


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        global QtWidgets
        from PyQt5 import QtWidgets

    def test_transparent_pixels_do_not_become_black_ink(self):
        image = np.array([[[0, 0, 0, 0], [30, 20, 10, 255]]], dtype=np.uint8)

        result = prepare_image_for_processing(image)

        np.testing.assert_array_equal(result[0, 0], [255, 255, 255])
        np.testing.assert_array_equal(result[0, 1], [30, 20, 10])

    def test_processing_applies_the_selected_resolution_enhancement(self):
        window = SimpleHalftoneApp()
        window.image = np.zeros((10, 10, 3), dtype=np.uint8)
        window.white_base_cb.setChecked(False)
        window.resolution_combo.setCurrentText("Mejorar 150% (recomendado)")

        window.process_cmyk()

        self.assertEqual(window.channel_arrays["C"].shape, (15, 15))
        window.close()

    def test_selected_point_shape_is_mapped_to_the_halftone_engine(self):
        window = SimpleHalftoneApp()
        window.shape_combo.setCurrentText("Elipse")

        _, shape, _ = window._get_halftone_params()

        self.assertEqual(shape, "ellipse")
        window.close()

    def test_circle_halftone_preserves_midtone_coverage(self):
        image = np.full((200, 200), 128, dtype=np.uint8)

        screen = halftone(image, 20, "circle", 0)
        ink_coverage = np.mean(screen == 0)

        self.assertGreater(ink_coverage, 0.45)
        self.assertLess(ink_coverage, 0.55)

    def test_pure_black_respects_total_ink_limit(self):
        window = SimpleHalftoneApp()
        window.image = np.zeros((10, 10, 3), dtype=np.uint8)
        window.white_base_cb.setChecked(False)

        window.process_cmyk()

        total = sum(window.channel_arrays[c].astype(float) for c in "CMYK") / 255 * 100
        self.assertLessEqual(total.max(), TOTAL_INK_LIMIT + 1)
        self.assertGreater(window.channel_arrays["K"].max(), 200)
        window.close()

    def test_halftone_cell_matches_requested_lpi(self):
        window = SimpleHalftoneApp()
        window.print_format_combo.setCurrentText("A4")
        window.lpi_combo.setCurrentText("45 LPI (malla 120)")

        scale, _, _ = window._get_halftone_params()

        self.assertAlmostEqual(scale, 300 / 45)
        window.close()

    def test_fit_to_paper_screens_at_print_size(self):
        settings = JobSettings(fit_to_paper=True, paper_width_mm=148, paper_height_mm=210, dpi=300)
        image = np.full((50, 40, 3), 128, dtype=np.uint8)

        _, screens, scale = render(image, None, settings, preview=False)

        # A5 = 148 x 210 mm a 300 dpi
        self.assertEqual(scale, 1.0)
        self.assertEqual(screens["C"].shape, (2480, 1748))

    def test_preview_is_reduced_but_keeps_readable_cells(self):
        settings = JobSettings(fit_to_paper=True, paper_width_mm=297, paper_height_mm=420, dpi=300, lpi=45)
        image = np.full((100, 70, 3), 128, dtype=np.uint8)

        _, screens, scale = render(image, None, settings, preview=True)

        full_height = settings.paper_px[1]
        self.assertLess(scale, 1.0)
        self.assertLess(screens["C"].shape[0], full_height)
        self.assertGreaterEqual(settings.cell_px * scale, 4.0)

    def test_registration_guides_do_not_rescale_the_screen(self):
        settings = JobSettings(fit_to_paper=True, registration_guides=True,
                               paper_width_mm=100, paper_height_mm=150, dpi=300)
        screen = np.full(settings.paper_px[::-1], 255, dtype=np.uint8)
        screen[::7, ::7] = 0

        positive = output.add_registration_guides(screen, "C", settings)

        margin = int(settings.guide_margin_mm / 25.4 * settings.dpi)
        paper_w, paper_h = settings.paper_px
        self.assertEqual(positive.shape, (paper_h + 2 * margin, paper_w + 2 * margin))
        inner = positive[margin + 400:margin + 800, margin + 400:margin + 800]
        np.testing.assert_array_equal(inner, screen[400:800, 400:800])

    def test_job_settings_round_trip_through_json(self):
        settings = JobSettings(lpi=32, white_base=True, thresholds={"K": 90}, channel_order=["W", "K", "C", "M", "Y"])
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "trabajo.json")
            settings.save(path)
            loaded = JobSettings.load(path)

        self.assertEqual(loaded.lpi, 32)
        self.assertEqual(loaded.thresholds["K"], 90)
        self.assertEqual(loaded.thresholds["C"], 128)
        self.assertEqual(loaded.channels(), ["W", "K", "C", "M", "Y"])

    def test_neutral_threshold_does_not_change_the_channel(self):
        channel = np.arange(256, dtype=np.uint8).reshape(16, 16)
        np.testing.assert_array_equal(adjust_levels(channel, 128), channel)
        self.assertGreater(adjust_levels(channel, 64).mean(), channel.mean())

    def test_export_writes_positives_with_dpi_and_job_file(self):
        window = SimpleHalftoneApp()
        window._set_loaded_image(np.full((40, 30, 3), 90, dtype=np.uint8))
        window.white_base_cb.setChecked(True)
        window.process_cmyk()

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=folder), \
                mock.patch.object(QtWidgets.QMessageBox, "information"):
            window.save_results()
            files = os.listdir(folder)
            positive = [f for f in files if f.startswith("POSITIVO_W_")][0]
            with Image.open(os.path.join(folder, positive)) as im:
                dpi = im.info.get("dpi")

        self.assertEqual(len([f for f in files if f.startswith("POSITIVO_")]), 5)
        self.assertTrue(any(f.startswith("configuracion_") for f in files))
        self.assertAlmostEqual(dpi[0], 300, places=0)
        window.close()

    def test_white_base_follows_lightness_and_skips_black(self):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        image[:, 10:20] = (40, 40, 200)   # rojo: necesita base completa
        image[:, 20:] = (128, 128, 128)   # gris medio: base parcial

        base = generate_white_base(image, threshold=160, choke=0)

        self.assertEqual(base[:, :10].max(), 0)
        self.assertEqual(base[:, 10:20].min(), 255)
        self.assertTrue(0 < base[0, 25] < 255)

    def test_transparent_margin_gets_no_white_base(self):
        window = SimpleHalftoneApp()
        rgba = np.zeros((20, 20, 4), dtype=np.uint8)
        rgba[5:15, 5:15] = (40, 40, 200, 255)
        window._set_loaded_image(rgba)
        window.white_base_cb.setChecked(True)

        window.process_cmyk()

        white = window.channel_arrays["W"]
        self.assertEqual(white[0, 0], 0)
        self.assertGreater(white[10, 10], 200)
        # La transparencia no se separa como tinta negra
        self.assertEqual(window.channel_arrays["K"][0, 0], 0)
        window.close()

    def test_rotate_image_preserves_color_images(self):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        image[5:15, 10:20] = (10, 20, 30)

        result = rotate_image(image, 45)

        self.assertGreater(result.shape[0], image.shape[0])
        self.assertGreater(result.shape[1], image.shape[1])
        self.assertEqual(result.shape[2], 3)
        self.assertGreater(np.count_nonzero(result), 0)

    def test_constants_support_the_ui_contract(self):
        self.assertTrue(LPI_VALUES)
        self.assertTrue(POINT_SHAPES)
        for unit in MEASUREMENT_UNITS.values():
            self.assertIn("to_mm_factor", unit)


if __name__ == "__main__":
    unittest.main()
