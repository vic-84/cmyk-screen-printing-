"""Conversión de unidades de medida."""

from .constants import MEASUREMENT_UNITS

def convert_units(value, from_unit, to_unit):
    """Convertir entre diferentes unidades de medida"""
    if from_unit == to_unit:
        return value
    
    # Convertir a mm como unidad base
    mm_value = value * MEASUREMENT_UNITS[from_unit]["to_mm_factor"]
    
    # Convertir de mm a unidad destino
    result = mm_value / MEASUREMENT_UNITS[to_unit]["to_mm_factor"]
    
    return round(result, MEASUREMENT_UNITS[to_unit]["precision"])
