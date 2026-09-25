"""
Salida de positivos: colocación en el papel, guías de registro y archivos
con la resolución incrustada.
"""

import cv2
import numpy as np
from PIL import Image

CHANNEL_LABELS = {'C': 'CIAN', 'M': 'MAGENTA', 'Y': 'AMARILLO', 'K': 'NEGRO', 'W': 'BASE BLANCA'}


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
    # Las fuentes Hershey de OpenCV solo tienen ASCII: sin tildes ni "°"
    label = (f"{position}/{len(order)} {CHANNEL_LABELS.get(channel, channel)}  "
             f"{settings.lpi:g} LPI  ang {settings.angles.get(channel, 0):g}  {dpi} DPI")
    font_scale = dpi / 600
    text_y = max(margin // 2 + int(10 * font_scale), int(20 * font_scale))
    cv2.putText(canvas, label, (margin, text_y), cv2.FONT_HERSHEY_SIMPLEX,
                font_scale, 0, max(1, thickness // 2), cv2.LINE_AA)
    return canvas


def finish_positive(screen, channel, settings):
    """Positivo final de un canal según los ajustes de salida."""
    if settings.registration_guides:
        return add_registration_guides(screen, channel, settings)
    if settings.fit_to_paper:
        return place_on_paper(screen, settings)
    return screen


def save_png(path, image, dpi):
    """PNG con el DPI incrustado; sin él el RIP asume 72/96 dpi."""
    Image.fromarray(image).save(path, dpi=(dpi, dpi))


def save_pdf(path, images, dpi):
    """PDF con una página por canal, al tamaño físico correcto."""
    pages = [Image.fromarray(img).convert('L') for img in images]
    if pages:
        pages[0].save(path, save_all=True, append_images=pages[1:], resolution=dpi)
