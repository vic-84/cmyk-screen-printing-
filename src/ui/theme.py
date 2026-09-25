"""
Tema visual de la aplicación.

Criterio: la interfaz es gris neutro para no teñir la lectura del color; el
único color saturado en pantalla es la tinta de cada canal. La vista previa
va sobre gris medio (entorno estándar de preprensa para juzgar color).
"""

# Paleta
MESA = "#DADDDB"          # Fondo de la ventana
SUPERFICIE = "#F3F4F2"    # Paneles y campos
TEXTO = "#1E2427"         # Texto principal
TEXTO_SUAVE = "#5B6569"   # Texto secundario
LINEA = "#B8BEBD"         # Bordes
EMULSION = "#2F4690"      # Acento: acción principal y foco
EMULSION_OSCURA = "#233670"
GRIS_PREPRENSA = "#80827F"  # Entorno de la vista previa

# Estados (detector de moiré)
ESTADO_OK = "#2E6B45"
ESTADO_ALERTA = "#9A6414"
ESTADO_RIESGO = "#A3261D"

APP_QSS = f"""
QMainWindow, QDialog {{
    background: {MESA};
}}
QWidget {{
    color: {TEXTO};
    font-size: 10pt;
}}
QScrollArea#panelControles, QWidget#contenedorControles {{
    background: {MESA};
    border: none;
}}
QGroupBox {{
    background: {SUPERFICIE};
    border: 1px solid {LINEA};
    border-radius: 3px;
    margin-top: 1.4em;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    padding: 0 2px;
    color: {TEXTO};
    background: {MESA};
}}
QLabel {{
    background: transparent;
    font-weight: normal;
}}
QLabel[rol="secundario"] {{
    color: {TEXTO_SUAVE};
    font-size: 9pt;
}}
QLabel[rol="campo"] {{
    color: {TEXTO_SUAVE};
}}
QComboBox, QSpinBox, QDoubleSpinBox {{
    background: white;
    border: 1px solid {LINEA};
    border-radius: 3px;
    padding: 3px 6px;
    min-height: 22px;
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {EMULSION};
}}
QPushButton {{
    background: white;
    border: 1px solid {LINEA};
    border-radius: 3px;
    padding: 6px 12px;
    min-height: 22px;
}}
QPushButton:hover {{
    border-color: {TEXTO_SUAVE};
}}
QPushButton:focus {{
    border: 1px solid {EMULSION};
}}
QPushButton:disabled {{
    color: {TEXTO_SUAVE};
    background: {SUPERFICIE};
}}
QPushButton#accionPrincipal {{
    background: {EMULSION};
    border: 1px solid {EMULSION_OSCURA};
    color: white;
    font-weight: 600;
    min-height: 30px;
}}
QPushButton#accionPrincipal:hover {{
    background: {EMULSION_OSCURA};
}}
QPushButton#accionSecundaria {{
    font-weight: 600;
    min-height: 30px;
}}
QWidget#barraAcciones {{
    background: {SUPERFICIE};
    border-top: 1px solid {LINEA};
}}
QCheckBox {{
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {TEXTO_SUAVE};
    border-radius: 2px;
    background: white;
}}
QCheckBox::indicator:checked {{
    background: {EMULSION};
    border-color: {EMULSION_OSCURA};
}}
QCheckBox::indicator:disabled {{
    background: {SUPERFICIE};
    border-color: {LINEA};
}}
QCheckBox:disabled {{
    color: {TEXTO_SUAVE};
}}
QListWidget {{
    background: white;
    border: 1px solid {LINEA};
    border-radius: 3px;
    outline: none;
}}
QTabWidget::pane {{
    border: 1px solid {LINEA};
    border-radius: 3px;
    background: {SUPERFICIE};
    top: -1px;
}}
QTabBar::tab {{
    background: {MESA};
    border: 1px solid {LINEA};
    border-bottom: none;
    padding: 5px 14px;
    margin-right: 2px;
    border-top-left-radius: 3px;
    border-top-right-radius: 3px;
    color: {TEXTO_SUAVE};
}}
QTabBar::tab:selected {{
    background: {SUPERFICIE};
    color: {TEXTO};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {LINEA};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {EMULSION};
    width: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:disabled {{
    background: {LINEA};
}}
QSplitter::handle {{
    background: {MESA};
    width: 6px;
}}
QScrollArea#vistaPrevia {{
    background: {GRIS_PREPRENSA};
    border: 1px solid {LINEA};
}}
QLabel#vistaOriginal {{
    background: {GRIS_PREPRENSA};
    color: {SUPERFICIE};
    border-radius: 2px;
}}
QStatusBar {{
    background: {SUPERFICIE};
    border-top: 1px solid {LINEA};
    color: {TEXTO_SUAVE};
}}
QMenuBar {{
    background: {SUPERFICIE};
    border-bottom: 1px solid {LINEA};
}}
QMenuBar::item:selected, QMenu::item:selected {{
    background: {EMULSION};
    color: white;
}}
QToolTip {{
    background: {TEXTO};
    color: white;
    border: none;
    padding: 4px 6px;
}}
"""


def moire_style(nivel):
    """Color del borde lateral y del texto del detector de moiré según el riesgo."""
    return {
        'CRITICO': ESTADO_RIESGO,
        'ALTO': ESTADO_RIESGO,
        'MEDIO': ESTADO_ALERTA,
        'BAJO': ESTADO_OK,
    }.get(nivel, TEXTO_SUAVE)
