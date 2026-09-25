"""
Separación de color plano (spot).

Cada píxel se asigna a la tinta más cercana en Lab (o a la prenda, que no se
imprime). Con la asignación exclusiva los colores no se solapan (knockout).

- Sólido: la tinta cubre 100 % donde es la más cercana.
- Con semitono: la cantidad de tinta sigue la cercanía al color, así los
  degradados y sombras de ese color salen como puntos.
- Trapping: cada color se expande bajo los colores más oscuros vecinos, para
  que un descalce no deje ver la prenda.
- Base blanca: bajo los colores marcados con base; los que no la necesitan
  (por ejemplo, el negro sobre prenda negra) quedan fuera ("underbase removal").
"""

import cv2
import numpy as np

from .color import rgb_to_lab
from .screening import BAND_ROWS

GARMENT_ID = '_prenda'


def default_needs_base(color_rgb, garment_rgb):
    """En prenda oscura, llevan base los colores más claros que la prenda."""
    garment_l = rgb_to_lab([garment_rgb])[0][0]
    color_l = rgb_to_lab([color_rgb])[0][0]
    return garment_l < 50 and color_l > garment_l + 10


def order_light_to_dark(spots):
    """Orden de impresión habitual: de claro a oscuro."""
    return sorted(spots, key=lambda spot: -float(rgb_to_lab([spot['rgb']])[0][0]))


def separate_spot(bgr, alpha, settings, scale=1.0):
    """
    Devuelve {id_de_tinta: canal uint8 (255 = 100 %)} y 'W' si hay base.
    """
    spots = settings.spot_colors
    h, w = bgr.shape[:2]
    if not spots:
        return {}

    palette_rgb = [spot['rgb'] for spot in spots] + [list(settings.garment_rgb)]
    palette_lab = rgb_to_lab(palette_rgb).astype(np.float32)          # (n+1, 3); la última es la prenda
    power = max(1.0, 24.0 / max(settings.spot_softness, 1.0))

    # uint8 (255 = 100 %): con 8 tintas en A3 a 600 dpi, float32 pasaría de 2 GB
    amounts = np.zeros((len(spots), h, w), dtype=np.uint8)
    nearest = np.empty((h, w), dtype=np.int16)
    for top in range(0, h, BAND_ROWS):
        bottom = min(h, top + BAND_ROWS)
        rgb = cv2.cvtColor(bgr[top:bottom], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
        distances = np.linalg.norm(lab[None, ...] - palette_lab[:, None, None, :], axis=-1)  # (n+1, filas, w)
        band_nearest = distances.argmin(axis=0)
        nearest[top:bottom] = band_nearest
        # Pesos para las tintas con semitono: distancia inversa, así un píxel a medio
        # camino entre la tinta y la prenda lleva ~50 % de tinta. Más reparto
        # (sigma) = exponente menor = transiciones más amplias.
        weights = np.power(np.maximum(distances, 0.5), -power)
        weights /= weights.sum(axis=0, keepdims=True)
        opacity = alpha[top:bottom].astype(np.float32) / 255.0 if alpha is not None else 1.0
        for i, spot in enumerate(spots):
            amount = weights[i] if spot.get('halftone') else (band_nearest == i).astype(np.float32)
            amounts[i, top:bottom] = np.round(np.clip(amount * opacity, 0, 1) * 255).astype(np.uint8)

    trap_px = int(round(settings.trap_mm / 25.4 * settings.dpi * scale))
    if trap_px > 0:
        _apply_trapping(amounts, nearest, spots, trap_px)

    channels = {spot['id']: amounts[i] for i, spot in enumerate(spots)}

    if settings.white_base:
        with_base = [i for i, spot in enumerate(spots) if spot.get('base', True)]
        base = amounts[with_base].max(axis=0) if with_base else np.zeros((h, w), np.uint8)
        choke = int(round(settings.white_base_choke_px * scale))
        if choke > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (choke * 2 + 1, choke * 2 + 1))
            base = cv2.erode(base, kernel)
        channels['W'] = base
    return channels


def _apply_trapping(amounts, nearest, spots, trap_px):
    """
    Expande cada tinta trap_px píxeles, pero solo sobre zonas de tintas más
    oscuras (la tinta oscura, impresa después, tapa el solapamiento). Nunca
    crece hacia la prenda. Modifica amounts en el lugar.
    """
    lightness = [float(rgb_to_lab([spot['rgb']])[0][0]) for spot in spots]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (trap_px * 2 + 1, trap_px * 2 + 1))
    original = amounts.copy()
    for i in range(len(spots)):
        darker = np.zeros(nearest.shape, dtype=bool)
        for j in range(len(spots)):
            if j != i and lightness[j] < lightness[i]:
                darker |= nearest == j
        if darker.any():
            grown = cv2.dilate(original[i], kernel)
            amounts[i] = np.where(darker, np.maximum(original[i], grown), original[i])
