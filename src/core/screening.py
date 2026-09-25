"""
Generación de tramas de semitono (AM).

Convención de las máscaras: 0 = tinta (punto), 255 = sin tinta. Es la misma
que la del positivo impreso (negro = zona que bloquea la luz al insolar).
"""

from functools import lru_cache

import cv2
import numpy as np


def adjust_levels(channel, threshold):
    """
    Corrección gamma controlada por el umbral del canal (0-255).
    128 = sin cambio; menor = más tinta; mayor = menos tinta.
    """
    if threshold == 128:
        return channel
    # Los canales son cantidad de tinta: exponente < 1 sube la tinta en los medios tonos
    exponent = max(threshold, 1) / 128.0
    table = (((np.arange(256) / 255.0) ** exponent) * 255).astype(np.uint8)
    return cv2.LUT(channel, table)


def _spot_function(dx, dy, half, shape):
    """
    Forma del punto: valor bajo en el centro de la celda, alto en los bordes.
    dx, dy: posición respecto al centro de la celda; half: media celda.
    """
    if shape == 'line':
        return np.abs(dy)
    if shape == 'ellipse':
        return np.sqrt(dx**2 + (dy * 1.4)**2)  # eje menor = 1/1.4 del mayor
    if shape == 'diamond':
        return np.abs(dx) + np.abs(dy)
    if shape == 'square':
        # Punto cuadrado (típico del color índice): el lado crece con la tinta
        return np.maximum(np.abs(dx), np.abs(dy))
    return np.sqrt(dx**2 + dy**2)


CALIBRATION_BINS = 4096


@lru_cache(maxsize=None)
def _spot_calibration(shape):
    """
    Tabla que convierte el valor de la función de punto en su percentil dentro
    de la celda. Con ella la cobertura de tinta es igual al tono pedido para
    cualquier forma (sin calibrar, un 25 % salía al 15 % y un 75 % al 89 % con
    punto redondo). Devuelve (valor máximo de la función, tabla float32).
    """
    n = 512
    coords = (np.arange(n, dtype=np.float64) + 0.5) / n - 0.5
    dx, dy = np.meshgrid(coords, coords)
    reference = np.sort(_spot_function(dx, dy, 0.5, shape).ravel())
    corner = np.array([0.5]), np.array([0.5])
    max_value = float(max(reference[-1], _spot_function(*corner, 0.5, shape)[0]))
    bins = np.linspace(0.0, max_value, CALIBRATION_BINS)
    table = np.searchsorted(reference, bins, side='left') / reference.size
    return max_value, table.astype(np.float32)


BAND_ROWS = 512


def halftone(channel, cell_px, shape='circle', angle=0.0):
    """
    Trama un canal de tinta (uint8, 255 = 100 % de tinta).

    cell_px: tamaño de la celda en píxeles (DPI / LPI). Se usa en float para
    que el LPI real coincida con el pedido. Se procesa por franjas de filas
    para que un A3 a 1200 dpi (≈ 277 Mpx) no agote la memoria.
    """
    h, w = channel.shape
    out = np.empty((h, w), dtype=np.uint8)
    max_value, table = _spot_calibration(shape)
    scale_index = (CALIBRATION_BINS - 1) / max_value

    angle_rad = np.radians(angle)
    cos_a, sin_a = np.float32(np.cos(angle_rad)), np.float32(np.sin(angle_rad))
    xs = np.arange(w, dtype=np.float32)

    for top in range(0, h, BAND_ROWS):
        bottom = min(h, top + BAND_ROWS)
        x, y = np.meshgrid(xs, np.arange(top, bottom, dtype=np.float32))
        x_rot = x * cos_a + y * sin_a
        y_rot = -x * sin_a + y * cos_a
        # Posición dentro de la celda normalizada a -0.5 .. 0.5
        dx = (x_rot % cell_px) / cell_px - 0.5
        dy = (y_rot % cell_px) / cell_px - 0.5
        spot = _spot_function(dx, dy, 0.5, shape)
        # Percentil del valor dentro de la celda: 0 en el centro, 1 en el borde
        index = np.clip(spot * scale_index, 0, CALIBRATION_BINS - 1).astype(np.int32)
        pattern = table[index]
        ink_level = channel[top:bottom].astype(np.float32) / 255.0
        ink = (ink_level > pattern) | (ink_level >= 1.0)
        out[top:bottom] = np.where(ink, 0, 255)
    return out


def screen_channel(channel, name, settings, scale=1.0):
    """
    Aplica umbral, densidad, ganancia y rango tonal del canal y lo trama.
    scale < 1 genera una vista previa reducida con la misma cantidad de puntos
    por imagen (la celda se reduce en la misma proporción).
    """
    from .tone import apply_tone  # tone importa adjust_levels de este módulo
    toned = apply_tone(channel, name, settings)
    cell = max(2.0, settings.cell_px * scale)
    return halftone(toned, cell, settings.dot_shape, settings.channel_angle(name))
