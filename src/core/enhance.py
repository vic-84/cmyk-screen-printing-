"""
Mejora de la imagen de entrada antes de separar.

- Limpieza de ruido: quita el ruido y los halos de compresión JPEG, que en
  color plano se convierten en motas y bordes sucios. Se aplica a la
  resolución original (antes de ampliar), que es donde está el ruido.
- Enfoque (máscara de enfoque): recupera nitidez perdida al ampliar una
  imagen de poca resolución; útil en fotos para cuatricromía.
- Suavizado de bordes en tintas sólidas: al ampliar una imagen chica, cada
  píxel original se vuelve un escalón. Se suavizan los límites entre tintas
  sin crear solapes ni huecos (se decide la tinta de cada píxel con las
  máscaras suavizadas de todas a la vez).
"""

import cv2
import numpy as np

DENOISE_LEVELS = {'off': 0, 'light': 1, 'strong': 2}
NLM_MAX_PIXELS = 6_000_000   # más grande: filtro bilateral (NLM sería demasiado lento)


def denoise(bgr, level):
    """Quita ruido y artefactos JPEG conservando los bordes."""
    strength = DENOISE_LEVELS.get(level, level) if isinstance(level, str) else int(level)
    if strength <= 0:
        return bgr
    h = 4 if strength == 1 else 9
    if bgr.shape[0] * bgr.shape[1] <= NLM_MAX_PIXELS:
        return cv2.fastNlMeansDenoisingColored(bgr, None, h, h, 7, 21)
    return cv2.bilateralFilter(bgr, 7, 20 * strength, 5)


def sharpen(bgr, amount, radius_px=1.2):
    """Máscara de enfoque: amount en % (0 = sin cambio, 100 = fuerte)."""
    if amount <= 0:
        return bgr
    blurred = cv2.GaussianBlur(bgr, (0, 0), radius_px)
    return cv2.addWeighted(bgr, 1 + amount / 100.0, blurred, -amount / 100.0, 0)


def smooth_partition(amounts, sigma):
    """
    Suaviza los bordes de tintas sólidas exclusivas (uint8, 0/255, una capa
    por tinta) sin solapes ni huecos: se suaviza cada capa y la de la prenda,
    y cada píxel queda con la que domina. Devuelve el índice ganador por
    píxel (len(amounts) = prenda) y modifica amounts en el lugar.
    """
    if sigma < 0.3 or len(amounts) == 0:
        return None
    garment = 255 - np.clip(amounts.astype(np.int16).sum(axis=0), 0, 255).astype(np.uint8)
    best = cv2.GaussianBlur(garment, (0, 0), sigma).astype(np.int16)
    winner = np.full(garment.shape, len(amounts), dtype=np.int16)
    blurred = []
    for i in range(len(amounts)):
        layer = cv2.GaussianBlur(amounts[i], (0, 0), sigma).astype(np.int16)
        blurred.append(layer)
        take = layer > best
        winner[take] = i
        best = np.maximum(best, layer)
    for i in range(len(amounts)):
        amounts[i] = np.where(winner == i, 255, 0).astype(np.uint8)
    return winner


# ---------------------------------------------------------------- efecto desgastado

# Tamaño de las manchas del desgaste (mm) y su peso: grandes para romper el
# contorno, chicas para la textura. Se calcula a ~8 px/mm y se amplía después.
DISTRESS_OCTAVES = ((14.0, 0.45), (5.0, 0.30), (1.6, 0.15), (0.5, 0.10))
DISTRESS_PX_PER_MM = 8.0


def _value_noise(shape, feature_px, rng):
    h, w = shape
    small = rng.random((max(2, int(h / feature_px) + 2), max(2, int(w / feature_px) + 2))).astype(np.float32)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)


def distress_edges(alpha, shape, width_mm, px_per_mm, seed=7):
    """
    Efecto desgastado: el borde del diseño se rompe en manchas irregulares a lo
    largo de width_mm hacia adentro, para que la imagen no quede cuadrada. Deja
    espacio en blanco en el contorno (donde caben las guías de registro).

    alpha: máscara del diseño (None = rectángulo opaco). Devuelve la nueva alfa
    (uint8, bordes duros: la película no tiene grises). Misma semilla = mismo
    desgaste en todos los canales, en la vista previa y en la exportación.
    """
    h, w = shape[:2]
    if width_mm <= 0:
        return alpha
    work = min(1.0, DISTRESS_PX_PER_MM / max(px_per_mm, 1e-6))
    gw, gh = max(8, round(w * work)), max(8, round(h * work))
    mm_px = px_per_mm * (gw / w)               # px por mm en la rejilla de trabajo
    if alpha is None:
        opaque = np.full((gh, gw), 255, dtype=np.uint8)
    else:
        opaque = np.where(cv2.resize(alpha, (gw, gh), interpolation=cv2.INTER_AREA) >= 128, 255, 0).astype(np.uint8)
    # Distancia al borde del diseño (el contorno del lienzo cuenta como borde)
    padded = cv2.copyMakeBorder(opaque, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    distance = cv2.distanceTransform(padded, cv2.DIST_L2, 5)[1:-1, 1:-1] / mm_px
    # El 20 % exterior queda limpio: ahí caben las guías
    ramp = np.clip((distance / width_mm - 0.2) / 0.8, 0.0, 1.0)

    rng = np.random.default_rng(seed)
    noise = np.zeros((gh, gw), dtype=np.float32)
    for feature_mm, weight in DISTRESS_OCTAVES:
        noise += weight * _value_noise((gh, gw), max(1.0, feature_mm * mm_px), rng)
    z = (noise - noise.mean()) / max(float(noise.std()), 1e-6)
    uniform = 0.5 * (1 + np.tanh(0.7978845608 * (z + 0.044715 * z ** 3)))   # ≈ distribución uniforme
    keep = ((uniform < ramp) & (opaque > 0)).astype(np.uint8) * 255
    keep = cv2.morphologyEx(keep, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    # Islas sueltas de menos de ~1.5 mm: en la malla son suciedad, no desgaste
    count, labels, stats, _ = cv2.connectedComponentsWithStats(keep, connectivity=8)
    if count > 1:
        small = np.zeros(count, dtype=bool)
        small[1:] = stats[1:, cv2.CC_STAT_AREA] < np.pi / 4 * (1.5 * mm_px) ** 2
        keep[small[labels]] = 0
    keep = cv2.resize(keep, (w, h), interpolation=cv2.INTER_LINEAR)
    keep = np.where(keep >= 128, 255, 0).astype(np.uint8)
    if alpha is not None:
        keep = np.minimum(keep, alpha)
    return keep
