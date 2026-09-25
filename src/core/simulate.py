"""
Simulación del impreso sobre la prenda y herramientas de control de calidad.

La simulación compone las tintas en el orden de impresión. Cada tinta tiene
una opacidad: 0 = transparente (filtra la luz, se multiplica con lo que hay
debajo), 1 = cubriente (tapa lo de abajo). La base blanca siempre es cubriente.
"""

import math

import cv2
import numpy as np

from .tone import GainModel, apply_tone, printed_ink

# Opacidad típica por tipo de tinta (0 = transparente, 1 = cubriente)
INK_TYPES = {
    "Plastisol de proceso": 0.25,
    "Base agua": 0.10,
    "Plastisol cubriente": 0.85,
    "Transparente": 0.0,
}
DEFAULT_INK_TYPE = "Plastisol de proceso"

# Perfiles de sustrato: color de la prenda y ajustes de separación recomendados
SUBSTRATE_PROFILES = {
    "Algodón blanco": {"garment": [255, 255, 255], "white_base": False, "ink_limit": 260,
                       "min_dot": 8, "max_dot": 92, "dot_gain": 20},
    "Algodón negro": {"garment": [20, 20, 22], "white_base": True, "ink_limit": 240,
                      "min_dot": 10, "max_dot": 90, "dot_gain": 22},
    "Algodón de color": {"garment": [30, 60, 120], "white_base": True, "ink_limit": 250,
                         "min_dot": 10, "max_dot": 90, "dot_gain": 22},
    "Poliéster claro": {"garment": [245, 245, 245], "white_base": False, "ink_limit": 240,
                        "min_dot": 6, "max_dot": 94, "dot_gain": 15},
    "Papel / cartulina": {"garment": [250, 250, 245], "white_base": False, "ink_limit": 300,
                          "min_dot": 4, "max_dot": 96, "dot_gain": 10},
}

# Desplazamientos de la prueba de calce: cada tinta se corre en otra dirección
MISREGISTER_DIRECTIONS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]


def ink_opacity(channel, settings, default_opacity):
    if channel == 'W':
        return 1.0
    spot = settings.spot(channel)
    if spot is not None:
        return 0.85 if spot.get('opaque') else 0.0
    return default_opacity


