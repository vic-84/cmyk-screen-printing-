"""
Separación de color y preparación de la imagen a la resolución de salida.

Flujo: imagen → prepare_image (mejora + ajuste al papel, a DPI de salida)
       → separate_channels (C, M, Y, K y base blanca, 0-255 = % de tinta)
       → screening.screen_channel (trama por canal)
"""

import cv2
import numpy as np

from .image_processing import enhance_image_resolution, generate_white_base, resize_to_print_format
from .screening import BAND_ROWS, screen_channel
from .spot import separate_spot

PREVIEW_MAX_SIDE = 1600
PREVIEW_MIN_CELL_PX = 4.0


def _paper_format(settings):
    return {'width': settings.paper_width_mm, 'height': settings.paper_height_mm, 'name': 'papel'}


def needs_paper_fit(image_shape, settings):
    """Se ajusta al papel si se pide, o si hay guías y la imagen no cabe."""
    if settings.fit_to_paper:
        return True
    if settings.registration_guides:
        paper_w, paper_h = settings.paper_px
        return image_shape[1] > paper_w or image_shape[0] > paper_h
    return False


def prepare_image(image, alpha, settings):
    """
    Devuelve (bgr, alpha) a la resolución final del positivo. La trama se
    genera sobre esta imagen: si se tramara antes y luego se reescalara, el
    LPI real dependería de la foto.
    """
    working = enhance_image_resolution(image, settings.resolution_factor, settings.resolution_method)
    if alpha is not None:
        alpha = cv2.resize(alpha, (working.shape[1], working.shape[0]), interpolation=cv2.INTER_LINEAR)

    if needs_paper_fit(working.shape, settings):
        if alpha is None:
            # Imagen opaca: solo el margen agregado queda sin base
            alpha = np.full(working.shape[:2], 255, dtype=np.uint8)
        paper = _paper_format(settings)
        working = resize_to_print_format(working, paper, settings.dpi)
        alpha = resize_to_print_format(alpha, paper, settings.dpi, background=0)

    return working, alpha


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


def separate_channels(bgr, alpha, settings, scale=1.0):
    """Canales de tinta continuos (sin tramar) para la imagen preparada."""
    if settings.mode == 'spot':
        return separate_spot(bgr, alpha, settings, scale)
    if settings.mode == 'mono':
        # Semitono de una tinta: la cantidad de tinta sigue la oscuridad de la imagen
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        channels = {'K': 255 - gray}
    else:
        channels = separate_cmyk(bgr, settings.gcr, settings.ink_limit)
    if settings.white_base:
        channels['W'] = generate_white_base(
            bgr, settings.white_base_threshold,
            int(round(settings.white_base_choke_px * scale)), alpha)
    return channels


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

    channels = separate_channels(prepared, prepared_alpha, settings, scale)
    screens = {name: screen_channel(data, name, settings, scale) for name, data in channels.items()}
    return channels, screens, scale
