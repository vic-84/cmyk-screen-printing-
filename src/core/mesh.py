"""
Relación malla / lineatura.

Regla: cada línea de trama debe apoyarse en 3.5 a 4.75 hilos, es decir
LPI = malla (hilos/pulgada) ÷ 3.5 … ÷ 4.75. Con menos hilos por línea el
punto pequeño cae por la abertura y se pierden las luces.
"""

CM_PER_INCH = 2.54
MIN_THREADS_PER_LINE = 3.5
MAX_THREADS_PER_LINE = 4.75
INTEGER_RATIO_TOLERANCE = 0.05


def to_threads_per_inch(value, unit):
    """Convierte hilos/cm → hilos/pulgada ('in' se devuelve igual)."""
    return value * CM_PER_INCH if unit == 'cm' else value


def from_threads_per_inch(tpi, unit):
    return tpi / CM_PER_INCH if unit == 'cm' else tpi


def suggested_lpi_range(mesh_tpi):
    """(LPI mínimo, LPI máximo) recomendados para la malla."""
    return mesh_tpi / MAX_THREADS_PER_LINE, mesh_tpi / MIN_THREADS_PER_LINE


def threads_per_line(mesh_tpi, lpi):
    return mesh_tpi / lpi if lpi > 0 else 0.0


def is_integer_ratio(mesh_tpi, lpi):
    """
    True si malla/LPI es casi entero (p. ej. 120/30 = 4). Es una fuente
    habitual de moiré de malla; el ángulo de trama también influye.
    """
    ratio = threads_per_line(mesh_tpi, lpi)
    return ratio > 0 and abs(ratio - round(ratio)) < INTEGER_RATIO_TOLERANCE


def suggested_lpi(mesh_tpi):
    """
    LPI recomendado: el entero más cercano a malla ÷ 4 que no dé relación
    entera y quede dentro del rango recomendado.
    """
    low, high = suggested_lpi_range(mesh_tpi)
    target = mesh_tpi / 4.0
    candidates = [lpi for lpi in range(max(1, int(low)), int(high) + 2)
                  if low <= lpi <= high and not is_integer_ratio(mesh_tpi, lpi)]
    if not candidates:
        return round(target)
    return min(candidates, key=lambda lpi: (abs(lpi - target), lpi))


def assess(mesh_tpi, lpi):
    """
    Evaluación de la combinación malla/LPI para mostrar al usuario.
    Devuelve (nivel, mensaje) con nivel 'ok', 'aviso' o 'riesgo'.
    """
    ratio = threads_per_line(mesh_tpi, lpi)
    if ratio < MIN_THREADS_PER_LINE:
        return 'riesgo', (f"Malla muy abierta: {ratio:.1f} hilos por línea "
                          f"(mínimo {MIN_THREADS_PER_LINE}). Se perderán las luces.")
    if ratio > MAX_THREADS_PER_LINE + 1.25:
        return 'aviso', (f"{ratio:.1f} hilos por línea: la malla admite más lineatura "
                         f"(hasta {mesh_tpi / MIN_THREADS_PER_LINE:.0f} LPI).")
    if is_integer_ratio(mesh_tpi, lpi):
        return 'aviso', (f"Relación entera ({ratio:.2f}): riesgo de moiré con la malla. "
                         f"Prueba {suggested_lpi(mesh_tpi)} LPI.")
    return 'ok', f"{ratio:.1f} hilos por línea: dentro del rango recomendado."
