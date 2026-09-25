import io
import json
import os
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.core.image_processing import generate_white_base, prepare_image_for_processing
from src.core import mesh as mesh_rules
from src.core import color, output, tone
from src.core import simulate as sim
from src.core import input as doc_input
from src.core import icc
from src.core.job import JobSettings
from src.core.screening import adjust_levels, halftone
from src.core.separation import design_size_mm, layout, render
from src.ui.main_window import SimpleHalftoneApp
from src.utils.constants import LPI_VALUES, MEASUREMENT_UNITS, POINT_SHAPES, TOTAL_INK_LIMIT


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        global QtWidgets, QtGui, QtCore
        from PyQt5 import QtCore, QtGui, QtWidgets

    def test_transparent_pixels_do_not_become_black_ink(self):
        image = np.array([[[0, 0, 0, 0], [30, 20, 10, 255]]], dtype=np.uint8)

        result = prepare_image_for_processing(image)

        np.testing.assert_array_equal(result[0, 0], [255, 255, 255])
        np.testing.assert_array_equal(result[0, 1], [30, 20, 10])

    def test_processing_applies_the_selected_resolution_enhancement(self):
        window = SimpleHalftoneApp()
        window.image = np.zeros((10, 10, 3), dtype=np.uint8)
        window.white_base_cb.setChecked(False)
        window.placement_combo.setCurrentIndex(window.placement_combo.findData("real"))
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
        settings = JobSettings(placement="fit", paper_width_mm=148, paper_height_mm=210, dpi=300)
        image = np.full((50, 40, 3), 128, dtype=np.uint8)

        _, screens, scale = render(image, None, settings, preview=False)

        # A5 = 148 x 210 mm a 300 dpi: el ancho limita, la proporción se conserva
        self.assertEqual(scale, 1.0)
        self.assertEqual(screens["C"].shape, (2185, 1748))
        # La película mide el lienzo completo
        self.assertEqual(output.finish_positive(screens["C"], "C", settings).shape, (2480, 1748))

    def test_preview_is_reduced_but_keeps_readable_cells(self):
        settings = JobSettings(placement="fit", paper_width_mm=297, paper_height_mm=420, dpi=300, lpi=45)
        image = np.full((100, 70, 3), 128, dtype=np.uint8)

        _, screens, scale = render(image, None, settings, preview=True)

        full_height = settings.paper_px[1]
        self.assertLess(scale, 1.0)
        self.assertLess(screens["C"].shape[0], full_height)
        self.assertGreaterEqual(settings.cell_px * scale, 4.0)

    def test_registration_guides_do_not_rescale_the_screen(self):
        settings = JobSettings(placement="fit", registration_guides=True,
                               paper_width_mm=100, paper_height_mm=150, dpi=300)
        margin = settings.guide_margin_px
        paper_w, paper_h = settings.paper_px
        screen = np.full((paper_h - 2 * margin, paper_w - 2 * margin), 255, dtype=np.uint8)
        screen[::7, ::7] = 0

        positive = output.add_registration_guides(screen, "C", settings)

        # Las guías van dentro del lienzo: la película no crece
        self.assertEqual(positive.shape, (paper_h, paper_w))
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
        h, w = white.shape
        self.assertEqual(white[0, 0], 0)
        self.assertGreater(white[h // 2, w // 2], 200)
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
        # Lienzo del mismo tamaño que la trama (60 × 40 px a 600 dpi)
        settings = JobSettings(mirror=True, negative=True, output_format="tiff", dpi=600,
                               paper_width_mm=60 / 600 * 25.4, paper_height_mm=40 / 600 * 25.4)
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
        settings = JobSettings(placement="fit", registration_guides=True, control_strip=True,
                               paper_width_mm=150, paper_height_mm=100)
        margin = settings.guide_margin_px
        paper_w, paper_h = settings.paper_px
        blank = np.full((paper_h - 2 * margin, paper_w - 2 * margin), 255, dtype=np.uint8)
        with_strip = output.finish_positive(blank, "C", settings)
        settings.control_strip = False
        without = output.finish_positive(blank, "C", settings)

        # La tira va dentro del lienzo, en el margen inferior
        self.assertEqual(with_strip.shape, (paper_h, paper_w))
        bottom = slice(paper_h - margin + 2, None)
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

    def test_holdable_dot_range_depends_on_mesh_and_lpi(self):
        low, high = sim.holdable_range(120, 29)
        self.assertAlmostEqual(low, 10.3, delta=0.2)
        self.assertAlmostEqual(high, 100 - low)
        self.assertGreater(sim.holdable_range(120, 45)[0], low)   # malla más abierta, punto mínimo mayor

    def _flat_job(self, rgb_ink, opaque=None, **extra):
        settings = JobSettings(mode="spot", garment_rgb=[0, 0, 0],
                               spot_colors=[{"id": "S1", "name": "Tinta", "rgb": rgb_ink, "halftone": False,
                                             "opaque": bool(opaque), "base": True}], **extra)
        image = np.zeros((40, 40, 3), dtype=np.uint8)
        image[:] = rgb_ink[::-1]
        channels, screens, _ = render(image, None, settings)
        return settings, channels, screens

    def test_transparent_ink_disappears_on_black_and_opaque_covers(self):
        red = [220, 30, 30]
        settings, channels, screens = self._flat_job(red, opaque=False)
        transparent = sim.simulate(channels, screens, settings, {"S1": red}, [0, 0, 0])
        settings, channels, screens = self._flat_job(red, opaque=True)
        opaque = sim.simulate(channels, screens, settings, {"S1": red}, [0, 0, 0])
        self.assertLess(transparent.mean(), 10)
        self.assertGreater(opaque[..., 0].mean(), 150)

    def test_registration_test_reveals_the_underbase(self):
        red = [220, 30, 30]
        settings, channels, screens = self._flat_job(red, opaque=True, white_base=True, white_base_choke_px=0)
        # Área de tinta rodeada de prenda
        for name in screens:
            screens[name][:, :] = 255
            screens[name][10:30, 10:30] = 0
        colors = {"S1": red, "W": (255, 255, 255)}
        aligned = sim.simulate(channels, screens, settings, colors, [0, 0, 0])
        shifted = sim.simulate(channels, screens, settings, colors, [0, 0, 0], misregister_mm=0.5)
        white = lambda img: ((img > 240).all(axis=-1)).sum()
        self.assertEqual(white(aligned), 0)
        self.assertGreater(white(shifted), 0)

    def test_print_steps_show_passes_in_order(self):
        settings, channels, screens = self._flat_job([220, 30, 30], opaque=True, white_base=True)
        colors = {"S1": (220, 30, 30), "W": (255, 255, 255)}
        only_base = sim.simulate(channels, screens, settings, colors, [0, 0, 0], steps=1)
        garment = sim.simulate(channels, screens, settings, colors, [0, 0, 0], steps=0)
        self.assertGreater(only_base.mean(), 200)
        self.assertEqual(garment.max(), 0)

    def test_quality_report_flags_ink_limit_and_lost_dots(self):
        settings = JobSettings(ink_limit=150, gcr=0.0, mesh_tpi=120, lpi=45)
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        image[:, :10] = (30, 30, 30)       # casi negro: mucha tinta
        image[:, 10:] = (245, 245, 245)    # luces muy claras: puntos pequeños
        channels, _, _ = render(image, None, settings)
        report = sim.quality_report(channels, settings)
        self.assertLessEqual(report["tac_max"], 151)
        self.assertGreater(report["lost"], 0.3)
        overlay = sim.dot_risk_overlay(np.zeros((20, 20, 3), np.uint8), channels, settings)
        self.assertTrue((overlay[5, 15] == (235, 120, 20)).all())

    def test_thin_lines_in_solid_inks_are_flagged_by_mesh(self):
        image = np.full((200, 200, 3), 230, dtype=np.uint8)
        image[20:180, 40:42] = 0      # línea de 2 px = 0.17 mm a 300 dpi
        image[20:180, 100:130] = 0    # trazo de 2.5 mm
        spots = [{"id": "S1", "name": "Negro", "rgb": [0, 0, 0], "halftone": False}]
        coarse = JobSettings(mode="spot", garment_rgb=[230, 230, 230], spot_colors=spots, mesh_tpi=120)
        fine = JobSettings(mode="spot", garment_rgb=[230, 230, 230], spot_colors=spots, mesh_tpi=355)
        channels, _, _ = render(image, None, coarse)
        report = sim.quality_report(channels, coarse)
        self.assertAlmostEqual(report["min_line_mm"], 0.3175, places=3)
        self.assertIn("S1", report["thin"])
        self.assertGreater(report["thin"]["S1"]["lines"], 0.05)          # la línea fina
        self.assertLess(report["thin"]["S1"]["lines"], 0.2)              # el trazo grueso no
        self.assertNotIn("S1", sim.quality_report(channels, fine)["thin"])  # 0.17 mm sí aguanta en malla 355
        # Un cuadrado grueso con esquinas vivas no es «línea fina»
        square = np.full((200, 200, 3), 230, dtype=np.uint8)
        square[40:160, 40:160] = 0
        square_channels, _, _ = render(square, None, coarse)
        self.assertNotIn("S1", sim.quality_report(square_channels, coarse)["thin"])
        overlay = sim.dot_risk_overlay(np.zeros((200, 200, 3), np.uint8), channels, coarse)
        self.assertTrue((overlay[100, 41] == (235, 120, 20)).all())
        self.assertFalse((overlay[100, 115] == (235, 120, 20)).all())

    def test_substrate_profile_and_view_modes_in_the_window(self):
        window = SimpleHalftoneApp()
        window._set_loaded_image(np.full((60, 40, 3), 120, dtype=np.uint8))
        window.substrate_combo.setCurrentText("Algodón negro")
        self.assertTrue(window.white_base_cb.isChecked())
        self.assertEqual(window.ink_limit_spin.value(), 240)
        window.process_cmyk()
        for index in range(window.view_mode_combo.count()):
            window.view_mode_combo.setCurrentIndex(index)
        window.step_slider.setValue(1)
        self.assertIn("Base blanca", window.step_label.text())
        window.close()

    def test_cmyk_image_is_converted_with_its_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "cmyk.tif")
            Image.new("CMYK", (20, 10), (0, 255, 255, 0)).save(path, dpi=(240, 240))  # rojo de proceso
            document = doc_input.load_document(path)
        r, g, b = document.bgr[5, 5][::-1]
        self.assertGreater(r, 180)
        self.assertLess(g, 90)
        self.assertEqual(document.dpi, 240)
        self.assertTrue(any("CMYK" in note for note in document.notes))

    def test_png_transparency_and_psd_composite_load(self):
        from psd_tools import PSDImage
        rgba = Image.new("RGBA", (30, 20), (0, 0, 0, 0))
        rgba.paste((200, 30, 40, 255), (5, 5, 25, 15))
        with tempfile.TemporaryDirectory() as folder:
            png = os.path.join(folder, "logo.png")
            rgba.save(png)
            psd = os.path.join(folder, "logo.psd")
            PSDImage.frompil(rgba.convert("RGB")).save(psd)
            png_doc = doc_input.load_document(png)
            psd_doc = doc_input.load_document(psd)
        self.assertEqual(png_doc.alpha[0, 0], 0)
        self.assertEqual(png_doc.alpha[10, 10], 255)
        self.assertEqual(psd_doc.source_type, "PSD")
        self.assertEqual(tuple(psd_doc.bgr[10, 10][::-1]), (200, 30, 40))

    def test_vector_documents_are_rasterized_at_the_requested_dpi(self):
        import fitz
        with tempfile.TemporaryDirectory() as folder:
            pdf_path = os.path.join(folder, "arte.pdf")
            doc = fitz.open()
            page = doc.new_page(width=72, height=144)          # 1 × 2 pulgadas
            page.draw_rect(fitz.Rect(18, 18, 54, 54), color=(1, 0, 0), fill=(1, 0, 0))
            doc.new_page(width=72, height=72)
            doc.save(pdf_path)
            ai_path = os.path.join(folder, "arte.ai")
            with open(pdf_path, "rb") as src, open(ai_path, "wb") as dst:
                dst.write(src.read())
            svg_path = os.path.join(folder, "arte.svg")
            with open(svg_path, "w") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" width="72pt" height="72pt">'
                        '<rect x="0" y="0" width="72" height="72" fill="#00ff00"/></svg>')
            pdf_doc = doc_input.load_document(pdf_path, dpi=300)
            ai_doc = doc_input.load_document(ai_path, dpi=150)
            svg_doc = doc_input.load_document(svg_path, dpi=100)
            pages = doc_input.page_count(pdf_path)
        self.assertEqual(pdf_doc.bgr.shape[:2], (600, 300))
        self.assertEqual(tuple(pdf_doc.bgr[150, 150]), (0, 0, 255))
        self.assertEqual(ai_doc.bgr.shape[:2], (300, 150))
        self.assertEqual(svg_doc.bgr[50, 50][1], 255)
        self.assertEqual(pages, 2)

    def test_eps_loads_with_ghostscript(self):
        from PIL import EpsImagePlugin
        if not EpsImagePlugin.has_ghostscript():
            self.skipTest("Ghostscript no instalado")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "arte.eps")
            Image.new("RGB", (36, 36), (0, 0, 255)).save(path)
            document = doc_input.load_document(path, dpi=144)
        self.assertEqual(document.source_type, "EPS")
        self.assertGreater(document.bgr[..., 0].mean(), 200)

    def test_specks_are_removed_from_solid_spot_inks(self):
        image = np.full((120, 120, 3), 230, dtype=np.uint8)
        image[20:100, 20:60] = 0                 # trazo real
        image[10, 100] = image[50, 90] = 0       # motas de 1 px (ruido)
        image[80:82, 100:102] = 0                # mota de 2×2 px
        spots = [{"id": "S1", "name": "Negro", "rgb": [0, 0, 0], "halftone": False}]
        clean = JobSettings(mode="spot", garment_rgb=[230, 230, 230], spot_colors=spots, despeckle_mm=0.25)
        raw = JobSettings(mode="spot", garment_rgb=[230, 230, 230], spot_colors=spots, despeckle_mm=0, mesh_tpi=120)
        cleaned, _, _ = render(image, None, clean)
        kept, _, _ = render(image, None, raw)
        self.assertEqual(cleaned["S1"][10, 100], 0)
        self.assertEqual(cleaned["S1"][81, 101], 0)
        self.assertEqual(cleaned["S1"][60, 40], 255)             # el trazo sigue
        self.assertEqual(kept["S1"][10, 100], 255)
        report = sim.quality_report(kept, raw)["thin"]["S1"]
        self.assertEqual(report["specks"], 3)          # malla 120: mínimo 0.32 mm = 3.75 px
        self.assertLess(report["lines"], 0.005)        # el trazo grueso no cuenta como línea fina

    # ---------------------------------------------------------------- medidas de salida

    def test_paper_sizes_are_rounded_not_truncated(self):
        self.assertEqual(JobSettings(paper_width_mm=297, paper_height_mm=420, dpi=300).paper_px, (3508, 4961))
        self.assertEqual(JobSettings(paper_width_mm=210, paper_height_mm=297, dpi=600).paper_px, (4961, 7016))

    def test_fit_to_paper_fills_the_limiting_side_exactly(self):
        image = np.full((1104, 736, 3), 128, dtype=np.uint8)
        settings = JobSettings(placement="fit", paper_width_mm=300, paper_height_mm=400, dpi=300)
        _, screens, _ = render(image, None, settings)
        paper_w, paper_h = settings.paper_px
        film = output.finish_positive(screens["K"], "K", settings)
        self.assertEqual(film.shape, (paper_h, paper_w))
        ink_rows = np.where((film == 0).any(axis=1))[0]
        width_mm, height_mm = design_size_mm(image.shape, settings)
        self.assertAlmostEqual(height_mm, 400, delta=0.2)
        self.assertAlmostEqual(width_mm, 400 * 736 / 1104, delta=0.3)
        self.assertGreaterEqual(ink_rows.max() - ink_rows.min() + 1, paper_h - 2)

    def test_without_fit_the_image_prints_at_its_own_physical_size(self):
        image = np.full((1104, 736, 3), 128, dtype=np.uint8)   # 72 dpi → 259.6 × 389.5 mm
        for factor in (1.0, 2.0):
            settings = JobSettings(placement="real", source_dpi=72, dpi=300, resolution_factor=factor,
                                   paper_width_mm=400, paper_height_mm=500)
            _, screens, _ = render(image, None, settings)
            h, w = screens["K"].shape
            self.assertAlmostEqual(w / 300 * 25.4, 736 / 72 * 25.4, delta=0.2)
            self.assertAlmostEqual(h / 300 * 25.4, 1104 / 72 * 25.4, delta=0.2)
        width_mm, height_mm = design_size_mm(image.shape, settings)
        self.assertAlmostEqual(width_mm, 259.6, delta=0.1)
        # En lienzo A4 con guías el diseño (26 × 39 cm) no cabe: se reduce al área útil, sin cortar
        guided = JobSettings(placement="real", source_dpi=72, dpi=300, registration_guides=True,
                             paper_width_mm=210, paper_height_mm=297)
        placed = layout(image.shape, guided)
        self.assertTrue(placed.reduced)
        self.assertAlmostEqual(placed.mm(300)[1], 297 - 2 * guided.guide_margin_mm, delta=0.2)

    def test_custom_size_in_every_unit_reaches_the_film(self):
        window = SimpleHalftoneApp()
        window._set_loaded_image(np.full((400, 300, 3), 90, dtype=np.uint8))
        window.image_info = {"dpi_x": 150.0, "dpi_y": 150.0, "width_px": 300, "height_px": 400}
        window.print_format_combo.setCurrentText("Personalizado")
        window.placement_combo.setCurrentIndex(window.placement_combo.findData("fit"))
        units = [window.unit_combo.itemText(i).split()[0] for i in range(window.unit_combo.count())]
        window.unit_combo.setCurrentIndex(units.index("mm"))
        window.custom_width.setValue(300)
        window.custom_height.setValue(400)
        settings = window.job_settings()
        self.assertEqual((settings.paper_width_mm, settings.paper_height_mm), (300, 400))
        # Cambiar de unidad convierte el valor y conserva la medida
        window.unit_combo.setCurrentIndex(units.index("cm"))
        self.assertAlmostEqual(window.custom_width.value(), 30.0)
        self.assertAlmostEqual(window.job_settings().paper_width_mm, 300, delta=0.5)
        window.unit_combo.setCurrentIndex(units.index("in"))
        self.assertAlmostEqual(window.job_settings().paper_height_mm, 400, delta=0.5)
        window.custom_width.setValue(20)          # 20 in = 508 mm
        self.assertAlmostEqual(window.job_settings().paper_width_mm, 508, delta=0.5)
        self.assertIn("mm", window.design_size_label.text())
        window.close()

    def test_export_matches_the_chosen_size(self):
        window = SimpleHalftoneApp()
        window._set_loaded_image(np.full((1104, 736, 3), 100, dtype=np.uint8))
        window.image_info = {"dpi_x": 72.0, "dpi_y": 72.0, "width_px": 736, "height_px": 1104, "file_path": "x.png"}
        window.print_format_combo.setCurrentText("Personalizado")
        window.custom_width.setValue(280)
        window.custom_height.setValue(350)
        window.placement_combo.setCurrentIndex(window.placement_combo.findData("fit"))
        window.guides_cb.setChecked(False)
        window.process_cmyk()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=folder), \
                mock.patch.object(QtWidgets.QMessageBox, "information"):
            window.save_results()
            path = [f for f in os.listdir(folder) if f.startswith("POSITIVO_K")][0]
            with Image.open(os.path.join(folder, path)) as im:
                dpi = im.info["dpi"][0]
                size_mm = (im.size[0] / dpi * 25.4, im.size[1] / dpi * 25.4)
            spec = open(os.path.join(folder, [f for f in os.listdir(folder) if f.startswith("especificaciones")][0]),
                        encoding="utf-8").read()
        self.assertAlmostEqual(size_mm[0], 280, delta=0.1)
        self.assertAlmostEqual(size_mm[1], 350, delta=0.1)
        self.assertIn("Diseño impreso: 233.3 × 350.0 mm", spec)
        window.close()

    # ---------------------------------------------------------------- lienzo primero

    def _ink_box(self, film):
        rows = np.where((film == 0).any(axis=1))[0]
        cols = np.where((film == 0).any(axis=0))[0]
        return rows.min(), rows.max(), cols.min(), cols.max()

    def test_film_always_measures_the_canvas_and_the_design_is_never_cut(self):
        image = np.zeros((1104, 736, 3), dtype=np.uint8)            # negro sólido: todo es tinta
        for placement in ("fit", "real", "width"):
            for guides in (False, True):
                settings = JobSettings(mode="mono", placement=placement, design_width_mm=900, source_dpi=72,
                                       registration_guides=guides, control_strip=False, dpi=150,
                                       paper_width_mm=210, paper_height_mm=297)
                _, screens, _ = render(image, None, settings)
                film = output.finish_positive(screens["K"], "K", settings)
                paper_w, paper_h = settings.paper_px
                self.assertEqual(film.shape, (paper_h, paper_w), (placement, guides))
                placed = layout(image.shape, settings)
                x, y = placed.offset
                w, h = placed.design
                # El diseño completo (sin recorte) está dentro del área útil
                self.assertEqual(screens["K"].shape, (h, w))
                self.assertTrue((film[y:y + h, x:x + w] == 0).all(), (placement, guides))
                margin = settings.guide_margin_px
                self.assertGreaterEqual(x, margin)
                self.assertLessEqual(x + w, paper_w - margin)
                self.assertLessEqual(y + h, paper_h - margin)

    def test_design_width_and_top_alignment(self):
        image = np.full((400, 300, 3), 60, dtype=np.uint8)
        settings = JobSettings(placement="width", design_width_mm=250, align="top", registration_guides=True,
                               paper_width_mm=400, paper_height_mm=500, dpi=300)
        placed = layout(image.shape, settings)
        width_mm, height_mm = placed.mm(300)
        self.assertAlmostEqual(width_mm, 250, delta=0.1)
        self.assertAlmostEqual(height_mm, 250 * 400 / 300, delta=0.2)
        self.assertFalse(placed.reduced)
        self.assertEqual(placed.offset[1], settings.guide_margin_px)                   # arriba
        self.assertAlmostEqual(placed.offset[0] + placed.design[0] / 2, settings.paper_px[0] / 2, delta=1)

    def test_guides_are_drawn_inside_the_canvas(self):
        settings = JobSettings(placement="fit", registration_guides=True, control_strip=True,
                               paper_width_mm=200, paper_height_mm=250, dpi=300)
        _, screens, _ = render(np.full((200, 160, 3), 255, dtype=np.uint8), None, settings)
        film = output.finish_positive(screens["K"], "K", settings)
        self.assertEqual(film.shape, settings.paper_px[::-1])
        margin = settings.guide_margin_px
        # Cruz de registro en la esquina del área útil
        self.assertTrue((film[margin, margin - 5:margin + 5] == 0).all())

    def test_old_jobs_with_fit_to_paper_load_as_placement(self):
        self.assertEqual(JobSettings.from_dict({"fit_to_paper": True}).placement, "fit")
        self.assertEqual(JobSettings.from_dict({"fit_to_paper": False}).placement, "real")
        self.assertNotIn("fit_to_paper", JobSettings().to_dict())

    def test_canvas_preview_shows_the_whole_canvas(self):
        settings = JobSettings(placement="fit", registration_guides=True, paper_width_mm=300, paper_height_mm=400)
        design = np.zeros((500, 300, 3), dtype=np.uint8)
        preview = output.canvas_preview(design, settings, 0.2, (255, 255, 255))
        paper_w, paper_h = settings.paper_px
        self.assertAlmostEqual(preview.shape[1] / preview.shape[0], paper_w / paper_h, delta=0.01)
        self.assertLessEqual(max(preview.shape[:2]), 1600)
        self.assertTrue((preview[preview.shape[0] // 2, preview.shape[1] // 2] == 0).all())

    # ---------------------------------------------------------------- desgaste y guías en blanco

    def test_distress_breaks_the_edges_and_leaves_them_blank(self):
        from src.core.enhance import distress_edges
        dpi = 150
        alpha = distress_edges(None, (600, 450), 20, dpi / 25.4)
        edge_px = round(20 / 25.4 * dpi)
        self.assertEqual(set(np.unique(alpha)), {0, 255})                 # bordes duros
        self.assertTrue((alpha[edge_px:-edge_px, edge_px:-edge_px] == 255).all())   # el centro intacto
        self.assertEqual(alpha[:3].max(), 0)                                # el borde exterior limpio
        band = alpha[edge_px // 2:edge_px, edge_px:-edge_px]
        self.assertTrue(0.05 < (band == 255).mean() < 0.95)                 # zona desgastada irregular
        # Misma semilla: mismo desgaste (todos los canales, vista previa y exportación)
        np.testing.assert_array_equal(alpha, distress_edges(None, (600, 450), 20, dpi / 25.4))

    def test_guides_go_in_the_blank_space_left_by_the_distress(self):
        image = np.zeros((1104, 736, 3), dtype=np.uint8)                   # todo tinta: el desgaste abre lugar
        settings = JobSettings(mode="cmyk", placement="fit", registration_guides=True, distress_mm=25,
                               control_strip=False,
                               paper_width_mm=260, paper_height_mm=390, dpi=150, source_dpi=72)
        self.assertTrue(settings.guides_in_blank)                           # automático con desgaste
        self.assertEqual(settings.guide_margin_px, 0)                       # el diseño no se reduce
        self.assertAlmostEqual(design_size_mm(image.shape, settings)[1], 390, delta=0.3)
        _, screens, _ = render(image, None, settings)
        films, plan = output.finish_positives(screens, settings)
        self.assertEqual(len(plan.crosses), 4)
        self.assertIsNotNone(plan.label)
        self.assertEqual(plan.missing, [])
        # Las cruces caen en el mismo punto en todas las películas y nunca sobre tinta del diseño
        x, y = plan.crosses[0]
        for channel, film in films.items():
            self.assertEqual(film.shape, settings.paper_px[::-1])
            self.assertEqual(film[y, x], 0)
            placed = output.place_on_paper(screens[channel], settings)
            r = round(settings.guide_cross_mm / 25.4 * settings.dpi / 2)
            self.assertTrue((placed[max(0, y - r):y + r, max(0, x - r):x + r] == 255).all())

    def test_without_distress_the_design_shrinks_to_leave_the_guide_margin(self):
        image = np.zeros((400, 300, 3), dtype=np.uint8)
        settings = JobSettings(placement="fit", registration_guides=True, guide_margin_mm=12,
                               paper_width_mm=300, paper_height_mm=400)
        self.assertFalse(settings.guides_in_blank)
        self.assertAlmostEqual(design_size_mm(image.shape, settings)[0], 300 - 24, delta=0.2)

    def test_distress_wears_the_outline_not_thin_strokes_on_transparent_art(self):
        from src.core.enhance import distress_edges
        dpi = 150
        alpha = np.zeros((600, 600), dtype=np.uint8)
        for x in range(100, 500, 60):                      # barras de 6 mm con fondo transparente
            alpha[100:500, x:x + 35] = 255
        worn = distress_edges(alpha, alpha.shape, 15, dpi / 25.4)
        inner = slice(250, 350)
        # El centro de la composición conserva sus trazos; solo se desgasta el contorno
        np.testing.assert_array_equal(worn[inner, 250:350], alpha[inner, 250:350])
        self.assertGreater((worn == 255).sum(), 0.4 * (alpha == 255).sum())   # antes: 0 %

    def test_guide_margin_never_eats_a_small_canvas(self):
        settings = JobSettings(placement="fit", registration_guides=True, paper_width_mm=25, paper_height_mm=25)
        placed = layout((100, 100, 3), settings)
        self.assertGreaterEqual(placed.mm(settings.dpi)[0], 12)

    def test_full_bleed_design_without_blank_space_reports_missing_guides(self):
        image = np.zeros((400, 300, 3), dtype=np.uint8)
        settings = JobSettings(mode="mono", placement="fit", registration_guides=True, guide_position="blank",
                               paper_width_mm=150, paper_height_mm=200, dpi=150)
        _, screens, _ = render(image, None, settings)
        films, plan = output.finish_positives(screens, settings)
        self.assertEqual(len(plan.crosses), 0)
        self.assertIn("datos del canal", plan.missing)
        # No se dibuja nada sobre el diseño
        np.testing.assert_array_equal(films["K"], output.place_on_paper(screens["K"], settings))

    def test_mouse_wheel_scrolls_the_panel_instead_of_changing_values(self):
        window = SimpleHalftoneApp()
        window.resize(1200, 800)
        window.show()
        spin = window.density_spins["W"]
        combo = window.placement_combo
        QtWidgets.QApplication.processEvents()
        for widget in (spin, combo):
            before = spin.value(), combo.currentIndex()
            event = QtGui.QWheelEvent(QtCore.QPointF(5, 5), QtCore.QPointF(5, 5), QtCore.QPoint(0, 0),
                                      QtCore.QPoint(0, -120), QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                                      QtCore.Qt.NoScrollPhase, False)
            QtWidgets.QApplication.sendEvent(widget, event)
            self.assertEqual((spin.value(), combo.currentIndex()), before)
        # Con foco (tras hacer clic) la rueda sí cambia el valor
        spin.setFocus()
        QtWidgets.QApplication.processEvents()
        if spin.hasFocus():
            event = QtGui.QWheelEvent(QtCore.QPointF(5, 5), QtCore.QPointF(5, 5), QtCore.QPoint(0, 0),
                                      QtCore.QPoint(0, -120), QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                                      QtCore.Qt.NoScrollPhase, False)
            QtWidgets.QApplication.sendEvent(spin, event)
            self.assertLess(spin.value(), 100)
        window.close()

    def test_zero_density_is_reported_after_separating(self):
        window = SimpleHalftoneApp()
        window._set_loaded_image(np.full((60, 40, 3), 200, dtype=np.uint8))
        window.white_base_cb.setChecked(True)
        window.density_spins["W"].setValue(0)
        window.density_spins["Y"].setValue(0)
        window.process_cmyk()
        self.assertIn("densidad 0 %", window.status_bar.currentMessage())
        window.close()

    def test_gray_base_is_named_and_simulated_with_its_color(self):
        red = [220, 30, 30]
        spots = [{"id": "S1", "name": "Rojo", "rgb": red, "halftone": False, "opaque": False, "base": True}]
        image = np.zeros((30, 30, 3), dtype=np.uint8)
        image[:] = red[::-1]
        results = {}
        for name, rgb in (("Base blanca", [255, 255, 255]), ("Base gris", [150, 150, 150])):
            settings = JobSettings(mode="spot", garment_rgb=[0, 0, 0], spot_colors=spots, white_base=True,
                                   white_base_choke_px=0, base_name=name, base_rgb=rgb)
            channels, screens, _ = render(image, None, settings)
            results[name] = sim.simulate(channels, screens, settings, {"S1": red, "W": rgb}, [0, 0, 0])
            self.assertEqual(settings.channel_name("W"), name)
        # Tinta transparente: sobre base gris queda más oscura que sobre base blanca
        self.assertGreater(results["Base blanca"][..., 0].mean(), results["Base gris"][..., 0].mean() + 40)

    def test_edge_smoothing_removes_steps_without_overlaps_or_gaps(self):
        # Imagen chica con diagonal en escalones, ampliada 6×: dos tintas que se tocan
        small = np.zeros((20, 20, 3), dtype=np.uint8)
        small[:] = (30, 30, 200)                     # rojo (BGR)
        small[np.tril_indices(20)] = (20, 20, 20)    # negro bajo la diagonal
        spots = [{"id": "S1", "name": "Negro", "rgb": [20, 20, 20], "halftone": False},
                 {"id": "S2", "name": "Rojo", "rgb": [200, 30, 30], "halftone": False}]
        base = dict(mode="spot", garment_rgb=[255, 255, 255], spot_colors=spots, resolution_factor=6.0,
                    resolution_method="INTER_NEAREST", despeckle_mm=0)
        steps = render(small, None, JobSettings(smooth_edges=False, **base))[0]
        smooth = render(small, None, JobSettings(smooth_edges=True, **base))[0]
        black_steps, black_smooth = steps["S1"] >= 128, smooth["S1"] >= 128
        both = black_smooth & (smooth["S2"] >= 128)
        neither = ~black_smooth & ~(smooth["S2"] >= 128)
        self.assertFalse(both.any())                 # sin solapes
        self.assertFalse(neither.any())              # sin huecos: toda la imagen es tinta
        self.assertAlmostEqual(black_smooth.mean(), black_steps.mean(), delta=0.03)
        # Desviación del borde respecto a la diagonal ideal: la escalera oscila ±3 px
        def wobble(mask):
            rows = np.arange(10, mask.shape[0] - 10)
            boundary = mask[rows].sum(axis=1).astype(float)
            fit = np.polyval(np.polyfit(rows, boundary, 1), rows)
            return float(np.std(boundary - fit))
        self.assertLess(wobble(black_smooth), wobble(black_steps) * 0.6)

    def test_denoise_removes_jpeg_specks_and_sharpen_adds_contrast(self):
        from src.core import enhance
        rng = np.random.default_rng(3)
        flat = np.full((80, 80, 3), 200, dtype=np.uint8)
        noisy = np.clip(flat.astype(int) + rng.normal(0, 12, flat.shape), 0, 255).astype(np.uint8)
        self.assertLess(enhance.denoise(noisy, "light").std(), noisy.std() * 0.6)
        self.assertIs(enhance.denoise(noisy, "off"), noisy)
        edge = np.zeros((20, 40, 3), dtype=np.uint8)
        edge[:, 20:] = 200
        edge = cv2.GaussianBlur(edge, (0, 0), 2)
        sharpened = enhance.sharpen(edge, 100)
        self.assertGreater(np.abs(np.diff(sharpened[10, :, 0].astype(int))).max(),
                           np.abs(np.diff(edge[10, :, 0].astype(int))).max())

    def test_base_presets_and_substrate_in_the_window(self):
        window = SimpleHalftoneApp()
        window.substrate_combo.setCurrentText("Poliéster oscuro")
        settings = window.job_settings()
        self.assertEqual(settings.base_name, "Base gris bloqueadora")
        self.assertEqual(settings.channel_name("W"), "Base gris bloqueadora")
        self.assertTrue(settings.white_base)
        window.base_combo.setCurrentIndex(window.base_combo.findText("Base gris"))
        window.on_base_changed()
        self.assertEqual(window.job_settings().base_rgb, [150, 150, 150])
        self.assertEqual(window.channel_colors["W"].getRgb()[:3], (150, 150, 150))
        window.denoise_combo.setCurrentIndex(window.denoise_combo.findData("strong"))
        self.assertEqual(window.job_settings().denoise, "strong")
        window.close()

    def test_resolution_advice_for_solid_spot_colors(self):
        spots = [{"id": "S1", "name": "Negro", "rgb": [0, 0, 0], "halftone": False}]
        settings = JobSettings(mode="spot", spot_colors=spots, lpi=45, placement="fit",
                               paper_width_mm=254, paper_height_mm=254)
        level, message = doc_input.resolution_advice((1000, 1000), 72, settings)   # 100 dpi
        self.assertEqual(level, "riesgo")
        self.assertIn("tintas sólidas", message)
        self.assertEqual(doc_input.resolution_advice((2000, 2000), 72, settings)[0], "ok")

    def test_resolution_advice_uses_the_final_size(self):
        settings = JobSettings(placement="fit", paper_width_mm=254, paper_height_mm=254, lpi=60)
        level, message = doc_input.resolution_advice((1000, 1000), 72, settings)   # 100 dpi en 10", ideal 120
        self.assertEqual(level, "aviso")
        self.assertIn("100 dpi", message)
        self.assertEqual(doc_input.resolution_advice((3000, 3000), 72, settings)[0], "ok")
        self.assertEqual(doc_input.resolution_advice((500, 500), 72, settings)[0], "riesgo")

    def test_opening_a_pdf_after_a_transparent_png_resets_alpha(self):
        import fitz
        window = SimpleHalftoneApp()
        with tempfile.TemporaryDirectory() as folder:
            png = os.path.join(folder, "a.png")
            Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(png)
            png_alpha_shape = (50, 50)
            pdf = os.path.join(folder, "b.pdf")
            doc = fitz.open()
            page = doc.new_page(width=72, height=72)
            page.draw_rect(fitz.Rect(0, 0, 72, 72), color=(0, 0, 1), fill=(0, 0, 1))
            doc.save(pdf)
            window.load_image_file(png)
            self.assertEqual(window.image_alpha.shape, png_alpha_shape)
            window.load_image_file(pdf)
        # PDF con arte a sangre: opaco, sin el alfa del PNG anterior
        self.assertIsNone(window.image_alpha)
        window.process_cmyk()
        self.assertIn("C", window.preview_cache)
        window.close()

    def test_index_color_uses_one_ink_per_pixel(self):
        gradient = np.zeros((60, 256, 3), dtype=np.uint8)
        gradient[:, :, 2] = np.arange(256, dtype=np.uint8)
        settings = JobSettings(mode="index", garment_rgb=[0, 0, 0], index_resolution=100,
                               spot_colors=[{"id": "S1", "name": "Rojo", "rgb": [255, 0, 0]},
                                            {"id": "S2", "name": "Granate", "rgb": [128, 0, 0]}])
        _, screens, _ = render(gradient, None, settings)
        ink1, ink2 = screens["S1"] == 0, screens["S2"] == 0
        self.assertFalse((ink1 & ink2).any())
        self.assertLess(ink1[:, :64].mean(), ink1[:, 192:].mean())
        self.assertTrue(set(np.unique(screens["S1"])) <= {0, 255})

    def test_cmyk_plus_spot_knocks_out_process_inks(self):
        image = np.full((50, 50, 3), 200, dtype=np.uint8)
        image[10:30, 10:30] = (46, 16, 200)   # rojo 186 en BGR
        settings = JobSettings(mode="cmyk_spot", spot_colors=[{"id": "S1", "name": "PMS 186 C", "rgb": [200, 16, 46]}])
        channels, _, _ = render(image, None, settings)
        self.assertEqual(settings.channels()[-1], "S1")
        self.assertEqual(channels["S1"][20, 20], 255)
        self.assertEqual(channels["M"][20, 20], 0)
        self.assertEqual(channels["S1"][40, 40], 0)
        self.assertGreater(channels["K"][40, 40] + channels["C"][40, 40], 0)

    def test_simulated_process_adds_highlight_white_last(self):
        window = SimpleHalftoneApp()
        window._set_loaded_image(self._spot_test_image())
        window.garment_color = QtGui.QColor(0, 0, 0)
        window.mode_combo.setCurrentText("Color plano (spot)")
        window.spot_count_spin.setValue(2)
        window.prepare_simulated_process()
        settings = window.job_settings()
        self.assertEqual(settings.channel_name(settings.channels()[-1]), "Blanco de luces")
        self.assertEqual(settings.channels()[0], "W")
        self.assertTrue(all(sp["halftone"] for sp in settings.spot_colors))
        window.close()

    def test_batch_cli_processes_a_folder(self):
        from src import cli
        with tempfile.TemporaryDirectory() as folder:
            images = os.path.join(folder, "imagenes")
            os.makedirs(images)
            Image.new("RGB", (60, 40), (200, 40, 40)).save(os.path.join(images, "a.png"))
            Image.new("RGB", (40, 60), (40, 40, 200)).save(os.path.join(images, "b.jpg"))
            with open(os.path.join(images, "notas.txt"), "w") as f:
                f.write("no es imagen")
            config = os.path.join(folder, "trabajo.json")
            JobSettings(lpi=30, output_format="tiff", registration_guides=True, placement="fit",
                        paper_width_mm=60, paper_height_mm=60).save(config)
            out = os.path.join(folder, "salida")
            with mock.patch("sys.stdout", new=io.StringIO()):
                code = cli.main([config, images, "-o", out])
            produced = sorted(os.listdir(os.path.join(out, "a")))
            with open(os.path.join(out, "lote.json"), encoding="utf-8") as f:
                batch = json.load(f)
        self.assertEqual(code, 0)
        self.assertEqual(len(batch), 2)
        self.assertIn("POSITIVO_K.tif", produced)
        self.assertIn("configuracion.json", produced)

    # ---------------------------------------------------------------- perfiles ICC

    GRACOL = "GRACoL2006_Coated1v2.icc"

    def test_bundled_profiles_are_valid_and_classified(self):
        profiles = {info.name: info for info in icc.list_profiles()}
        self.assertTrue(profiles[self.GRACOL].usable_for_separation)
        self.assertTrue(profiles["AdobeRGB1998.icc"].usable_as_input)
        self.assertEqual(len(profiles[self.GRACOL].md5), 32)

    def test_icc_separation_follows_the_profile(self):
        image = np.zeros((1, 3, 3), dtype=np.uint8)
        image[0, 1] = (255, 255, 255)
        image[0, 2] = (128, 128, 128)
        settings = JobSettings(icc_profile=self.GRACOL)
        channels, _, _ = render(image, None, settings)
        black = [int(channels[c][0, 0]) for c in "CMYK"]
        self.assertEqual(black[3], 255)
        self.assertAlmostEqual(sum(black) / 2.55, icc.total_ink_limit(self.GRACOL), delta=1)
        self.assertEqual([int(channels[c][0, 1]) for c in "CMYK"], [0, 0, 0, 0])
        # Balance de grises de GRACoL: el gris neutro lleva más cian que magenta
        self.assertGreater(channels["C"][0, 2], channels["M"][0, 2])

    def test_icc_ink_limit_is_optional(self):
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        strict = JobSettings(icc_profile=self.GRACOL, icc_ink_limit=False, ink_limit=260)
        limited = JobSettings(icc_profile=self.GRACOL, icc_ink_limit=True, ink_limit=260)
        total = lambda ch: sum(int(ch[c][0, 0]) for c in "CMYK") / 2.55
        self.assertGreater(total(render(image, None, strict)[0]), 300)
        self.assertLessEqual(total(render(image, None, limited)[0]), 260.5)

    def test_icc_round_trip_keeps_in_gamut_colors(self):
        rgb = np.array([[[90, 140, 60], [200, 120, 80], [70, 90, 160]]], dtype=np.uint8)
        channels = icc.rgb_to_profile_cmyk(rgb[..., ::-1].copy(), self.GRACOL)
        proof = icc.cmyk_to_srgb(channels, self.GRACOL)
        difference = color.delta_e2000(color.rgb_to_lab(rgb.reshape(-1, 3)), color.rgb_to_lab(proof.reshape(-1, 3)))
        self.assertLess(float(difference.max()), 3.0)

    def test_intents_give_different_separations(self):
        saturated = np.array([[[255, 0, 0]]], dtype=np.uint8)   # azul fuera de gama (BGR)
        relative = icc.rgb_to_profile_cmyk(saturated, self.GRACOL, "Colorimétrico relativo")
        perceptual = icc.rgb_to_profile_cmyk(saturated, self.GRACOL, "Perceptual")
        self.assertNotEqual([int(relative[c][0, 0]) for c in "CMYK"], [int(perceptual[c][0, 0]) for c in "CMYK"])

    def test_import_profile_validates_and_copies(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"SERIGRAFIA_PROFILES_DIR": folder}):
            source = os.path.join(folder, "origen")
            os.makedirs(source)
            marca = os.path.join(source, "Marca_Textil.icc")
            with open(icc.find_profile(self.GRACOL), "rb") as src, open(marca, "wb") as dst:
                dst.write(src.read())
            info = icc.import_profile(marca)
            self.assertTrue(os.path.isfile(os.path.join(folder, "Marca_Textil.icc")))
            self.assertIn("Marca_Textil.icc", [p.name for p in icc.list_profiles()])
            bad = os.path.join(source, "roto.icc")
            with open(bad, "wb") as f:
                f.write(b"no es un perfil")
            with self.assertRaises(ValueError):
                icc.import_profile(bad)
        self.assertTrue(info.usable_for_separation)

    def test_untagged_image_uses_the_assumed_input_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "sin_perfil.png")
            Image.new("RGB", (4, 4), (0, 200, 0)).save(path)
            as_srgb = doc_input.load_document(path)
            as_adobe = doc_input.load_document(path, input_profile="AdobeRGB1998.icc")
        self.assertNotEqual(tuple(as_srgb.bgr[0, 0]), tuple(as_adobe.bgr[0, 0]))
        self.assertTrue(any("AdobeRGB1998" in note for note in as_adobe.notes))

    def test_embedded_profile_is_respected(self):
        from PIL import ImageCms
        adobe = ImageCms.getOpenProfile(icc.find_profile("AdobeRGB1998.icc"))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "adobe.jpg")
            Image.new("RGB", (4, 4), (0, 200, 0)).save(path, icc_profile=adobe.tobytes(), quality=100)
            document = doc_input.load_document(path)
        self.assertTrue(any("Adobe RGB" in note for note in document.notes))

    def test_cmyk_tiff_embeds_the_profile_and_films_are_labeled(self):
        settings = JobSettings(icc_profile=self.GRACOL, registration_guides=True, placement="fit",
                               paper_width_mm=80, paper_height_mm=60)
        image = np.full((40, 50, 3), 120, dtype=np.uint8)
        channels, screens, _ = render(image, None, settings)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "compuesto.tif")
            icc.save_cmyk_tiff(path, channels, settings.icc_profile, settings.dpi)
            with Image.open(path) as im:
                self.assertEqual(im.mode, "CMYK")
                embedded = im.info.get("icc_profile")
        self.assertIn("GRACoL", icc.embedded_description(embedded))
        film = output.finish_positive(screens["C"], "C", settings)
        self.assertGreater((film[:80] == 0).sum(), 0)

    def test_icc_films_carry_the_profile_values_after_screening(self):
        # Separación con GRACoL -> trama -> la cobertura de cada película reproduce el valor del perfil
        ramp = np.linspace(0, 255, 240, dtype=np.uint8)
        image = np.dstack([np.tile(ramp, (240, 1)), np.tile(ramp[:, None], (1, 240)), np.full((240, 240), 90, np.uint8)])
        settings = JobSettings(icc_profile=self.GRACOL, lpi=45, dpi=300)
        channels, screens, _ = render(image, None, settings)
        cell = 24
        for name in "CMYK":
            coverage = (screens[name] == 0).reshape(10, cell, 10, cell).mean(axis=(1, 3)) * 100
            target = channels[name].astype(np.float32).reshape(10, cell, 10, cell).mean(axis=(1, 3)) / 2.55
            self.assertLess(float(np.abs(coverage - target).mean()), 2.5, name)

    def test_icc_controls_in_the_window(self):
        window = SimpleHalftoneApp()
        index = window.icc_profile_combo.findData(self.GRACOL)
        self.assertGreaterEqual(index, 0)
        window.icc_profile_combo.setCurrentIndex(index)
        self.assertIn("320 %", window.icc_info_label.text())
        window._set_loaded_image(np.full((30, 30, 3), 90, dtype=np.uint8))
        window.process_cmyk()
        window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("proof"))
        settings = window.job_settings()
        self.assertEqual(settings.icc_profile, self.GRACOL)
        self.assertEqual(settings.icc_intent, "Colorimétrico relativo")
        window.close()

    def test_constants_support_the_ui_contract(self):
        self.assertTrue(LPI_VALUES)
        self.assertTrue(POINT_SHAPES)
        for unit in MEASUREMENT_UNITS.values():
            self.assertIn("to_mm_factor", unit)


if __name__ == "__main__":
    unittest.main()
