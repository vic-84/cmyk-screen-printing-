# Origen: claude_test_fixed_lpi.py
# Sección: Variables globales de configuración y constantes


# Especificaciones técnicas detalladas de mallas serigráficas
MESH_SPECIFICATIONS = {
    90: {
        'count': 90, 'thread_diameter': 100, 'aperture': 173, 'open_area': 41,
        'ink_thickness': 166, 'weight': 75, 'theoretical_deposit': 68, 'tension': 56,
        'recommended_lpi': [25, 30, 35], 'max_lpi': 40,
        'applications': ['textil grueso', 'tintas espesas', 'efectos especiales'],
        'ink_types': ['plastisol', 'base agua gruesa', 'tintas de relieve']
    },
    110: {
        'count': 110, 'thread_diameter': 80, 'aperture': 149, 'open_area': 41,
        'ink_thickness': 130, 'weight': 64, 'theoretical_deposit': 53, 'tension': 37,
        'recommended_lpi': [30, 35, 40], 'max_lpi': 45,
        'applications': ['textil estándar', 'camisetas', 'sudaderas'],
        'ink_types': ['plastisol', 'base agua', 'tintas estándar']
    },
    120: {
        'count': 120, 'thread_diameter': 64, 'aperture': 143, 'open_area': 48,
        'ink_thickness': 110, 'weight': 48, 'theoretical_deposit': 53, 'tension': 35,
        'recommended_lpi': [35, 40, 45], 'max_lpi': 50,
        'applications': ['textil premium', 'algodón fino', 'poliéster'],
        'ink_types': ['plastisol fino', 'base agua', 'tintas híbridas']
    },
    135: {
        'count': 135, 'thread_diameter': 64, 'aperture': 125, 'open_area': 44,
        'ink_thickness': 100, 'weight': 51, 'theoretical_deposit': 39, 'tension': 40,
        'recommended_lpi': [40, 45, 50], 'max_lpi': 55,
        'applications': ['textil detallado', 'gráficos complejos'],
        'ink_types': ['plastisol fino', 'tintas de baja viscosidad']
    },
    150: {
        'count': 150, 'thread_diameter': 55, 'aperture': 114, 'open_area': 46,
        'ink_thickness': 105, 'weight': 45, 'theoretical_deposit': 48, 'tension': 31,
        'recommended_lpi': [45, 50, 55], 'max_lpi': 60,
        'applications': ['papel', 'cartón', 'materiales porosos', 'textil fino'],
        'ink_types': ['base agua', 'tintas solventes', 'UV']
    },
    180: {
        'count': 180, 'thread_diameter': 48, 'aperture': 90, 'open_area': 36,
        'ink_thickness': 78, 'weight': 39, 'theoretical_deposit': 33, 'tension': 34,
        'recommended_lpi': [50, 55, 60], 'max_lpi': 65,
        'applications': ['detalle fino', 'etiquetas', 'gráficos precisos'],
        'ink_types': ['tintas de baja viscosidad', 'UV', 'solventes']
    },
    200: {
        'count': 200, 'thread_diameter': 48, 'aperture': 72, 'open_area': 33,
        'ink_thickness': 74, 'weight': 56, 'theoretical_deposit': 25, 'tension': 34,
        'recommended_lpi': [55, 60, 65], 'max_lpi': 70,
        'applications': ['alta definición', 'electrónicos', 'cerámica'],
        'ink_types': ['tintas especiales', 'conductivas', 'cerámicas']
    }
}

# El LPI recomendado se calcula con la regla de 3.5 a 4.75 hilos por línea
# (antes los valores implicaban ~2.5-3 hilos por línea y el punto se perdía).
for _mesh, _specs in MESH_SPECIFICATIONS.items():
    _specs['recommended_lpi'] = [round(_mesh / 4.5), round(_mesh / 4), round(_mesh / 3.6)]
    _specs['max_lpi'] = int(_mesh / 3.5)

# Mallas comunes en hilos/pulgada y su equivalente aproximado en hilos/cm
COMMON_MESHES_TPI = [86, 110, 125, 140, 156, 160, 180, 196, 200, 230, 255, 280, 305, 355]

# Lineaturas ofrecidas en la interfaz (el campo también acepta otros valores)
LPI_OPTIONS = [20, 22, 25, 28, 30, 32, 35, 38, 40, 42, 45, 48, 50, 55, 60, 65, 70, 75, 85]
LPI_VALUES = {f"{lpi} LPI": lpi for lpi in LPI_OPTIONS}

