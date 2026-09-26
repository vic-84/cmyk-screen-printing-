# Origen: claude_test_fixed_lpi.py
# Sección: Bloque de ejecución principal

import sys
from PyQt5 import QtWidgets
from src.ui.main_window import SimpleHalftoneApp

def main():
    """Función principal"""
    print("Iniciando aplicación Halftone...")
    
    try:
        app = QtWidgets.QApplication(sys.argv)
        app.setStyle('Fusion')
        
        print("QApplication creada")
        
        window = SimpleHalftoneApp()
        window.showMaximized()
        
        print("Ventana mostrada")
        print("La aplicación debería estar visible ahora")
        
        sys.exit(app.exec_())
        
    except Exception as e:
        print(f"Error crítico: {str(e)}")
        print(f"Tipo de error: {type(e).__name__}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()