"""
Separación de color y preparación de la imagen a la resolución de salida.

Flujo: imagen → prepare_image (mejora + tamaño en el lienzo, a DPI de salida)
       → separate_channels (C, M, Y, K y base blanca, 0-255 = % de tinta)
       → screening.screen_channel (trama por canal)
"""

import cv2
import numpy as np

from .image_processing import enhance_image_resolution, generate_white_base
from . import enhance, icc
from .screening import BAND_ROWS, screen_channel
from .spot import separate_index, separate_spot, spot_masks_for_cmyk

PREVIEW_MAX_SIDE = 1600
PREVIEW_MIN_CELL_PX = 4.0


class Layout:
    """
    Colocación del diseño en el lienzo, en píxeles de salida.

    canvas: (ancho, alto) del lienzo = película. area: (x, y, ancho, alto) del
    área útil (el lienzo menos el margen de las guías). design: (ancho, alto)
    del diseño. offset: (x, y) de su esquina. reduced: el tamaño pedido no
    cabía y se redujo al área útil (nunca se corta).
    """

    def __init__(self, canvas, area, design, offset, reduced):
        self.canvas, self.area, self.design, self.offset, self.reduced = canvas, area, design, offset, reduced

    def mm(self, dpi):
        return self.design[0] / dpi * 25.4, self.design[1] / dpi * 25.4


