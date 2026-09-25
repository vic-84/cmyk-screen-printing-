"""
Curva de tono por canal, aplicada al canal continuo antes de tramar
(mínimo, máximo y ganancia: los del canal si los tiene, si no los generales):

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


class GainModel:
    """
    Ganancia de punto de la prensa. Usa la curva medida con la plantilla
    (pares película % → impreso %) si existe; si no, el modelo de un solo
    valor al 50 %.
    """

    def __init__(self, gain_at_50=0.0, curve=None):
        self.gain_at_50 = gain_at_50
        self.curve = None
        if curve:
            points = sorted({(0.0, 0.0), (100.0, 100.0), *[(float(f), float(p)) for f, p in curve]})
            film = np.array([f for f, _ in points]) / 100.0
            printed = np.maximum.accumulate(np.array([p for _, p in points]) / 100.0)
            self.curve = (film, np.clip(printed, 0.0, 1.0))

    @classmethod
    def from_settings(cls, settings, channel=None):
        """Ganancia del canal (la suya si la tiene) o la general del trabajo."""
        tone = settings.tone_for(channel) if channel else {'dot_gain': settings.dot_gain,
                                                          'dot_gain_curve': settings.dot_gain_curve}
        return cls(tone['dot_gain'], tone['dot_gain_curve'])

    @property
    def active(self):
        return self.curve is not None or self.gain_at_50 > 0

    def printed(self, film):
        if self.curve is not None:
            return np.interp(film, *self.curve)
        return printed_from_film(film, self.gain_at_50) if self.gain_at_50 > 0 else np.asarray(film, dtype=np.float64)

    def film(self, printed):
        if self.curve is not None:
            film_points, printed_points = self.curve
            return np.interp(printed, printed_points, film_points)
        return film_for_printed(printed, self.gain_at_50) if self.gain_at_50 > 0 else np.asarray(printed, dtype=np.float64)


def tone_lut(density=100.0, gain=None, min_dot=0.0, max_dot=100.0):
    """
    Tabla de 256 valores con densidad, compensación de ganancia y rango tonal.

    density: % de la tinta del canal (100 = sin cambio).
    gain: GainModel o ganancia al 50 % en puntos.
    min_dot / max_dot: en %. Por debajo del mínimo el punto no se sostiene en
    la malla y se elimina; por encima del máximo los puntos se cierran por la
    ganancia y se imprime sólido.
    """
    model = gain if isinstance(gain, GainModel) else GainModel(gain or 0.0)
    values = np.arange(256) / 255.0
    values = np.clip(values * density / 100.0, 0.0, 1.0)
    if model.active:
        values = model.film(values)
    low, high = min_dot / 100.0, max_dot / 100.0
    values = np.where(values < low, 0.0, values)
    values = np.where(values > high, 1.0, values)
    return np.round(values * 255).astype(np.uint8)


def apply_tone(channel, name, settings):
    """Aplica umbral y curva de tono del trabajo a un canal continuo."""
    adjusted = adjust_levels(channel, settings.thresholds.get(name, 128))
    tone = settings.tone_for(name)
    lut = tone_lut(settings.density.get(name, 100.0), GainModel.from_settings(settings, name),
                   tone['min_dot'], tone['max_dot'])
    return lut[adjusted]


def printed_ink(screen, gain, cell_px):
    """
    Cobertura de tinta esperada en la prenda (0-1 por píxel) a partir de la
    trama de la película, sumando la ganancia de punto de la prensa. Se usa en
    la simulación: con compensación, la película lleva puntos más chicos que
    vuelven a crecer al imprimir.
    """
    model = gain if isinstance(gain, GainModel) else GainModel(gain or 0.0)
    ink = (255 - screen).astype(np.float32) / 255.0
    if not model.active:
        return ink
    sigma = max(0.5, cell_px * 0.35)
    coverage = cv2.GaussianBlur(ink, (0, 0), sigma)
    printed = model.printed(coverage).astype(np.float32)
    # El punto crece hacia el papel que lo rodea: la tinta extra se reparte
    # solo sobre el papel libre, así el promedio local queda en el tono impreso
    spread = np.clip((printed - coverage) / np.maximum(1.0 - coverage, 1e-3), 0.0, 1.0)
    return ink + (1.0 - ink) * spread
