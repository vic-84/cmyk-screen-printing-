# Origen: claude_test_fixed_lpi.py
# Sección: Funciones de utilidad

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

def format_dimension_display(width, height, unit):
    """Formatear dimensiones para mostrar"""
    unit_info = MEASUREMENT_UNITS[unit]
    precision = unit_info["precision"]
    symbol = unit_info["symbol"]
    
    if precision == 0:
        return f"{int(width)}×{int(height)}{symbol}"
    else:
        return f"{width:.{precision}f}×{height:.{precision}f}{symbol}"