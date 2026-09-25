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
from .enhance import smooth_partition
from .screening import BAND_ROWS

GARMENT_ID = '_prenda'


def default_needs_base(color_rgb, garment_rgb):
    """En prenda oscura, llevan base los colores más claros que la prenda."""
    garment_l = rgb_to_lab([garment_rgb])[0][0]
    color_l = rgb_to_lab([color_rgb])[0][0]
    return garment_l < 50 and color_l > garment_l + 10


def order_light_to_dark(spots):
    """
    Orden de impresión habitual: de claro a oscuro. Las tintas marcadas
    'print_last' (p. ej. el blanco de luces del proceso simulado) van al final.
    """
    return sorted(spots, key=lambda spot: (bool(spot.get('print_last')),
                                           -float(rgb_to_lab([spot['rgb']])[0][0])))


BAYER_8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]], dtype=np.float32) / 64.0 - 0.5


def separate_index(bgr, alpha, settings, scale=1.0):
    """
    Color índice: la imagen se reduce a la paleta de tintas con tramado
    ordenado (Bayer 8×8) en una rejilla de píxeles cuadrados de
    index_resolution por pulgada. Cada píxel lleva una sola tinta, sin
    solapamientos: las películas son sólidas (sin semitono).
    """
    spots = settings.spot_colors
    h, w = bgr.shape[:2]
    if not spots:
        return {}
    cell = max(1.0, settings.dpi * scale / settings.index_resolution)
    grid_w, grid_h = max(1, round(w / cell)), max(1, round(h / cell))
    small = cv2.resize(bgr, (grid_w, grid_h), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)

    palette_lab = rgb_to_lab([spot['rgb'] for spot in spots] + [list(settings.garment_rgb)]).astype(np.float32)
    # Tramado ordenado: se perturba el color con la matriz de Bayer antes de elegir la tinta
    ys, xs = np.mgrid[0:grid_h, 0:grid_w]
    noise = BAYER_8[ys % 8, xs % 8][..., None] * settings.index_spread
    distances = np.linalg.norm((lab + noise)[None, ...] - palette_lab[:, None, None, :], axis=-1)
    nearest = distances.argmin(axis=0).astype(np.uint8)
    nearest = cv2.resize(nearest, (w, h), interpolation=cv2.INTER_NEAREST)

    channels = {}
    opacity = alpha >= 128 if alpha is not None else True
    for i, spot in enumerate(spots):
        channels[spot['id']] = np.where((nearest == i) & opacity, 255, 0).astype(np.uint8)
    if settings.white_base:
        with_base = [spots[i]['id'] for i in range(len(spots)) if spots[i].get('base', True)]
        base = np.zeros((h, w), dtype=np.uint8)
        for spot_id in with_base:
            base = np.maximum(base, channels[spot_id])
        choke = int(round(settings.white_base_choke_px * scale))
        if choke > 0:
            base = cv2.erode(base, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (choke * 2 + 1, choke * 2 + 1)))
        channels['W'] = base
    return channels


def spot_masks_for_cmyk(bgr, alpha, settings):
    """
    Tintas planas añadidas a una cuatricromía: cada una cubre los píxeles a
    menos de spot_tolerance (ΔE) de su color. Con semitono el borde se
    difumina hasta 2 × la tolerancia. Devuelve {id: canal} y la máscara
    total (0-1) para quitar esas zonas de C, M, Y, K (knockout).
    """
    spots = settings.spot_colors
    h, w = bgr.shape[:2]
    channels = {spot['id']: np.zeros((h, w), dtype=np.uint8) for spot in spots}
    knockout = np.zeros((h, w), dtype=np.float32)
    if not spots:
        return channels, knockout
    palette_lab = rgb_to_lab([spot['rgb'] for spot in spots]).astype(np.float32)
    tolerance = max(1.0, settings.spot_tolerance)
    for top in range(0, h, BAND_ROWS):
        bottom = min(h, top + BAND_ROWS)
        lab = cv2.cvtColor(cv2.cvtColor(bgr[top:bottom], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0,
                           cv2.COLOR_RGB2LAB)
        distances = np.linalg.norm(lab[None, ...] - palette_lab[:, None, None, :], axis=-1)
        nearest = distances.argmin(axis=0)
        opacity = alpha[top:bottom].astype(np.float32) / 255.0 if alpha is not None else 1.0
        for i, spot in enumerate(spots):
            if spot.get('halftone'):
                amount = np.clip((2 * tolerance - distances[i]) / tolerance, 0.0, 1.0)
            else:
                amount = (distances[i] < tolerance).astype(np.float32)
            amount = amount * (nearest == i) * opacity
            channels[spot['id']][top:bottom] = np.round(amount * 255).astype(np.uint8)
            knockout[top:bottom] = np.maximum(knockout[top:bottom], amount)
    return channels, knockout


def separate_spot(bgr, alpha, settings, scale=1.0, source_px=1.0):
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

    # Al ampliar una imagen chica cada píxel original es un escalón: se suavizan
    # los límites entre tintas sólidas (sin crear solapes ni huecos)
    solid = [i for i, spot in enumerate(spots) if not spot.get('halftone')]
    if settings.smooth_edges and solid and source_px > 1.3:
        layers = amounts[solid]            # copia: smooth_partition la modifica
        winner = smooth_partition(layers, 0.45 * source_px)
        if winner is not None:
            for k, i in enumerate(solid):
                amounts[i] = layers[k]
                nearest[winner == k] = i
            was_solid = np.isin(nearest, solid)
            nearest[(winner == len(solid)) & was_solid] = len(spots)

    speck_px = settings.despeckle_mm / 25.4 * settings.dpi * scale
    if speck_px >= 1:
        for i, spot in enumerate(spots):
            if not spot.get('halftone'):
                removed = remove_specks(amounts[i], speck_px)
                nearest[removed] = len(spots)   # la mota eliminada pasa a ser prenda

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


def remove_specks(channel, diameter_px):
    """
    Elimina en el lugar las manchas de tinta más chicas que un círculo de
    diameter_px (ruido JPEG, píxeles sueltos del antialias). Esas motas no se
    sostienen en la malla o se imprimen como suciedad. Devuelve la máscara
    de lo eliminado.
    """
    min_area = max(1, int(np.pi / 4 * diameter_px ** 2))
    ink = (channel >= 128).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    small = np.zeros(count, dtype=bool)
    small[1:] = stats[1:, cv2.CC_STAT_AREA] < min_area
    removed = small[labels]
    channel[removed] = 0
    return removed


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
