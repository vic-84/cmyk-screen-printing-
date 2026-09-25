"""
Salida de positivos: colocación en el lienzo, guías de registro y archivos
con la resolución incrustada.
"""

import os
import unicodedata

import cv2
import numpy as np
from PIL import Image

from .screening import halftone
from .separation import canvas_offset

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
    Coloca la trama en el lienzo (la película, del tamaño del papel) sin
    reescalarla: la imagen ya llega al tamaño que ocupa (separation.layout).
    Reescalar una trama cambia el LPI y deforma los puntos.
    """
    canvas_w, canvas_h = settings.paper_px
    margin = settings.guide_margin_px
    area_w, area_h = max(1, canvas_w - 2 * margin), max(1, canvas_h - 2 * margin)
    h, w = screen.shape
    if w > area_w or h > area_h:
        # No debería ocurrir: layout nunca deja el diseño más grande que el área útil
        top, left = max(0, (h - area_h) // 2), max(0, (w - area_w) // 2)
        screen = screen[top:top + area_h, left:left + area_w]
        h, w = screen.shape
    canvas = np.full((canvas_h, canvas_w), 255, dtype=np.uint8)
    x, y = canvas_offset(w, h, settings)
    canvas[y:y + h, x:x + w] = screen
    return canvas


def canvas_preview(rgb, settings, scale, background_rgb, max_side=1600):
    """
    Vista previa del diseño (RGB, a la escala de la vista) dentro del lienzo:
    fondo del color de la prenda, borde del lienzo y, con guías, el área útil
    punteada y las cruces. Así se ve dónde cae el diseño antes de exportar.
    """
    canvas_w, canvas_h = settings.paper_px
    view = min(scale, max_side / max(canvas_w, canvas_h))
    size = (max(1, round(canvas_w * view)), max(1, round(canvas_h * view)))
    background = np.asarray(background_rgb, dtype=np.uint8)
    canvas = np.empty((size[1], size[0], 3), dtype=np.uint8)
    canvas[:] = background
    h, w = rgb.shape[:2]
    if view != scale:
        factor = view / scale
        rgb = cv2.resize(rgb, (max(1, round(w * factor)), max(1, round(h * factor))), interpolation=cv2.INTER_AREA)
        h, w = rgb.shape[:2]
    full_w, full_h = round(w / view), round(h / view)
    x, y = canvas_offset(full_w, full_h, settings)
    x, y = min(round(x * view), size[0] - w), min(round(y * view), size[1] - h)
    canvas[max(0, y):max(0, y) + h, max(0, x):max(0, x) + w] = rgb[:size[1] - max(0, y), :size[0] - max(0, x)]
    # Línea que contrasta con la prenda
    line = (40, 40, 40) if int(background.astype(int).mean()) > 128 else (215, 215, 215)
    cv2.rectangle(canvas, (0, 0), (size[0] - 1, size[1] - 1), line, 1)
    margin = round(settings.guide_margin_px * view)
    if margin > 0:
        right, bottom = size[0] - 1 - margin, size[1] - 1 - margin
        for start, end in [((margin, margin), (right, margin)), ((right, margin), (right, bottom)),
                           ((right, bottom), (margin, bottom)), ((margin, bottom), (margin, margin))]:
            length = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
            for t in range(0, length, 8):
                a = t / max(length, 1)
                b = min(t + 4, length) / max(length, 1)
                p1 = (round(start[0] + (end[0] - start[0]) * a), round(start[1] + (end[1] - start[1]) * a))
                p2 = (round(start[0] + (end[0] - start[0]) * b), round(start[1] + (end[1] - start[1]) * b))
                cv2.line(canvas, p1, p2, line, 1)
        cross = max(4, round(settings.guide_cross_mm / 25.4 * settings.dpi * view) // 2)
        for cx, cy in [(margin, margin), (right, margin), (margin, bottom), (right, bottom)]:
            cv2.line(canvas, (cx - cross, cy), (cx + cross, cy), line, 1)
            cv2.line(canvas, (cx, cy - cross), (cx, cy + cross), line, 1)
    return canvas


def add_registration_guides(screen, channel, settings):
    """
    Positivo con las guías DENTRO del lienzo: cruces de registro en las
    esquinas del área útil, marcas de centro, identificación del canal
    (nombre, orden, LPI, ángulo, DPI) en el margen superior y la tira de
    control en el inferior. La película mide exactamente el lienzo.
    """
    dpi = settings.dpi
    margin = settings.guide_margin_px
    cross = int(settings.guide_cross_mm / 25.4 * dpi)
    thickness = max(1, round(dpi / 150))
    canvas = place_on_paper(screen, settings)
    canvas_h, canvas_w = canvas.shape
    right, bottom = canvas_w - margin, canvas_h - margin

    for cx, cy in [(margin, margin), (right, margin), (margin, bottom), (right, bottom)]:
        cv2.line(canvas, (cx - cross // 2, cy), (cx + cross // 2, cy), 0, thickness)
        cv2.line(canvas, (cx, cy - cross // 2), (cx, cy + cross // 2), 0, thickness)
        cv2.circle(canvas, (cx, cy), cross // 3, 0, thickness)

    center_x, center_y = canvas_w // 2, canvas_h // 2
    tick = cross // 3
    cv2.line(canvas, (center_x - tick, margin - tick), (center_x + tick, margin - tick), 0, thickness)
    cv2.line(canvas, (center_x - tick, bottom + tick), (center_x + tick, bottom + tick), 0, thickness)
    cv2.line(canvas, (margin - tick, center_y - tick), (margin - tick, center_y + tick), 0, thickness)
    cv2.line(canvas, (right + tick, center_y - tick), (right + tick, center_y + tick), 0, thickness)

    order = settings.channels()
    position = order.index(channel) + 1 if channel in order else 0
    label = (f"{position}/{len(order)} {settings.channel_name(channel)}  "
             f"{settings.lpi:g} LPI  ang {settings.channel_angle(channel):g}  {dpi} DPI")
    if settings.icc_profile and settings.mode in ('cmyk', 'cmyk_spot'):
        # Trazabilidad: perfil con el que se separó (lo exigen algunas marcas)
        label += f"  ICC {os.path.splitext(settings.icc_profile)[0][:28]}"
    text_height = max(10, int(margin * 0.22))
    put_text(canvas, label, (margin, max(text_height + 4, margin // 2)), text_height, max(1, thickness // 2))

    if settings.control_strip:
        add_control_strip(canvas, channel, settings, margin, bottom)
    return canvas


def add_control_strip(canvas, channel, settings, margin, area_bottom):
    """
    Tira 5-95 % en el margen inferior, tramada con la misma lineatura, ángulo
    y forma que el canal: sirve para revisar exposición y ganancia en la
    misma pantalla que se imprime.
    """
    dpi = settings.dpi
    patch = int(8 / 25.4 * dpi)
    height = max(8, int(min(margin * 0.45, 6 / 25.4 * dpi)))
    top = area_bottom + (margin - height) // 2
    left = margin
    for i, tone_value in enumerate(CONTROL_STRIP_TONES):
        x = left + i * (patch + patch // 5)
        if x + patch > canvas.shape[1] - margin:
            break
        block = np.full((height, patch), int(round(tone_value * 2.55)), dtype=np.uint8)
        canvas[top:top + height, x:x + patch] = halftone(
            block, settings.cell_px, settings.dot_shape, settings.channel_angle(channel))
        cv2.rectangle(canvas, (x, top), (x + patch - 1, top + height - 1), 0, 1)
    label_x = left + len(CONTROL_STRIP_TONES) * (patch + patch // 5)
    if label_x < canvas.shape[1] - margin:
        put_text(canvas, "5 10 25 50 75 90 95 %", (label_x, top + height), max(8, height // 2))


def finish_positive(screen, channel, settings):
    """Positivo final de un canal según los ajustes de salida."""
    if settings.registration_guides:
        film = add_registration_guides(screen, channel, settings)
    else:
        film = place_on_paper(screen, settings)
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
