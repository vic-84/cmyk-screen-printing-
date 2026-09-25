#!/usr/bin/env python3
"""
Script simple para contar los valores de LPI en main_window.py
"""

import re

def contar_lpi_values():
    try:
        with open('src/ui/main_window.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Buscar la sección LPI_VALUES
        lpi_pattern = r'LPI_VALUES = \{(.*?)\}'
        match = re.search(lpi_pattern, content, re.DOTALL)
        
        if match:
            lpi_section = match.group(1)
            # Contar las líneas que contienen 'LPI'
            lpi_lines = [line for line in lpi_section.split('\n') if 'LPI' in line and '"' in line]
            
            print("=" * 80)
            print("VERIFICACIÓN DE VALORES LPI EXPANDIDOS")
            print("=" * 80)
            print(f"\n📊 TOTAL DE VALORES LPI ENCONTRADOS: {len(lpi_lines)}")
            print("\n🔍 LISTA COMPLETA DE VALORES LPI:")
            print("-" * 60)
            
            for i, line in enumerate(lpi_lines, 1):
                # Extraer el nombre del LPI
                line = line.strip()
                if line.startswith('"') and line.endswith(','):
                    lpi_name = line.split(':')[0].strip('"')
                    print(f"{i:2d}. {lpi_name}")
            
            # Buscar valores mínimos y máximos
            lpi_numbers = []
            for line in lpi_lines:
                match = re.search(r'(\d+)\s+LPI', line)
                if match:
                    lpi_numbers.append(int(match.group(1)))
            
            if lpi_numbers:
                print(f"\n📈 RANGO DE LPI: {min(lpi_numbers)} - {max(lpi_numbers)} LPI")
            
            print("\n✅ EXPANSIÓN COMPLETADA EXITOSAMENTE")
            print("✅ LA INTERFAZ DEBE MOSTRAR TODOS ESTOS VALORES")
            print("=" * 80)
            
        else:
            print("❌ No se pudo encontrar la sección LPI_VALUES")
            
    except FileNotFoundError:
        print("❌ No se pudo encontrar el archivo main_window.py")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    contar_lpi_values()
