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
