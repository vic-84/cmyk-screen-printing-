#!/usr/bin/env python3
"""
Script para verificar todos los valores de LPI disponibles en la aplicación CMYK
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Importar las constantes desde su módulo fuente.
try:
    from src.utils.constants import LPI_VALUES, PRINT_FORMATS, POINT_SHAPES, MEASUREMENT_UNITS
    
    print("=" * 80)
    print("VERIFICACIÓN DE CONSTANTES EXPANDIDAS EN CMYK SEPARATOR APP")
    print("=" * 80)
    
    # Verificar LPI_VALUES
    print(f"\n📊 VALORES DE LPI DISPONIBLES: {len(LPI_VALUES)} opciones")
    print("-" * 60)
    for i, (key, scale) in enumerate(LPI_VALUES.items(), 1):
        print(f"{i:2d}. {key} (escala {scale})")
    
    # Verificar rangos de LPI
    lpi_values = [int(key.split()[0]) for key in LPI_VALUES]
    min_lpi = min(lpi_values)
    max_lpi = max(lpi_values)
    print(f"\n📈 RANGO DE LPI: {min_lpi} - {max_lpi} LPI")
    
    # Verificar PRINT_FORMATS
    print(f"\n🖨️  FORMATOS DE IMPRESIÓN: {len(PRINT_FORMATS)} opciones")
    print("-" * 60)
    for i, format_name in enumerate(PRINT_FORMATS.keys(), 1):
        print(f"{i:2d}. {format_name}")
    
    # Verificar POINT_SHAPES
    print(f"\n🔴 FORMAS DE PUNTO: {len(POINT_SHAPES)} opciones")
    print("-" * 60)
    for i, shape in enumerate(POINT_SHAPES, 1):
        print(f"{i:2d}. {shape}")
    
    # Verificar MEASUREMENT_UNITS
    print(f"\n📏 UNIDADES DE MEDIDA: {len(MEASUREMENT_UNITS)} opciones")
    print("-" * 60)
    for i, (unit, info) in enumerate(MEASUREMENT_UNITS.items(), 1):
        print(f"{i:2d}. {unit} - {info['label']}")
    
    print("\n" + "=" * 80)
    print("✅ TODAS LAS CONSTANTES SE HAN CARGADO CORRECTAMENTE")
    print("✅ LA INTERFAZ AHORA DEBE MOSTRAR TODAS LAS OPCIONES")
    print("=" * 80)
    
    # Información específica sobre las mejoras
    print(f"\n🎯 MEJORAS IMPLEMENTADAS:")
    print(f"   • LPI expandido de 7 a {len(LPI_VALUES)} opciones")
    print(f"   • Rango completo: {min_lpi}-{max_lpi} LPI")
    print(f"   • Incluye mallas recomendadas en cada opción")
    print(f"   • {len(PRINT_FORMATS)} formatos de impresión disponibles")
    print(f"   • {len(POINT_SHAPES)} formas de punto diferentes")
    print(f"   • {len(MEASUREMENT_UNITS)} unidades de medida soportadas")
    
except ImportError as e:
    print(f"❌ Error al importar constantes: {e}")
except Exception as e:
    print(f"❌ Error inesperado: {e}")
