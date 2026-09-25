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


def canvas_preview(rgb, settings, scale, background_rgb, max_side=1600, plan=None):
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
    if plan is not None and settings.registration_guides:
        _draw_plan_preview(canvas, settings, plan, view, line)
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


def _draw_plan_preview(canvas, settings, plan, view, line):
    """Cruces y recuadros de datos y tira donde plan_guides los colocó."""
    factor = view / plan.scale
    cross, (label_w, label_h), (strip_w, strip_h) = _blank_sizes(settings)
    arm = max(4, round(cross * view) // 2)
    for cx, cy in plan.crosses:
        x, y = round(cx * factor), round(cy * factor)
        cv2.line(canvas, (x - arm, y), (x + arm, y), line, 1)
        cv2.line(canvas, (x, y - arm), (x, y + arm), line, 1)
        cv2.circle(canvas, (x, y), max(2, arm * 2 // 3), line, 1)
    for placed, (w, h) in ((plan.label, (label_w, label_h)), (plan.strip, (strip_w, strip_h))):
        if placed is None:
            continue
        x, y, rotated = placed
        if rotated:
            w, h = h, w
        x, y = round(x * factor), round(y * factor)
        cv2.rectangle(canvas, (x, y), (x + max(1, round(w * view)), y + max(1, round(h * view))), line, 1)


def channel_label(channel, settings):
    """Identificación del canal en la película: orden, nombre, LPI, ángulo, DPI (e ICC)."""
    order = settings.channels()
    position = order.index(channel) + 1 if channel in order else 0
    label = (f"{position}/{len(order)} {settings.channel_name(channel)}  "
             f"{settings.lpi:g} LPI  ang {settings.channel_angle(channel):g}  {settings.dpi} DPI")
    if settings.icc_profile and settings.mode in ('cmyk', 'cmyk_spot'):
        # Trazabilidad: perfil con el que se separó (lo exigen algunas marcas)
        label += f"  ICC {os.path.splitext(settings.icc_profile)[0][:28]}"
    return label


def text_width(text, height_px, thickness=1):
    scale = max(0.3, height_px / 22.0)
    return cv2.getTextSize(ascii_label(text), cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, thickness))[0][0]


def draw_cross(canvas, cx, cy, cross, thickness):
    cv2.line(canvas, (cx - cross // 2, cy), (cx + cross // 2, cy), 0, thickness)
    cv2.line(canvas, (cx, cy - cross // 2), (cx, cy + cross // 2), 0, thickness)
    cv2.circle(canvas, (cx, cy), cross // 3, 0, thickness)


def add_registration_guides(screen, channel, settings, plan=None):
    """
    Positivo con las guías DENTRO del lienzo; la película mide exactamente el lienzo.

    En margen: cruces en las esquinas del área útil, marcas de centro, datos
    del canal en el margen superior y tira de control en el inferior.
    En espacios en blanco: cruces, datos y tira donde plan_guides encontró
    lugar sin tinta en todas las películas (el diseño no se reduce).
    """
    if settings.guides_in_blank:
        return _guides_in_blank(place_on_paper(screen, settings), channel, settings, plan)
    dpi = settings.dpi
    margin = settings.guide_margin_px
    cross = int(settings.guide_cross_mm / 25.4 * dpi)
    thickness = max(1, round(dpi / 150))
    canvas = place_on_paper(screen, settings)
    canvas_h, canvas_w = canvas.shape
    right, bottom = canvas_w - margin, canvas_h - margin

    for cx, cy in [(margin, margin), (right, margin), (margin, bottom), (right, bottom)]:
        draw_cross(canvas, cx, cy, cross, thickness)

    center_x, center_y = canvas_w // 2, canvas_h // 2
    tick = cross // 3
    cv2.line(canvas, (center_x - tick, margin - tick), (center_x + tick, margin - tick), 0, thickness)
    cv2.line(canvas, (center_x - tick, bottom + tick), (center_x + tick, bottom + tick), 0, thickness)
    cv2.line(canvas, (margin - tick, center_y - tick), (margin - tick, center_y + tick), 0, thickness)
    cv2.line(canvas, (right + tick, center_y - tick), (right + tick, center_y + tick), 0, thickness)

    text_height = max(10, int(margin * 0.22))
    put_text(canvas, channel_label(channel, settings), (margin, max(text_height + 4, margin // 2)),
             text_height, max(1, thickness // 2))

    if settings.control_strip:
        height = max(8, int(min(margin * 0.45, 6 / 25.4 * dpi)))
        draw_control_strip(canvas, channel, settings, margin, bottom + (margin - height) // 2, height,
                           canvas_w - margin)
    return canvas


def draw_control_strip(canvas, channel, settings, left, top, height, max_right):
    """
    Tira 5-95 %, tramada con la misma lineatura, ángulo y forma que el canal:
    sirve para revisar exposición y ganancia en la misma pantalla que se imprime.
    """
    patch = int(8 / 25.4 * settings.dpi)
    for i, tone_value in enumerate(CONTROL_STRIP_TONES):
        x = left + i * (patch + patch // 5)
        if x + patch > max_right:
            break
        block = np.full((height, patch), int(round(tone_value * 2.55)), dtype=np.uint8)
        canvas[top:top + height, x:x + patch] = halftone(
            block, settings.cell_px, settings.dot_shape, settings.channel_angle(channel))
        cv2.rectangle(canvas, (x, top), (x + patch - 1, top + height - 1), 0, 1)
    label_x = left + len(CONTROL_STRIP_TONES) * (patch + patch // 5)
    if label_x < max_right:
        put_text(canvas, STRIP_LABEL, (label_x, top + height), max(8, height // 2))


# ---------------------------------------------------------------- guías en espacios en blanco

STRIP_LABEL = "5 10 25 50 75 90 95 %"
BLANK_GRID_MM = 0.5          # rejilla de búsqueda de espacio libre
BLANK_CLEARANCE_MM = 2.0     # distancia mínima entre una cruz y la tinta
BLANK_TEXT_CLEARANCE_MM = 1.0   # entre los datos o la tira y la tinta
BLANK_TEXT_MM = 2.5          # altura del texto de identificación
BLANK_STRIP_MM = 5.0         # altura de la tira de control


class GuidePlan:
    """
    Dónde van las guías, en píxeles del lienzo a la escala en que se calculó.
    crosses: [(x, y)] (una por esquina encontrada); label/strip: (x, y) de la
    esquina superior izquierda o None; missing: lo que no tuvo lugar.
    """

    def __init__(self, scale):
        self.scale = scale
        self.crosses, self.label, self.strip, self.missing = [], None, None, []


def _blank_sizes(settings):
    """Tamaños en píxeles de salida: cruz, texto (el más largo de todos los canales) y tira."""
    dpi = settings.dpi
    mm = dpi / 25.4
    text_h = max(10, round(BLANK_TEXT_MM * mm))
    label_w = max(text_width(channel_label(ch, settings), text_h) for ch in settings.channels())
    patch = int(8 * mm)
    strip_h = max(8, round(BLANK_STRIP_MM * mm))
    strip_w = len(CONTROL_STRIP_TONES) * (patch + patch // 5) + text_width(STRIP_LABEL, max(8, strip_h // 2))
    return int(settings.guide_cross_mm * mm), (label_w, text_h), (strip_w, strip_h)


def _free_rect(blocked, rect_w, rect_h, from_bottom=False):
    """Esquina (x, y) del primer rectángulo sin nada bloqueado; arriba primero (o abajo)."""
    gh, gw = blocked.shape
    if rect_w > gw or rect_h > gh:
        return None
    integral = cv2.integral(blocked.astype(np.uint8))
    sums = (integral[rect_h:, rect_w:] - integral[:-rect_h, rect_w:]
            - integral[rect_h:, :-rect_w] + integral[:-rect_h, :-rect_w])
    ys, xs = np.nonzero(sums == 0)
    if len(ys) == 0:
        return None
    # Arriba (o abajo) y lo más a la izquierda posible
    i = np.lexsort((xs, -ys if from_bottom else ys))[0]
    return int(xs[i]), int(ys[i])


def plan_guides(screens, settings, scale=1.0):
    """
    Busca espacio sin tinta en TODAS las películas (el mismo lugar en cada una,
    si no las cruces no registran) para las 4 cruces, los datos del canal y la
    tira de control. Nunca se ponen sobre el diseño: si no hay lugar, se anota
    en plan.missing.
    """
    plan = GuidePlan(scale)
    canvas_w, canvas_h = settings.paper_px
    step = BLANK_GRID_MM / 25.4 * settings.dpi * scale            # px de lienzo por celda
    gw, gh = max(1, int(np.ceil(canvas_w * scale / step))), max(1, int(np.ceil(canvas_h * scale / step)))
    ink = np.zeros((gh, gw), dtype=bool)
    for screen in screens.values():
        h, w = screen.shape
        x, y = canvas_offset(round(w / scale), round(h / scale), settings)
        x0, y0 = int(x * scale / step), int(y * scale / step)
        cells = (max(1, int(np.ceil(w / step))), max(1, int(np.ceil(h / step))))
        small = cv2.resize((screen == 0).astype(np.uint8) * 255, cells, interpolation=cv2.INTER_AREA) > 0
        small = small[:gh - y0, :gw - x0]
        ink[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] |= small

    cells_per_mm = 1.0 / BLANK_GRID_MM
    clearance = BLANK_CLEARANCE_MM * cells_per_mm
    # Distancia de cada celda libre a la tinta más cercana (en celdas)
    to_ink = cv2.distanceTransform((~ink).astype(np.uint8), cv2.DIST_L2, 5)
    # Bloqueado para los datos y la tira: tinta y su alrededor (las cruces se agregan después)
    blocked = to_ink < BLANK_TEXT_CLEARANCE_MM * cells_per_mm

    cross, (label_w, label_h), (strip_w, strip_h) = _blank_sizes(settings)
    px_to_cells = 1.0 / (BLANK_GRID_MM / 25.4 * settings.dpi)     # px de salida → celdas
    radius = cross / 2 * px_to_cells
    edge = radius + 1 * cells_per_mm                               # la cruz entera cabe en la película
    ys, xs = np.mgrid[0:gh, 0:gw]
    fits = (to_ink >= radius + clearance) & (xs >= edge) & (ys >= edge) & (xs < gw - edge) & (ys < gh - edge)
    names = ("arriba izquierda", "arriba derecha", "abajo izquierda", "abajo derecha")
    for name, (cx, cy) in zip(names, [(0, 0), (gw, 0), (0, gh), (gw, gh)]):
        candidates = np.argwhere(fits)
        if len(candidates) == 0:
            plan.missing.append(f"cruz {name}")
            continue
        d = (candidates[:, 0] - cy) ** 2 + (candidates[:, 1] - cx) ** 2
        best = candidates[int(np.argmin(d))]
        # Si la cruz más cercana a esta esquina queda en la otra mitad del lienzo, no sirve
        if (cx == 0) != (best[1] < gw / 2) or (cy == 0) != (best[0] < gh / 2):
            plan.missing.append(f"cruz {name}")
            continue
        plan.crosses.append((round(best[1] * step), round(best[0] * step)))
        r = int(np.ceil(radius + clearance))
        blocked[max(0, best[0] - r):best[0] + r + 1, max(0, best[1] - r):best[1] + r + 1] = True
        fits[max(0, best[0] - 2 * r):best[0] + 2 * r + 1, max(0, best[1] - 2 * r):best[1] + 2 * r + 1] = False

    # Los datos y la tira pueden ir más cerca de la tinta que las cruces
    clearance = BLANK_TEXT_CLEARANCE_MM * cells_per_mm

    def place(width_px, height_px, from_bottom):
        """Primero en horizontal; si no cabe, en vertical (a lo largo de un costado)."""
        for rotated in (False, True):
            w_px, h_px = (height_px, width_px) if rotated else (width_px, height_px)
            rw = int(np.ceil(w_px * px_to_cells + 2 * clearance))
            rh = int(np.ceil(h_px * px_to_cells + 2 * clearance))
            corner = _free_rect(blocked, rw, rh, from_bottom)
            if corner is not None:
                blocked[corner[1]:corner[1] + rh, corner[0]:corner[0] + rw] = True
                return round((corner[0] + clearance) * step), round((corner[1] + clearance) * step), rotated
        return None

    plan.label = place(label_w, label_h, from_bottom=False)
    if plan.label is None:
        plan.missing.append("datos del canal")
    if settings.control_strip:
        plan.strip = place(strip_w, strip_h, from_bottom=True)
        if plan.strip is None:
            plan.missing.append("tira de control")
    return plan


def _paste(canvas, patch, x, y, rotated):
    """Pega un elemento (texto, tira) en el lienzo; en vertical se lee de abajo hacia arriba."""
    if rotated:
        patch = np.rot90(patch)
    h, w = patch.shape
    region = canvas[y:y + h, x:x + w]
    np.minimum(region, patch[:region.shape[0], :region.shape[1]], out=region)


def _guides_in_blank(canvas, channel, settings, plan):
    if plan is None:
        # Un solo canal: el lugar sale de esta película (usa finish_positives
        # para que las cruces caigan en el mismo punto en todas)
        plan = plan_guides({channel: canvas}, settings)
    dpi = settings.dpi
    cross, (label_w, label_h), (strip_w, strip_h) = _blank_sizes(settings)
    thickness = max(1, round(dpi / 150))
    factor = 1.0 / plan.scale
    for cx, cy in plan.crosses:
        draw_cross(canvas, round(cx * factor), round(cy * factor), cross, thickness)
    if plan.label is not None:
        x, y, rotated = plan.label
        patch = np.full((label_h + label_h // 3, label_w + 4), 255, dtype=np.uint8)
        put_text(patch, channel_label(channel, settings), (0, label_h), label_h, max(1, thickness // 2))
        _paste(canvas, patch, round(x * factor), round(y * factor), rotated)
    if plan.strip is not None:
        x, y, rotated = plan.strip
        patch = np.full((strip_h + 2, strip_w + 4), 255, dtype=np.uint8)
        draw_control_strip(patch, channel, settings, 0, 0, strip_h, patch.shape[1])
        _paste(canvas, patch, round(x * factor), round(y * factor), rotated)
    return canvas


def finish_positive(screen, channel, settings, plan=None):
    """Positivo final de un canal según los ajustes de salida."""
    if settings.registration_guides:
        film = add_registration_guides(screen, channel, settings, plan)
    else:
        film = place_on_paper(screen, settings)
    if settings.mirror:
        film = np.ascontiguousarray(film[:, ::-1])
    if settings.negative:
        film = 255 - film
    return film


def finish_positives(screens, settings):
    """
    Positivos de todos los canales. Con guías en espacios en blanco, el lugar
    de las guías se calcula una vez con todas las películas: las cruces caen
    en el mismo punto en todas. Devuelve ({canal: película}, plan o None).
    """
    plan = plan_guides(screens, settings) if settings.registration_guides and settings.guides_in_blank else None
    films = {channel: finish_positive(screens[channel], channel, settings, plan)
             for channel in settings.channels() if channel in screens}
    return films, plan


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