def canvas_offset(design_w, design_h, settings):
    """Esquina del diseño en el lienzo: centrado, o arriba al centro."""
    canvas_w, canvas_h = settings.paper_px
    margin = settings.guide_margin_px
    area_w, area_h = max(1, canvas_w - 2 * margin), max(1, canvas_h - 2 * margin)
    x = margin + max(0, (area_w - design_w) // 2)
    y = margin if settings.align == 'top' else margin + max(0, (area_h - design_h) // 2)
    return x, y


def layout(image_shape, settings):
    """Dónde y a qué tamaño va la imagen dentro del lienzo."""
    canvas_w, canvas_h = settings.paper_px
    margin = settings.guide_margin_px
    area_w, area_h = max(1, canvas_w - 2 * margin), max(1, canvas_h - 2 * margin)
    h, w = image_shape[:2]
    fit = min(area_w / w, area_h / h)
    if settings.placement == 'width' and settings.design_width_mm > 0:
        scale = settings.design_width_mm / 25.4 * settings.dpi / w
    elif settings.placement == 'real':
        # Sin DPI conocido: 1 px de la imagen (ya mejorada) = 1 px de salida
        scale = settings.dpi / settings.source_dpi if settings.source_dpi > 0 else settings.resolution_factor
    else:
        scale = fit
    reduced = False
    if round(w * scale) > area_w or round(h * scale) > area_h:
        # No cabe en el área útil: se reduce conservando la proporción
        scale, reduced = fit, settings.placement != 'fit'
    design = (min(area_w, max(1, round(w * scale))), min(area_h, max(1, round(h * scale))))
    offset = canvas_offset(design[0], design[1], settings)
    return Layout((canvas_w, canvas_h), (margin, margin, area_w, area_h), design, offset, reduced)


def prepare_image(image, alpha, settings):
    """
    Devuelve (bgr, alpha) del diseño al tamaño final que ocupa en el lienzo,
    a la resolución de salida. La trama se genera sobre esta imagen: si se
    tramara antes y luego se reescalara, el LPI real dependería de la foto.
    La colocación en el lienzo la hace output.finish_positive.
    """
    # El ruido JPEG se limpia a la resolución original, donde está
    source = enhance.denoise(image, settings.denoise)
    working = enhance_image_resolution(source, settings.resolution_factor, settings.resolution_method)
    # La mejora de resolución solo aporta detalle: el tamaño lo decide el lienzo
    target = layout(image.shape, settings).design
    if target != (working.shape[1], working.shape[0]):
        shrink = target[0] < working.shape[1]
        working = cv2.resize(working, target, interpolation=cv2.INTER_AREA if shrink else cv2.INTER_CUBIC)
    if alpha is not None:
        alpha = cv2.resize(alpha, target, interpolation=cv2.INTER_LINEAR)
    if settings.distress_mm > 0:
        # En píxeles de salida: el ancho del desgaste es físico (mm en la prenda)
        alpha = enhance.distress_edges(alpha, working.shape, settings.distress_mm,
                                       settings.dpi / 25.4, settings.distress_seed)
    working = enhance.sharpen(working, settings.sharpen)
    return working, alpha


def design_size_mm(image_shape, settings):
    """Tamaño final del diseño impreso (ancho, alto) en mm, tal como saldrá en la película."""
    return layout(image_shape, settings).mm(settings.dpi)


def source_pixel_size(original_shape, prepared_shape):
    """Cuántos píxeles de salida ocupa un píxel de la imagen original."""
    return max(prepared_shape[0] / max(original_shape[0], 1), prepared_shape[1] / max(original_shape[1], 1))


def separate_cmyk(bgr, gcr, ink_limit):
    """
    CMYK con GCR y límite de tinta total. Devuelve C, M, Y, K en uint8
    (255 = 100 % de tinta). Se procesa por franjas para limitar la memoria.

    El gris común se RESTA de C, M, Y. La fórmula (1-R-K)/(1-K) con K reducido
    dejaba C=M=Y=100 % en el negro puro (390 % de tinta).
    """
    h, w = bgr.shape[:2]
    channels = {name: np.empty((h, w), dtype=np.uint8) for name in 'CMYK'}
    limit = ink_limit / 100.0
    for top in range(0, h, BAND_ROWS):
        bottom = min(h, top + BAND_ROWS)
        rgb = cv2.cvtColor(bgr[top:bottom], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        cmy = 1.0 - rgb
        k = cmy.min(axis=2) * gcr
        cmy -= k[..., np.newaxis]
        # Donde C+M+Y+K supera el límite, se reduce C, M, Y proporcionalmente
        reduction = np.clip((limit - k) / np.maximum(cmy.sum(axis=2), 1e-6), 0.0, 1.0)
        cmy *= reduction[..., np.newaxis]
        for i, name in enumerate('CMY'):
            channels[name][top:bottom] = (np.clip(cmy[..., i], 0, 1) * 255).astype(np.uint8)
        channels['K'][top:bottom] = (np.clip(k, 0, 1) * 255).astype(np.uint8)
    return channels


def limit_total_ink(channels, ink_limit):
    """Reduce C, M, Y donde C+M+Y+K supera el límite (K se conserva)."""
    k = channels['K'].astype(np.float32) / 255.0
    cmy = np.dstack([channels[c] for c in 'CMY']).astype(np.float32) / 255.0
    reduction = np.clip((ink_limit / 100.0 - k) / np.maximum(cmy.sum(axis=2), 1e-6), 0.0, 1.0)
    for i, name in enumerate('CMY'):
        channels[name] = np.round(cmy[..., i] * reduction * 255).astype(np.uint8)
    return channels


def process_channels(bgr, settings):
    """C, M, Y, K con el perfil ICC de salida si hay uno; si no, con la fórmula GCR."""
    if settings.icc_profile:
        channels = icc.rgb_to_profile_cmyk(bgr, settings.icc_profile, settings.icc_intent, settings.icc_bpc)
        if settings.icc_ink_limit:
            channels = limit_total_ink(channels, settings.ink_limit)
        return channels
    return separate_cmyk(bgr, settings.gcr, settings.ink_limit)


def separate_channels(bgr, alpha, settings, scale=1.0, source_px=1.0):
    """Canales de tinta continuos (sin tramar) para la imagen preparada."""
    if settings.mode == 'spot':
        return separate_spot(bgr, alpha, settings, scale, source_px)
    if settings.mode == 'index':
        return separate_index(bgr, alpha, settings, scale)
    if settings.mode == 'cmyk_spot':
        spot_channels, knockout = spot_masks_for_cmyk(bgr, alpha, settings)
        channels = process_channels(bgr, settings)
        keep = 1.0 - knockout
        for name in 'CMYK':
            channels[name] = np.round(channels[name] * keep).astype(np.uint8)
        channels.update(spot_channels)
    elif settings.mode == 'mono':
        # Semitono de una tinta: la cantidad de tinta sigue la oscuridad de la imagen
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        channels = {'K': 255 - gray}
    else:
        channels = process_channels(bgr, settings)
    if alpha is not None:
        # Fuera del diseño (transparente) no va tinta
        for name in list(channels):
            channels[name] = np.round(channels[name] * (alpha.astype(np.float32) / 255.0)).astype(np.uint8)
    if settings.white_base:
        channels['W'] = generate_white_base(
            bgr, settings.white_base_threshold,
            int(round(settings.white_base_choke_px * scale)), alpha)
    if settings.uses_garment_as_black:
        garment_as_black(channels, bgr, alpha, settings, scale)
    return channels


GARMENT_BLACK_WHITE_POINT = 0.85   # desde esta luz la base va al 100 %


def garment_as_black(channels, bgr, alpha, settings, scale=1.0):
    """
    Prenda como negro (4 estaciones en prenda oscura): se elimina la película K
    y la tela hace de negro.

    Sin negro impreso, la base es la que dibuja los grises: su cantidad sigue
    la luz de la imagen (puntos de blanco sobre la tela), no el canal K. Así
    el cabello, el humo y los encajes conservan su detalle.
    - Por debajo de garment_black_shadow % de luz no hay base: se ve la tela.
    - garment_black_boost aclara los grises intermedios.
    - C, M y Y se recortan donde no queda base: sobre la tela oscura sin base
      no se verían y solo gastarían tinta.
    Modifica channels en el lugar.
    """
    channels.pop('K', None)
    value = bgr.max(axis=2).astype(np.float32) / 255.0
    shadow = min(settings.garment_black_shadow / 100.0, GARMENT_BLACK_WHITE_POINT - 0.05)
    base = np.clip((value - shadow) / (GARMENT_BLACK_WHITE_POINT - shadow), 0.0, 1.0)
    base = np.power(base, 1.0 / (1.0 + settings.garment_black_boost / 100.0))
    if alpha is not None:
        base *= alpha.astype(np.float32) / 255.0
    base = np.round(base * 255).astype(np.uint8)
    choke = int(round(settings.white_base_choke_px * scale))
    if choke > 0:
        base = cv2.erode(base, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (choke * 2 + 1, choke * 2 + 1)))
    channels['W'] = base
    cover = np.clip(base.astype(np.float32) / 255.0 * 1.5, 0.0, 1.0)
    for name in 'CMY':
        channels[name] = np.round(channels[name] * cover).astype(np.uint8)


def preview_scale(prepared_shape, settings):
    """
    Escala de la vista previa: el lado mayor a ~1600 px, pero sin que la celda
    baje de 4 px (por debajo la trama ya no se lee en pantalla).
    """
    long_side = max(prepared_shape[:2])
    scale = max(PREVIEW_MAX_SIDE / long_side, PREVIEW_MIN_CELL_PX / settings.cell_px)
    return min(1.0, scale)


def render(image, alpha, settings, preview=False):
    """
    Ejecuta el trabajo completo. Devuelve (canales_continuos, tramas, escala).
    Con preview=True trabaja a resolución reducida; la exportación usa escala 1.
    """
    prepared, prepared_alpha = prepare_image(image, alpha, settings)
    scale = preview_scale(prepared.shape, settings) if preview else 1.0
    if scale < 1.0:
        size = (max(1, round(prepared.shape[1] * scale)), max(1, round(prepared.shape[0] * scale)))
        prepared = cv2.resize(prepared, size, interpolation=cv2.INTER_AREA)
        if prepared_alpha is not None:
            prepared_alpha = cv2.resize(prepared_alpha, size, interpolation=cv2.INTER_AREA)

    source_px = source_pixel_size(image.shape, prepared.shape)
    channels = separate_channels(prepared, prepared_alpha, settings, scale, source_px)
    screens = {name: screen_channel(data, name, settings, scale) for name, data in channels.items()}
    return channels, screens, scale