def shift(mask, dx, dy):
    """Desplaza una máscara dx, dy píxeles (lo que entra queda sin tinta)."""
    if dx == 0 and dy == 0:
        return mask
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(mask, matrix, (mask.shape[1], mask.shape[0]),
                          flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def simulate(channels, screens, settings, ink_rgb, garment_rgb, scale=1.0, show_screen=True,
             opacity=INK_TYPES[DEFAULT_INK_TYPE], steps=None, misregister_mm=0.0):
    """
    Imagen RGB (uint8) de lo impreso.

    channels: canales continuos; screens: tramas (0 = tinta).
    ink_rgb: {canal: (r, g, b)}. steps: cuántas pasadas mostrar (None = todas).
    misregister_mm: corre cada tinta (salvo la base) en una dirección distinta
    para ver dónde asoma la base o la prenda con un descalce.
    """
    order = [ch for ch in settings.channels() if ch in screens]
    if steps is not None:
        order = order[:steps]
    h, w = next(iter(screens.values())).shape
    canvas = np.empty((h, w, 3), dtype=np.float32)
    canvas[:] = np.asarray(garment_rgb, dtype=np.float32) / 255.0

    gain = GainModel.from_settings(settings)
    offset_px = misregister_mm / 25.4 * settings.dpi * scale
    moving = 0
    for channel in order:
        if show_screen:
            ink = printed_ink(screens[channel], gain, settings.cell_px * scale)
        else:
            film = apply_tone(channels[channel], channel, settings)
            ink = gain.printed(film / 255.0).astype(np.float32)
        if offset_px and channel != 'W':
            dx, dy = MISREGISTER_DIRECTIONS[moving % len(MISREGISTER_DIRECTIONS)]
            ink = shift(ink, int(round(dx * offset_px)), int(round(dy * offset_px)))
            moving += 1
        color = np.asarray(ink_rgb[channel], dtype=np.float32) / 255.0
        alpha = ink_opacity(channel, settings, opacity)
        mask = ink[..., None]
        covered = alpha * color + (1.0 - alpha) * canvas * color
        canvas = canvas * (1.0 - mask) + covered * mask
    return np.clip(canvas * 255, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- control de calidad

def holdable_range(mesh_tpi, lpi, threads=1.5):
    """
    Rango de puntos que la malla sostiene (en %). Un punto de luz necesita un
    diámetro de al menos ~1.5 pasos de hilo para apoyarse; lo mismo vale para
    el hueco de un punto de sombra.
    """
    if mesh_tpi <= 0 or lpi <= 0:
        return 0.0, 100.0
    minimum = min(50.0, 100 * math.pi / 4 * (threads * lpi / mesh_tpi) ** 2)
    return minimum, 100.0 - minimum


def toned_channels(channels, settings):
    return {name: apply_tone(data, name, settings) for name, data in channels.items()}


def total_ink(toned, settings):
    """Tinta total (%) por píxel sumando las tintas de color (sin la base)."""
    inks = [toned[ch] for ch in settings.ink_channels() if ch in toned]
    if not inks:
        return None
    total = np.zeros(inks[0].shape, dtype=np.float32)
    for ink in inks:
        total += ink.astype(np.float32)
    return total / 2.55


def quality_report(channels, settings):
    """Resumen: tinta total y puntos que la malla no sostiene."""
    toned = toned_channels(channels, settings)
    tac = total_ink(toned, settings)
    low, high = holdable_range(settings.mesh_tpi, settings.lpi)
    lost = plugged = 0.0
    for channel, data in toned.items():
        percent = data.astype(np.float32) / 2.55
        lost = max(lost, float(((percent > 0.5) & (percent < low)).mean()))
        plugged = max(plugged, float(((percent > high) & (percent < 99.5)).mean()))
    return {
        'tac_max': float(tac.max()) if tac is not None else 0.0,
        'tac_over': float((tac > settings.ink_limit).mean()) if tac is not None else 0.0,
        'hold_min': low, 'hold_max': high,
        'lost': lost, 'plugged': plugged,
    }


def _dimmed(base_rgb):
    gray = cv2.cvtColor(base_rgb, cv2.COLOR_RGB2GRAY)
    return cv2.cvtColor((gray * 0.45 + 140).astype(np.uint8), cv2.COLOR_GRAY2RGB)


def tac_overlay(base_rgb, channels, settings):
    """Imagen en gris con rojo donde la tinta total supera el límite y ámbar cerca de él."""
    toned = toned_channels(channels, settings)
    tac = total_ink(toned, settings)
    out = _dimmed(base_rgb)
    if tac is None:
        return out
    near = (tac > settings.ink_limit * 0.9) & (tac <= settings.ink_limit)
    over = tac > settings.ink_limit
    out[near] = (230, 160, 40)
    out[over] = (200, 30, 30)
    return out


def dot_risk_overlay(base_rgb, channels, settings):
    """Naranja: puntos de luz que se perderán. Azul: sombras que se cerrarán."""
    toned = toned_channels(channels, settings)
    low, high = holdable_range(settings.mesh_tpi, settings.lpi)
    out = _dimmed(base_rgb)
    lost = np.zeros(out.shape[:2], dtype=bool)
    plugged = np.zeros(out.shape[:2], dtype=bool)
    for data in toned.values():
        percent = data.astype(np.float32) / 2.55
        lost |= (percent > 0.5) & (percent < low)
        plugged |= (percent > high) & (percent < 99.5)
    out[lost] = (235, 120, 20)
    out[plugged] = (40, 90, 200)
    return out
