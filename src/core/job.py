"""
Configuración completa de un trabajo de separación.

El motor (separation, screening, output) solo recibe un JobSettings; no lee la
interfaz. Así el mismo trabajo se puede repetir, guardar en JSON, procesar en
lote o servir desde otra interfaz.
"""

import json
from dataclasses import asdict, dataclass, field, fields

from ..utils.constants import (
    ANGLE_PRESETS, CMYK_ANGLES, GCR_AMOUNT, REGISTRATION_GUIDE_SETTINGS, TOTAL_INK_LIMIT,
    WHITE_BASE_SETTINGS,
)

PROCESS_CHANNELS = ('C', 'M', 'Y', 'K')
CHANNEL_NAMES = {'C': 'Cian', 'M': 'Magenta', 'Y': 'Amarillo', 'K': 'Negro', 'W': 'Base blanca'}
MONO_CHANNELS = ('K',)
NEUTRAL_THRESHOLD = 128


@dataclass
class JobSettings:
    # Técnica
    mode: str = 'cmyk'                   # cmyk | mono | spot | index | cmyk_spot

    # Malla y trama
    mesh_tpi: float = 200.0              # hilos por pulgada
    mesh_unit: str = 'in'                # unidad mostrada: 'in' (hilos/pulg) o 'cm'
    lpi: float = 45.0
    dot_shape: str = 'circle'            # circle | ellipse | square | diamond | line
    angle_preset: str = next(iter(ANGLE_PRESETS))
    angles: dict = field(default_factory=lambda: dict(CMYK_ANGLES))

    # Tono (en %): puntos fuera del rango se eliminan o se hacen sólidos
    min_dot: float = 0.0
    max_dot: float = 100.0
    dot_gain: float = 0.0                # ganancia medida a 50 %, en puntos
    dot_gain_curve: list = field(default_factory=list)  # [[película %, impreso %], ...]

    # Salida
    dpi: int = 300
    output_format: str = 'png'           # png | tiff (1 bit)
    mirror: bool = False                 # espejo (emulsión abajo)
    negative: bool = False
    control_strip: bool = True           # tira 5-95 % en el margen (requiere guías)
    paper_width_mm: float = 210.0
    paper_height_mm: float = 297.0
    fit_to_paper: bool = False
    registration_guides: bool = False
    guide_margin_mm: float = REGISTRATION_GUIDE_SETTINGS['margin_mm']
    guide_cross_mm: float = REGISTRATION_GUIDE_SETTINGS['cross_size_mm']

    # Separación
    gcr: float = GCR_AMOUNT
    # Gestión de color: con un perfil CMYK la separación la hace el perfil (GCR y TAC incluidos)
    icc_profile: str = ''                # nombre del perfil CMYK de salida; '' = fórmula GCR
    icc_intent: str = 'Colorimétrico relativo'
    icc_bpc: bool = True                 # compensación de punto negro
    icc_ink_limit: bool = False          # aplicar además el límite de tinta de la app
    input_profile: str = 'sRGB'          # perfil asumido para imágenes RGB sin perfil
    ink_type: str = "Plastisol de proceso"
    substrate: str = "Personalizado"
    ink_limit: float = TOTAL_INK_LIMIT   # %
    resolution_factor: float = 1.0
    resolution_method: str = 'INTER_CUBIC'

    # Base blanca
    white_base: bool = False
    white_base_threshold: int = WHITE_BASE_SETTINGS['opacity_threshold']
    white_base_choke_px: int = WHITE_BASE_SETTINGS['choke_pixels']

    # Color plano: [{'id', 'name', 'rgb', 'halftone', 'opaque', 'base', 'library'}]
    spot_colors: list = field(default_factory=list)
    garment_rgb: list = field(default_factory=lambda: [255, 255, 255])
    trap_mm: float = 0.0
    spot_softness: float = 12.0          # ΔE: cuánto se reparte una tinta con semitono
    spot_angle: float = 22.5
    despeckle_mm: float = 0.25           # tintas sólidas: se eliminan manchas menores (0 = no)
    spot_tolerance: float = 10.0         # ΔE: alcance de una tinta plana en CMYK + planos
    index_resolution: float = 150.0      # píxeles cuadrados por pulgada en color índice
    index_spread: float = 18.0           # ΔE: intensidad del tramado ordenado del índice

    # Canales: umbral 128 = sin ajuste; menor = más tinta. Densidad en %.
    thresholds: dict = field(default_factory=lambda: {c: NEUTRAL_THRESHOLD for c in 'CMYKW'})
    density: dict = field(default_factory=lambda: {c: 100.0 for c in 'CMYKW'})
    channel_order: list = field(default_factory=lambda: ['W', 'Y', 'C', 'M', 'K'])

    @property
    def cell_px(self):
        """Tamaño de la celda de trama en píxeles del positivo."""
        return self.dpi / self.lpi

    @property
    def paper_px(self):
        """Tamaño del papel en píxeles al DPI de salida (ancho, alto)."""
        return (int(self.paper_width_mm / 25.4 * self.dpi),
                int(self.paper_height_mm / 25.4 * self.dpi))

    def ink_channels(self):
        """Canales de tinta de la técnica actual (sin base blanca)."""
        if self.mode in ('spot', 'index'):
            return [spot['id'] for spot in self.spot_colors]
        if self.mode == 'cmyk_spot':
            return list(PROCESS_CHANNELS) + [spot['id'] for spot in self.spot_colors]
        return list(MONO_CHANNELS if self.mode == 'mono' else PROCESS_CHANNELS)

    def channels(self):
        """Canales que se generan, en orden de impresión (la base siempre primero)."""
        active = self.ink_channels() + (['W'] if self.white_base else [])
        ordered = [c for c in self.channel_order if c in active]
        missing = [c for c in active if c not in ordered]
        ordered += missing
        if 'W' in ordered:
            ordered.remove('W')
            ordered.insert(0, 'W')
        return ordered

    def spot(self, channel):
        return next((spot for spot in self.spot_colors if spot['id'] == channel), None)

    def channel_name(self, channel):
        spot = self.spot(channel)
        if spot:
            return spot.get('name') or channel
        return CHANNEL_NAMES.get(channel, channel)

    def channel_angle(self, channel):
        if self.spot(channel):
            return self.angles.get(channel, self.spot_angle)
        return self.angles.get(channel, 0.0)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in data.items() if k in known})
        # Rellenar canales que falten en archivos antiguos
        settings.angles = {**CMYK_ANGLES, **settings.angles}
        settings.thresholds = {**{c: NEUTRAL_THRESHOLD for c in 'CMYKW'}, **settings.thresholds}
        settings.density = {**{c: 100.0 for c in 'CMYKW'}, **settings.density}
        return settings

    def save(self, path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as f:
            return cls.from_dict(json.load(f))