# Clasificación por aplicación
APPLICATION_CATEGORIES = {
    'Textil Básico': {
        'meshes': [90, 110],
        'description': 'Camisetas gruesas, sudaderas, efectos especiales',
        'lpi_range': '19-31',
        'ink_deposit': 'Alto (50-70 micrones)'
    },
    'Textil Premium': {
        'meshes': [120, 135],
        'description': 'Algodón fino, poliéster, gráficos detallados',
        'lpi_range': '25-39',
        'ink_deposit': 'Medio (35-50 micrones)'
    },
    'Papel y Cartón': {
        'meshes': [150, 180],
        'description': 'Materiales porosos, etiquetas, packaging',
        'lpi_range': '32-51',
        'ink_deposit': 'Medio-Bajo (25-45 micrones)'
    },
    'Alta Definición': {
        'meshes': [200],
        'description': 'Electrónicos, cerámica, aplicaciones técnicas',
        'lpi_range': '42-57',
        'ink_deposit': 'Bajo (15-35 micrones)'
    }
}

# Formatos de impresión estándar con múltiples unidades
PRINT_FORMATS = {
    "A4 (210×297mm)": {"width": 210, "height": 297, "dpi_recommended": 300, "unit": "mm"},
    "A3 (297×420mm)": {"width": 297, "height": 420, "dpi_recommended": 300, "unit": "mm"},
    "A3+ (329×483mm)": {"width": 329, "height": 483, "dpi_recommended": 300, "unit": "mm"}, 
    "A2 (420×594mm)": {"width": 420, "height": 594, "dpi_recommended": 300, "unit": "mm"},
    "A1 (594×841mm)": {"width": 594, "height": 841, "dpi_recommended": 200, "unit": "mm"},
    "A0 (841×1189mm)": {"width": 841, "height": 1189, "dpi_recommended": 150, "unit": "mm"},
    "Letter (8.5×11in)": {"width": 8.5, "height": 11, "dpi_recommended": 300, "unit": "in"},
    "Legal (8.5×14in)": {"width": 8.5, "height": 14, "dpi_recommended": 300, "unit": "in"},
    "Tabloid (11×17in)": {"width": 11, "height": 17, "dpi_recommended": 300, "unit": "in"},
    "Póster 50×70cm": {"width": 50, "height": 70, "dpi_recommended": 150, "unit": "cm"},
    "Póster 70×100cm": {"width": 70, "height": 100, "dpi_recommended": 120, "unit": "cm"},
    "Banner 100×200cm": {"width": 100, "height": 200, "dpi_recommended": 100, "unit": "cm"},
    "Playera S (45×60cm)": {"width": 45, "height": 60, "dpi_recommended": 200, "unit": "cm"},
    "Playera M (50×70cm)": {"width": 50, "height": 70, "dpi_recommended": 200, "unit": "cm"},
    "Playera L (55×75cm)": {"width": 55, "height": 75, "dpi_recommended": 200, "unit": "cm"},
    "Playera XL (60×80cm)": {"width": 60, "height": 80, "dpi_recommended": 200, "unit": "cm"},
    "Personalizado": {"width": 210, "height": 297, "dpi_recommended": 300, "unit": "mm"}
}

# Unidades de medida disponibles
MEASUREMENT_UNITS = {
    "mm": {
        "label": "Milímetros",
        "name": "Milímetros",
        "symbol": "mm",
        "to_mm_factor": 1.0,
        "precision": 0,
        "step": 1,
        "range": (10, 5000)
    },
    "cm": {
        "label": "Centímetros",
        "name": "Centímetros",
        "symbol": "cm",
        "to_mm_factor": 10.0,
        "precision": 1,
        "step": 0.1,
        "range": (1.0, 500.0)
    },
    "in": {
        "label": "Pulgadas",
        "name": "Pulgadas",
        "symbol": "in",
        "to_mm_factor": 25.4,
        "precision": 2,
        "step": 0.01,
        "range": (0.5, 200.0)
    }
}

# Configuración de guías de registro
REGISTRATION_GUIDE_SETTINGS = {
    "margin_mm": 15,          # Margen desde el borde del papel
    "cross_size_mm": 8,       # Tamaño de las cruces de registro
    "corner_marks": True,     # Marcas en las esquinas
    "center_marks": True,     # Marcas en los centros de los lados
    "bleed_marks": True,      # Marcas de sangrado
    "color_bars": True,       # Barras de color para control
    "info_text": True         # Información técnica
}

POINT_SHAPES = {
    "Redonda": "circle",
    "Elíptica": "ellipse",
    "Cuadrada": "square",
    "Diamante": "diamond",
    "Lineal": "line"
}

# Ángulos para serigrafía: el juego offset (15/75/0/45) desplazado 7.5° para no
# alinear ninguna placa con los hilos de la malla (0°/90°/45°). Con punto redondo
# 90° equivale a 0°, por eso la base blanca no puede ir a 90° (chocaría con Y).
CMYK_ANGLES = {'C': 22.5, 'M': 52.5, 'Y': 7.5, 'K': 82.5, 'W': 37.5}

