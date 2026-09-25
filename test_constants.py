#!/usr/bin/env python3
"""Verifica que las constantes usadas por la aplicación sean utilizables."""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

print("🧪 Probando constantes de la aplicación CMYK...")

try:
    # Importar el módulo principal
    from src.ui.main_window import PRINT_FORMATS, POINT_SHAPES, LPI_VALUES, MEASUREMENT_UNITS
    
    print("✅ Importación exitosa")
    
    # Verificar formatos de impresión
    print(f"\n📄 FORMATOS DE IMPRESIÓN ({len(PRINT_FORMATS)} disponibles):")
    for fmt_name in list(PRINT_FORMATS.keys())[:5]:  # Solo mostrar primeros 5
        fmt_info = PRINT_FORMATS[fmt_name]
        print(f"  • {fmt_name}: {fmt_info['width']}×{fmt_info['height']}mm @ {fmt_info['dpi_recommended']}DPI")
    if len(PRINT_FORMATS) > 5:
        print(f"  ... y {len(PRINT_FORMATS) - 5} más")
    
    # Verificar formas de punto
    print(f"\n🔵 FORMAS DE PUNTO ({len(POINT_SHAPES)} disponibles):")
    for shape_name, shape_value in POINT_SHAPES.items():
        print(f"  • {shape_name}: {shape_value}")
    
    # Verificar valores LPI
    print(f"\n📊 VALORES LPI ({len(LPI_VALUES)} disponibles):")
    for lpi_name, scale in LPI_VALUES.items():
        print(f"  • {lpi_name}: escala {scale}")
    
    # Verificar unidades
    print(f"\n📏 UNIDADES DE MEDIDA ({len(MEASUREMENT_UNITS)} disponibles):")
    for unit_name, unit_info in MEASUREMENT_UNITS.items():
        print(f"  • {unit_name} ({unit_info['label']}): rango {unit_info['range']}")
    
    print("\n🎯 RESULTADO: Todas las constantes se cargaron correctamente")
    print("La interfaz ahora debería mostrar todas las opciones disponibles")
    
except ImportError as e:
    print(f"❌ Error de importación: {e}")
except Exception as e:
    print(f"❌ Error general: {e}")

print("\n" + "="*60)
print("DIAGNÓSTICO COMPLETADO")
print("="*60)
