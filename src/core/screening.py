"""
Generación de tramas de semitono (AM).

Convención de las máscaras: 0 = tinta (punto), 255 = sin tinta. Es la misma
que la del positivo impreso (negro = zona que bloquea la luz al insolar).
"""

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


def halftone(channel, cell_px, shape='circle', angle=0.0):
    """
    Trama un canal de tinta (uint8, 255 = 100 % de tinta).

    cell_px: tamaño de la celda en píxeles (DPI / LPI). Se usa en float para
    que el LPI real coincida con el pedido.
    """
    h, w = channel.shape
    # float32: a tamaño de impresión (A3 @ 300 dpi ≈ 17 Mpx) float64 duplica la RAM
    x, y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))

    angle_rad = np.radians(angle)
    cos_a, sin_a = np.float32(np.cos(angle_rad)), np.float32(np.sin(angle_rad))
    x_rot = x * cos_a + y * sin_a
    y_rot = -x * sin_a + y * cos_a
    del x, y

    # Posición dentro de la celda, centrada en el punto (-cell/2 .. cell/2)
    dx = (x_rot % cell_px) - cell_px / 2
    dy = (y_rot % cell_px) - cell_px / 2
    del x_rot, y_rot

    # Patrón normalizado: 0 en el centro del punto, 1 en la esquina de la celda
    if shape == 'line':
        pattern = np.abs(dy) / (cell_px / 2)
    elif shape == 'ellipse':
        aspect = 1.4  # Eje menor = 1/1.4 del mayor; celda cuadrada, misma lineatura
        pattern = np.sqrt(dx**2 + (dy * aspect)**2)
        pattern /= np.sqrt((cell_px / 2)**2 + (cell_px / 2 * aspect)**2)
    elif shape == 'diamond':
        pattern = (np.abs(dx) + np.abs(dy)) / cell_px
    else:
        pattern = np.sqrt(dx**2 + dy**2)
        pattern /= (cell_px / np.sqrt(2))

    # La distancia radial crece más rápido que el área del punto; esta
    # corrección conserva mejor los medios tonos.
    ink_level = np.power(channel.astype(np.float32) / 255.0, 0.85)
    return np.where(ink_level < pattern, 255, 0).astype(np.uint8)


def screen_channel(channel, name, settings, scale=1.0):
    """
    Aplica el umbral del canal y lo trama con los ajustes del trabajo.
    scale < 1 genera una vista previa reducida con la misma cantidad de puntos
    por imagen (la celda se reduce en la misma proporción).
    """
    adjusted = adjust_levels(channel, settings.thresholds.get(name, 128))
    cell = max(2.0, settings.cell_px * scale)
    return halftone(adjusted, cell, settings.dot_shape, settings.angles.get(name, 0.0))