# Juegos de ángulos seleccionables
ANGLE_PRESETS = {
    "Serigrafía (offset + 7.5°)": dict(CMYK_ANGLES),
    # Juego clásico de offset: 0°/45°/90° coinciden con la geometría de la malla
    "Offset (15/75/0/45)": {'C': 15, 'M': 75, 'Y': 0, 'K': 45, 'W': 30},
    # Una sola tinta: se usa el ángulo en K (y en la base blanca)
    "Monocromo 22.5°": {**CMYK_ANGLES, 'K': 22.5, 'W': 67.5},
    "Monocromo 25°": {**CMYK_ANGLES, 'K': 25, 'W': 70},
}
CUSTOM_ANGLE_PRESET = "Personalizado"

# Técnicas de separación
SEPARATION_MODES = {
    "Cuatricromía (CMYK)": "cmyk",
    "Semitono (1 tinta)": "mono",
    "Color plano (spot)": "spot",
    "Color índice": "index",
    "Cuatricromía + planos": "cmyk_spot",
}
SPOT_PALETTE_MODES = ("spot", "index", "cmyk_spot")
CHANNEL_NAMES = ["C", "M", "Y", "K", "W"]  # Incluimos base blanca

# Configuraciones de resolución
RESOLUTION_ENHANCEMENT = {
    "Mantener original": {"factor": 1.0, "method": None},
    "Mejorar 150% (recomendado)": {"factor": 1.5, "method": "INTER_CUBIC"},
    "Mejorar 200% (alta calidad)": {"factor": 2.0, "method": "INTER_CUBIC"},
    "Mejorar 300% (máxima calidad)": {"factor": 3.0, "method": "INTER_LANCZOS4"},
    "Personalizado": {"factor": 2.0, "method": "INTER_CUBIC"}
}

# Configuración de base blanca
WHITE_BASE_SETTINGS = {
    "enabled": True,
    "lpi": 45,  # Lineatura específica para base blanca
    "opacity_threshold": 160,  # Valor (0-255) del canal más claro desde el que la base es 100 %
    "choke_pixels": 2,  # Contracción de la base para que no asome por los bordes
    "shape": "circle"  # Forma específica para base blanca
}

# Separación de color
GCR_AMOUNT = 0.8        # Fracción del gris común (min C,M,Y) que se pasa a negro
TOTAL_INK_LIMIT = 260   # Límite de tinta total (TAC) en %, textil: 240-280
# Agregar esto al inicio de tu archivo main_window.py o en constants.py

PRINT_FORMATS = {
    'A4': {
        'width': 210,    # mm
        'height': 297,   # mm
        'dpi_recommended': 300
    },
    'A3': {
        'width': 297,    # mm
        'height': 420,   # mm
        'dpi_recommended': 300
    },
    'A5': {
        'width': 148,    # mm
        'height': 210,   # mm
        'dpi_recommended': 300
    },
    'Letter': {
        'width': 216,    # mm
        'height': 279,   # mm
        'dpi_recommended': 300
    },
    'Legal': {
        'width': 216,    # mm
        'height': 356,   # mm
        'dpi_recommended': 300
    },
    'Tabloid': {
        'width': 279,    # mm
        'height': 432,   # mm
        'dpi_recommended': 300
    }
}


MEASUREMENT_UNITS = {
    'mm': {
        'label': 'Milímetros',
        'symbol': 'mm',
        'to_mm_factor': 1.0,
        'range': (1.0, 2000.0),
        'step': 1.0,
        'precision': 1
    },
    'cm': {
        'label': 'Centímetros', 
        'symbol': 'cm',
        'to_mm_factor': 10.0,
        'range': (0.1, 200.0),
        'step': 0.1,
        'precision': 1
    },
    'in': {
        'label': 'Pulgadas',
        'symbol': 'in',
        'to_mm_factor': 25.4,
        'range': (0.1, 78.0),
        'step': 0.1,
        'precision': 2
    }
}

def convert_units(value, from_unit, to_unit):
    """Convertir entre unidades de medida"""
    # Convertir todo a mm primero
    to_mm_factors = {
        'mm': 1.0,
        'cm': 10.0,
        'in': 25.4
    }
    
    if from_unit not in to_mm_factors or to_unit not in to_mm_factors:
        return value
    
    # Convertir a mm
    value_mm = value * to_mm_factors[from_unit]
    
    # Convertir a la unidad destino
    result = value_mm / to_mm_factors[to_unit]
    
    return result

REGISTRATION_GUIDE_SETTINGS = {
    "margin_mm": 15,          # Margen para guías en mm
    "cross_size_mm": 8,       # Tamaño de cruces de registro en mm
    "corner_marks": True,     # Marcas en esquinas
    "center_marks": True,     # Marcas centrales
    "text_info": True,        # Información de canal
    "line_thickness": 2       # Grosor de líneas
}

def format_dimension_display(width, height, unit):
    """Formatear dimensiones para mostrar"""
    unit_info = MEASUREMENT_UNITS.get(unit, {'symbol': unit})
    symbol = unit_info.get('symbol', unit)
    return f"{width:.1f}×{height:.1f}{symbol}"
