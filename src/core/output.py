"""
Salida de positivos: colocación en el papel, guías de registro y archivos
con la resolución incrustada.
"""

import unicodedata

import cv2
import numpy as np
from PIL import Image

from .screening import halftone

CONTROL_STRIP_TONES = (5, 10, 25, 50, 75, 90, 95)
SHAPE_NAMES = {'circle': 'redondo', 'ellipse': 'eliptico', 'square': 'cuadrado',
               'diamond': 'diamante', 'line': 'lineal'}


def ascii_label(text):
    """Las fuentes Hershey de OpenCV solo tienen ASCII: se quitan tildes y símbolos."""
    normalized = unicodedata.normalize('NFKD', str(text))
    return normalized.encode('ascii', 'ignore').decode('ascii').upper()


def put_text(canvas, text, origin, height_px, thickness=1):
    """Texto negro de altura aproximada height_px."""
    scale = max(0.3, height_px / 22.0)
    cv2.putText(canvas, ascii_label(text), origin, cv2.FONT_HERSHEY_SIMPLEX, scale, 0,
                max(1, thickness), cv2.LINE_AA)


def place_on_paper(screen, settings):
    """
    Centra la trama en el papel sin reescalarla. La imagen ya llega al tamaño
    de salida (separation.prepare_image); reescalar una trama cambia el LPI y
    deforma los puntos.
    """
    paper_w, paper_h = settings.paper_px
    h, w = screen.shape
    if w > paper_w or h > paper_h:
        # No debería ocurrir: prepare_image ajusta al papel cuando hay guías
        top, left = max(0, (h - paper_h) // 2), max(0, (w - paper_w) // 2)
        screen = screen[top:top + paper_h, left:left + paper_w]
        h, w = screen.shape
    paper = np.full((paper_h, paper_w), 255, dtype=np.uint8)
    y, x = (paper_h - h) // 2, (paper_w - w) // 2
    paper[y:y + h, x:x + w] = screen
    return paper


def add_registration_guides(screen, channel, settings):
    """
    Positivo con margen, cruces de registro en las esquinas del papel, marcas
    de centro e identificación del canal (nombre, orden, LPI, ángulo, DPI).
    """
    dpi = settings.dpi
    margin = int(settings.guide_margin_mm / 25.4 * dpi)
    cross = int(settings.guide_cross_mm / 25.4 * dpi)
    thickness = max(1, round(dpi / 150))
    paper_w, paper_h = settings.paper_px

    canvas = np.full((paper_h + 2 * margin, paper_w + 2 * margin), 255, dtype=np.uint8)
    canvas[margin:margin + paper_h, margin:margin + paper_w] = place_on_paper(screen, settings)

    corners = [(margin, margin), (margin + paper_w, margin),
               (margin, margin + paper_h), (margin + paper_w, margin + paper_h)]
    for cx, cy in corners:
        cv2.line(canvas, (cx - cross // 2, cy), (cx + cross // 2, cy), 0, thickness)
        cv2.line(canvas, (cx, cy - cross // 2), (cx, cy + cross // 2), 0, thickness)
        cv2.circle(canvas, (cx, cy), cross // 3, 0, thickness)

    center_x, center_y = margin + paper_w // 2, margin + paper_h // 2
    tick = cross // 3
    cv2.line(canvas, (center_x - tick, margin - tick), (center_x + tick, margin - tick), 0, thickness)
    cv2.line(canvas, (center_x - tick, margin + paper_h + tick), (center_x + tick, margin + paper_h + tick), 0, thickness)
    cv2.line(canvas, (margin - tick, center_y - tick), (margin - tick, center_y + tick), 0, thickness)
    cv2.line(canvas, (margin + paper_w + tick, center_y - tick), (margin + paper_w + tick, center_y + tick), 0, thickness)

    order = settings.channels()
    position = order.index(channel) + 1 if channel in order else 0
    label = (f"{position}/{len(order)} {settings.channel_name(channel)}  "
             f"{settings.lpi:g} LPI  ang {settings.angles.get(channel, 0):g}  {dpi} DPI")
    text_height = max(10, int(margin * 0.22))
    put_text(canvas, label, (margin, max(text_height + 4, margin // 2)), text_height, max(1, thickness // 2))

    if settings.control_strip:
        add_control_strip(canvas, channel, settings, margin, paper_h)
    return canvas


def add_control_strip(canvas, channel, settings, margin, paper_h):
    """
    Tira 5-95 % en el margen inferior, tramada con la misma lineatura, ángulo
    y forma que el canal: sirve para revisar exposición y ganancia en la
    misma pantalla que se imprime.
    """
    dpi = settings.dpi
    patch = int(8 / 25.4 * dpi)
    height = max(8, int(min(margin * 0.45, 6 / 25.4 * dpi)))
    top = margin + paper_h + (margin - height) // 2
    left = margin
    for i, tone_value in enumerate(CONTROL_STRIP_TONES):
        x = left + i * (patch + patch // 5)
        if x + patch > canvas.shape[1] - margin:
            break
        block = np.full((height, patch), int(round(tone_value * 2.55)), dtype=np.uint8)
        canvas[top:top + height, x:x + patch] = halftone(
            block, settings.cell_px, settings.dot_shape, settings.angles.get(channel, 0.0))
        cv2.rectangle(canvas, (x, top), (x + patch - 1, top + height - 1), 0, 1)
    label_x = left + len(CONTROL_STRIP_TONES) * (patch + patch // 5)
    if label_x < canvas.shape[1] - margin:
        put_text(canvas, "5 10 25 50 75 90 95 %", (label_x, top + height), max(8, height // 2))


def finish_positive(screen, channel, settings):
    """Positivo final de un canal según los ajustes de salida."""
    if settings.registration_guides:
        film = add_registration_guides(screen, channel, settings)
    elif settings.fit_to_paper:
        film = place_on_paper(screen, settings)
    else:
        film = screen
    if settings.mirror:
        film = np.ascontiguousarray(film[:, ::-1])
    if settings.negative:
        film = 255 - film
    return film


def dot_gain_template(settings, lpis=(20, 25, 30, 35, 40, 45, 50, 55, 60),
                      tones=(5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95)):
    """
    Plantilla para medir la ganancia de punto: una fila por lineatura y una
    columna por porcentaje, con la forma y el DPI del trabajo. Se imprime,
    se graba, se estampa en la tela real y se compara con lupa.
    """
    dpi = settings.dpi
    patch = int(12 / 25.4 * dpi)
    gap = patch // 6
    margin = int(15 / 25.4 * dpi)
    label_w = int(22 / 25.4 * dpi)
    text_h = max(10, patch // 4)
    width = margin * 2 + label_w + len(tones) * (patch + gap)
    height = margin * 2 + text_h * 3 + len(lpis) * (patch + gap)
    canvas = np.full((height, width), 255, dtype=np.uint8)

    put_text(canvas, f"PLANTILLA DE GANANCIA  {dpi} DPI  PUNTO {SHAPE_NAMES.get(settings.dot_shape, settings.dot_shape)}  ANG 22.5",
             (margin, margin), text_h)
    header_y = margin + text_h * 2
    for j, tone_value in enumerate(tones):
        put_text(canvas, f"{tone_value}%", (margin + label_w + j * (patch + gap), header_y), text_h)
    for i, lpi in enumerate(lpis):
        y = header_y + text_h + i * (patch + gap)
        put_text(canvas, f"{lpi} LPI", (margin, y + patch // 2 + text_h // 2), text_h)
        for j, tone_value in enumerate(tones):
            x = margin + label_w + j * (patch + gap)
            block = np.full((patch, patch), int(round(tone_value * 2.55)), dtype=np.uint8)
            canvas[y:y + patch, x:x + patch] = halftone(block, dpi / lpi, settings.dot_shape, 22.5)
    return canvas


def save_png(path, image, dpi):
    """PNG con el DPI incrustado; sin él el RIP asume 72/96 dpi."""
    Image.fromarray(image).save(path, dpi=(dpi, dpi))


def save_tiff_1bit(path, image, dpi):
    """TIFF de 1 bit con compresión CCITT G4, el formato habitual para RIP de película."""
    Image.fromarray(image).point(lambda v: 255 if v >= 128 else 0).convert('1').save(
        path, compression='group4', dpi=(dpi, dpi))


def save_positive(path_without_ext, image, settings):
    """Guarda el positivo en el formato del trabajo y devuelve el nombre del archivo."""
    if settings.output_format == 'tiff':
        path = path_without_ext + '.tif'
        save_tiff_1bit(path, image, settings.dpi)
    else:
        path = path_without_ext + '.png'
        save_png(path, image, settings.dpi)
    return path


def save_pdf(path, images, dpi):
    """PDF con una página por canal, al tamaño físico correcto."""
    pages = [Image.fromarray(img).convert('L') for img in images]
    if pages:
        pages[0].save(path, save_all=True, append_images=pages[1:], resolution=dpi)
