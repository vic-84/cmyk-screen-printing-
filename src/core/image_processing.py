"""Preparación de la imagen de entrada: transparencia, ampliación y base blanca."""

import cv2
import numpy as np

INTERPOLATION = {
    'INTER_CUBIC': cv2.INTER_CUBIC,
    'INTER_LANCZOS4': cv2.INTER_LANCZOS4,
    'INTER_LINEAR': cv2.INTER_LINEAR,
    'INTER_NEAREST': cv2.INTER_NEAREST,
}


def prepare_image_for_processing(img):
    """Convierte imágenes con transparencia a BGR sobre fondo blanco."""
    if img is None:
        raise ValueError("La imagen no puede ser nula")

    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    if img.shape[2] != 4:
        return img

    alpha = img[:, :, 3:4].astype(np.float32) / 255.0
    bgr = img[:, :, :3].astype(np.float32)
    return np.clip(bgr * alpha + 255 * (1 - alpha), 0, 255).astype(np.uint8)


def enhance_image_resolution(img, factor=2.0, method='INTER_CUBIC'):
    """Amplía la imagen factor veces con la interpolación elegida (1.0 = sin cambio)."""
    if factor == 1.0 or method is None:
        return img.copy()
    h, w = img.shape[:2]
    return cv2.resize(img, (int(w * factor), int(h * factor)),
                      interpolation=INTERPOLATION.get(method, cv2.INTER_CUBIC))


def generate_white_base(img, threshold=160, choke=2, alpha=None):
    """
    Base blanca proporcional para prenda oscura.

    La cantidad de blanco sigue la luminosidad del color que va encima: 100 %
    bajo los colores claros o saturados, nada bajo los negros (ahí se deja ver
    la tela). Una máscara sólida ponía blanco también bajo las sombras y el
    negro salía grisáceo.

    threshold: valor (0-255) del canal más claro a partir del cual la base es
    100 %. alpha: máscara de transparencia; sin alfa se asume imagen opaca.
    """
    value = (img.max(axis=2) if img.ndim == 3 else img).astype(np.float32)

    # Sombras (< 10 %) sin base; desde 'threshold' base completa
    low = 0.10 * 255
    high = max(float(threshold), low + 1)
    base = np.clip((value - low) / (high - low), 0.0, 1.0)
    if alpha is not None:
        base *= alpha.astype(np.float32) / 255.0
    white_base_mask = (base * 255).astype(np.uint8)

    # Choke: la base se contrae para que no asome por los bordes del color
    # cuando el registro de prensa no es perfecto
    if choke > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (choke * 2 + 1, choke * 2 + 1))
        white_base_mask = cv2.erode(white_base_mask, kernel)
    return white_base_mask
