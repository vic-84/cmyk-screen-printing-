import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.core.image_processing import generate_white_base, prepare_image_for_processing, rotate_image
from src.core import mesh as mesh_rules
from src.core import color, output, tone
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
        global QtWidgets, QtGui
        from PyQt5 import QtGui, QtWidgets

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
        window.shape_combo.setCurrentText("Elíptica")

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
        window.lpi_combo.setCurrentText("45 LPI")

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

    def test_mesh_rules_follow_threads_per_line(self):
        low, high = mesh_rules.suggested_lpi_range(120)
        self.assertAlmostEqual(low, 120 / 4.75)
        self.assertAlmostEqual(high, 120 / 3.5)
        # 120 / 4 = 30 es relación entera: se sugiere un vecino (29 o 31)
        self.assertTrue(mesh_rules.is_integer_ratio(120, 30))
        self.assertIn(mesh_rules.suggested_lpi(120), (29, 31))
        self.assertEqual(mesh_rules.assess(120, 45)[0], "riesgo")
        self.assertEqual(mesh_rules.assess(120, 31)[0], "ok")
        self.assertAlmostEqual(mesh_rules.to_threads_per_inch(47, "cm"), 119.38)

    def test_dot_gain_compensation_prints_the_requested_tone(self):
        film = tone.film_for_printed(0.5, 20)
        self.assertLess(film, 0.5)
        self.assertAlmostEqual(float(tone.printed_from_film(film, 20)), 0.5, places=6)
        self.assertAlmostEqual(float(tone.printed_from_film(0.5, 20)), 0.7, places=6)

    def test_print_simulation_regrows_compensated_dots(self):
        film = int(round(tone.film_for_printed(0.5, 20) * 255))
        screen = halftone(np.full((300, 300), film, dtype=np.uint8), 10.3, "circle", 0)

        printed = tone.printed_ink(screen, 20, 10.3)

        self.assertAlmostEqual(float(np.mean(screen == 0)), film / 255, delta=0.03)
        self.assertAlmostEqual(float(printed.mean()), 0.5, delta=0.04)

    def test_tone_range_drops_small_dots_and_fills_large_ones(self):
        lut = tone.tone_lut(min_dot=10, max_dot=90)
        self.assertEqual(lut[int(0.05 * 255)], 0)
        self.assertEqual(lut[int(0.95 * 255)], 255)
        self.assertEqual(lut[128], 128)
        self.assertEqual(tone.tone_lut(density=50)[255], 128)

    def test_mono_mode_generates_a_single_plate(self):
        settings = JobSettings(mode="mono", lpi=30)
        image = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
        image = np.dstack([image] * 3)

        channels, screens, _ = render(image, None, settings)

        self.assertEqual(list(screens), ["K"])
        self.assertGreater(channels["K"][:, 0].mean(), channels["K"][:, -1].mean())

    def test_every_dot_shape_reproduces_the_requested_tone(self):
        # La cobertura en película debe igualar el tono (±3 %) en todas las formas
        for shape in ("circle", "ellipse", "square", "diamond", "line"):
            for tone_value in (0.1, 0.25, 0.5, 0.75, 0.9):
                image = np.full((240, 240), int(round(tone_value * 255)), dtype=np.uint8)
                coverage = np.mean(halftone(image, 20.37, shape, 0) == 0)
                self.assertAlmostEqual(coverage, tone_value, delta=0.03, msg=f"{shape} {tone_value}")
            self.assertEqual(np.mean(halftone(np.full((60, 60), 255, np.uint8), 20.37, shape, 0) == 0), 1.0)
            self.assertEqual(np.mean(halftone(np.zeros((60, 60), np.uint8), 20.37, shape, 0) == 0), 0.0)

    def test_angle_preset_and_mesh_reach_the_job(self):
        window = SimpleHalftoneApp()
        window.angle_preset_combo.setCurrentText("Offset (15/75/0/45)")
        window.mesh_unit_combo.setCurrentIndex(window.mesh_unit_combo.findData("cm"))
        window.mesh_spin.setValue(48)
        window.min_dot_spin.setValue(8)

        settings = window.job_settings()

        self.assertEqual(settings.angles["K"], 45)
        self.assertAlmostEqual(settings.mesh_tpi, 48 * 2.54)
        self.assertEqual(settings.min_dot, 8)
        window.angle_spins["C"].setValue(20)
        self.assertEqual(window.angle_preset_combo.currentText(), "Personalizado")
        window.close()

    def test_measured_gain_curve_is_compensated(self):
        model = tone.GainModel(curve=[[25, 40], [50, 70], [75, 88]])
        self.assertAlmostEqual(float(model.printed(0.5)), 0.70)
        self.assertAlmostEqual(float(model.film(0.70)), 0.5)
        settings = JobSettings(dot_gain_curve=[[50, 70]])
        lut = tone.tone_lut(gain=tone.GainModel.from_settings(settings))
        self.assertLess(lut[178], 178)  # un 70 % pedido va a la película más claro

    def test_film_options_mirror_negative_and_tiff(self):
        settings = JobSettings(mirror=True, negative=True, output_format="tiff", dpi=600)
        screen = np.full((40, 60), 255, dtype=np.uint8)
        screen[:, :10] = 0

        film = output.finish_positive(screen, "K", settings)

        self.assertTrue((film[:, -10:] == 255).all())   # espejo + negativo
        self.assertTrue((film[:, :50] == 0).all())
        with tempfile.TemporaryDirectory() as folder:
            path = output.save_positive(os.path.join(folder, "K"), film, settings)
            with Image.open(path) as im:
                self.assertEqual(im.mode, "1")
                self.assertAlmostEqual(im.info["dpi"][0], 600, places=0)

    def test_films_carry_a_control_strip_in_the_margin(self):
        settings = JobSettings(fit_to_paper=True, registration_guides=True, control_strip=True,
                               paper_width_mm=150, paper_height_mm=100)
        blank = np.full(settings.paper_px[::-1], 255, dtype=np.uint8)
        with_strip = output.finish_positive(blank, "C", settings)
        settings.control_strip = False
        without = output.finish_positive(blank, "C", settings)

        margin = int(settings.guide_margin_mm / 25.4 * settings.dpi)
        bottom = slice(margin + settings.paper_px[1] + 2, None)
        self.assertGreater((with_strip[bottom] == 0).sum(), (without[bottom] == 0).sum() * 3)

    def test_dot_gain_template_has_one_patch_per_lpi_and_tone(self):
        template = output.dot_gain_template(JobSettings(dpi=300), lpis=(20, 40), tones=(10, 50, 90))
        self.assertGreater(template.shape[0], 0)
        self.assertLess((template == 0).mean(), 0.6)

    def test_print_films_writes_one_page_per_film(self):
        from PyQt5 import QtPrintSupport
        from src.ui.main_window import print_films
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "positivos.pdf")
            printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
            printer.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
            printer.setOutputFileName(path)
            films = [np.full((300, 200), 255, np.uint8), np.zeros((300, 200), np.uint8)]
            print_films(printer, films, 300)
            with open(path, "rb") as f:
                pdf = f.read()
        self.assertEqual(pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page\r") + pdf.count(b"/Type /Page "), 2)

    def _spot_test_image(self):
        # Prenda negra con un bloque rojo y uno amarillo que se tocan (BGR)
        image = np.zeros((120, 200, 3), dtype=np.uint8)
        image[20:100, 20:100] = (0, 0, 230)
        image[20:100, 100:180] = (0, 220, 255)
        return image

    def _spot_settings(self, **extra):
        spots = [{"id": "S1", "name": "Rojo", "rgb": [230, 0, 0], "halftone": False, "opaque": True, "base": True},
                 {"id": "S2", "name": "Amarillo", "rgb": [255, 220, 0], "halftone": False, "opaque": True, "base": True}]
        return JobSettings(mode="spot", garment_rgb=[0, 0, 0], spot_colors=spots, **extra)

    def test_palette_detection_finds_the_inks_and_skips_the_garment(self):
        palette = color.detect_palette(self._spot_test_image(), 2, garment_rgb=[0, 0, 0])
        found = [entry["rgb"] for entry in palette]
        self.assertEqual(len(found), 2)
        for expected in ([230, 0, 0], [255, 220, 0]):
            self.assertTrue(any(np.abs(np.subtract(rgb, expected)).max() < 12 for rgb in found), found)

    def test_solid_spot_colors_knock_out_each_other(self):
        channels, _, _ = render(self._spot_test_image(), None, self._spot_settings())
        red, yellow = channels["S1"] > 0, channels["S2"] > 0
        self.assertFalse((red & yellow).any())
        self.assertAlmostEqual(red.mean(), 80 * 80 / (120 * 200), delta=0.01)
        self.assertEqual(channels["S1"][0, 0], 0)  # la prenda no se imprime

    def test_trapping_spreads_the_light_color_under_the_dark_one(self):
        settings = self._spot_settings(trap_mm=0.5, dpi=300)
        channels, _, _ = render(self._spot_test_image(), None, settings)
        yellow = channels["S2"] > 0
        # El amarillo (claro) invade el rojo (oscuro) unos 6 px, pero no la prenda
        self.assertTrue(yellow[60, 95])
        self.assertFalse(yellow[60, 185])
        self.assertFalse((channels["S1"][:, 101:] > 0).any())

    def test_underbase_skips_colors_marked_without_base(self):
        settings = self._spot_settings(white_base=True, white_base_choke_px=0)
        settings.spot_colors[0]["base"] = False
        channels, _, _ = render(self._spot_test_image(), None, settings)
        self.assertEqual(channels["W"][60, 60], 0)
        self.assertEqual(channels["W"][60, 140], 255)
        self.assertEqual(settings.channels()[0], "W")

    def test_halftone_spot_shades_gradients(self):
        gradient = np.zeros((40, 256, 3), dtype=np.uint8)
        gradient[:, :, 2] = np.arange(256, dtype=np.uint8)  # de negro a rojo
        settings = self._spot_settings()
        settings.spot_colors = [dict(settings.spot_colors[0], halftone=True)]
        channels, _, _ = render(gradient, None, settings)
        row = channels["S1"][20]
        self.assertLess(row[30], row[128])
        self.assertLess(row[128], row[250])
        self.assertTrue(0 < row[128] < 255)

    def test_color_libraries_round_trip_and_match(self):
        library = [{"name": "PMS 186 C", "rgb": [200, 16, 46]}, {"name": "PMS 116 C", "rgb": [255, 205, 0]}]
        with tempfile.TemporaryDirectory() as folder:
            ase = os.path.join(folder, "lib.ase")
            color.write_ase(ase, library)
            loaded = color.read_library(ase)
            csv_path = os.path.join(folder, "lib.csv")
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write("Nombre,Hex\nPMS 186 C,#C8102E\nPMS 116 C,255,205,0\n")
            from_csv = color.read_library(csv_path)
        self.assertEqual([c["name"] for c in loaded], ["PMS 186 C", "PMS 116 C"])
        self.assertEqual(loaded[0]["rgb"], [200, 16, 46])
        self.assertEqual(from_csv[0]["rgb"], [200, 16, 46])
        self.assertEqual(from_csv[1]["rgb"], [255, 205, 0])
        entry, distance = color.match_library([210, 20, 40], loaded)
        self.assertEqual(entry["name"], "PMS 186 C")
        self.assertLess(distance, 5)

    def test_spot_workflow_in_the_window(self):
        window = SimpleHalftoneApp()
        window._set_loaded_image(self._spot_test_image())
        window.garment_color = QtGui.QColor(0, 0, 0)
        window.mode_combo.setCurrentText("Color plano (spot)")
        window.spot_count_spin.setValue(2)
        window.white_base_cb.setChecked(True)
        window.detect_spot_colors()

        settings = window.job_settings()
        self.assertEqual(len(settings.spot_colors), 2)
        self.assertTrue(all(sp["base"] for sp in settings.spot_colors))
        self.assertEqual(settings.channels()[0], "W")
        self.assertEqual(set(window.preview_cache), {"W", "S1", "S2"})
        self.assertEqual(window.channel_list.count(), 3)
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
