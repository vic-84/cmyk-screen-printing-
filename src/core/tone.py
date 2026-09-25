"""
Curva de tono por canal, aplicada al canal continuo antes de tramar:

  densidad → umbral → compensación de ganancia de punto → rango tonal

Todos los valores son cantidad de tinta (0 = sin tinta, 255 = 100 %).
"""

import math

import cv2
import numpy as np

from .screening import adjust_levels


def dot_gain_exponent(gain_at_50):
    """
    Modelo de ganancia: impreso = 1 − (1 − película)^k, con k tal que un 50 %
    en la película imprime 50 % + ganancia. Es monótono para cualquier
    ganancia entre 0 y 50 puntos.
    """
    printed_50 = min(max(0.5 + gain_at_50 / 100.0, 0.5), 0.99)
    return math.log(1.0 - printed_50) / math.log(0.5)


def printed_from_film(film, gain_at_50):
    """Tono impreso esperado (0-1) para un tono en película (0-1)."""
    k = dot_gain_exponent(gain_at_50)
    return 1.0 - np.power(1.0 - np.asarray(film, dtype=np.float64), k)


def film_for_printed(printed, gain_at_50):
    """Tono en película que imprime el tono deseado (inversa del modelo)."""
    k = dot_gain_exponent(gain_at_50)
    return 1.0 - np.power(1.0 - np.asarray(printed, dtype=np.float64), 1.0 / k)


def tone_lut(density=100.0, gain_at_50=0.0, min_dot=0.0, max_dot=100.0):
    """
    Tabla de 256 valores con densidad, compensación de ganancia y rango tonal.

    density: % de la tinta del canal (100 = sin cambio).
    min_dot / max_dot: en %. Por debajo del mínimo el punto no se sostiene en
    la malla y se elimina; por encima del máximo los puntos se cierran por la
    ganancia y se imprime sólido.
    """
    values = np.arange(256) / 255.0
    values = np.clip(values * density / 100.0, 0.0, 1.0)
    if gain_at_50 > 0:
        values = film_for_printed(values, gain_at_50)
    low, high = min_dot / 100.0, max_dot / 100.0
    values = np.where(values < low, 0.0, values)
    values = np.where(values > high, 1.0, values)
    return np.round(values * 255).astype(np.uint8)


def apply_tone(channel, name, settings):
    """Aplica umbral y curva de tono del trabajo a un canal continuo."""
    adjusted = adjust_levels(channel, settings.thresholds.get(name, 128))
    lut = tone_lut(settings.density.get(name, 100.0), settings.dot_gain,
                   settings.min_dot, settings.max_dot)
    return lut[adjusted]


def printed_ink(screen, gain_at_50, cell_px):
    """
    Cobertura de tinta esperada en la prenda (0-1 por píxel) a partir de la
    trama de la película, sumando la ganancia de punto de la prensa. Se usa en
    la simulación: con compensación, la película lleva puntos más chicos que
    vuelven a crecer al imprimir.
    """
    ink = (255 - screen).astype(np.float32) / 255.0
    if gain_at_50 <= 0:
        return ink
    sigma = max(0.5, cell_px * 0.35)
    coverage = cv2.GaussianBlur(ink, (0, 0), sigma)
    printed = printed_from_film(coverage, gain_at_50).astype(np.float32)
    # El punto crece hacia el papel que lo rodea: la tinta extra se reparte
    # solo sobre el papel libre, así el promedio local queda en el tono impreso
    spread = np.clip((printed - coverage) / np.maximum(1.0 - coverage, 1e-3), 0.0, 1.0)
    return ink + (1.0 - ink) * spread
