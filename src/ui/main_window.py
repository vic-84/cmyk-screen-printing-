# Origen: claude_test_fixed_lpi.py
# Versión: FINAL CORREGIDA Y REESTRUCTURADA

import sys
import os
import io
import json
from datetime import datetime
import numpy as np
import cv2
from PIL import Image
from PyQt5 import QtWidgets, QtGui, QtCore
import re

# --- Importaciones de módulos locales (ajusta las rutas si es necesario) ---
# Se asume una estructura de carpetas como:
# main.py
# src/
#  - core/
#    - halftone.py
#    - image_processing.py
#  - ui/
#    - main_window.py  <-- ESTE ARCHIVO
#    - pdf_selector.py
#  - utils/
#    - constants.py
#    - helpers.py
#  - data/
#    - inks.json
#    - squeegees.json
#    - troubleshooting.json

from ..core.image_processing import detect_image_complexity, prepare_image_for_processing
from ..utils.constants import *
from ..utils.helpers import convert_units, format_dimension_display
from .pdf_selector import PDFPageSelector
from . import theme
from ..core.job import JobSettings
from ..core.screening import halftone, screen_channel
from ..core.separation import render
from ..core import mesh as mesh_rules
from ..core import output
from ..core import input as doc_input
from ..core import simulate as sim
from ..core.simulate import INK_TYPES, SUBSTRATE_PROFILES
from ..core.color import detect_palette, lab_to_rgb, match_library, read_library, rgb_to_lab, write_ase
from ..core.spot import default_needs_base, order_light_to_dark
from ..core import tone as tone_rules


# --- Verificación de dependencias ---
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
    print("✅ Soporte PDF habilitado (PyMuPDF)")
except ImportError:
    try:
        from pdf2image import convert_from_path
        PDF_SUPPORT = True
        print("✅ Soporte PDF habilitado (pdf2image)")
    except ImportError:
        PDF_SUPPORT = False
        print("⚠️ Soporte PDF no disponible. Instala: pip install PyMuPDF")

# =====================================================================
# == CLASES DE WIDGETS PERSONALIZADOS
# =====================================================================

class DraggableChannelList(QtWidgets.QListWidget):
    """
    Lista de canales que permite reordenar sus elementos mediante drag and drop.
    Emite una señal 'orderChanged' con la nueva lista de nombres de canales.
    """
    orderChanged = QtCore.pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)

    def dropEvent(self, event):
        """
        Sobrescribe el evento drop para emitir una señal con el nuevo orden.
        """
        super().dropEvent(event)
        new_order = [self.item(i).text() for i in range(self.count())]
        self.orderChanged.emit(new_order)


CHANNEL_NAMES = {'C': 'Cian', 'M': 'Magenta', 'Y': 'Amarillo', 'K': 'Negro', 'W': 'Base blanca'}


class ChannelScreenDelegate(QtWidgets.QStyledItemDelegate):
    """
    Pinta cada canal como una pantalla de la prensa: muestra de tinta, nombre y
    ángulo de trama. El texto del item sigue siendo la letra del canal ('C', 'M'...)
    porque el resto del código lo usa como identificador.
    """
    ROW_HEIGHT = 30

    def __init__(self, color_for_channel, angle_for_channel=None, parent=None, name_for_channel=None):
        super().__init__(parent)
        self._color_for_channel = color_for_channel
        self._name_for_channel = name_for_channel or (lambda ch: CHANNEL_NAMES.get(ch, ch))
        self._angle_for_channel = angle_for_channel or (lambda ch: CMYK_ANGLES.get(ch))

    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        channel = index.data(QtCore.Qt.DisplayRole)
        rect = option.rect
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        selected = option.state & QtWidgets.QStyle.State_Selected
        painter.fillRect(rect, QtGui.QColor("#DDE3F2") if selected else QtGui.QColor("white"))
        painter.setPen(QtGui.QColor(theme.LINEA))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if selected:
            painter.fillRect(QtCore.QRect(rect.left(), rect.top(), 3, rect.height()),
                             QtGui.QColor(theme.EMULSION))

        # Muestra de tinta
        swatch = QtCore.QRectF(rect.left() + 12, rect.top() + 6, 30, rect.height() - 12)
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.TEXTO_SUAVE), 1))
        painter.setBrush(self._color_for_channel(channel))
        painter.drawRoundedRect(swatch, 2, 2)

        # Nombre y ángulo
        text_rect = rect.adjusted(54, 0, -12, 0)
        painter.setPen(QtGui.QColor(theme.TEXTO))
        painter.drawText(text_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                         self._name_for_channel(channel))
        painter.setPen(QtGui.QColor(theme.TEXTO_SUAVE))
        angle = self._angle_for_channel(channel)
        if angle is not None:
            painter.drawText(text_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, f"{angle:g}°")
        painter.restore()


class AspectRatioPixmapLabel(QtWidgets.QLabel):
    """
    Un QLabel personalizado que escala su pixmap para ajustarse al tamaño del widget
    mientras mantiene la relación de aspecto original de la imagen.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pixmap = QtGui.QPixmap()
        self.setMinimumSize(1, 1)
        self.setAlignment(QtCore.Qt.AlignCenter)

    def setPixmap(self, pixmap):
        """Establece el pixmap original y actualiza la vista."""
        if isinstance(pixmap, QtGui.QPixmap):
            self._pixmap = pixmap
            self.update_pixmap()
        else:
            # Maneja el caso de que no sea un QPixmap (ej. texto inicial)
            super().setText(str(pixmap))
            self._pixmap = QtGui.QPixmap()

    def pixmap(self):
        """Devuelve el pixmap original (sin escalar)."""
        return self._pixmap

    def resizeEvent(self, event):
        """Maneja el evento de cambio de tamaño para reescalar el pixmap."""
        self.update_pixmap()
        super().resizeEvent(event)

    def update_pixmap(self):
        """
        Escala el pixmap para que se ajuste al tamaño actual del widget manteniendo
        la proporción y lo establece como el pixmap visible.
        """
        if self._pixmap.isNull():
            return

        scaled_pixmap = self._pixmap.scaled(
            self.size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        )
        super().setPixmap(scaled_pixmap)

class ZoomablePreviewLabel(QtWidgets.QScrollArea):
    """
    Widget de vista previa con zoom interactivo y navegación con mouse.
    Reemplaza el AspectRatioPixmapLabel para la vista previa.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Configuración básica
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setAlignment(QtCore.Qt.AlignCenter)
        
        # Label interno para mostrar la imagen
        self.image_label = QtWidgets.QLabel()
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.setObjectName("vistaPrevia")
        self.viewport().setStyleSheet(f"background: {theme.GRIS_PREPRENSA};")
        self.image_label.setStyleSheet(f"background: {theme.GRIS_PREPRENSA};")
        self.setWidget(self.image_label)
        
        # Variables de zoom
        self.zoom_factor = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 10.0
        self.zoom_step = 0.1
        
        # Imagen original
        self.original_pixmap = QtGui.QPixmap()
        
        # Configurar eventos
        self.setMouseTracking(True)
        self.image_label.setMouseTracking(True)
        
        # Variables para pan (arrastre)
        self.last_pan_point = QtCore.QPoint()
        self.is_panning = False
        
        # Crear controles de zoom
        self.setup_zoom_controls()
        
    def setup_zoom_controls(self):
        """Barra de zoom compacta superpuesta en la esquina superior derecha."""
        self.controls_widget = QtWidgets.QFrame(self)
        self.controls_widget.setObjectName("barraZoom")
        self.controls_widget.setStyleSheet(f"""
            QFrame#barraZoom {{
                background: {theme.SUPERFICIE};
                border: 1px solid {theme.LINEA};
                border-radius: 3px;
            }}
            QPushButton {{ padding: 0; min-height: 0; border-radius: 2px; }}
            QLabel {{ color: {theme.TEXTO_SUAVE}; font-size: 9pt; }}
        """)

        controls_layout = QtWidgets.QHBoxLayout(self.controls_widget)
        controls_layout.setContentsMargins(6, 4, 6, 4)
        controls_layout.setSpacing(4)

        self.zoom_out_btn = QtWidgets.QPushButton("−")
        self.zoom_out_btn.setFixedSize(26, 24)
        self.zoom_out_btn.setToolTip("Alejar")
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        controls_layout.addWidget(self.zoom_out_btn)

        self.zoom_label = QtWidgets.QLabel("100%")
        self.zoom_label.setAlignment(QtCore.Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(44)
        controls_layout.addWidget(self.zoom_label)

        self.zoom_in_btn = QtWidgets.QPushButton("+")
        self.zoom_in_btn.setFixedSize(26, 24)
        self.zoom_in_btn.setToolTip("Acercar")
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        controls_layout.addWidget(self.zoom_in_btn)

        self.zoom_fit_btn = QtWidgets.QPushButton("Ajustar")
        self.zoom_fit_btn.setFixedHeight(24)
        self.zoom_fit_btn.setToolTip("Ver la imagen completa")
        self.zoom_fit_btn.clicked.connect(self.zoom_to_fit)
        controls_layout.addWidget(self.zoom_fit_btn)

        self.zoom_100_btn = QtWidgets.QPushButton("1:1")
        self.zoom_100_btn.setFixedHeight(24)
        self.zoom_100_btn.setToolTip("Tamaño real en píxeles, para revisar la trama")
        self.zoom_100_btn.clicked.connect(self.zoom_to_100)
        controls_layout.addWidget(self.zoom_100_btn)

        self.controls_widget.adjustSize()
        self.position_controls()
        
    def position_controls(self):
        """Posicionar controles en la esquina superior derecha"""
        parent_rect = self.rect()
        x = parent_rect.width() - self.controls_widget.width() - 10
        y = 10
        self.controls_widget.move(x, y)
        
    def resizeEvent(self, event):
        """Reposicionar controles cuando cambia el tamaño"""
        super().resizeEvent(event)
        self.position_controls()
        
    def setPixmap(self, pixmap):
        """Establecer nueva imagen y resetear zoom"""
        if isinstance(pixmap, QtGui.QPixmap) and not pixmap.isNull():
            self.original_pixmap = pixmap
            self.zoom_factor = 1.0
            self.update_display()
            self.zoom_to_fit()  # Ajustar automáticamente
        else:
            # Manejar texto o imagen vacía
            self.original_pixmap = QtGui.QPixmap()
            self.image_label.setText(str(pixmap) if pixmap else "Sin imagen")
            
    def update_display(self):
        """Actualizar la imagen mostrada con el zoom actual"""
        if self.original_pixmap.isNull():
            return
            
        # Calcular nuevo tamaño
        new_size = self.original_pixmap.size() * self.zoom_factor
        
        # Escalar imagen
        scaled_pixmap = self.original_pixmap.scaled(
            new_size,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation if self.zoom_factor < 2.0 else QtCore.Qt.FastTransformation
        )
        
        # Mostrar imagen
        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.resize(scaled_pixmap.size())
        
        # Actualizar etiqueta de zoom
        zoom_percent = int(self.zoom_factor * 100)
        self.zoom_label.setText(f"{zoom_percent}%")
        
    def wheelEvent(self, event):
        """Zoom con rueda del mouse"""
        if event.modifiers() == QtCore.Qt.ControlModifier:
            # Zoom con Ctrl + rueda
            angle_delta = event.angleDelta().y()
            zoom_in = angle_delta > 0
            
            # Obtener posición del mouse para zoom centrado
            mouse_pos = event.pos()
            self.zoom_at_point(mouse_pos, zoom_in)
            
        else:
            # Scroll normal sin Ctrl
            super().wheelEvent(event)
            
    def zoom_at_point(self, point, zoom_in):
        """Zoom centrado en un punto específico"""
        old_zoom = self.zoom_factor
        
        # Calcular nuevo zoom
        if zoom_in:
            new_zoom = min(self.max_zoom, old_zoom * (1 + self.zoom_step))
        else:
            new_zoom = max(self.min_zoom, old_zoom * (1 - self.zoom_step))
            
        if new_zoom == old_zoom:
            return
            
        # Guardar posición del scroll
        h_scroll = self.horizontalScrollBar()
        v_scroll = self.verticalScrollBar()
        
        old_h = h_scroll.value()
        old_v = v_scroll.value()
        
        # Aplicar zoom
        self.zoom_factor = new_zoom
        self.update_display()
        
        # Ajustar scroll para mantener punto bajo el mouse
        zoom_ratio = new_zoom / old_zoom
        
        # Calcular nuevas posiciones de scroll
        new_h = int(old_h * zoom_ratio + (point.x() * (zoom_ratio - 1)))
        new_v = int(old_v * zoom_ratio + (point.y() * (zoom_ratio - 1)))
        
        h_scroll.setValue(new_h)
        v_scroll.setValue(new_v)
        
    def zoom_in(self):
        """Zoom in desde el centro"""
        center = self.rect().center()
        self.zoom_at_point(center, True)
        
    def zoom_out(self):
        """Zoom out desde el centro"""
        center = self.rect().center()
        self.zoom_at_point(center, False)
        
    def zoom_to_fit(self):
        """Ajustar imagen al tamaño del widget"""
        if self.original_pixmap.isNull():
            return
            
        # Calcular factor de escala para ajustar
        widget_size = self.viewport().size()
        pixmap_size = self.original_pixmap.size()
        
        scale_x = widget_size.width() / pixmap_size.width()
        scale_y = widget_size.height() / pixmap_size.height()
        
        # Usar el menor para que quepa completo
        fit_zoom = min(scale_x, scale_y, 1.0)  # No ampliar más de 100%
        
        self.zoom_factor = max(self.min_zoom, fit_zoom)
        self.update_display()
        
    def zoom_to_100(self):
        """Zoom al 100% (tamaño real)"""
        self.zoom_factor = 1.0
        self.update_display()
        
    def mousePressEvent(self, event):
        """Iniciar pan con clic del mouse"""
        if event.button() == QtCore.Qt.LeftButton:
            self.is_panning = True
            self.last_pan_point = event.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
        super().mousePressEvent(event)
        
    def mouseMoveEvent(self, event):
        """Pan (arrastre) de la imagen"""
        if self.is_panning:
            # Calcular desplazamiento
            delta = event.pos() - self.last_pan_point
            self.last_pan_point = event.pos()
            
            # Aplicar desplazamiento a las barras de scroll
            h_scroll = self.horizontalScrollBar()
            v_scroll = self.verticalScrollBar()
            
            h_scroll.setValue(h_scroll.value() - delta.x())
            v_scroll.setValue(v_scroll.value() - delta.y())
            
        super().mouseMoveEvent(event)
        
    def mouseReleaseEvent(self, event):
        """Finalizar pan"""
        if event.button() == QtCore.Qt.LeftButton:
            self.is_panning = False
            self.setCursor(QtCore.Qt.ArrowCursor)
        super().mouseReleaseEvent(event)
        
    def get_mouse_image_position(self, mouse_pos):
        """Convertir posición del mouse a coordenadas de la imagen original"""
        if self.original_pixmap.isNull():
            return None
            
        # Obtener posición relativa en el label de imagen
        label_pos = self.image_label.mapFromParent(mouse_pos)
        
        # Convertir a coordenadas de imagen original
        scaled_size = self.image_label.pixmap().size()
        original_size = self.original_pixmap.size()
        
        scale_x = original_size.width() / scaled_size.width()
        scale_y = original_size.height() / scaled_size.height()
        
        image_x = int(label_pos.x() * scale_x)
        image_y = int(label_pos.y() * scale_y)
        
        return QtCore.QPoint(image_x, image_y)

# =====================================================================
# == CLASE PRINCIPAL DE LA APLICACIÓN
# =====================================================================

def print_films(printer, films, dpi):
    """Dibuja cada película en una página al tamaño físico (px / dpi pulgadas)."""
    painter = QtGui.QPainter(printer)
    try:
        for i, film in enumerate(films):
            if i:
                printer.newPage()
            h, w = film.shape
            image = QtGui.QImage(np.ascontiguousarray(film).data, w, h, w, QtGui.QImage.Format_Grayscale8)
            scale = printer.resolution() / dpi
            painter.drawImage(QtCore.QRectF(0, 0, w * scale, h * scale), image)
    finally:
        painter.end()


class SimpleHalftoneApp(QtWidgets.QMainWindow):
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Asistente de Serigrafía Profesional")
        self.setGeometry(100, 100, 1400, 900)

        # --- 1. Inicialización de Variables de Trabajo (PRIMERO) ---
        self.db_inks = []
        self.db_squeegees = []
        self.db_troubleshooting = {}
        self.channel_color_buttons = {}
        self.image = None
        self.image_alpha = None
        self.image_info = None
        self.preview_cache = {}
        self.channel_arrays = {}
        self.preview_scale = 1.0
        self.dot_gain_curve = []
        self.spot_colors = []
        self.color_library = []
        self._next_spot_number = 1
        self.output_dir = "outputs"
        os.makedirs(self.output_dir, exist_ok=True)
        self.channel_order = ['W', 'Y', 'C', 'M', 'K']
        self.is_preview_updating = False
        self.current_channel = None
        
        # Timer para umbrales
        self.threshold_timer = QtCore.QTimer()
        self.threshold_timer.setSingleShot(True)
        self.threshold_timer.timeout.connect(self._delayed_threshold_update)

        # Detector de moiré - SOLO UNA VEZ
        self.moire_detector = MoireDetector()
        self.moire_warning = None

        # DEBUG: Prueba rápida
        print("🔍 Detector de moiré creado")
        test_analysis = self.moire_detector.analyze_moire_risk(25, 120, {})
        print(f"🧪 Prueba rápida LPI 25: {test_analysis['risk_level']}")

        # --- 2. Carga de Bases de Datos ---
        self.load_databases()

        # --- 3. Definición de Constantes y Colores ---
        # Aproximación sRGB de tintas de cuatricromía (process cyan/magenta/yellow).
        # Los primarios RGB puros (0,255,255 / 255,0,255) sobresaturan la simulación.
        self.PURE_CMYK_COLORS = {
            'C': QtGui.QColor(0, 174, 239), 'M': QtGui.QColor(236, 0, 140),
            'Y': QtGui.QColor(255, 242, 0), 'K': QtGui.QColor(35, 31, 32),
            'W': QtGui.QColor(255, 255, 255)
        }
        self.PURE_DEFAULT_THRESHOLDS = {
            'C': 128, 'M': 128, 'Y': 128, 'K': 128, 'W': 128
        }

        # --- 4. Reset a Valores por Defecto ---
        self.reset_all_to_defaults()
        print("🚀 Aplicación iniciada con valores puros.")

        # --- 5. Inicialización de la Interfaz Gráfica (SIEMPRE AL FINAL) ---
        self.init_ui()
        print("✅ Interfaz inicializada.")

    def init_ui(self):
        """
        Construye la interfaz. El panel izquierdo sigue el orden del trabajo
        (imagen → trama → salida → canales) y las dos acciones principales
        quedan fijas abajo, siempre visibles aunque el panel haga scroll.
        """
        self.setStyleSheet(theme.APP_QSS)

        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 8, 0)
        main_layout.setSpacing(8)

        def field_label(text):
            label = QtWidgets.QLabel(text)
            label.setProperty("rol", "campo")
            return label

        def secondary_label(text):
            label = QtWidgets.QLabel(text)
            label.setProperty("rol", "secundario")
            label.setWordWrap(True)
            return label

        # -- PANEL IZQUIERDO: controles con scroll + barra de acciones fija --
        left_panel = QtWidgets.QWidget()
        left_panel.setFixedWidth(430)
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        controls_scroll = QtWidgets.QScrollArea()
        controls_scroll.setObjectName("panelControles")
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        controls_container = QtWidgets.QWidget()
        controls_container.setObjectName("contenedorControles")
        controls_layout = QtWidgets.QVBoxLayout(controls_container)
        controls_layout.setAlignment(QtCore.Qt.AlignTop)
        controls_layout.setSpacing(4)
        controls_layout.setContentsMargins(10, 4, 10, 10)

        # === IMAGEN ===
        def compact_combo(combo):
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.view().setMinimumWidth(240)
            return combo

        image_group = QtWidgets.QGroupBox("Imagen")
        image_layout = QtWidgets.QVBoxLayout(image_group)
        image_layout.setSpacing(6)

        image_buttons = QtWidgets.QHBoxLayout()
        self.load_btn = QtWidgets.QPushButton("Abrir imagen o PDF…")
        self.load_btn.clicked.connect(self.load_image_or_pdf)
        image_buttons.addWidget(self.load_btn, 1)
        self.wizard_btn = QtWidgets.QPushButton("Asistente")
        self.wizard_btn.setToolTip("Recomienda malla, LPI y tinta según soporte y prenda")
        self.wizard_btn.clicked.connect(self.show_setup_wizard)
        image_buttons.addWidget(self.wizard_btn)
        image_layout.addLayout(image_buttons)

        self.image_res_label = secondary_label("Ninguna imagen abierta")
        image_layout.addWidget(self.image_res_label)
        self.complexity_label = secondary_label("")
        image_layout.addWidget(self.complexity_label)
        controls_layout.addWidget(image_group)

        # === TRAMA ===
        params_group = QtWidgets.QGroupBox("Trama")
        params_layout = QtWidgets.QGridLayout(params_group)
        params_layout.setHorizontalSpacing(10)
        params_layout.setVerticalSpacing(6)
        params_layout.setColumnStretch(1, 1)

        params_layout.addWidget(field_label("Técnica"), 0, 0)
        self.mode_combo = compact_combo(QtWidgets.QComboBox())
        self.mode_combo.addItems(list(SEPARATION_MODES.keys()))
        self.mode_combo.setToolTip("Cuatricromía: 4 tintas (+ base). Semitono: una tinta a partir de los grises")
        params_layout.addWidget(self.mode_combo, 0, 1, 1, 2)

        params_layout.addWidget(field_label("Malla"), 1, 0)
        self.mesh_spin = QtWidgets.QDoubleSpinBox()
        self.mesh_spin.setRange(10, 500)
        self.mesh_spin.setDecimals(0)
        self.mesh_spin.setValue(200)
        self.mesh_spin.setToolTip("Número de hilos de la malla de la pantalla")
        params_layout.addWidget(self.mesh_spin, 1, 1)
        self.mesh_unit_combo = QtWidgets.QComboBox()
        self.mesh_unit_combo.addItem("hilos/pulg", "in")
        self.mesh_unit_combo.addItem("hilos/cm", "cm")
        params_layout.addWidget(self.mesh_unit_combo, 1, 2)

        params_layout.addWidget(field_label("Lineatura"), 2, 0)
        self.lpi_combo = compact_combo(QtWidgets.QComboBox())
        self.lpi_combo.setEditable(True)
        self.lpi_combo.addItems(list(LPI_VALUES.keys()))
        self.lpi_combo.setCurrentText("45 LPI")
        self.lpi_combo.setToolTip("Líneas por pulgada. Puedes escribir cualquier valor, por ejemplo 31")
        params_layout.addWidget(self.lpi_combo, 2, 1)
        self.suggest_lpi_btn = QtWidgets.QPushButton("Sugerida")
        self.suggest_lpi_btn.setToolTip("Usar la lineatura recomendada para la malla (malla ÷ 3.5 a 4.75, sin relación entera)")
        self.suggest_lpi_btn.clicked.connect(self.apply_suggested_lpi)
        params_layout.addWidget(self.suggest_lpi_btn, 2, 2)

        self.moire_warning = MoireWarningWidget(self)
        params_layout.addWidget(self.moire_warning, 3, 0, 1, 3)

        params_layout.addWidget(field_label("Forma de punto"), 4, 0)
        self.shape_combo = compact_combo(QtWidgets.QComboBox())
        self.shape_combo.addItems(list(POINT_SHAPES.keys()))
        params_layout.addWidget(self.shape_combo, 4, 1, 1, 2)

        params_layout.addWidget(field_label("Ángulos"), 5, 0)
        self.angle_preset_combo = compact_combo(QtWidgets.QComboBox())
        self.angle_preset_combo.addItems(list(ANGLE_PRESETS.keys()) + [CUSTOM_ANGLE_PRESET])
        self.angle_preset_combo.setToolTip("En serigrafía, 0°, 45° y 90° coinciden con los hilos de la malla")
        self.angle_preset_combo.currentTextChanged.connect(self.on_angle_preset_changed)
        params_layout.addWidget(self.angle_preset_combo, 5, 1, 1, 2)

        params_layout.addWidget(field_label("Resolución"), 6, 0)
        self.resolution_combo = compact_combo(QtWidgets.QComboBox())
        self.resolution_combo.addItems(list(RESOLUTION_ENHANCEMENT.keys()))
        params_layout.addWidget(self.resolution_combo, 6, 1, 1, 2)
        controls_layout.addWidget(params_group)

        # === COLOR PLANO ===
        self.spot_group = QtWidgets.QGroupBox("Color plano")
        spot_layout = QtWidgets.QVBoxLayout(self.spot_group)
        spot_layout.setSpacing(6)

        detect_row = QtWidgets.QHBoxLayout()
        detect_row.addWidget(field_label("Colores"))
        self.spot_count_spin = QtWidgets.QSpinBox()
        self.spot_count_spin.setRange(1, 16)
        self.spot_count_spin.setValue(6)
        detect_row.addWidget(self.spot_count_spin)
        self.detect_spots_btn = QtWidgets.QPushButton("Detectar colores")
        self.detect_spots_btn.setToolTip("Busca los colores dominantes de la imagen (sin contar el color de la prenda)")
        self.detect_spots_btn.clicked.connect(self.detect_spot_colors)
        detect_row.addWidget(self.detect_spots_btn, 1)
        spot_layout.addLayout(detect_row)

        self.spot_table = QtWidgets.QTableWidget(0, 5)
        self.spot_table.setHorizontalHeaderLabels(["", "Tinta", "Semitono", "Cubriente", "Base"])
        self.spot_table.verticalHeader().setVisible(False)
        header = self.spot_table.horizontalHeader()
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for column in (0, 2, 3, 4):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        self.spot_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.spot_table.setMinimumHeight(170)
        self.spot_table.setToolTip("Doble clic en la muestra para cambiar el color. "
                                   "Semitono: la tinta lleva puntos en sus degradados. "
                                   "Base: se imprime blanco debajo en prenda oscura.")
        self.spot_table.itemChanged.connect(self.on_spot_table_edited)
        self.spot_table.cellDoubleClicked.connect(self.on_spot_cell_double_clicked)
        spot_layout.addWidget(self.spot_table)

        spot_buttons = QtWidgets.QGridLayout()
        spot_buttons.setSpacing(6)
        for i, (text, handler, tip) in enumerate([
                ("Agregar…", self.add_spot_color, "Agregar una tinta eligiendo el color"),
                ("Quitar", self.remove_spot_colors, "Quitar las tintas seleccionadas"),
                ("Unir", self.merge_spot_colors, "Unir las tintas seleccionadas en una sola"),
                ("Biblioteca…", self.import_color_library, "Importar muestras ASE o CSV (p. ej. Pantone exportado de tu software)"),
                ("Igualar", self.match_spot_colors, "Asignar a cada tinta la muestra más cercana de la biblioteca (ΔE2000)"),
                ("Exportar ASE…", self.export_spot_palette, "Guardar la paleta del trabajo como biblioteca ASE")]):
            button = QtWidgets.QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            spot_buttons.addWidget(button, i // 3, i % 3)
        spot_layout.addLayout(spot_buttons)

        spot_grid = QtWidgets.QGridLayout()
        spot_grid.setHorizontalSpacing(10)
        spot_grid.setColumnStretch(1, 1)
        spot_grid.addWidget(field_label("Trapping"), 0, 0)
        self.trap_spin = QtWidgets.QDoubleSpinBox()
        self.trap_spin.setRange(0, 2)
        self.trap_spin.setSingleStep(0.05)
        self.trap_spin.setDecimals(2)
        self.trap_spin.setSuffix(" mm")
        self.trap_spin.setToolTip("Cuánto se expande cada color bajo los colores más oscuros vecinos")
        spot_grid.addWidget(self.trap_spin, 0, 1)
        spot_grid.addWidget(field_label("Reparto de semitono"), 1, 0)
        self.spot_softness_spin = QtWidgets.QDoubleSpinBox()
        self.spot_softness_spin.setRange(3, 40)
        self.spot_softness_spin.setValue(12)
        self.spot_softness_spin.setSuffix(" ΔE")
        self.spot_softness_spin.setToolTip("Más alto = degradados más amplios en las tintas con semitono")
        spot_grid.addWidget(self.spot_softness_spin, 1, 1)
        spot_grid.addWidget(field_label("Ángulo de trama"), 2, 0)
        self.spot_angle_spin = QtWidgets.QDoubleSpinBox()
        self.spot_angle_spin.setRange(0, 179.5)
        self.spot_angle_spin.setDecimals(1)
        self.spot_angle_spin.setValue(22.5)
        self.spot_angle_spin.setSuffix(" °")
        spot_grid.addWidget(self.spot_angle_spin, 2, 1)

        spot_grid.addWidget(field_label("Resolución índice"), 3, 0)
        self.index_resolution_spin = QtWidgets.QDoubleSpinBox()
        self.index_resolution_spin.setRange(50, 400)
        self.index_resolution_spin.setDecimals(0)
        self.index_resolution_spin.setValue(150)
        self.index_resolution_spin.setSuffix(" ppp")
        self.index_resolution_spin.setToolTip("Píxeles cuadrados por pulgada del color índice (suele ir de 100 a 200)")
        spot_grid.addWidget(self.index_resolution_spin, 3, 1)
        spot_grid.addWidget(field_label("Alcance del plano"), 4, 0)
        self.spot_tolerance_spin = QtWidgets.QDoubleSpinBox()
        self.spot_tolerance_spin.setRange(2, 40)
        self.spot_tolerance_spin.setValue(10)
        self.spot_tolerance_spin.setSuffix(" ΔE")
        self.spot_tolerance_spin.setToolTip("En cuatricromía + planos: qué tan parecido debe ser un píxel a la tinta plana")
        spot_grid.addWidget(self.spot_tolerance_spin, 4, 1)
        spot_layout.addLayout(spot_grid)
        self.simulated_btn = QtWidgets.QPushButton("Preparar proceso simulado")
        self.simulated_btn.setToolTip("Para prenda oscura: todas las tintas con semitono y cubrientes, "
                                      "base blanca y blanco de luces al final")
        self.simulated_btn.clicked.connect(self.prepare_simulated_process)
        spot_layout.addWidget(self.simulated_btn)
        self.spot_library_label = secondary_label("Sin biblioteca de color cargada.")
        spot_layout.addWidget(self.spot_library_label)
        self.spot_group.setVisible(False)
        controls_layout.addWidget(self.spot_group)

        # === TONO ===
        tone_group = QtWidgets.QGroupBox("Tono")
        tone_layout = QtWidgets.QGridLayout(tone_group)
        tone_layout.setHorizontalSpacing(10)
        tone_layout.setVerticalSpacing(6)
        tone_layout.setColumnStretch(1, 1)

        def percent_spin(value, maximum=100.0, tooltip=""):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0, maximum)
            spin.setDecimals(0)
            spin.setSuffix(" %")
            spin.setValue(value)
            spin.setToolTip(tooltip)
            return spin

        tone_layout.addWidget(field_label("Punto mínimo"), 0, 0)
        self.min_dot_spin = percent_spin(0, 50, "Los puntos más pequeños se eliminan: la malla no los sostiene")
        tone_layout.addWidget(self.min_dot_spin, 0, 1)
        tone_layout.addWidget(field_label("Punto máximo"), 1, 0)
        self.max_dot_spin = percent_spin(100, 100, "Los puntos más grandes se imprimen sólidos: se cerrarían por la ganancia")
        tone_layout.addWidget(self.max_dot_spin, 1, 1)
        tone_layout.addWidget(field_label("Ganancia al 50 %"), 2, 0)
        self.dot_gain_spin = percent_spin(0, 45, "Cuánto crece un punto del 50 % al imprimir (mídelo con la plantilla). "
                                                  "La trama se compensa para que imprima el tono correcto")
        tone_layout.addWidget(self.dot_gain_spin, 2, 1)
        self.hold_limits_btn = QtWidgets.QPushButton("Según malla")
        self.hold_limits_btn.setToolTip("Punto mínimo y máximo que la malla puede sostener con esta lineatura")
        self.hold_limits_btn.clicked.connect(self.apply_mesh_hold_limits)
        tone_layout.addWidget(self.hold_limits_btn, 0, 2)
        self.gain_curve_btn = QtWidgets.QPushButton("Curva medida…")
        self.gain_curve_btn.setToolTip("Cargar los valores medidos con la plantilla de ganancia")
        self.gain_curve_btn.clicked.connect(self.edit_gain_curve)
        tone_layout.addWidget(self.gain_curve_btn, 2, 2)
        self.tone_hint = secondary_label("Referencia textil: punto mínimo 5–10 %, máximo 85–95 %, ganancia 15–30 %.")
        tone_layout.addWidget(self.tone_hint, 3, 0, 1, 3)
        controls_layout.addWidget(tone_group)

        # === SUSTRATO ===
        substrate_group = QtWidgets.QGroupBox("Sustrato y tinta")
        substrate_layout = QtWidgets.QGridLayout(substrate_group)
        substrate_layout.setHorizontalSpacing(10)
        substrate_layout.setVerticalSpacing(6)
        substrate_layout.setColumnStretch(1, 1)
        substrate_layout.addWidget(field_label("Sustrato"), 0, 0)
        self.substrate_combo = compact_combo(QtWidgets.QComboBox())
        self.substrate_combo.addItems(["Personalizado"] + list(SUBSTRATE_PROFILES.keys()))
        self.substrate_combo.setToolTip("Aplica color de prenda, base blanca, límite de tinta, rango tonal y ganancia típicos")
        self.substrate_combo.currentTextChanged.connect(self.apply_substrate_profile)
        substrate_layout.addWidget(self.substrate_combo, 0, 1)
        substrate_layout.addWidget(field_label("Tinta"), 1, 0)
        self.ink_type_combo = compact_combo(QtWidgets.QComboBox())
        self.ink_type_combo.addItems(list(INK_TYPES.keys()))
        self.ink_type_combo.setToolTip("Opacidad de la tinta en la simulación: la transparente filtra el color de abajo")
        self.ink_type_combo.currentTextChanged.connect(lambda *_: self.update_preview())
        substrate_layout.addWidget(self.ink_type_combo, 1, 1)
        substrate_layout.addWidget(field_label("Límite de tinta"), 2, 0)
        self.ink_limit_spin = QtWidgets.QDoubleSpinBox()
        self.ink_limit_spin.setRange(100, 400)
        self.ink_limit_spin.setDecimals(0)
        self.ink_limit_spin.setSuffix(" %")
        self.ink_limit_spin.setValue(TOTAL_INK_LIMIT)
        self.ink_limit_spin.setToolTip("Suma máxima de C+M+Y+K. Textil: 240–280 %")
        substrate_layout.addWidget(self.ink_limit_spin, 2, 1)
        controls_layout.addWidget(substrate_group)

        # === SALIDA ===
        format_group = QtWidgets.QGroupBox("Salida")
        format_layout = QtWidgets.QVBoxLayout(format_group)
        format_layout.setSpacing(6)

        format_row = QtWidgets.QHBoxLayout()
        format_row.addWidget(field_label("Formato"))
        self.print_format_combo = compact_combo(QtWidgets.QComboBox())
        self.print_format_combo.addItems(list(PRINT_FORMATS.keys()) + ["Personalizado"])
        self.print_format_combo.currentIndexChanged.connect(self.on_print_format_changed)
        format_row.addWidget(self.print_format_combo, 1)
        format_layout.addLayout(format_row)

        self.format_info_label = secondary_label("")
        format_layout.addWidget(self.format_info_label)

        self.custom_size_widget = QtWidgets.QWidget()
        custom_layout = QtWidgets.QGridLayout(self.custom_size_widget)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(6)
        self.unit_combo = compact_combo(QtWidgets.QComboBox())
        self.unit_combo.addItems([f"{k} ({v['label']})" for k, v in MEASUREMENT_UNITS.items()])
        self.custom_width = QtWidgets.QDoubleSpinBox()
        self.custom_height = QtWidgets.QDoubleSpinBox()
        self.custom_dpi = QtWidgets.QSpinBox()
        self.custom_dpi.setRange(72, 1200)
        self.custom_dpi.setValue(300)
        custom_layout.addWidget(field_label("Unidad"), 0, 0)
        custom_layout.addWidget(self.unit_combo, 0, 1)
        custom_layout.addWidget(field_label("Ancho"), 1, 0)
        custom_layout.addWidget(self.custom_width, 1, 1)
        custom_layout.addWidget(field_label("Alto"), 2, 0)
        custom_layout.addWidget(self.custom_height, 2, 1)
        custom_layout.addWidget(field_label("DPI"), 3, 0)
        custom_layout.addWidget(self.custom_dpi, 3, 1)
        self.custom_size_widget.setVisible(False)
        format_layout.addWidget(self.custom_size_widget)

        options_grid = QtWidgets.QGridLayout()
        options_grid.setHorizontalSpacing(12)
        options_grid.setVerticalSpacing(4)
        self.fit_format_cb = QtWidgets.QCheckBox("Ajustar al formato")
        self.fit_format_cb.setToolTip("Escala la imagen al papel y genera la trama a la resolución de salida")
        self.guides_cb = QtWidgets.QCheckBox("Guías de registro")
        self.white_base_cb = QtWidgets.QCheckBox("Base blanca")
        self.white_base_cb.setToolTip("Para prenda oscura: se imprime primero y se contrae 2 px en los bordes")
        self.show_halftones_cb = QtWidgets.QCheckBox("Ver trama")
        self.show_halftones_cb.setChecked(True)
        self.show_halftones_cb.stateChanged.connect(self.update_preview)
        options_grid.addWidget(self.fit_format_cb, 0, 0)
        options_grid.addWidget(self.guides_cb, 0, 1)
        options_grid.addWidget(self.white_base_cb, 1, 0)
        options_grid.addWidget(self.show_halftones_cb, 1, 1)
        format_layout.addLayout(options_grid)

        self.garment_color_btn = QtWidgets.QPushButton("Color de la prenda…")
        self.garment_color_btn.clicked.connect(self.select_garment_color)
        format_layout.addWidget(self.garment_color_btn)

        film_grid = QtWidgets.QGridLayout()
        film_grid.setHorizontalSpacing(10)
        film_grid.setVerticalSpacing(6)
        film_grid.setColumnStretch(1, 1)
        film_grid.addWidget(field_label("Resolución"), 0, 0)
        self.output_dpi_combo = compact_combo(QtWidgets.QComboBox())
        self.output_dpi_combo.addItem("Según formato", 0)
        for dpi in (300, 600, 720, 1200):
            self.output_dpi_combo.addItem(f"{dpi} dpi", dpi)
        self.output_dpi_combo.setToolTip("DPI de la impresora de película. Más DPI = más niveles de gris por punto")
        film_grid.addWidget(self.output_dpi_combo, 0, 1)
        film_grid.addWidget(field_label("Archivo"), 1, 0)
        self.output_format_combo = compact_combo(QtWidgets.QComboBox())
        self.output_format_combo.addItem("PNG", "png")
        self.output_format_combo.addItem("TIFF 1 bit (RIP)", "tiff")
        film_grid.addWidget(self.output_format_combo, 1, 1)
        format_layout.addLayout(film_grid)

        film_options = QtWidgets.QGridLayout()
        film_options.setHorizontalSpacing(12)
        self.control_strip_cb = QtWidgets.QCheckBox("Tira de control")
        self.control_strip_cb.setChecked(True)
        self.control_strip_cb.setToolTip("Parches 5–95 % en el margen de cada película (requiere guías)")
        self.mirror_cb = QtWidgets.QCheckBox("Espejo")
        self.mirror_cb.setToolTip("Invierte la película de izquierda a derecha (emulsión abajo)")
        self.negative_cb = QtWidgets.QCheckBox("Negativo")
        film_options.addWidget(self.control_strip_cb, 0, 0)
        film_options.addWidget(self.mirror_cb, 0, 1)
        film_options.addWidget(self.negative_cb, 1, 0)
        format_layout.addLayout(film_options)
        controls_layout.addWidget(format_group)

        # === CANALES ===
        channels_group = QtWidgets.QGroupBox("Canales")
        channels_layout = QtWidgets.QVBoxLayout(channels_group)
        channels_layout.setSpacing(6)

        channels_tabs = QtWidgets.QTabWidget()

        channels_tab = QtWidgets.QWidget()
        channels_tab_layout = QtWidgets.QVBoxLayout(channels_tab)
        channels_tab_layout.setSpacing(6)
        channels_tab_layout.setContentsMargins(8, 8, 8, 8)

        channels_tab_layout.addWidget(secondary_label(
            "Orden de impresión de arriba abajo. Arrastra para cambiarlo; "
            "selecciona un canal para ajustar su umbral."))

        self.channel_list = DraggableChannelList(self)
        self.channel_list.setItemDelegate(
            ChannelScreenDelegate(
                lambda ch: self.channel_colors.get(ch, QtGui.QColor("white")),
                self.channel_angle_for_list,
                self.channel_list,
                self.channel_display_name))
        self.channel_list.orderChanged.connect(self.set_channel_order)
        self.channel_list.itemSelectionChanged.connect(self.on_channel_selection_changed)
        self.channel_list.setFixedHeight(ChannelScreenDelegate.ROW_HEIGHT * 5 + 4)
        channels_tab_layout.addWidget(self.channel_list)

        self.view_individual_channel_cb = QtWidgets.QCheckBox("Ver solo el canal seleccionado")
        self.view_individual_channel_cb.stateChanged.connect(self.update_preview)
        channels_tab_layout.addWidget(self.view_individual_channel_cb)

        threshold_row = QtWidgets.QHBoxLayout()
        self.threshold_label = field_label("Umbral")
        threshold_row.addWidget(self.threshold_label)
        self.threshold_value_label = secondary_label("Selecciona un canal")
        self.threshold_value_label.setWordWrap(False)
        self.threshold_value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        threshold_row.addWidget(self.threshold_value_label, 1)
        channels_tab_layout.addLayout(threshold_row)

        self.threshold_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.threshold_slider.setRange(0, 255)
        self.threshold_slider.sliderMoved.connect(self.on_threshold_slider_changed)
        self.threshold_slider.sliderReleased.connect(self._delayed_threshold_update)
        channels_tab_layout.addWidget(self.threshold_slider)

        channels_tabs.addTab(channels_tab, "Orden y umbral")

        colors_tab = QtWidgets.QWidget()
        colors_tab_layout = QtWidgets.QGridLayout(colors_tab)
        colors_tab_layout.setContentsMargins(8, 8, 8, 8)
        colors_tab_layout.setHorizontalSpacing(8)
        colors_tab_layout.setVerticalSpacing(6)
        colors_tab_layout.setColumnStretch(1, 1)

        for i, ch in enumerate(['C', 'M', 'Y', 'K', 'W']):
            colors_tab_layout.addWidget(QtWidgets.QLabel(CHANNEL_NAMES[ch]), i, 0)

            color_btn = QtWidgets.QPushButton("")
            color_btn.setToolTip(f"Cambiar el color de simulación de {CHANNEL_NAMES[ch].lower()}")
            color_btn.clicked.connect(lambda _, c=ch: self.pick_channel_color(c))
            self.channel_color_buttons[ch] = color_btn
            colors_tab_layout.addWidget(color_btn, i, 1)

            restore_btn = QtWidgets.QPushButton("Restaurar")
            restore_btn.setToolTip("Volver al color de tinta por defecto")
            restore_btn.clicked.connect(lambda _, c=ch: self.restore_channel_color_default(c))
            colors_tab_layout.addWidget(restore_btn, i, 2)

        channels_tabs.addTab(colors_tab, "Colores de tinta")

        adjust_tab = QtWidgets.QWidget()
        adjust_layout = QtWidgets.QGridLayout(adjust_tab)
        adjust_layout.setContentsMargins(8, 8, 8, 8)
        adjust_layout.setHorizontalSpacing(8)
        adjust_layout.setVerticalSpacing(6)
        adjust_layout.addWidget(field_label("Canal"), 0, 0)
        adjust_layout.addWidget(field_label("Densidad"), 0, 1)
        adjust_layout.addWidget(field_label("Ángulo"), 0, 2)
        self.density_spins, self.angle_spins = {}, {}
        for i, ch in enumerate(['C', 'M', 'Y', 'K', 'W'], 1):
            adjust_layout.addWidget(QtWidgets.QLabel(CHANNEL_NAMES[ch]), i, 0)
            density = QtWidgets.QDoubleSpinBox()
            density.setRange(0, 150)
            density.setDecimals(0)
            density.setSuffix(" %")
            density.setValue(100)
            density.setToolTip("Cantidad de tinta del canal: 100 % = sin cambio")
            adjust_layout.addWidget(density, i, 1)
            angle = QtWidgets.QDoubleSpinBox()
            angle.setRange(0, 179.5)
            angle.setDecimals(1)
            angle.setSingleStep(7.5)
            angle.setSuffix(" °")
            angle.setValue(CMYK_ANGLES[ch])
            angle.valueChanged.connect(self.on_channel_angle_edited)
            adjust_layout.addWidget(angle, i, 2)
            self.density_spins[ch], self.angle_spins[ch] = density, angle
        channels_tabs.addTab(adjust_tab, "Densidad y ángulo")
        channels_layout.addWidget(channels_tabs)
        controls_layout.addWidget(channels_group)
        controls_layout.addStretch()

        controls_scroll.setWidget(controls_container)
        left_layout.addWidget(controls_scroll, 1)

        # Barra de acciones fija
        actions_bar = QtWidgets.QWidget()
        actions_bar.setObjectName("barraAcciones")
        actions_layout = QtWidgets.QHBoxLayout(actions_bar)
        actions_layout.setContentsMargins(10, 10, 10, 10)
        actions_layout.setSpacing(8)

        self.process_btn = QtWidgets.QPushButton("Separar colores")
        self.process_btn.setObjectName("accionPrincipal")
        self.process_btn.setShortcut("Ctrl+R")
        self.process_btn.setToolTip("Separa en C, M, Y, K (y base blanca) y genera la trama  (Ctrl+R)")
        self.process_btn.clicked.connect(self.process_cmyk)
        actions_layout.addWidget(self.process_btn, 1)

        self.save_btn = QtWidgets.QPushButton("Exportar positivos…")
        self.save_btn.setObjectName("accionSecundaria")
        self.save_btn.setShortcut("Ctrl+S")
        self.save_btn.setToolTip("Guarda un PNG por canal, los PDF y las especificaciones  (Ctrl+S)")
        self.save_btn.clicked.connect(self.save_results)
        actions_layout.addWidget(self.save_btn, 1)
        left_layout.addWidget(actions_bar)

        # -- VISTAS: original (pequeña) y vista previa (principal) --
        original_group = QtWidgets.QGroupBox("Original")
        original_layout = QtWidgets.QVBoxLayout(original_group)
        self.original_label = AspectRatioPixmapLabel("Abre una imagen o PDF\npara empezar")
        self.original_label.setObjectName("vistaOriginal")
        original_layout.addWidget(self.original_label)

        preview_group = QtWidgets.QGroupBox("Simulación de impresión")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        view_bar = QtWidgets.QHBoxLayout()
        view_bar.setSpacing(8)
        self.view_mode_combo = QtWidgets.QComboBox()
        self.view_mode_combo.addItem("Impreso", "print")
        self.view_mode_combo.addItem("Tinta total", "tac")
        self.view_mode_combo.addItem("Puntos en riesgo", "dots")
        self.view_mode_combo.addItem("Prueba de calce", "registration")
        self.view_mode_combo.setToolTip("Tinta total: rojo = supera el límite. Puntos en riesgo: naranja se pierde, "
                                        "azul se cierra. Prueba de calce: corre cada tinta para ver dónde asoma la base")
        self.view_mode_combo.currentIndexChanged.connect(self.on_view_mode_changed)
        view_bar.addWidget(QtWidgets.QLabel("Ver"))
        view_bar.addWidget(self.view_mode_combo)
        self.misregister_spin = QtWidgets.QDoubleSpinBox()
        self.misregister_spin.setRange(0.05, 2.0)
        self.misregister_spin.setSingleStep(0.05)
        self.misregister_spin.setValue(0.3)
        self.misregister_spin.setSuffix(" mm")
        self.misregister_spin.setToolTip("Descalce simulado de cada tinta")
        self.misregister_spin.valueChanged.connect(lambda *_: self.update_preview())
        self.misregister_spin.setVisible(False)
        view_bar.addWidget(self.misregister_spin)
        view_bar.addStretch()
        self.step_label = QtWidgets.QLabel("Todas las pasadas")
        self.step_label.setProperty("rol", "secundario")
        view_bar.addWidget(self.step_label)
        self.step_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.step_slider.setRange(0, 5)
        self.step_slider.setValue(5)
        self.step_slider.setFixedWidth(160)
        self.step_slider.setToolTip("Simula el trabajo pasada por pasada, en el orden de impresión")
        self.step_slider.valueChanged.connect(self.on_step_changed)
        view_bar.addWidget(self.step_slider)
        preview_layout.addLayout(view_bar)
        self.preview_label = ZoomablePreviewLabel()
        self.preview_label.setMinimumSize(400, 300)
        preview_layout.addWidget(self.preview_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(original_group)
        splitter.addWidget(preview_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([280, 840])

        views_panel = QtWidgets.QWidget()
        views_layout = QtWidgets.QVBoxLayout(views_panel)
        views_layout.setContentsMargins(0, 4, 0, 8)
        views_layout.addWidget(splitter)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(views_panel, 1)

        # Barra de estado y menú
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Abre una imagen o PDF para empezar")

        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&Archivo")
        open_action = QtWidgets.QAction("Abrir imagen o PDF…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.load_image_or_pdf)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        load_job_action = QtWidgets.QAction("Abrir configuración…", self)
        load_job_action.triggered.connect(self.load_job_settings)
        file_menu.addAction(load_job_action)
        save_job_action = QtWidgets.QAction("Guardar configuración…", self)
        save_job_action.triggered.connect(self.save_job_settings)
        file_menu.addAction(save_job_action)
        file_menu.addSeparator()
        export_action = QtWidgets.QAction("Exportar positivos…", self)
        export_action.triggered.connect(self.save_results)
        file_menu.addAction(export_action)
        print_action = QtWidgets.QAction("Imprimir positivos…", self)
        print_action.setShortcut("Ctrl+P")
        print_action.triggered.connect(self.print_positives)
        file_menu.addAction(print_action)

        tools_menu = menu_bar.addMenu("&Herramientas")
        lpi_calc_action = QtWidgets.QAction("Calculadora de LPI…", self)
        lpi_calc_action.triggered.connect(self.show_lpi_calculator)
        tools_menu.addAction(lpi_calc_action)
        troubleshoot_action = QtWidgets.QAction("Solucionador de problemas…", self)
        troubleshoot_action.triggered.connect(self.show_troubleshooter)
        tools_menu.addAction(troubleshoot_action)
        tools_menu.addSeparator()
        template_action = QtWidgets.QAction("Plantilla de ganancia de punto…", self)
        template_action.triggered.connect(self.generate_dot_gain_template)
        tools_menu.addAction(template_action)
        curve_action = QtWidgets.QAction("Curva de ganancia medida…", self)
        curve_action.triggered.connect(self.edit_gain_curve)
        tools_menu.addAction(curve_action)

        # Estado inicial
        self.threshold_slider.setEnabled(False)
        self.view_individual_channel_cb.setEnabled(False)

        self.update_channel_list_ui()
        self.update_all_color_buttons_ui()

        self.lpi_combo.currentTextChanged.connect(self.update_moire_analysis)
        self.mesh_spin.valueChanged.connect(self.update_moire_analysis)
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        self.lpi_combo.currentTextChanged.connect(lambda *_: self.update_resolution_advice())
        self.fit_format_cb.stateChanged.connect(lambda *_: self.update_resolution_advice())
        self.print_format_combo.currentIndexChanged.connect(lambda *_: self.update_resolution_advice())
        self.ink_limit_spin.valueChanged.connect(self.schedule_reseparation)
        for control in (self.trap_spin, self.spot_softness_spin, self.spot_tolerance_spin, self.index_resolution_spin):
            control.valueChanged.connect(self.schedule_reseparation)
        self.spot_angle_spin.valueChanged.connect(self.schedule_rescreen)
        for control in (self.min_dot_spin, self.max_dot_spin, self.dot_gain_spin, *self.density_spins.values()):
            control.valueChanged.connect(self.schedule_rescreen)
        for control in (self.shape_combo, self.angle_preset_combo, self.lpi_combo):
            control.currentTextChanged.connect(self.schedule_rescreen)
        for control in self.angle_spins.values():
            control.valueChanged.connect(self.schedule_rescreen)
        self.mesh_unit_combo.currentIndexChanged.connect(self.on_mesh_unit_changed)
        self.shape_combo.currentTextChanged.connect(self.update_moire_analysis)
        self.update_moire_analysis()

    # =====================================================================
    # == MÉTODOS DE LÓGICA Y EVENTOS
    # =====================================================================

    def show_troubleshooter(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Solucionador de Problemas de Taller")
        dialog.setMinimumSize(500, 400)
        layout = QtWidgets.QVBoxLayout(dialog)

    def update_channel_list_ui(self):
        """Puebla o actualiza los items en la lista de canales."""
        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        for ch in self.list_channels():
            item = QtWidgets.QListWidgetItem(ch)
            item.setToolTip(f"{self.channel_display_name(ch)}: arrastra para cambiar el orden de impresión")
            self.channel_list.addItem(item)
        self.channel_list.blockSignals(False)

    def update_all_color_buttons_ui(self):
        """Actualiza el color de fondo de todos los botones de canal."""
        for channel, button in self.channel_color_buttons.items():
            if channel in self.channel_colors:
                button.setStyleSheet(f"background-color: {self.channel_colors[channel].name()};")

    def get_current_channel(self):
        selected = self.channel_list.selectedItems()
        if selected:
            return selected[0].text()
        return None

    def get_selected_channels_ordered(self):
        """Obtiene canales seleccionados en orden"""
        selected_items = self.channel_list.selectedItems()
        return [item.text() for item in selected_items]
    
    def get_channel_threshold(self, channel_name):
        """Obtiene umbral de un canal"""
        return self.channel_thresholds.get(channel_name, 128)
    
    def get_channel_color(self, channel_name):
        """Obtiene color RGB de un canal"""
        if channel_name in self.channel_colors:
            color = self.channel_colors[channel_name]
            return (color.red(), color.green(), color.blue())
        return (128, 128, 128)  # Gris por defecto


    def update_single_color_button(self, channel, color):
        """
        Actualiza UN SOLO botón de color.
        """
        try:
            print(f"🎨 Actualizando botón individual para canal {channel}...")
            
            # ✅ USANDO TU ESTRUCTURA REAL: self.channel_color_buttons
            if channel in self.channel_color_buttons:
                button = self.channel_color_buttons[channel]
                button.setStyleSheet(f"background-color: {color.name()};")
                print(f"✅ Botón {channel} actualizado a {color.name()}")
            else:
                print(f"❌ Botón para canal {channel} no encontrado")
                
        except Exception as e:
            print(f"❌ Error actualizando botón {channel}: {e}")


    def update_channel_list_colors(self):
        """
        Actualiza los colores de fondo en tu channel_list.
        """
        try:
            print("🎨 Actualizando colores de la lista de canales...")
            
            for i in range(self.channel_list.count()):
                item = self.channel_list.item(i)
                if item:
                    channel = item.text()
                    if channel in self.channel_colors:
                        color = self.channel_colors[channel]
                        self.channel_list.viewport().update()
                        print(f"✅ Item {channel} actualizado en lista")
            for i in range(self.channel_list.count()):
                item = self.channel_list.item(i)
                if item:
                    channel = item.text()
                    if channel in self.channel_colors:
                        color = self.channel_colors[channel]
                        self.channel_list.viewport().update()
                        print(f"✅ Item {channel} actualizado en lista")

        except Exception as e:
            print(f"❌ Error actualizando lista: {e}")

    def show_setup_wizard(self):
        """Muestra un diálogo para guiar al usuario en la configuración del trabajo."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Asistente de Configuración de Taller")
        layout = QtWidgets.QFormLayout(dialog)

        # Crear los widgets
        soporte_combo = QtWidgets.QComboBox()
        soportes = sorted(list(set(ink['soportes_ideales'][0] for ink in self.db_inks)))
        soporte_combo.addItems(soportes)
        layout.addRow("¿Qué material vas a imprimir?", soporte_combo)

        color_soporte_combo = QtWidgets.QComboBox()
        color_soporte_combo.addItems(["Claro / Blanco", "Oscuro / Negro"])
        layout.addRow("¿El material es de color claro u oscuro?", color_soporte_combo)

        result_label = QtWidgets.QLabel("Las recomendaciones aparecerán aquí.")
        result_label.setWordWrap(True)
        result_label.setStyleSheet("padding: 15px; border: 1px solid #ccc; background-color: #f8f8f8;")
        layout.addRow(result_label)

        # Conectar las señales usando una 'lambda' para pasar los argumentos necesarios
        # Esto llama al NUEVO método de la clase cada vez que cambia un combo
        soporte_combo.currentIndexChanged.connect(
            lambda: self.update_recommendations(soporte_combo, color_soporte_combo, result_label)
        )
        color_soporte_combo.currentIndexChanged.connect(
            lambda: self.update_recommendations(soporte_combo, color_soporte_combo, result_label)
        )

        # Botón de cierre
        close_button = QtWidgets.QPushButton("Cerrar")
        close_button.clicked.connect(dialog.accept)
        layout.addRow(close_button)

        # Carga inicial de las recomendaciones
        self.update_recommendations(soporte_combo, color_soporte_combo, result_label)
        
        dialog.exec_()

    def update_recommendations(self, soporte_combo, color_soporte_combo, result_label):
        """
        Este es ahora un método de la clase. Recibe los widgets como argumentos.
        """
        soporte = soporte_combo.currentText()
        es_oscuro = "Oscuro" in color_soporte_combo.currentText()

        # Lógica de recomendación
        tinta_rec = "No encontrada"
        malla_rec = "N/A"
        racleta_rec = "N/A"
        proceso_rec = ""

        # Buscar tinta ideal (Ahora 'self' funciona correctamente)
        for ink in self.db_inks:
            if "Textil" in soporte and es_oscuro:
                if "Oscuro" in ink["soportes_ideales"][0]:
                    tinta_rec = ink["nombre"]
                    malla_rec = f"{ink['malla_recomendada'][0]}-{ink['malla_recomendada'][1]} hilos/cm"
                    proceso_rec = "<b>Proceso Clave:</b> Usar esta tinta cubriente directamente o imprimir una base blanca primero (Pág. 210)."
                    break
            elif soporte in ink["soportes_ideales"]:
                tinta_rec = ink["nombre"]
                malla_rec = f"{ink['malla_recomendada'][0]}-{ink['malla_recomendada'][1]} hilos/cm"
                break

        # Buscar racleta ideal (Ahora 'self' funciona correctamente)
        if "Textil" in soporte:
            racleta_rec = self.db_squeegees[0]["dureza"] + ", " + self.db_squeegees[0]["perfil"]
        elif "PVC" in soporte:
            racleta_rec = self.db_squeegees[2]["dureza"] + ", " + self.db_squeegees[2]["perfil"]
        else:  # Papel, cartón
            racleta_rec = self.db_squeegees[1]["dureza"] + ", " + self.db_squeegees[1]["perfil"]

        # Formatear el texto de recomendación
        recommendation_html = f"""
            <h3>Recomendaciones para tu trabajo:</h3>
            <p><b>Tinta Sugerida:</b> {tinta_rec}</p>
            <p><b>Malla Recomendada:</b> {malla_rec} (Ver Pág. 44)</p>
            <p><b>Racleta Ideal:</b> {racleta_rec} (Ver Pág. 59-60)</p>
            <p>{proceso_rec}</p>
            <hr>
            <i><b>Aviso:</b> Estas son sugerencias basadas en el "Manual de Serigrafía". Realiza siempre una prueba de impresión.</i>
        """
        result_label.setText(recommendation_html)

    def set_channel_order(self, new_order):
        """Actualiza el orden (conserva los canales de otras técnicas) y refresca la vista."""
        hidden = [c for c in self.channel_order if c not in new_order]
        self.channel_order = list(new_order) + hidden
        self.update_preview()

    def channel_angle_for_list(self, channel):
        if channel in getattr(self, 'angle_spins', {}):
            return self.angle_spins[channel].value()
        if hasattr(self, 'spot_angle_spin'):
            return self.spot_angle_spin.value()
        return CMYK_ANGLES.get(channel)

    def list_channels(self):
        """Canales que muestra la lista: tintas de la técnica actual + base blanca."""
        settings = self.job_settings()
        visible = settings.ink_channels() + ['W']
        ordered = [c for c in self.channel_order if c in visible]
        return ordered + [c for c in visible if c not in ordered]

    def channel_display_name(self, channel):
        spot = next((sp for sp in self.spot_colors if sp['id'] == channel), None)
        return spot['name'] if spot else CHANNEL_NAMES.get(channel, channel)

    def select_garment_color(self):
        """Permite al usuario seleccionar el color de fondo para la simulación."""
        color = QtWidgets.QColorDialog.getColor(self.garment_color, self)
        if color.isValid():
            self.garment_color = color
            self.garment_color_btn.setText(f"Color de la prenda: {color.name().upper()}")
            if self.spot_colors:
                # La prenda cambia qué colores necesitan base y qué zona no se imprime
                self.process_cmyk()
            else:
                self.update_preview()

    def load_image_or_pdf(self):
        """Abre imágenes, PSD, PDF, AI, SVG o EPS."""
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Abrir imagen o documento", "", doc_input.OPEN_FILTER)
        if not file_path:
            return
        page, dpi = 0, self.job_settings().dpi
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext in doc_input.VECTOR_EXTENSIONS and doc_input.page_count(file_path) > 1 and ext == '.pdf':
                selector = PDFPageSelector(file_path, self)
                if selector.exec_() != QtWidgets.QDialog.Accepted:
                    return
                page, dpi = selector.get_selected_page(), selector.get_resolution()
        except Exception:
            pass
        self.load_image_file(file_path, page=page, dpi=dpi)

    def _set_loaded_image(self, img):
        """
        Guarda la imagen como BGR opaco sobre blanco y conserva la transparencia
        aparte. Convertir BGRA→BGR sin componer dejaba los píxeles transparentes
        en negro, que se separaban como tinta al 100 % en todas las placas.
        """
        if img.ndim == 3 and img.shape[2] == 4:
            self.image_alpha = img[:, :, 3].copy()
        else:
            self.image_alpha = None
        self.image = prepare_image_for_processing(img)

    def load_image_file(self, file_path, page=0, dpi=None):
        """
        Carga cualquier formato admitido (ver core/input.py). Los vectoriales se
        rasterizan al DPI de salida, así la trama se genera con todo el detalle.
        """
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            document = doc_input.load_document(file_path, dpi or self.job_settings().dpi, page)
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "No se pudo abrir el archivo",
                                           f"{os.path.basename(file_path)}\n\n{e}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()

        self._set_loaded_image(document.bgra)
        self.image_info = document.info()
        self.image_info['file_size_mb'] = os.path.getsize(file_path) / (1024 * 1024)
        self.channel_arrays, self.preview_cache = {}, {}
        self.display_original()
        self.analyze_image_complexity()
        self.update_print_format_compatibility()
        self.update_resolution_advice()
        notes = "; ".join(document.notes)
        self.status_bar.showMessage(f"Abierto: {os.path.basename(file_path)}" + (f". {notes}" if notes else ""), 10000)

    def update_resolution_advice(self):
        """Resolución efectiva al tamaño final frente a la lineatura elegida."""
        if self.image is None or not self.image_info:
            return
        level, message = doc_input.resolution_advice(self.image.shape, self.image_info.get('dpi_x', 72),
                                                     self.job_settings())
        color = {'ok': theme.ESTADO_OK, 'aviso': theme.ESTADO_ALERTA, 'riesgo': theme.ESTADO_RIESGO}[level]
        self.complexity_label.setText(message)
        self.complexity_label.setStyleSheet(f"color: {color}; font-size: 9pt;")

    def calculate_optimal_lpi(self, mesh_count, factor=2.5, equipment_factor=1.0, viewing_distance=1.0):
            """Algoritmo inteligente de cálculo de LPI"""
            # Cálculo base
            base_lpi = mesh_count / factor
            # Esta función parece incompleta en el original, la dejo como está.
            return {'base_lpi': base_lpi, 'optimal_lpi': int(base_lpi), 'distance_factor': 1.0}
    
    def load_databases(self):
        """
        Carga las bases de datos de insumos desde archivos JSON.
        NOTA: Se ha eliminado toda la lógica de creación de UI de este método.
        Su única responsabilidad es cargar datos.
        """
        try:
            current_dir = os.path.dirname(__file__)
            
            with open(os.path.join(current_dir, '..', 'data', 'inks.json'), 'r', encoding='utf-8') as f:
                self.db_inks = json.load(f)
            print(f"✅ Base de datos de tintas cargada: {len(self.db_inks)} registros.")

            with open(os.path.join(current_dir, '..', 'data', 'squeegees.json'), 'r', encoding='utf-8') as f:
                self.db_squeegees = json.load(f)
            print(f"✅ Base de datos de racletas cargada: {len(self.db_squeegees)} registros.")

            with open(os.path.join(current_dir, '..', 'data', 'troubleshooting.json'), 'r', encoding='utf-8') as f:
                self.db_troubleshooting = json.load(f)
            print("✅ Base de datos de problemas cargada.")

        except FileNotFoundError as e:
            QtWidgets.QMessageBox.critical(self, "Error de Carga", f"No se pudo encontrar el archivo de base de datos: {os.path.basename(e.filename)}\nAsegúrate de que la carpeta 'data' exista y contenga los archivos .json.")
        except json.JSONDecodeError as e:
            QtWidgets.QMessageBox.critical(self, "Error de Carga", f"Error de formato en el archivo JSON: {e}")

    # ------------------------------------------------------------ color plano

    def on_mode_changed(self, *_):
        mode = SEPARATION_MODES.get(self.mode_combo.currentText(), 'cmyk')
        self.spot_group.setVisible(mode in SPOT_PALETTE_MODES)
        self.spot_group.setTitle({'index': "Color índice", 'cmyk_spot': "Tintas planas adicionales"}.get(mode, "Color plano"))
        self.index_resolution_spin.setEnabled(mode == 'index')
        self.spot_tolerance_spin.setEnabled(mode == 'cmyk_spot')
        self.simulated_btn.setVisible(mode == 'spot')
        self.set_spot_colors(self.spot_colors)
        self.update_channel_list_ui()
        if self.image is not None and self.preview_cache:
            self.process_cmyk()

    def schedule_reseparation(self, *_):
        """Repite la separación poco después del último cambio (trapping, reparto)."""
        if not self.channel_arrays:
            return
        if not hasattr(self, '_reseparate_timer'):
            self._reseparate_timer = QtCore.QTimer(self)
            self._reseparate_timer.setSingleShot(True)
            self._reseparate_timer.timeout.connect(self.process_cmyk)
        self._reseparate_timer.start(400)

    def _new_spot(self, rgb, name=None):
        spot_id = f"S{self._next_spot_number}"
        self._next_spot_number += 1
        garment = list(self.garment_color.getRgb()[:3])
        return {'id': spot_id, 'name': name or f"Tinta {spot_id[1:]}", 'rgb': [int(v) for v in rgb],
                'halftone': False, 'opaque': True, 'base': bool(default_needs_base(rgb, garment)),
                'library': ''}

    def set_spot_colors(self, spots):
        """Reemplaza la paleta, sincroniza colores de simulación y orden (claro → oscuro)."""
        self.spot_colors = [dict(spot) for spot in spots]
        numbers = [int(sp['id'][1:]) for sp in self.spot_colors if sp['id'][1:].isdigit()]
        self._next_spot_number = max(numbers, default=0) + 1
        for spot in self.spot_colors:
            self.channel_colors[spot['id']] = QtGui.QColor(*spot['rgb'])
        spot_ids = [sp['id'] for sp in order_light_to_dark(self.spot_colors)]
        others = [c for c in self.channel_order if not c.startswith('S') and c != 'W']
        if SEPARATION_MODES.get(self.mode_combo.currentText()) == 'cmyk_spot':
            # Las tintas planas se imprimen después de la cuatricromía
            self.channel_order = ['W'] + others + spot_ids
        else:
            self.channel_order = ['W'] + spot_ids + others
        self.refresh_spot_table()
        self.update_channel_list_ui()

    def refresh_spot_table(self):
        self.spot_table.blockSignals(True)
        self.spot_table.setRowCount(len(self.spot_colors))
        for row, spot in enumerate(self.spot_colors):
            swatch = QtWidgets.QTableWidgetItem("")
            swatch.setBackground(QtGui.QColor(*spot['rgb']))
            swatch.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            swatch.setToolTip(f"RGB {tuple(spot['rgb'])}" + (f"\n{spot['library']}" if spot.get('library') else ""))
            self.spot_table.setItem(row, 0, swatch)
            name = QtWidgets.QTableWidgetItem(spot['name'])
            name.setToolTip(spot.get('library') or "Doble clic para renombrar")
            self.spot_table.setItem(row, 1, name)
            for column, key in ((2, 'halftone'), (3, 'opaque'), (4, 'base')):
                flag = QtWidgets.QTableWidgetItem("")
                flag.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsSelectable)
                flag.setCheckState(QtCore.Qt.Checked if spot.get(key) else QtCore.Qt.Unchecked)
                self.spot_table.setItem(row, column, flag)
        self.spot_table.blockSignals(False)

    def on_spot_table_edited(self, item):
        spot = self.spot_colors[item.row()]
        if item.column() == 1:
            spot['name'] = item.text().strip() or spot['name']
            self.channel_list.viewport().update()
            return
        key = {2: 'halftone', 3: 'opaque', 4: 'base'}.get(item.column())
        if key:
            spot[key] = item.checkState() == QtCore.Qt.Checked
            if key == 'opaque':
                self.update_preview()
            else:
                self.schedule_reseparation()

    def on_spot_cell_double_clicked(self, row, column):
        if column != 0:
            return
        spot = self.spot_colors[row]
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(*spot['rgb']), self, "Color de la tinta")
        if color.isValid():
            spot['rgb'] = list(color.getRgb()[:3])
            spot['library'] = ''
            self.set_spot_colors(self.spot_colors)
            self.schedule_reseparation()

    def detect_spot_colors(self):
        if self.image is None:
            QtWidgets.QMessageBox.information(self, "Sin imagen", "Abre una imagen para detectar sus colores.")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            palette = detect_palette(self.image, self.spot_count_spin.value(), self.image_alpha,
                                     list(self.garment_color.getRgb()[:3]))
            self._next_spot_number = 1
            spots = [self._new_spot(entry['rgb']) for entry in palette]
            for spot, entry in zip(spots, palette):
                spot['name'] = f"Tinta {spot['id'][1:]} ({entry['share']:.0%})"
            self.set_spot_colors(spots)
            if self.color_library:
                self.match_spot_colors()
            self.process_cmyk()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def prepare_simulated_process(self):
        """
        Proceso simulado para prenda oscura: tintas cubrientes con semitono,
        base blanca y un blanco de luces que se imprime al final.
        """
        if not self.spot_colors:
            if self.image is None:
                QtWidgets.QMessageBox.information(self, "Sin imagen", "Abre una imagen primero.")
                return
            self.spot_count_spin.setValue(max(self.spot_count_spin.value(), 6))
            self.detect_spot_colors()
        spots = [dict(sp, halftone=True, opaque=True) for sp in self.spot_colors if not sp.get('print_last')]
        highlight = self._new_spot([255, 255, 255], "Blanco de luces")
        highlight.update({'halftone': True, 'opaque': True, 'base': False, 'print_last': True})
        self.white_base_cb.setChecked(True)
        self.set_spot_colors(spots + [highlight])
        self.process_cmyk()

    def selected_spot_rows(self):
        return sorted({index.row() for index in self.spot_table.selectedIndexes()})

    def add_spot_color(self):
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor("#C8102E"), self, "Nueva tinta")
        if color.isValid():
            self.set_spot_colors(self.spot_colors + [self._new_spot(color.getRgb()[:3])])
            self.schedule_reseparation()

    def remove_spot_colors(self):
        rows = self.selected_spot_rows()
        if rows:
            self.set_spot_colors([sp for i, sp in enumerate(self.spot_colors) if i not in rows])
            self.schedule_reseparation()

    def merge_spot_colors(self):
        """Une las tintas seleccionadas en una (color promedio en Lab)."""
        rows = self.selected_spot_rows()
        if len(rows) < 2:
            QtWidgets.QMessageBox.information(self, "Unir tintas", "Selecciona dos o más tintas para unirlas.")
            return
        merged_lab = rgb_to_lab([self.spot_colors[i]['rgb'] for i in rows]).mean(axis=0)
        merged = dict(self.spot_colors[rows[0]])
        merged['rgb'] = [int(v) for v in lab_to_rgb([merged_lab])[0]]
        merged['library'] = ''
        remaining = [sp for i, sp in enumerate(self.spot_colors) if i not in rows[1:]]
        remaining[rows[0]] = merged
        self.set_spot_colors(remaining)
        self.schedule_reseparation()

    def import_color_library(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Importar biblioteca de color", self.output_dir,
            "Bibliotecas de color (*.ase *.csv *.txt);;Adobe Swatch Exchange (*.ase);;CSV (*.csv *.txt)")
        if not path:
            return
        try:
            library = read_library(path)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            QtWidgets.QMessageBox.critical(self, "No se pudo leer la biblioteca",
                                           f"{e}\n\nFormatos admitidos: ASE (Adobe) y CSV «nombre,#RRGGBB».")
            return
        if not library:
            QtWidgets.QMessageBox.warning(self, "Biblioteca vacía", "El archivo no tiene muestras de color reconocibles.")
            return
        self.color_library = library
        self.spot_library_label.setText(f"Biblioteca: {os.path.basename(path)} ({len(library)} muestras).")
        if self.spot_colors:
            self.match_spot_colors()

    def match_spot_colors(self):
        """Asigna a cada tinta la muestra más cercana de la biblioteca (ΔE2000)."""
        if not self.color_library:
            QtWidgets.QMessageBox.information(self, "Sin biblioteca", "Importa primero una biblioteca ASE o CSV.")
            return
        for spot in self.spot_colors:
            entry, distance = match_library(spot['rgb'], self.color_library)
            if entry:
                spot['name'] = entry['name']
                spot['rgb'] = list(entry['rgb'])
                spot['library'] = f"{entry['name']}  (ΔE2000 {distance:.1f} respecto al color detectado)"
        self.set_spot_colors(self.spot_colors)
        self.schedule_reseparation()

    def export_spot_palette(self):
        if not self.spot_colors:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Exportar paleta", self.output_dir, "ASE (*.ase)")
        if path:
            write_ase(path, [{'name': sp['name'], 'rgb': sp['rgb']} for sp in self.spot_colors])
            self.status_bar.showMessage(f"Paleta exportada: {os.path.basename(path)}", 8000)

    def schedule_rescreen(self, *_):
        """Vuelve a tramar la vista previa poco después del último cambio de tono o trama."""
        if not self.channel_arrays:
            return
        if not hasattr(self, '_rescreen_timer'):
            self._rescreen_timer = QtCore.QTimer(self)
            self._rescreen_timer.setSingleShot(True)
            self._rescreen_timer.timeout.connect(self.rescreen_preview)
        self._rescreen_timer.start(250)

    def rescreen_preview(self):
        """Retrama los canales ya separados (no repite la separación de color)."""
        if not self.channel_arrays:
            return
        settings = self.job_settings()
        self.preview_cache = {name: screen_channel(data, name, settings, self.preview_scale)
                              for name, data in self.channel_arrays.items()}
        self.update_preview()

    def mesh_tpi(self):
        """Malla actual en hilos por pulgada."""
        return mesh_rules.to_threads_per_inch(self.mesh_spin.value(), self.mesh_unit_combo.currentData())

    def current_lpi(self):
        try:
            return float(self.lpi_combo.currentText().split()[0].replace(",", "."))
        except (ValueError, IndexError):
            return 0.0

    def on_mesh_unit_changed(self):
        """Convierte el valor mostrado al cambiar de hilos/pulg a hilos/cm y viceversa."""
        unit = self.mesh_unit_combo.currentData()
        previous = 'in' if unit == 'cm' else 'cm'
        tpi = mesh_rules.to_threads_per_inch(self.mesh_spin.value(), previous)
        self.mesh_spin.blockSignals(True)
        self.mesh_spin.setValue(round(mesh_rules.from_threads_per_inch(tpi, unit)))
        self.mesh_spin.blockSignals(False)
        self.update_moire_analysis()

    def apply_suggested_lpi(self):
        lpi = mesh_rules.suggested_lpi(self.mesh_tpi())
        self.lpi_combo.setCurrentText(f"{lpi} LPI")

    def current_angles(self):
        return {ch: spin.value() for ch, spin in self.angle_spins.items()}

    def on_angle_preset_changed(self, name):
        angles = ANGLE_PRESETS.get(name)
        if angles is None:
            return
        for ch, spin in self.angle_spins.items():
            spin.blockSignals(True)
            spin.setValue(angles.get(ch, spin.value()))
            spin.blockSignals(False)
        self.channel_list.viewport().update()

    def on_channel_angle_edited(self):
        """Un ángulo editado a mano pasa el juego a «Personalizado»."""
        self.angle_preset_combo.blockSignals(True)
        self.angle_preset_combo.setCurrentText(CUSTOM_ANGLE_PRESET)
        self.angle_preset_combo.blockSignals(False)
        self.channel_list.viewport().update()

    def update_moire_analysis(self):
        """Evalúa la malla y la lineatura actuales y actualiza el aviso."""
        if not hasattr(self, 'moire_warning') or not hasattr(self, 'mesh_spin'):
            return
        lpi = self.current_lpi()
        if lpi <= 0:
            return
        mesh = self.mesh_tpi()
        analysis = self.moire_detector.analyze_moire_risk(int(round(lpi)), int(round(mesh)), {})
        level, message = mesh_rules.assess(mesh, lpi)
        low, high = mesh_rules.suggested_lpi_range(mesh)
        self.moire_warning.update_moire_status(lpi, mesh, analysis, level, message,
                                               f"Recomendado: {low:.0f}–{high:.0f} LPI")


    def on_resolution_changed(self):
        """Responder a cambio de configuración de resolución"""
        resolution_name = self.resolution_combo.currentText()
        
        # En el código original, este widget no estaba definido en init_ui
        # if resolution_name == "Personalizado":
        #     self.custom_resolution_widget.setVisible(True)
        # else:
        #     self.custom_resolution_widget.setVisible(False)

        # Actualizar análisis si hay imagen cargada
        if hasattr(self, 'image') and self.image is not None:
            self.analyze_image_complexity()

    def analyze_image_complexity(self):
        """Analizar complejidad de imagen y dar recomendaciones"""
        if not hasattr(self, 'image') or self.image is None:
            return

        try:
            complexity, recommendation = detect_image_complexity(self.image)

            # Colores según complejidad
            colors = {
                "alta": "#FF5722",
                "media": "#FF9800",
                "baja": "#4CAF50",
                "desconocida": "#666"
            }

            color = colors.get(complexity, "#666")

            complexity_text = f"Detalle {complexity}: {recommendation}"
            self.complexity_label.setText(complexity_text)
            self.complexity_label.setStyleSheet(f"color: {color}; font-size: 9pt;")

            print(f"📊 Análisis de imagen: {complexity} - {recommendation}")

        except Exception as e:
            print(f"⚠️ Error analizando complejidad: {e}")

    def setup_dimension_spinbox(self, spinbox, unit):
        """Configurar spinbox según la unidad seleccionada"""
        unit_info = MEASUREMENT_UNITS[unit]

        spinbox.setRange(*unit_info["range"])
        spinbox.setSingleStep(unit_info["step"])
        spinbox.setDecimals(unit_info["precision"])
        spinbox.setSuffix(f" {unit_info['symbol']}")

    def get_current_unit(self):
        """Obtener unidad actualmente seleccionada"""
        unit_text = self.unit_combo.currentText()
        # Extraer símbolo de unidad del texto "mm (Milímetros)"
        return unit_text.split()[0]

    def on_unit_changed(self):
        """Responder a cambio de unidad"""
        if not hasattr(self, 'custom_width'):
            return

        try:
            # Obtener valores actuales
            old_width = self.custom_width.value()
            old_height = self.custom_height.value()

            # Obtener unidades
            new_unit = self.get_current_unit()
            old_unit_symbol = self.custom_width.suffix().strip()
            # Encontrar la clave de la unidad (ej. 'mm') a partir del símbolo (ej. 'mm')
            old_unit = next((k for k, v in MEASUREMENT_UNITS.items() if v['symbol'] == old_unit_symbol), old_unit_symbol)


            # Convertir valores
            new_width = convert_units(old_width, old_unit, new_unit)
            new_height = convert_units(old_height, old_unit, new_unit)

            # Reconfigurar spinboxes
            self.setup_dimension_spinbox(self.custom_width, new_unit)
            self.setup_dimension_spinbox(self.custom_height, new_unit)

            # Establecer nuevos valores
            self.custom_width.setValue(new_width)
            self.custom_height.setValue(new_height)

            # Actualizar información
            self.update_custom_format_info()

            print(f"🔄 Unidad cambiada: {old_width}{old_unit_symbol} × {old_height}{old_unit_symbol} → {new_width}{new_unit} × {new_height}{new_unit}")

        except Exception as e:
            print(f"⚠️ Error cambiando unidad: {e}")

    def update_custom_format_info(self):
        """Actualizar información del formato personalizado"""
        try:
            unit = self.get_current_unit()
            width = self.custom_width.value()
            height = self.custom_height.value()
            dpi = self.custom_dpi.value()

            # Mostrar información formateada
            dimension_text = format_dimension_display(width, height, unit)
            self.format_info_label.setText(f"{dimension_text} @ {dpi} DPI")
            self.update_print_format_compatibility()

        except Exception as e:
            print(f"⚠️ Error actualizando formato personalizado: {e}")

    def get_current_print_format(self):
        """
        Obtiene el diccionario de configuración completo para el formato de
        impresión seleccionado, interpretando correctamente el texto del ComboBox.
        VERSIÓN CORREGIDA con manejo de errores.
        """
        try:
            format_name_full = self.print_format_combo.currentText()
            
            if format_name_full == "Personalizado":
                # Manejar formato personalizado
                try:
                    unit = self.get_current_unit()
                    width = self.custom_width.value()
                    height = self.custom_height.value()
                    dpi = self.custom_dpi.value()
                    
                    # Convertir a mm si no está en mm
                    if unit != "mm":
                        width_mm = convert_units(width, unit, "mm")
                        height_mm = convert_units(height, unit, "mm")
                    else:
                        width_mm = width
                        height_mm = height
                    
                    return {
                        'width': width_mm,
                        'height': height_mm,
                        'dpi_recommended': dpi,
                        'name': 'Formato Personalizado'
                    }
                except:
                    # Si falla el formato personalizado, usar A4 por defecto
                    return {
                        'width': 210,
                        'height': 297,
                        'dpi_recommended': 300,
                        'name': 'A4 (por defecto)'
                    }

            # Extrae la clave del formato, por ejemplo "A4" de "A4 (297x420mm)"
            format_key = format_name_full.split(' ')[0]

            # Verificar si PRINT_FORMATS existe y contiene la clave
            if hasattr(self, 'PRINT_FORMATS') and format_key in self.PRINT_FORMATS:
                format_info = self.PRINT_FORMATS[format_key].copy()
            elif 'PRINT_FORMATS' in globals() and format_key in PRINT_FORMATS:
                format_info = PRINT_FORMATS[format_key].copy()
            else:
                # Si no se encuentra el formato, usar valores por defecto de A4
                print(f"⚠️ Formato {format_key} no encontrado, usando A4 por defecto")
                format_info = {
                    'width': 210,    # mm
                    'height': 297,   # mm
                    'dpi_recommended': 300,
                    'name': f'{format_key} (por defecto)'
                }
            
            # Guarda el nombre completo para mostrarlo en las especificaciones
            format_info['name'] = format_name_full
            
            return format_info
            
        except Exception as e:
            print(f"⚠️ Error en get_current_print_format: {e}")
            # Retornar formato A4 por defecto en caso de cualquier error
            return {
                'width': 210,
                'height': 297,
                'dpi_recommended': 300,
                'name': 'A4 (fallback)'
            }

    def get_current_resolution_settings(self):
        """Obtener configuración actual de resolución"""
        resolution_name = self.resolution_combo.currentText()

        # En el código original, este widget no estaba definido en init_ui
        # if resolution_name == "Personalizado":
        #     return {
        #         "factor": self.custom_resolution_factor.value(),
        #         "method": "INTER_CUBIC"
        #     }
        # else:
        return RESOLUTION_ENHANCEMENT[resolution_name]

    def on_print_format_changed(self):
        """Responder a cambio de formato de impresión - VERSIÓN CORREGIDA"""
        try:
            format_name = self.print_format_combo.currentText()

            if format_name == "Personalizado":
                self.custom_size_widget.setVisible(True)
                self.update_custom_format_info()
            else:
                self.custom_size_widget.setVisible(False)
                print_format = self.get_current_print_format()
                dpi_rec = print_format.get("dpi_recommended", 300)
                format_display = print_format.get("name", format_name)
                self.format_info_label.setText(f"{dpi_rec} DPI recomendado para {format_display}")

            # Actualizar compatibilidad si hay imagen cargada
            if hasattr(self, 'image_info') and self.image_info:
                self.update_print_format_compatibility()
                
        except Exception as e:
            print(f"⚠️ Error en cambio de formato: {e}")
            self.format_info_label.setText("300 DPI recomendado")

    def update_print_format_compatibility(self):
        """Verificar compatibilidad entre imagen y formato de impresión - VERSIÓN CORREGIDA"""
        if not hasattr(self, 'image_info') or not self.image_info:
            # Reset label if no image is loaded
            if self.print_format_combo.currentText() != "Personalizado":
                try:
                    print_format = self.get_current_print_format()
                    dpi_rec = print_format.get('dpi_recommended', 300)
                    format_name = print_format.get('name', 'Formato desconocido')
                    self.format_info_label.setText(f"{dpi_rec} DPI recomendado para {format_name}")
                    self.format_info_label.setStyleSheet(f"color: {theme.TEXTO_SUAVE}; font-size: 9pt;")
                except Exception as e:
                    print(f"⚠️ Error actualizando formato: {e}")
                    self.format_info_label.setText("300 DPI recomendado")
            return

        try:
            print_format = self.get_current_print_format()
            target_width = print_format["width"] / 10  # mm a cm
            target_height = print_format["height"] / 10
            target_dpi = print_format["dpi_recommended"]

            # Comparar con la imagen
            img_width = self.image_info['width_cm']
            img_height = self.image_info['height_cm']
            img_dpi = self.image_info['dpi_x']

            # Calcular si la imagen es adecuada para el formato
            width_ratio = img_width / target_width if target_width > 0 else 1.0
            height_ratio = img_height / target_height if target_height > 0 else 1.0
            dpi_ratio = img_dpi / target_dpi if target_dpi > 0 else 1.0

            # Determinar estado
            if width_ratio >= 0.9 and height_ratio >= 0.9 and dpi_ratio >= 0.8:
                status = "✅ Compatible"
                color = "#4CAF50"
            elif width_ratio >= 0.7 and height_ratio >= 0.7 and dpi_ratio >= 0.6:
                status = "⚠️ Aceptable"
                color = "#FF9800"
            else:
                status = "❌ Inadecuado"
                color = "#F44336"

            format_display_name = print_format.get('name', 'Formato').split('(')[0].strip()
            compatibility_text = f"{status} para {format_display_name}"
            self.format_info_label.setText(compatibility_text)
            self.format_info_label.setStyleSheet(f"color: {color}; font-size: 9pt;")

        except Exception as e:
            print(f"⚠️ Error calculando compatibilidad: {e}")
            self.format_info_label.setText("300 DPI recomendado")
            self.format_info_label.setStyleSheet(f"color: {theme.TEXTO_SUAVE}; font-size: 9pt;")

            try:
                print_format = self.get_current_print_format()
                target_width = print_format["width"] / 10  # mm a cm
                target_height = print_format["height"] / 10
                target_dpi = print_format["dpi_recommended"]

                # Comparar con la imagen
                img_width = self.image_info['width_cm']
                img_height = self.image_info['height_cm']
                img_dpi = self.image_info['dpi_x']

                # Calcular si la imagen es adecuada para el formato
                width_ratio = img_width / target_width if target_width > 0 else 1.0
                height_ratio = img_height / target_height if target_height > 0 else 1.0
                dpi_ratio = img_dpi / target_dpi if target_dpi > 0 else 1.0

                # Determinar estado
                if width_ratio >= 0.9 and height_ratio >= 0.9 and dpi_ratio >= 0.8:
                    status = "✅ Compatible"
                    color = "#4CAF50"
                elif width_ratio >= 0.7 and height_ratio >= 0.7 and dpi_ratio >= 0.6:
                    status = "⚠️ Aceptable"
                    color = "#FF9800"
                else:
                    status = "❌ Inadecuado"
                    color = "#F44336"

                compatibility_text = f"{status} para {print_format['name'].split('(')[0].strip()}"
                self.format_info_label.setText(compatibility_text)
                self.format_info_label.setStyleSheet(f"color: {color}; font-size: 9pt;")

            except Exception as e:
                print(f"Error calculando compatibilidad: {e}")

    def display_original(self):
        """Muestra la imagen cargada y actualiza la etiqueta de información."""
        if self.image is not None and self.image_info:
            rgb_img = cv2.cvtColor(self.image, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_img.shape
            bytes_per_line = ch * w
            qt_img = QtGui.QImage(rgb_img.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888)
            self.original_label.setPixmap(QtGui.QPixmap.fromImage(qt_img))
            
            # La información ya está calculada y guardada, solo la mostramos.
            info = self.image_info
            res_text = f"{info['width_px']}×{info['height_px']}px, {info['dpi_x']:.0f} DPI, {info['width_cm']:.1f}×{info['height_cm']:.1f}cm"
            self.image_res_label.setText(res_text)

    def on_threshold_slider_changed(self, value):
        """
        VERSIÓN OPTIMIZADA LIGERA: UI instantánea + procesamiento con delay
        """
        try:
            if not self.current_channel or self.current_channel == "COMPOSITE":
                return
                
            # 1. ACTUALIZACIÓN INSTANTÁNEA de la UI (sin cálculos)
            self.channel_thresholds[self.current_channel] = value
            self.threshold_value_label.setText(f"{value} / 255")
            
            # 2. CANCELAR timer anterior si existe
            self.threshold_timer.stop()
            
            # 3. PROGRAMAR actualización real con pequeño delay
            self.threshold_timer.start(100)  # 100ms - ajustable
        
        except Exception as e:
            print(f"❌ Error en slider: {e}")

    def _delayed_threshold_update(self):
        """
        NUEVA FUNCIÓN: Actualización real cuando el usuario para de mover el slider
        """
        try:
            if not self.current_channel or self.current_channel not in self.channel_arrays:
                return
                
            print(f"🔄 Actualizando umbral para {self.current_channel}")
            
            # Regenerar solo el canal actual
            if hasattr(self, '_regenerate_single_halftone'):
                self._regenerate_single_halftone(self.current_channel)
            
            # Actualizar vista previa
            self.update_preview()
            
            print(f"✅ Umbral actualizado")
            
        except Exception as e:
            print(f"❌ Error en delayed update: {e}")


    def pick_channel_color(self, channel):
        """Abre el diálogo de color y actualiza el color del canal."""
        color = QtWidgets.QColorDialog.getColor(self.channel_colors[channel], self)
        if color.isValid():
            self.channel_colors[channel] = color
            self.update_all_color_buttons_ui()
            self.update_channel_list_ui()
            self.update_preview()

    def job_settings(self):
        """Lee la interfaz y devuelve la configuración del trabajo para el motor."""
        print_format = self.get_current_print_format()
        resolution = self.get_current_resolution_settings()
        return JobSettings(
            mode=SEPARATION_MODES.get(self.mode_combo.currentText(), 'cmyk'),
            mesh_tpi=self.mesh_tpi(),
            mesh_unit=self.mesh_unit_combo.currentData(),
            lpi=self.current_lpi() or 45.0,
            dot_shape=POINT_SHAPES.get(self.shape_combo.currentText(), 'circle'),
            angle_preset=self.angle_preset_combo.currentText(),
            angles=self.current_angles(),
            min_dot=self.min_dot_spin.value(),
            max_dot=self.max_dot_spin.value(),
            dot_gain=self.dot_gain_spin.value(),
            dot_gain_curve=[list(point) for point in self.dot_gain_curve],
            dpi=int(self.output_dpi_combo.currentData() or print_format["dpi_recommended"]),
            output_format=self.output_format_combo.currentData(),
            mirror=self.mirror_cb.isChecked(),
            negative=self.negative_cb.isChecked(),
            control_strip=self.control_strip_cb.isChecked(),
            paper_width_mm=float(print_format["width"]),
            paper_height_mm=float(print_format["height"]),
            fit_to_paper=self.fit_format_cb.isChecked(),
            registration_guides=self.guides_cb.isChecked(),
            resolution_factor=resolution.get("factor", 1.0),
            resolution_method=resolution.get("method"),
            white_base=self.white_base_cb.isChecked(),
            ink_limit=self.ink_limit_spin.value(),
            ink_type=self.ink_type_combo.currentText(),
            substrate=self.substrate_combo.currentText(),
            thresholds=dict(self.channel_thresholds),
            density={ch: spin.value() for ch, spin in self.density_spins.items()},
            channel_order=list(self.channel_order),
            spot_colors=[dict(spot) for spot in self.spot_colors],
            garment_rgb=list(self.garment_color.getRgb()[:3]),
            trap_mm=self.trap_spin.value(),
            spot_softness=self.spot_softness_spin.value(),
            spot_angle=self.spot_angle_spin.value(),
            spot_tolerance=self.spot_tolerance_spin.value(),
            index_resolution=self.index_resolution_spin.value(),
        )

    def apply_job_settings(self, settings):
        """Refleja en la interfaz una configuración cargada de archivo."""
        for label, mode in SEPARATION_MODES.items():
            if mode == settings.mode:
                self.mode_combo.setCurrentText(label)
        unit_index = self.mesh_unit_combo.findData(settings.mesh_unit)
        self.mesh_unit_combo.blockSignals(True)
        self.mesh_unit_combo.setCurrentIndex(max(unit_index, 0))
        self.mesh_unit_combo.blockSignals(False)
        self.mesh_spin.setValue(round(mesh_rules.from_threads_per_inch(settings.mesh_tpi, settings.mesh_unit)))
        self.lpi_combo.setCurrentText(f"{settings.lpi:g} LPI")
        for label, shape in POINT_SHAPES.items():
            if shape == settings.dot_shape:
                self.shape_combo.setCurrentText(label)
        for ch, spin in self.angle_spins.items():
            spin.blockSignals(True)
            spin.setValue(settings.angles.get(ch, spin.value()))
            spin.blockSignals(False)
        self.angle_preset_combo.blockSignals(True)
        self.angle_preset_combo.setCurrentText(settings.angle_preset)
        self.angle_preset_combo.blockSignals(False)
        self.min_dot_spin.setValue(settings.min_dot)
        self.max_dot_spin.setValue(settings.max_dot)
        self.dot_gain_spin.setValue(settings.dot_gain)
        self.set_gain_curve(settings.dot_gain_curve)
        dpi_index = self.output_dpi_combo.findData(settings.dpi)
        self.output_dpi_combo.setCurrentIndex(max(dpi_index, 0))
        self.output_format_combo.setCurrentIndex(max(self.output_format_combo.findData(settings.output_format), 0))
        self.mirror_cb.setChecked(settings.mirror)
        self.negative_cb.setChecked(settings.negative)
        self.control_strip_cb.setChecked(settings.control_strip)
        self.ink_limit_spin.setValue(settings.ink_limit)
        self.ink_type_combo.setCurrentText(settings.ink_type)
        self.substrate_combo.blockSignals(True)
        self.substrate_combo.setCurrentText(settings.substrate)
        self.substrate_combo.blockSignals(False)
        self.garment_color = QtGui.QColor(*settings.garment_rgb)
        self.garment_color_btn.setText(f"Color de la prenda: {self.garment_color.name().upper()}")
        self.trap_spin.setValue(settings.trap_mm)
        self.spot_softness_spin.setValue(settings.spot_softness)
        self.spot_angle_spin.setValue(settings.spot_angle)
        self.spot_tolerance_spin.setValue(settings.spot_tolerance)
        self.index_resolution_spin.setValue(settings.index_resolution)
        self.set_spot_colors(settings.spot_colors)
        for ch, spin in self.density_spins.items():
            spin.setValue(settings.density.get(ch, 100.0))
        self.fit_format_cb.setChecked(settings.fit_to_paper)
        self.guides_cb.setChecked(settings.registration_guides)
        self.white_base_cb.setChecked(settings.white_base)
        self.channel_thresholds.update(settings.thresholds)
        self.set_channel_order(list(settings.channel_order))
        self.print_format_combo.setCurrentText("Personalizado")
        self.custom_width.setValue(convert_units(settings.paper_width_mm, "mm", self.get_current_unit()))
        self.custom_height.setValue(convert_units(settings.paper_height_mm, "mm", self.get_current_unit()))
        self.custom_dpi.setValue(settings.dpi)
        self.update_moire_analysis()

    def save_job_settings(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Guardar configuración", self.output_dir, "Configuración (*.json)")
        if path:
            self.job_settings().save(path)
            self.status_bar.showMessage(f"Configuración guardada: {os.path.basename(path)}", 5000)

    def load_job_settings(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Abrir configuración", self.output_dir, "Configuración (*.json)")
        if not path:
            return
        try:
            self.apply_job_settings(JobSettings.load(path))
            self.status_bar.showMessage(f"Configuración cargada: {os.path.basename(path)}", 5000)
        except (OSError, ValueError, TypeError) as e:
            QtWidgets.QMessageBox.critical(self, "No se pudo abrir la configuración",
                                           f"El archivo no es una configuración válida.\n\n{e}")

    def _get_halftone_params(self):
        """Celda, forma y ángulos actuales (en píxeles del positivo)."""
        settings = self.job_settings()
        return settings.cell_px, settings.dot_shape, dict(settings.angles)
    
    def reset_all_to_defaults(self):
        """Resetea las variables de la aplicación a sus valores iniciales."""
        self.channel_colors = self.PURE_CMYK_COLORS.copy()
        self.channel_thresholds = self.PURE_DEFAULT_THRESHOLDS.copy()
        self.garment_color = QtGui.QColor("#FFFFFF")

    def get_selected_channel(self):
        """
        Obtiene el canal seleccionado usando TU estructura real.
        """
        if hasattr(self, 'current_channel') and self.current_channel:
            return self.current_channel
        
        # Si no hay current_channel, buscar en la lista
        if hasattr(self, 'channel_list'):
            selected_items = self.channel_list.selectedItems()
            if selected_items:
                # Tomar el primer canal seleccionado
                return selected_items[0].text()
    
        return None
    
    def reset_single_channel_color(self):
        """
        Reset de un solo canal - para usar con botones individuales.
        """
        channel = self.get_selected_channel()
        if not channel:
            QtWidgets.QMessageBox.warning(self, "Sin selección", "Selecciona un canal primero")
            return
        
        self.restore_channel_color_default(channel)

    def generate_single_channel_preview(self, channel_name):
        """
        VERSIÓN LIGERAMENTE OPTIMIZADA de tu función existente - REEMPLAZA la que tienes
        """
        try:
            if channel_name not in self.preview_cache:
                print(f"⚠️ No hay datos en caché para el canal {channel_name}")
                return

            # Tu código existente pero más eficiente
            halftone_mask = self.preview_cache[channel_name]
            h, w = halftone_mask.shape

            # Crear canvas más eficientemente
            garment_rgb = self.garment_color.getRgb()[:3]
            canvas = np.full((h, w, 3), garment_rgb, dtype=np.uint8)

            # Aplicar color de tinta
            ink_color_rgb = self.channel_colors[channel_name].getRgb()[:3]
            dot_locations = halftone_mask == 0
            canvas[dot_locations] = ink_color_rgb

            # Convertir a QPixmap de forma optimizada
            qimage = QtGui.QImage(canvas.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
            self.preview_label.setPixmap(QtGui.QPixmap.fromImage(qimage))

        except Exception as e:
            print(f"❌ Error en vista previa de canal único: {e}")

    def _regenerate_single_halftone(self, channel_name):
        """Vuelve a tramar un canal de la vista previa tras cambiar su umbral."""
        if channel_name not in self.channel_arrays:
            return
        settings = self.job_settings()
        self.preview_cache[channel_name] = screen_channel(
            self.channel_arrays[channel_name], channel_name, settings, self.preview_scale)

    def update_preview(self):
        """
        VERSIÓN OPTIMIZADA de tu función existente - REEMPLAZA la que tienes
        """
        # Evitar actualizaciones múltiples simultáneas
        if hasattr(self, 'is_preview_updating') and self.is_preview_updating:
            return
            
        if not self.preview_cache:
            return

        try:
            self.is_preview_updating = True
            
            # Decidir qué vista mostrar (tu lógica existente)
            show_individual = self.view_individual_channel_cb.isChecked() and self.current_channel

            if show_individual:
                self.generate_single_channel_preview(self.current_channel)
            else:
                self.generate_composite_preview()
                
        except Exception as e:
            print(f"❌ Error en vista previa: {e}")
        finally:
            self.is_preview_updating = False

    def generate_composite_preview(self):
        """Simulación del impreso o vista de control de calidad, según «Ver»."""
        if not self.preview_cache:
            return
        settings = self.job_settings()
        order = [ch for ch in settings.channels() if ch in self.preview_cache]
        self.step_slider.blockSignals(True)
        at_end = self.step_slider.value() >= self.step_slider.maximum()
        self.step_slider.setMaximum(len(order))
        if at_end:
            self.step_slider.setValue(len(order))
        self.step_slider.blockSignals(False)
        steps = self.step_slider.value()
        self.step_label.setText("Todas las pasadas" if steps >= len(order) else
                                ("Solo la prenda" if steps == 0 else
                                 f"Pasada {steps}/{len(order)}: {self.channel_display_name(order[steps - 1])}"))

        mode = self.view_mode_combo.currentData()
        garment = list(self.garment_color.getRgb()[:3])
        ink_rgb = {ch: self.channel_colors[ch].getRgb()[:3] for ch in order}
        printed = sim.simulate(
            self.channel_arrays, self.preview_cache, settings, ink_rgb, garment,
            scale=self.preview_scale, show_screen=self.show_halftones_cb.isChecked(),
            opacity=INK_TYPES.get(self.ink_type_combo.currentText(), 0.25),
            steps=None if mode != 'print' else steps,
            misregister_mm=self.misregister_spin.value() if mode == 'registration' else 0.0)
        if mode == 'tac':
            image = sim.tac_overlay(printed, self.channel_arrays, settings)
        elif mode == 'dots':
            image = sim.dot_risk_overlay(printed, self.channel_arrays, settings)
        else:
            image = printed
        image = np.ascontiguousarray(image)
        h, w = image.shape[:2]
        qimage = QtGui.QImage(image.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
        self.preview_label.setPixmap(QtGui.QPixmap.fromImage(qimage.copy()))

    def on_view_mode_changed(self, *_):
        mode = self.view_mode_combo.currentData()
        self.misregister_spin.setVisible(mode == 'registration')
        self.step_slider.setEnabled(mode == 'print')
        self.update_preview()

    def on_step_changed(self, *_):
        self.update_preview()

    def apply_substrate_profile(self, name):
        profile = SUBSTRATE_PROFILES.get(name)
        if not profile:
            return
        self.garment_color = QtGui.QColor(*profile["garment"])
        self.garment_color_btn.setText(f"Color de la prenda: {self.garment_color.name().upper()}")
        self.white_base_cb.setChecked(profile["white_base"])
        self.ink_limit_spin.setValue(profile["ink_limit"])
        self.min_dot_spin.setValue(profile["min_dot"])
        self.max_dot_spin.setValue(profile["max_dot"])
        if not self.dot_gain_curve:
            self.dot_gain_spin.setValue(profile["dot_gain"])
        if self.image is not None and self.preview_cache:
            self.process_cmyk()

    def apply_mesh_hold_limits(self):
        low, high = sim.holdable_range(self.mesh_tpi(), self.current_lpi())
        self.min_dot_spin.setValue(round(low))
        self.max_dot_spin.setValue(round(high))

    def show_quality_summary(self, settings):
        """Resumen de control de calidad en la barra de estado tras separar."""
        report = sim.quality_report(self.channel_arrays, settings)
        parts = [f"{settings.lpi:g} LPI a {settings.dpi} DPI"]
        if settings.mode == 'cmyk':
            parts.append(f"tinta total máx. {report['tac_max']:.0f} %"
                         + (f" ({report['tac_over']:.1%} del área sobre el límite)" if report['tac_over'] > 0.001 else ""))
        if report['lost'] > 0.005:
            parts.append(f"{report['lost']:.1%} con puntos < {report['hold_min']:.0f} % que la malla no sostiene")
        if report['plugged'] > 0.005:
            parts.append(f"{report['plugged']:.1%} con sombras > {report['hold_max']:.0f} % que se cerrarán")
        parts.append(f"vista previa al {self.preview_scale:.0%}")
        self.status_bar.showMessage("Separado: " + "; ".join(parts) + ".")

    def on_threshold_changed(self):
        """
        Se ejecuta cuando cambia el valor del slider de umbral
        VERSIÓN MEJORADA CON DEBUG
        """
        try:
            if not hasattr(self, 'threshold_slider') or not hasattr(self, 'current_channel'):
                return
                
            if not self.current_channel or self.current_channel == "COMPOSITE":
                return
                
            # Obtener nuevo valor del slider
            new_threshold = self.threshold_slider.value()
            old_threshold = self.channel_thresholds.get(self.current_channel, 128)
            
            # Actualizar el valor almacenado para este canal
            self.channel_thresholds[self.current_channel] = new_threshold
            
            # Actualizar etiqueta de valor
            if hasattr(self, 'threshold_value_label'):
                self.threshold_value_label.setText(f"{new_threshold} / 255")
            
            print(f"🎯 UMBRAL CAMBIÓ: {self.current_channel} [{old_threshold} → {new_threshold}]")
            
            # Verificar si hay halftones activos
            has_halftones = hasattr(self, 'should_show_halftones') and self.should_show_halftones()
            print(f"   📊 Halftones activos: {has_halftones}")
            
            # ACTUALIZAR VISTA PREVIA INMEDIATAMENTE
            self.update_preview()
            
        except Exception as e:
            print(f"❌ Error en on_threshold_changed: {e}")
            import traceback
            traceback.print_exc()


    def get_selected_channels(self):
        """
        Función auxiliar para obtener canales seleccionados
        Útil para compatibilidad con código existente
        """
        try:
            selected_items = self.channel_list.selectedItems()
            return [item.text() for item in selected_items]
        except Exception as e:
            print(f"⚠️ Error obteniendo canales seleccionados: {e}")
            return []
        
    def on_channel_selection_changed(self):
        """
        Maneja la selección en la lista de canales. Activa el slider si se
        selecciona un solo canal.
        """
        selected_items = self.channel_list.selectedItems()
        
        is_single_selection = len(selected_items) == 1
        
        self.threshold_slider.setEnabled(is_single_selection)
        self.view_individual_channel_cb.setEnabled(is_single_selection)

        if is_single_selection:
            self.current_channel = selected_items[0].text()
            current_threshold = self.channel_thresholds.get(self.current_channel, 128)
            self.threshold_slider.blockSignals(True)
            self.threshold_slider.setValue(current_threshold)
            self.threshold_slider.blockSignals(False)
            self.threshold_label.setText(f"Umbral de {CHANNEL_NAMES.get(self.current_channel, self.current_channel).lower()}")
            self.threshold_value_label.setText(f"{current_threshold} / 255")
        else:
            self.current_channel = None
            self.threshold_label.setText("Umbral")
            self.threshold_value_label.setText("Selecciona un canal")
            # Si no hay un solo canal seleccionado, forzamos la vista de composición
            self.view_individual_channel_cb.setChecked(False)
            
        self.update_preview()

    def clear_preview(self):
        """
        Limpia la vista previa mostrando el color de fondo
        """
        try:
            if hasattr(self, 'preview_label'):
                # Crear imagen con el color de prenda de fondo
                pixmap = QtGui.QPixmap(400, 300)
                pixmap.fill(self.garment_color)
                
                # Agregar texto informativo
                painter = QtGui.QPainter(pixmap)
                painter.setPen(QtGui.QColor(100, 100, 100))
                painter.setFont(QtGui.QFont("Arial", 11))
                painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, 
                            "Selecciona uno o más canales\npara ver la vista previa")
                painter.end()
                
                self.preview_label.setPixmap(pixmap)
                
            if hasattr(self, 'status_bar'):
                self.status_bar.showMessage("Sin canales seleccionados")
                
        except Exception as e:
            print(f"⚠️ Error en clear_preview: {e}")

    def should_show_halftones(self):
        """
        Determina si mostrar halftones en vista previa basado en el checkbox
        """
        if hasattr(self, 'show_halftones_cb'):
            return self.show_halftones_cb.isChecked()
        return True  # Por defecto mostrar halftones si no existe el checkbox
                

    def restore_channel_color_default(self, channel):
        """Restaura el color de un canal a su valor por defecto."""
        self.channel_colors[channel] = self.PURE_CMYK_COLORS[channel]
        self.update_all_color_buttons_ui()
        self.update_channel_list_ui()
        self.update_preview()

        
    def show_mesh_info(self):
        """Mostrar información técnica de mallas"""
        info_html = """
        <h3>📐 Guía Técnica de Mallas Serigráficas</h3>
        <p>Selecciona la malla adecuada según tu aplicación:</p>
        """

        for category, details in APPLICATION_CATEGORIES.items():
            meshes_str = ", ".join([str(m) for m in details['meshes']])
            info_html += f"""
            <div style="margin: 15px 0; padding: 12px; border: 2px solid #4CAF50; border-radius: 8px;">
                <h4 style="color: #2E7D32;">🔸 {category}</h4>
                <p><b>Mallas:</b> {meshes_str}</p>
                <p><b>Aplicaciones:</b> {details['description']}</p>
                <p><b>LPI:</b> {details['lpi_range']} | <b>Depósito:</b> {details['ink_deposit']}</p>
            </div>
            """

        info_html += """
        <h4>💡 Reglas profesionales:</h4>
        <ul>
            <li><b>LPI óptimo:</b> Malla ÷ 2 a Malla ÷ 4.75</li>
            <li><b>Evitar moiré:</b> No usar múltiplos exactos</li>
            <li><b>Ganancia de punto:</b> Usar plantilla de verificación</li>
        </ul>

        <h4>🎯 Ángulos de Cuatricromía (aplicados automáticamente):</h4>
        <div style="background: #f0f8ff; padding: 10px; border-radius: 5px; margin: 10px 0;">
            <p><b>Cian (C):</b> 15° | <b>Magenta (M):</b> 75°</p>
            <p><b>Amarillo (Y):</b> 0° | <b>Negro (K):</b> 45°</p>
            <p><b>⚪ Base Blanca (W):</b> 90° - LPI fijo 45</p>
            <p><small>✨ Estos ángulos profesionales previenen automáticamente el efecto moiré</small></p>
        </div>

        <h4>🔍 Mejora de Resolución:</h4>
        <div style="background: #f8fff8; padding: 10px; border-radius: 5px; margin: 10px 0;">
            <p><b>📈 Recomendaciones según complejidad:</b></p>
            <p>• <b>Alta:</b> 200-300% para detalles finos</p>
            <p>• <b>Media:</b> 150-200% para calidad estándar</p>
            <p>• <b>Baja:</b> Mantener original puede ser suficiente</p>
            <p><small>🎯 Mayor resolución = positivos de mejor calidad</small></p>
        </div>

        <h4>⚪ Base Blanca Automática:</h4>
        <div style="background: #fffdf0; padding: 10px; border-radius: 5px; margin: 10px 0;">
            <p><b>📋 Configuración especializada:</b></p>
            <p>• <b>LPI fijo:</b> 45 (optimizado para cobertura)</p>
            <p>• <b>Ángulo:</b> 90° (evita interferencia)</p>
            <p>• <b>Uso:</b> Playeras oscuras y sustratos de color</p>
            <p><small>⚡ Se genera automáticamente detectando áreas que necesitan base</small></p>
        </div>
        """

        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle("Información Técnica Completa")
        msg_box.setTextFormat(QtCore.Qt.RichText)
        msg_box.setText(info_html)
        msg_box.exec_()

    def generate_dot_gain_template(self):
        """
        Guarda la plantilla de ganancia (lineaturas × porcentajes) con la forma
        de punto y el DPI actuales, junto con sus instrucciones.
        """
        settings = self.job_settings()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Guardar plantilla de ganancia",
            os.path.join(self.output_dir, f"plantilla_ganancia_{settings.dpi}dpi.png"), "PNG (*.png)")
        if not path:
            return
        output.save_png(path, output.dot_gain_template(settings), settings.dpi)
        with open(os.path.splitext(path)[0] + "_instrucciones.txt", "w", encoding="utf-8") as f:
            f.write("PLANTILLA DE GANANCIA DE PUNTO\n\n"
                    "1. Imprime la plantilla en película al 100 % (sin escalar).\n"
                    "2. Graba una pantalla con la malla que usarás en producción.\n"
                    "3. Estampa sobre la tela real con la tinta, racleta y presión de producción.\n"
                    "4. Con lupa o cuentahílos, estima el porcentaje impreso de cada parche en la fila\n"
                    "   de tu lineatura (por ejemplo, el 50 % de la película imprime 70 %).\n"
                    "5. En la app: Herramientas → Curva de ganancia medida…, anota los valores.\n"
                    "   La trama se compensará para que el tono impreso sea el del diseño.\n")
        self.status_bar.showMessage(f"Plantilla guardada: {os.path.basename(path)}", 10000)

    GAIN_CURVE_TONES = (10, 20, 30, 40, 50, 60, 70, 80, 90)

    def set_gain_curve(self, curve):
        self.dot_gain_curve = [list(point) for point in curve]
        active = bool(self.dot_gain_curve)
        self.dot_gain_spin.setEnabled(not active)
        self.gain_curve_btn.setText(f"Curva ({len(self.dot_gain_curve)} pts)" if active else "Curva medida…")
        self.schedule_rescreen()

    def edit_gain_curve(self):
        """Tabla para anotar el % impreso medido de cada % de película."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Curva de ganancia medida")
        layout = QtWidgets.QVBoxLayout(dialog)
        intro = QtWidgets.QLabel("Anota el porcentaje que imprimió cada parche de la plantilla, "
                                 "en la fila de tu lineatura. Deja 0 en los que no mediste.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QtWidgets.QFormLayout()
        current = {round(f): p for f, p in self.dot_gain_curve}
        spins = {}
        for tone_value in self.GAIN_CURVE_TONES:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0, 100)
            spin.setDecimals(0)
            spin.setSuffix(" %")
            spin.setValue(current.get(tone_value, 0))
            form.addRow(f"Película {tone_value} % imprime", spin)
            spins[tone_value] = spin
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        clear_btn = buttons.addButton("Borrar curva", QtWidgets.QDialogButtonBox.ResetRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        clear_btn.clicked.connect(lambda: [spin.setValue(0) for spin in spins.values()])
        layout.addWidget(buttons)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.set_gain_curve([[t, spin.value()] for t, spin in spins.items() if spin.value() > 0])

    def print_positives(self):
        """
        Imprime cada positivo en una página, a su tamaño físico real. La trama
        la calcula la app, así que sirve también en impresoras sin PostScript.
        """
        if self.image is None or not self.preview_cache:
            QtWidgets.QMessageBox.warning(self, "Nada que imprimir",
                                          "Abre una imagen y pulsa «Separar colores» antes de imprimir.")
            return
        from PyQt5 import QtPrintSupport
        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
        dialog = QtPrintSupport.QPrintDialog(printer, self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            pages = self.render_positives()
            print_films(printer, pages, self.job_settings().dpi)
            self.status_bar.showMessage(f"Enviados {len(pages)} positivos a la impresora", 10000)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def render_positives(self):
        """Positivos finales a resolución completa, en orden de impresión."""
        settings = self.job_settings()
        _, screens, _ = render(self.image, self.image_alpha, settings, preview=False)
        return [output.finish_positive(screens[ch], ch, settings) for ch in settings.channels()]


    def find_nearest_non_multiple(self, target_lpi, mesh_count):
        """Encontrar LPI cercano que no cause moiré"""
        # Verificar si es múltiplo problemático
        if mesh_count % target_lpi == 0 or target_lpi % (mesh_count // 10) == 0:
            # Buscar alternativas cercanas
            alternatives = []
            for offset in range(1, 6):
                for candidate in [target_lpi - offset, target_lpi + offset]:
                    if candidate > 0 and mesh_count % candidate != 0:
                        alternatives.append(candidate)

            return min(alternatives) if alternatives else target_lpi

        return target_lpi

    def calculate_lpi_from_mesh(self):
        """Calcular LPI basado en malla"""
        try:
            mesh = self.mesh_input.value()
            factor = self.calc_factor.value()

            result = self.calculate_optimal_lpi(mesh, factor)

            # Mostrar resultado principal
            lpi_text = f"""
            <h4 style="color: #2E7D32;">LPI Calculado: {result['optimal_lpi']}</h4>
            <p><b>Cálculo base:</b> {mesh} ÷ {factor} = {result['base_lpi']} LPI</p>
            <p><b>LPI optimizado (anti-moiré):</b> {result['optimal_lpi']} LPI</p>
            """
            self.lpi_result_label.setText(lpi_text)

            # Información técnica
            if mesh in MESH_SPECIFICATIONS:
                specs = MESH_SPECIFICATIONS[mesh]
                tech_text = f"""
                <b>📊 Especificaciones técnicas de malla {mesh}:</b><br>
                • Apertura: {specs['aperture']} μm<br>
                • Área abierta: {specs['open_area']}%<br>
                • Grosor de tinta: {specs['ink_thickness']} μm<br>
                • Aplicaciones: {', '.join(specs['applications'][:2])}<br>
                • LPI recomendado por fabricante: {'-'.join(map(str, specs['recommended_lpi']))}
                """
            else:
                # Encontrar malla más cercana
                closest_mesh = min(MESH_SPECIFICATIONS.keys(), key=lambda x: abs(x - mesh))
                tech_text = f"""
                <b>⚠️ Malla {mesh} no está en base de datos</b><br>
                <b>📊 Referencia más cercana (malla {closest_mesh}):</b><br>
                Considera usar especificaciones similares para tu cálculo.
                """

            self.tech_info_label.setText(tech_text)

        except Exception as e:
            self.lpi_result_label.setText(f"Error en cálculo: {str(e)}")

    def calculate_lpi_from_application(self):
        """Calcular LPI basado en aplicación"""
        try:
            app_name = self.app_selector.currentText()
            distance = self.viewing_distance.value()
            equipment_text = self.equipment_quality.currentText()

            # Extraer factor de equipo
            equipment_factor = float(equipment_text.split('factor ')[1].split(')')[0])

            app_details = APPLICATION_CATEGORIES[app_name]
            recommended_meshes = app_details['meshes']

            results_text = f"<h4 style='color: #2E7D32;'>Recomendaciones para {app_name}:</h4>"

            for mesh in recommended_meshes:
                result = self.calculate_optimal_lpi(mesh, 2.5, equipment_factor, distance)

                specs = MESH_SPECIFICATIONS[mesh]
                results_text += f"""
                <div style="margin: 8px 0; padding: 8px; border-left: 3px solid #4CAF50; background: #f0f8f0;">
                    <b>Malla {mesh}:</b> {result['optimal_lpi']} LPI óptimo<br>
                    <small>Depósito: {specs['ink_thickness']}μm | Aplicaciones: {', '.join(specs['applications'][:2])}</small>
                </div>
                """

            results_text += f"""
            <p style="margin-top: 15px;"><b>💡 Factores aplicados:</b><br>
            • Distancia de visualización: {distance}m (factor {self.calculate_optimal_lpi(120, 2.5, 1.0, distance)['distance_factor']})<br>
            • Calidad del equipo: {equipment_text}<br>
            • Depósito de tinta esperado: {app_details['ink_deposit']}</p>
            """

            self.app_result_label.setText(results_text)

        except Exception as e:
            self.app_result_label.setText(f"Error: {str(e)}")

    def check_moire_risk(self):
        """Verificar riesgo de moiré"""
        try:
            mesh = self.moire_mesh.value()
            lpi = self.moire_lpi.value()
            if lpi == 0: return # Avoid division by zero

            # Análisis de moiré
            is_exact_multiple = (mesh % lpi) == 0
            is_close_multiple = any((mesh % (lpi + i)) == 0 for i in range(-2, 3))
            is_harmonic = (lpi % (mesh // 10)) == 0 if (mesh // 10) > 0 else False

            risk_level = "BAJO"
            risk_color = "#4CAF50"

            if is_exact_multiple:
                risk_level = "CRÍTICO"
                risk_color = "#F44336"
            elif is_close_multiple or is_harmonic:
                risk_level = "ALTO"
                risk_color = "#FF9800"

            analysis_text = f"""
            <div style="background: {risk_color}; color: white; padding: 8px; border-radius: 4px; margin-bottom: 10px;">
                <b>RIESGO DE MOIRÉ: {risk_level}</b>
            </div>

            <b>📊 Análisis técnico:</b><br>
            • Malla {mesh} ÷ LPI {lpi} = {mesh/lpi:.2f}<br>
            • ¿Múltiplo exacto? {'❌ SÍ' if is_exact_multiple else '✅ NO'}<br>
            • ¿Múltiplo cercano? {'⚠️ SÍ' if is_close_multiple else '✅ NO'}<br>
            • ¿Armónico problemático? {'⚠️ SÍ' if is_harmonic else '✅ NO'}<br>
            """

            if risk_level != "BAJO":
                analysis_text += f"""<br><b style="color: {risk_color};">⚠️ RECOMENDACIÓN:</b> Cambiar LPI para evitar patrones de interferencia."""

            self.moire_result_label.setText(analysis_text)

            # Generar alternativas seguras
            safe_alternatives = []
            for candidate in range(max(10, lpi-5), lpi+6):
                if candidate != lpi and candidate > 0 and mesh % candidate != 0:
                    safe_alternatives.append(candidate)

            if safe_alternatives:
                alternatives_text = f"""
                <b>💡 LPI alternativos seguros:</b><br>
                {', '.join(map(str, safe_alternatives[:8]))}

                <br><br><b>🎯 Recomendación principal:</b> {safe_alternatives[len(safe_alternatives)//2]} LPI
                """
            else:
                alternatives_text = "No se encontraron alternativas en el rango cercano."

            self.alternatives_label.setText(alternatives_text)

        except Exception as e:
            self.moire_result_label.setText(f"Error: {str(e)}")

    def export_lpi_calculations(self, dialog):
        """Exportar cálculos de LPI a archivo"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"calculos_lpi_{timestamp}.txt"
            filepath = os.path.join(self.output_dir, filename)

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("CÁLCULOS DE LINEATURA (LPI) PROFESIONAL\n")
                f.write("=" * 50 + "\n\n")
                f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

                f.write("CÁLCULO POR MALLA:\n")
                f.write("-" * 20 + "\n")
                mesh = self.mesh_input.value()
                factor = self.calc_factor.value()
                result = self.calculate_optimal_lpi(mesh, factor)
                f.write(f"Malla: {mesh}\n")
                f.write(f"Factor: {factor}\n")
                f.write(f"LPI base: {result['base_lpi']}\n")
                f.write(f"LPI optimizado: {result['optimal_lpi']}\n\n")

                f.write("VERIFICACIÓN ANTI-MOIRÉ:\n")
                f.write("-" * 25 + "\n")
                moire_mesh = self.moire_mesh.value()
                moire_lpi = self.moire_lpi.value()
                if moire_lpi > 0:
                    f.write(f"Malla: {moire_mesh}\n")
                    f.write(f"LPI: {moire_lpi}\n")
                    f.write(f"Múltiplo exacto: {'SÍ' if (moire_mesh % moire_lpi) == 0 else 'NO'}\n")
                    f.write(f"Factor: {moire_mesh/moire_lpi:.2f}\n\n")

                f.write("RECOMENDACIONES GENERALES:\n")
                f.write("-" * 30 + "\n")
                f.write("• Usa la fórmula Malla ÷ 2.5 como punto de partida\n")
                f.write("• Evita múltiplos exactos para prevenir moiré\n")
                f.write("• Ajusta según distancia de visualización\n")
                f.write("• Considera la calidad de tu equipo\n")
                f.write("• Verifica con plantilla de ganancia de punto\n")

            msg = f"✅ Cálculos exportados:\n{filename}\n\nUbicación: {self.output_dir}"
            QtWidgets.QMessageBox.information(dialog, "Exportación Exitosa", msg)

        except Exception as e:
            QtWidgets.QMessageBox.critical(dialog, "Error", f"Error al exportar:\n{str(e)}")

    def save_results(self):
        """
        Exporta los positivos a resolución completa: un PNG por canal (con DPI),
        un PDF por página de canal, las especificaciones y la configuración
        del trabajo en JSON para poder repetirlo.
        """
        if self.image is None or not self.preview_cache:
            QtWidgets.QMessageBox.warning(self, "Nada que exportar",
                                          "Abre una imagen y pulsa «Separar colores» antes de exportar.")
            return

        folder_path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Carpeta para los positivos", self.output_dir)
        if not folder_path:
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            settings = self.job_settings()
            self.status_bar.showMessage("Generando positivos a resolución completa…")
            QtWidgets.QApplication.processEvents()

            _, screens, _ = render(self.image, self.image_alpha, settings, preview=False)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_files = []

            positives, clean_pages = [], []
            for channel in settings.channels():
                screen = screens[channel]
                positive = output.finish_positive(screen, channel, settings)
                path = output.save_positive(os.path.join(folder_path, f"POSITIVO_{channel}_{timestamp}"),
                                            positive, settings)
                saved_files.append(os.path.basename(path))
                positives.append(positive)
                clean_pages.append(output.place_on_paper(screen, settings) if settings.fit_to_paper else screen)

            pdf_name = f"cmyk_limpio_{timestamp}.pdf"
            output.save_pdf(os.path.join(folder_path, pdf_name), clean_pages, settings.dpi)
            saved_files.append(pdf_name)
            if settings.registration_guides:
                pdf_name = f"cmyk_con_guias_{timestamp}.pdf"
                output.save_pdf(os.path.join(folder_path, pdf_name), positives, settings.dpi)
                saved_files.append(pdf_name)

            config_name = f"configuracion_{timestamp}.json"
            settings.save(os.path.join(folder_path, config_name))
            saved_files.append(config_name)

            spec_name = f"especificaciones_{timestamp}.txt"
            self._write_specifications(os.path.join(folder_path, spec_name), settings, saved_files, positives)
            saved_files.append(spec_name)

            height_px, width_px = positives[0].shape
            size_mm = f"{width_px / settings.dpi * 25.4:.0f}×{height_px / settings.dpi * 25.4:.0f} mm"
            self.status_bar.showMessage(
                f"Exportados {len(settings.channels())} positivos de {size_mm} a {settings.dpi} DPI en {folder_path}",
                15000)
            QtWidgets.QMessageBox.information(
                self, "Positivos exportados",
                f"{len(saved_files)} archivos en:\n{folder_path}\n\n"
                f"Positivos de {size_mm} a {settings.dpi} DPI, {settings.lpi:g} LPI.\n\n"
                + "\n".join(saved_files))

        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "No se pudo exportar",
                                           f"{e}\n\nRevisa que la carpeta exista y tenga permiso de escritura.")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _write_specifications(self, path, settings, saved_files, positives):
        """Hoja de especificaciones del trabajo para el taller."""
        height_px, width_px = positives[0].shape
        with open(path, 'w', encoding='utf-8') as f:
            f.write("ESPECIFICACIONES DEL TRABAJO DE SERIGRAFÍA\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if self.image_info:
                f.write(f"Imagen: {os.path.basename(self.image_info.get('file_path', ''))}\n")
            f.write("\nSALIDA\n")
            f.write(f"  Papel: {settings.paper_width_mm:g} × {settings.paper_height_mm:g} mm\n")
            f.write(f"  Positivo: {width_px} × {height_px} px = "
                    f"{width_px / settings.dpi * 25.4:.1f} × {height_px / settings.dpi * 25.4:.1f} mm\n")
            f.write(f"  Resolución: {settings.dpi} DPI\n")
            f.write(f"  Ajustar al papel: {'sí' if settings.fit_to_paper else 'no'}\n")
            f.write(f"  Guías de registro: {'sí' if settings.registration_guides else 'no'}\n")
            f.write("\nTRAMA\n")
            f.write(f"  Lineatura: {settings.lpi:g} LPI (celda {settings.cell_px:.2f} px)\n")
            f.write(f"  Forma de punto: {self.shape_combo.currentText()}\n")
            f.write("\nSEPARACIÓN\n")
            f.write(f"  GCR: {settings.gcr:g}   Límite de tinta total: {settings.ink_limit:g} %\n")
            if settings.white_base:
                f.write(f"  Base blanca proporcional, choke {settings.white_base_choke_px} px\n")
            f.write(f"  Color de prenda: {self.garment_color.name()}\n")
            f.write("\nORDEN DE IMPRESIÓN\n")
            for i, channel in enumerate(settings.channels(), 1):
                f.write(f"  {i}. {CHANNEL_NAMES.get(channel, channel)}: ángulo "
                        f"{settings.angles.get(channel, 0):g}°, umbral {settings.thresholds.get(channel, 128)}\n")
            f.write("\nARCHIVOS\n")
            for name in saved_files:
                f.write(f"  {name}\n")

    def reset_channel_color(self):
        """
        Reset específico de colores - SINCRONIZADO con valores puros.
        Reemplaza tu función existente con esta versión.
        """
        print("🎨 Restableciendo color de canal a valor PURO...")

        # Obtiene el canal seleccionado
        if hasattr(self, "selected_channel"):
            canal = self.selected_channel
        else:
            # Busca el canal seleccionado en tu lista (ajusta según tu widget)
            selected_items = self.channel_list.selectedItems()
            if not selected_items:
                QtWidgets.QMessageBox.warning(self, "Sin selección", "Selecciona un canal primero")
                return
            # Se asume que el texto del item es solo la letra del canal, ej: "C"
            canal = selected_items[0].text()

        # --- LÍNEA CORREGIDA ---
        # Se accede al diccionario a través de 'self'
        color = self.PURE_CMYK_COLORS.get(canal)

        if color:
            self.channel_colors[canal] = color
            
            # Actualizar botón de color en la UI
            self.update_single_color_button(canal, color)
            
            # Redibuja la vista previa
            self.update_preview()

            print(f"✅ Color del canal {canal} restablecido a PURO: {color.name()}")
        else:
            print(f"❌ Canal {canal} no reconocido")


    def select_all_channels(self):
        self.channel_list.selectAll()
        self.update_preview()

    def show_lpi_calculator(self):
        """Calculadora inteligente de lineatura profesional"""
        try:
            # Crear diálogo especializado
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("🧮 Calculadora Inteligente de Lineatura (LPI)")
            dialog.setModal(True)
            dialog.resize(600, 700)

            layout = QtWidgets.QVBoxLayout(dialog)

            # Información principal
            info_label = QtWidgets.QLabel("""
            <h3>🧮 Calculadora Profesional de LPI</h3>
            <p>Calcula la lineatura óptima basada en especificaciones técnicas reales y
            evita automáticamente problemas de moiré siguiendo las mejores prácticas.</p>
            """)
            info_label.setWordWrap(True)
            layout.addWidget(info_label)

            # Pestañas para diferentes cálculos
            tabs = QtWidgets.QTabWidget()

            # === PESTAÑA 1: Cálculo por Malla ===
            mesh_tab = QtWidgets.QWidget()
            mesh_layout = QtWidgets.QFormLayout(mesh_tab)

            # Entrada de malla
            self.mesh_input = QtWidgets.QSpinBox()
            self.mesh_input.setRange(50, 500)
            self.mesh_input.setValue(120)
            self.mesh_input.valueChanged.connect(self.calculate_lpi_from_mesh)
            mesh_layout.addRow("🕸️ Número de malla:", self.mesh_input)

            # Factor de cálculo
            self.calc_factor = QtWidgets.QDoubleSpinBox()
            self.calc_factor.setRange(2.0, 5.0)
            self.calc_factor.setValue(2.5)
            self.calc_factor.setSingleStep(0.1)
            self.calc_factor.valueChanged.connect(self.calculate_lpi_from_mesh)
            mesh_layout.addRow("⚙️ Factor de cálculo:", self.calc_factor)

            # Resultados
            self.lpi_result_label = QtWidgets.QLabel()
            self.lpi_result_label.setStyleSheet("padding: 10px; border: 1px solid #ddd; background: #f9f9f9;")
            mesh_layout.addRow("📊 Resultado:", self.lpi_result_label)

            # Información técnica
            self.tech_info_label = QtWidgets.QLabel()
            self.tech_info_label.setWordWrap(True)
            self.tech_info_label.setStyleSheet("padding: 10px; border: 1px solid #4CAF50; background: #f8fff8;")
            mesh_layout.addRow("🔍 Información técnica:", self.tech_info_label)

            tabs.addTab(mesh_tab, "Por Malla")

            # === PESTAÑA 2: Cálculo por Aplicación ===
            app_tab = QtWidgets.QWidget()
            app_layout = QtWidgets.QFormLayout(app_tab)

            # Selector de aplicación
            self.app_selector = QtWidgets.QComboBox()
            self.app_selector.addItems(list(APPLICATION_CATEGORIES.keys()))
            self.app_selector.currentTextChanged.connect(self.calculate_lpi_from_application)
            app_layout.addRow("🎯 Aplicación:", self.app_selector)

            # Distancia de visualización
            self.viewing_distance = QtWidgets.QDoubleSpinBox()
            self.viewing_distance.setRange(0.1, 10.0)
            self.viewing_distance.setValue(1.0)
            self.viewing_distance.setSuffix(" metros")
            self.viewing_distance.valueChanged.connect(self.calculate_lpi_from_application)
            app_layout.addRow("👁️ Distancia de visualización:", self.viewing_distance)

            # Calidad del equipo
            self.equipment_quality = QtWidgets.QComboBox()
            self.equipment_quality.addItems([
                "Básico (factor 0.8)",
                "Estándar (factor 1.0)",
                "Profesional (factor 1.2)",
                "Industrial (factor 1.4)"
            ])
            self.equipment_quality.setCurrentIndex(1)
            self.equipment_quality.currentTextChanged.connect(self.calculate_lpi_from_application)
            app_layout.addRow("🏭 Calidad del equipo:", self.equipment_quality)

            # Resultados de aplicación
            self.app_result_label = QtWidgets.QLabel()
            self.app_result_label.setStyleSheet("padding: 10px; border: 1px solid #ddd; background: #f9f9f9;")
            app_layout.addRow("📋 Recomendaciones:", self.app_result_label)

            tabs.addTab(app_tab, "Por Aplicación")

            # === PESTAÑA 3: Verificador Anti-Moiré ===
            moire_tab = QtWidgets.QWidget()
            moire_layout = QtWidgets.QFormLayout(moire_tab)

            # Entrada para verificación
            self.moire_mesh = QtWidgets.QSpinBox()
            self.moire_mesh.setRange(50, 500)
            self.moire_mesh.setValue(120)
            self.moire_mesh.valueChanged.connect(self.check_moire_risk)
            moire_layout.addRow("🕸️ Malla:", self.moire_mesh)

            self.moire_lpi = QtWidgets.QSpinBox()
            self.moire_lpi.setRange(10, 120)
            self.moire_lpi.setValue(48)
            self.moire_lpi.valueChanged.connect(self.check_moire_risk)
            moire_layout.addRow("📏 LPI a verificar:", self.moire_lpi)

            # Resultado de verificación
            self.moire_result_label = QtWidgets.QLabel()
            self.moire_result_label.setWordWrap(True)
            self.moire_result_label.setStyleSheet("padding: 10px; border: 1px solid #ddd; background: #f9f9f9;")
            moire_layout.addRow("⚠️ Análisis de moiré:", self.moire_result_label)

            # Alternativas sugeridas
            self.alternatives_label = QtWidgets.QLabel()
            self.alternatives_label.setWordWrap(True)
            self.alternatives_label.setStyleSheet("padding: 10px; border: 1px solid #2196F3; background: #f0f8ff;")
            moire_layout.addRow("💡 Alternativas seguras:", self.alternatives_label)

            tabs.addTab(moire_tab, "Anti-Moiré")

            layout.addWidget(tabs)

            # Botones de acción
            button_layout = QtWidgets.QHBoxLayout()

            apply_btn = QtWidgets.QPushButton("✅ Aplicar al Proyecto")
            apply_btn.clicked.connect(lambda: self.apply_calculated_lpi(dialog))
            button_layout.addWidget(apply_btn)

            export_btn = QtWidgets.QPushButton("📄 Exportar Cálculos")
            export_btn.clicked.connect(lambda: self.export_lpi_calculations(dialog))
            button_layout.addWidget(export_btn)

            close_btn = QtWidgets.QPushButton("Cerrar")
            close_btn.clicked.connect(dialog.accept)
            button_layout.addWidget(close_btn)

            layout.addLayout(button_layout)

            # Calcular valores iniciales
            self.calculate_lpi_from_mesh()
            self.calculate_lpi_from_application()
            self.check_moire_risk()

            dialog.exec_()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Error en calculadora LPI:\n{str(e)}")

    def process_cmyk(self):
        """
        Separa y trama la vista previa. Trabaja a resolución reducida (misma
        cantidad de puntos por imagen) para responder rápido; la exportación
        vuelve a calcular todo a resolución completa.
        """
        if self.image is None:
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            settings = self.job_settings()
            self.channel_arrays, self.preview_cache, self.preview_scale = render(
                self.image, self.image_alpha, settings, preview=True)
            self.update_preview()

            self.show_quality_summary(settings)
        except Exception as e:
            print(f"❌ Error durante la separación: {e}")
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "No se pudo separar", str(e))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
    
    def apply_calculated_lpi(self, dialog):
        """Aplicar LPI calculado a la configuración actual"""
        try:
            # Obtener el LPI calculado
            lpi_value = self.lpi_result_label.text()
            if not lpi_value:
                QtWidgets.QMessageBox.warning(self, "Aviso", "Calcula un LPI primero")
                return

            # Extraer el valor numérico del LPI
            lpi_value = int(lpi_value.split()[0])  # Asume formato "LPI: 50"

            # Buscar la opción más cercana en la lista de LPI
            closest_option = None
            closest_diff = float('inf')

            for option in self.lpi_combo.findItems("", QtCore.Qt.MatchContains):
                option_text = option.text()
                option_value = int(option_text.split()[0])  # Asume formato "LPI: 50"
                diff = abs(option_value - lpi_value)

                if diff < closest_diff:
                    closest_diff = diff
                    closest_option = option_text

            if closest_option:
                print(f"🎯 Aplicando LPI optimizado: {closest_option}")
                self.lpi_combo.setCurrentText(closest_option)

                # Mostrar mensaje de éxito
                if dialog:
                    msg = f"✅ LPI optimizado aplicado: {closest_option}\n\n" \
                          "Este valor se ha seleccionado automáticamente para evitar moiré y asegurar una impresión de alta calidad.\n" \
                          "Asegúrate de revisar las especificaciones de tu impresora antes de imprimir."
                else:
                    msg = f"✅ LPI optimizado aplicado: {closest_option}\n\n" \
                          "Este valor se ha seleccionado automáticamente para evitar moiré y asegurar una impresión de alta calidad.\n" \
                          "Asegúrate de revisar las especificaciones de tu impresora antes de imprimir.\n\n" \
                          "Si necesitas ajustar manualmente, puedes hacerlo en la lista desplegable."
                    msg += "\n\nNota: Si no estás seguro, consulta con tu impresor o proveedor para confirmar que este LPI es adecuado para tu trabajo."
                    msg += "\n\nRecuerda que un LPI demasiado alto puede causar problemas de registro y calidad si no se maneja correctamente."
                    msg += "\n\nSi tienes dudas, consulta con un profesional o revisa las guías técnicas disponibles."

                if dialog:
                    QtWidgets.QMessageBox.information(dialog, "LPI Aplicado", msg)
                else:
                    QtWidgets.QMessageBox.information(self, "LPI Aplicado", msg)
            else:
                error_msg = "No se encontró un LPI cercano al calculado. Por favor, verifica el valor."
                print(f"❌ {error_msg}")
                if dialog:
                    QtWidgets.QMessageBox.warning(dialog, "Error", error_msg)
                else:
                    QtWidgets.QMessageBox.warning(self, "Error", error_msg)
        except Exception as e:
            error_msg = f"Error al aplicar LPI: {str(e)}"
            print(f"❌ {error_msg}")
            if dialog:
                QtWidgets.QMessageBox.critical(dialog, "Error", error_msg)
            else:
                QtWidgets.QMessageBox.critical(self, "Error", error_msg)

class MoireDetector:
    """
    Detector inteligente de patrones de moiré para serigrafía.
    VERSIÓN COMPLETA con todos los métodos necesarios.
    """
    
    def __init__(self):
        # UMBRALES MÁS REALISTAS
        self.risk_levels = {
            'CRITICO': {'color': '#D32F2F', 'threshold': 0.85},  # Solo casos extremos
            'ALTO': {'color': '#F57C00', 'threshold': 0.65},     # Casos problemáticos claros
            'MEDIO': {'color': '#FBC02D', 'threshold': 0.40},    # Algo de riesgo
            'BAJO': {'color': '#388E3C', 'threshold': 0.0}       # Casos seguros
        }
        
        self.common_meshes = [55, 77, 90, 110, 120, 140, 150, 160, 180, 200, 230, 260, 300, 350, 400]
        
    def analyze_moire_risk(self, lpi, mesh_count, channel_angles=None):
        """
        Análisis principal de riesgo de moiré - VERSIÓN CORREGIDA
        """
        try:
            # Casos críticos inmediatos
            if mesh_count % lpi == 0:
                print(f"🚨 MÚLTIPLO EXACTO: {mesh_count} ÷ {lpi} = {mesh_count/lpi}")
                return self._create_critical_result()
            
            # Análisis de frecuencias
            frequency_risk = self._analyze_frequency_interference_ultra(lpi, mesh_count)
            
            # Análisis de múltiplos
            multiple_risk = self._analyze_multiples_ultra(lpi, mesh_count)
            
            # Análisis de ángulos
            angle_risk = 0.0
            if channel_angles:
                angle_risk = self._analyze_angle_interference(lpi, mesh_count, channel_angles)
            
            # Calcular riesgo total (más peso a múltiplos)
            total_risk = (frequency_risk * 0.4 + multiple_risk * 0.6 + angle_risk * 0.0)
            
            # FORZAR MÍNIMO PARA CASOS PROBLEMÁTICOS
            ratio = mesh_count / lpi
            if self._is_problematic_ratio(ratio):
                total_risk = max(total_risk, 0.7)  # Forzar riesgo alto pero no crítico
                
            print(f"📊 Risks - Freq: {frequency_risk:.3f}, Mult: {multiple_risk:.3f}, Total: {total_risk:.3f}")
            
            # Determinar nivel de riesgo
            risk_level = self._get_risk_level(total_risk)
            
            # Generar recomendaciones
            recommendations = self._generate_recommendations(lpi, mesh_count, total_risk)
            
            return {
                'risk_level': risk_level,
                'risk_score': total_risk,
                'frequency_risk': frequency_risk,
                'multiple_risk': multiple_risk,
                'angle_risk': angle_risk,
                'recommendations': recommendations,
                'safe_alternatives': self._find_safe_alternatives(lpi, mesh_count)
            }
            
        except Exception as e:
            print(f"❌ Error en análisis de moiré: {e}")
            return self._get_default_analysis()
    
    def _is_problematic_ratio(self, ratio):
        """Detecta ratios problemáticos SOLO los realmente críticos"""
        problematic_ratios = [2.0, 3.0, 4.0, 5.0, 6.0]  # Solo enteros
        
        for prob_ratio in problematic_ratios:
            if abs(ratio - prob_ratio) < 0.15:  # Más tolerante
                print(f"⚠️ Ratio problemático detectado: {ratio:.3f} cerca de {prob_ratio}")
                return True
        return False
    
    def _analyze_frequency_interference_ultra(self, lpi, mesh_count):
        """VERSIÓN MÁS REALISTA de análisis de frecuencias"""
        if lpi == 0 or mesh_count == 0:
            return 0.0
            
        ratio = mesh_count / lpi
        print(f"🔢 Analizando ratio: {ratio:.3f}")
        
        # Solo ratios realmente críticos
        critical_ratios = [2.0, 3.0, 4.0, 5.0, 6.0]  # Simplificado
        
        min_distance = min(abs(ratio - cr) for cr in critical_ratios)
        print(f"📏 Distancia mínima a ratio crítico: {min_distance:.3f}")
        
        # UMBRALES MÁS REALISTAS
        if min_distance < 0.05:    # Prácticamente exacto
            return 0.95
        elif min_distance < 0.1:   # Muy cerca
            return 0.8
        elif min_distance < 0.2:   # Cerca
            return 0.6
        elif min_distance < 0.3:   # Moderadamente cerca
            return 0.4
        else:
            return 0.1  # Riesgo mínimo
    
    def _analyze_multiples_ultra(self, lpi, mesh_count):
        """VERSIÓN MÁS REALISTA de análisis de múltiplos"""
        if lpi == 0:
            return 0.0
            
        # Múltiplo exacto = crítico
        if mesh_count % lpi == 0:
            print(f"🚨 MÚLTIPLO EXACTO DETECTADO: {mesh_count} ÷ {lpi} = {mesh_count // lpi}")
            return 0.95
        
        # Analizar cercanía a múltiplos (más tolerante)
        remainder = mesh_count % lpi
        close_multiple = min(remainder, lpi - remainder)
        
        print(f"📐 Resto de división: {remainder}, Cercanía a múltiplo: {close_multiple}")
        
        # MÁS TOLERANTE
        if close_multiple <= 1:
            return 0.8
        elif close_multiple <= 2:
            return 0.6
        elif close_multiple <= 3:
            return 0.4
        else:
            return 0.1
    
    def _create_critical_result(self):
        """Resultado crítico inmediato"""
        return {
            'risk_level': 'CRITICO',
            'risk_score': 1.0,
            'frequency_risk': 1.0,
            'multiple_risk': 1.0,
            'angle_risk': 0.0,
            'recommendations': ["🚨 MÚLTIPLO EXACTO - CAMBIAR LPI INMEDIATAMENTE"],
            'safe_alternatives': []
        }
    
    def _analyze_angle_interference(self, lpi, mesh_count, channel_angles):
        """Análisis de interferencia entre ángulos"""
        return 0.0  # Simplificado por ahora
    
    def _get_risk_level(self, risk_score):
        """Determina el nivel de riesgo - UMBRALES REALISTAS"""
        if risk_score >= 0.85:      # Solo casos extremos
            return 'CRITICO'
        elif risk_score >= 0.65:    # Casos problemáticos claros
            return 'ALTO'
        elif risk_score >= 0.40:    # Algo de riesgo
            return 'MEDIO'
        else:
            return 'BAJO'
    
    def _generate_recommendations(self, lpi, mesh_count, risk_score):
        """Genera recomendaciones específicas"""
        recommendations = []
        
        if risk_score >= 0.85:
            recommendations.append("🚨 CAMBIAR LPI INMEDIATAMENTE")
            recommendations.append("📏 Usar LPI ±3-5 unidades del actual")
            
        if mesh_count % lpi == 0:
            recommendations.append("⚠️ Múltiplo exacto detectado")
            
        if risk_score >= 0.65:
            recommendations.append("🧪 Hacer prueba antes de producción")
            recommendations.append("🔍 Verificar con lupa en material final")
        elif risk_score >= 0.40:
            recommendations.append("✅ Riesgo moderado - se puede usar con precaución")
        else:
            recommendations.append("✅ Configuración segura para impresión")
            
        return recommendations
    
    def _find_safe_alternatives(self, lpi, mesh_count):
        """Encuentra LPI alternativos seguros"""
        safe_alternatives = []
        
        for candidate in range(max(15, lpi - 8), lpi + 9):
            if candidate != lpi:
                # Análisis rápido
                if mesh_count % candidate != 0:  # No es múltiplo exacto
                    ratio = mesh_count / candidate
                    if not self._is_problematic_ratio(ratio):
                        safe_alternatives.append({
                            'lpi': candidate,
                            'risk_level': 'BAJO',
                            'risk_score': 0.1
                        })
        
        return safe_alternatives[:5]
    
    def _get_default_analysis(self):
        """Análisis por defecto en caso de error"""
        return {
            'risk_level': 'MEDIO',
            'risk_score': 0.5,
            'frequency_risk': 0.5,
            'multiple_risk': 0.5,
            'angle_risk': 0.0,
            'recommendations': ["⚠️ No se pudo analizar completamente"],
            'safe_alternatives': []
        }

# CLASES PARA EL SISTEMA DE DETECCIÓN DE MOIRÉ MEJORADO
# Agrega estas clases a tu main_window.py

class MoireWarningWidget(QtWidgets.QWidget):
    """
    Widget de alerta visual MEJORADO - más grande y funcional
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(52)
        self.risk_level = 'BAJO'
        self.risk_score = 0.0
        self.current_lpi = 0
        self.current_mesh = 0
        self.analysis_result = {}
        self.parent_window = parent  # Referencia a la ventana principal
        
        self.setup_ui()
        
    def setup_ui(self):
        """Franja de estado: barra lateral de color, veredicto, detalle y botón."""
        self.setObjectName("detectorMoire")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(8)

        # Se conserva para compatibilidad; el estado se comunica con color y texto
        self.status_icon = QtWidgets.QLabel()
        self.status_icon.setVisible(False)

        info_layout = QtWidgets.QVBoxLayout()
        info_layout.setSpacing(1)
        info_layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QtWidgets.QLabel("Moiré: sin analizar")
        self.status_label.setWordWrap(True)
        info_layout.addWidget(self.status_label)

        self.detail_label = QtWidgets.QLabel("")
        self.detail_label.setProperty("rol", "secundario")
        self.detail_label.setWordWrap(True)
        info_layout.addWidget(self.detail_label)
        layout.addLayout(info_layout, 1)

        self.analyze_btn = QtWidgets.QPushButton("Detalles")
        self.analyze_btn.setToolTip("Ver el análisis completo y alternativas de LPI")
        self.analyze_btn.clicked.connect(self.show_detailed_analysis)
        layout.addWidget(self.analyze_btn)
        
        # No usar addStretch() para evitar problemas de layout
        
    def update_moire_status(self, lpi, mesh, analysis_result, level='ok', message='', recommendation=''):
        """
        Muestra la evaluación malla/LPI (hilos por línea y relación entera).
        El análisis detallado del detector queda en «Detalles».
        """
        self.current_lpi = lpi
        self.current_mesh = mesh
        self.risk_level = analysis_result['risk_level']
        self.risk_score = analysis_result['risk_score']
        self.analysis_result = analysis_result

        verdicts = {'ok': "Malla y lineatura compatibles",
                    'aviso': "Revisa la lineatura",
                    'riesgo': "Malla muy abierta para esta lineatura"}
        color = {'ok': theme.ESTADO_OK, 'aviso': theme.ESTADO_ALERTA, 'riesgo': theme.ESTADO_RIESGO}[level]

        self.status_label.setText(verdicts[level])
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.detail_label.setText(f"{message} {recommendation}".strip())
        self.setStyleSheet(f"""
            QWidget#detectorMoire {{
                background: white;
                border: 1px solid {theme.LINEA};
                border-left: 4px solid {color};
                border-radius: 3px;
            }}
        """)
        self.analyze_btn.setText("Corregir…" if level == 'riesgo' else "Detalles")
        
    def show_detailed_analysis(self):
        """Mostrar análisis detallado en diálogo mejorado"""
        if not hasattr(self, 'analysis_result') or not self.analysis_result:
            QtWidgets.QMessageBox.information(self, "Sin datos", 
                "No hay análisis de moiré disponible.\n\nCarga una imagen y configura los parámetros LPI primero.")
            return
            
        try:
            # Crear diálogo pasando la referencia correcta a la ventana principal
            dialog = MoireAnalysisDialog(
                self.analysis_result, 
                self.current_lpi, 
                self.current_mesh, 
                self.parent_window  # Pasar la ventana principal
            )
            dialog.exec_()
            
        except Exception as e:
            print(f"❌ Error mostrando análisis: {e}")
            QtWidgets.QMessageBox.critical(self, "Error", 
                f"Error al mostrar el análisis detallado:\n{str(e)}")


# DIÁLOGO DE ANÁLISIS DE MOIRÉ MEJORADO

class MoireAnalysisDialog(QtWidgets.QDialog):
    """
    Diálogo MEJORADO con análisis detallado de moiré y recomendaciones.
    """
    
    def __init__(self, analysis_result, lpi, mesh, parent=None):
        super().__init__(parent)
        self.analysis_result = analysis_result
        self.lpi = lpi
        self.mesh = mesh
        self.parent_window = parent  # Referencia a la ventana principal
        
        self.setWindowTitle("🔍 Análisis Detallado de Moiré")
        self.setMinimumSize(600, 700)
        self.setMaximumSize(800, 900)
        self.setup_ui()
        
    def setup_ui(self):
        """Configurar interfaz mejorada del diálogo"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # === TÍTULO PRINCIPAL ===
        title_widget = QtWidgets.QWidget()
        title_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                           stop:0 #2196F3, stop:1 #21CBF3);
                border-radius: 10px;
                padding: 15px;
            }
        """)
        title_layout = QtWidgets.QVBoxLayout(title_widget)
        
        main_title = QtWidgets.QLabel(f"📊 Análisis de Moiré Profesional")
        main_title.setStyleSheet("font-size: 18px; font-weight: bold; color: white; margin: 0;")
        main_title.setAlignment(QtCore.Qt.AlignCenter)
        title_layout.addWidget(main_title)
        
        subtitle = QtWidgets.QLabel(f"LPI {self.lpi} vs Malla {self.mesh}")
        subtitle.setStyleSheet("font-size: 14px; color: white; margin: 0;")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        title_layout.addWidget(subtitle)
        
        layout.addWidget(title_widget)
        
        # === SCROLL AREA PARA CONTENIDO ===
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        
        content_widget = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_widget)
        content_layout.setSpacing(12)
        
        # === RESUMEN DE RIESGO MEJORADO ===
        self.add_improved_risk_summary(content_layout)
        
        # === ANÁLISIS TÉCNICO MEJORADO ===
        self.add_improved_technical_analysis(content_layout)
        
        # === RECOMENDACIONES MEJORADAS ===
        self.add_improved_recommendations(content_layout)
        
        # === ALTERNATIVAS SEGURAS MEJORADAS ===
        self.add_improved_safe_alternatives(content_layout)
        
        scroll.setWidget(content_widget)
        layout.addWidget(scroll)
        
        # === BOTONES MEJORADOS ===
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)
        
        # Botón de aplicar - SIEMPRE mostrar si hay alternativas O si es crítico
        alternatives = self.analysis_result.get('safe_alternatives', [])
        risk_level = self.analysis_result.get('risk_level', 'BAJO')
        
        # Mostrar botón si hay alternativas O si es crítico (para cambiar a algo mejor)
        show_apply_button = len(alternatives) > 0 or risk_level == 'CRITICO'
        
        if show_apply_button:
            apply_btn = QtWidgets.QPushButton("✅ Aplicar LPI Recomendado")
            apply_btn.setMinimumHeight(45)
            apply_btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                               stop:0 #4CAF50, stop:1 #45a049);
                    color: white;
                    font-weight: bold;
                    font-size: 12px;
                    border: none;
                    border-radius: 8px;
                    padding: 10px 20px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                               stop:0 #5CBF60, stop:1 #4CAF50);
                }
                QPushButton:pressed {
                    background: #45a049;
                }
            """)
            apply_btn.clicked.connect(self.apply_recommended_lpi)
            button_layout.addWidget(apply_btn)
        
        # Botón de cerrar
        close_btn = QtWidgets.QPushButton("Cerrar")
        close_btn.setMinimumHeight(45)
        close_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                           stop:0 #757575, stop:1 #616161);
                color: white;
                font-weight: bold;
                font-size: 12px;
                border: none;
                border-radius: 8px;
                padding: 10px 20px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                           stop:0 #858585, stop:1 #757575);
            }
        """)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
    def add_improved_risk_summary(self, layout):
        """Agregar resumen de riesgo mejorado"""
        group = QtWidgets.QGroupBox()
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                border: 2px solid #ddd;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 10px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px 0 8px;
                background: white;
            }
        """)
        group.setTitle("📈 Resumen de Riesgo")
        
        group_layout = QtWidgets.QVBoxLayout(group)
        group_layout.setSpacing(10)
        group_layout.setContentsMargins(15, 15, 15, 15)
        
        risk_level = self.analysis_result['risk_level']
        risk_score = self.analysis_result['risk_score']
        
        # === INDICADOR VISUAL DE RIESGO ===
        risk_widget = QtWidgets.QWidget()
        risk_layout = QtWidgets.QHBoxLayout(risk_widget)
        risk_layout.setContentsMargins(0, 0, 0, 0)
        
        # Icono de riesgo
        risk_icons = {
            'CRITICO': '🔴', 'ALTO': '🟠', 'MEDIO': '🟡', 'BAJO': '🟢'
        }
        risk_icon = QtWidgets.QLabel(risk_icons.get(risk_level, '⚪'))
        risk_icon.setStyleSheet("font-size: 24px;")
        risk_layout.addWidget(risk_icon)
        
        # Texto de riesgo
        risk_text = QtWidgets.QLabel(f"Nivel de Riesgo: {risk_level}")
        risk_text.setStyleSheet("font-size: 16px; font-weight: bold; margin-left: 10px;")
        risk_layout.addWidget(risk_text)
        
        risk_layout.addStretch()
        
        # Porcentaje
        percentage_label = QtWidgets.QLabel(f"{risk_score:.1%}")
        percentage_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #2196F3;")
        risk_layout.addWidget(percentage_label)
        
        group_layout.addWidget(risk_widget)
        
        # === BARRA DE PROGRESO VISUAL ===
        progress_container = QtWidgets.QWidget()
        progress_layout = QtWidgets.QVBoxLayout(progress_container)
        progress_layout.setContentsMargins(0, 5, 0, 5)
        
        progress_label = QtWidgets.QLabel("Nivel de Riesgo:")
        progress_label.setStyleSheet("font-size: 12px; color: #666; margin-bottom: 5px;")
        progress_layout.addWidget(progress_label)
        
        progress = QtWidgets.QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(int(risk_score * 100))
        progress.setMinimumHeight(25)
        
        # Colores según riesgo
        risk_colors = {
            'CRITICO': '#F44336', 'ALTO': '#FF9800', 'MEDIO': '#FFC107', 'BAJO': '#4CAF50'
        }
        color = risk_colors.get(risk_level, '#666')
        
        progress.setStyleSheet(f"""
            QProgressBar {{
                border: 2px solid #ddd;
                border-radius: 8px;
                text-align: center;
                font-weight: bold;
                font-size: 11px;
                background: #f0f0f0;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                           stop:0 {color}, stop:1 {color}AA);
                border-radius: 6px;
            }}
        """)
        
        progress_layout.addWidget(progress)
        group_layout.addWidget(progress_container)
        
        layout.addWidget(group)
        
    def add_improved_technical_analysis(self, layout):
        """Agregar análisis técnico mejorado"""
        group = QtWidgets.QGroupBox()
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                border: 2px solid #ddd;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 10px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px 0 8px;
                background: white;
            }
        """)
        group.setTitle("🔬 Análisis Técnico Detallado")
        
        group_layout = QtWidgets.QGridLayout(group)
        group_layout.setSpacing(12)
        group_layout.setContentsMargins(15, 15, 15, 15)
        
        # === DATOS TÉCNICOS ===
        technical_data = [
            ("🔄 Interferencia de frecuencia:", f"{self.analysis_result['frequency_risk']:.1%}"),
            ("⚠️ Riesgo de múltiplos:", f"{self.analysis_result['multiple_risk']:.1%}"),
            ("🔀 Interferencia de ángulos:", f"{self.analysis_result['angle_risk']:.1%}"),
            ("📏 Relación Malla/LPI:", f"{self.mesh / self.lpi if self.lpi > 0 else 0:.2f}"),
            ("🔢 Resto de división:", f"{self.mesh % self.lpi if self.lpi > 0 else 0}"),
        ]
        
        row = 0
        for label_text, value_text in technical_data:
            # Label
            label = QtWidgets.QLabel(label_text)
            label.setStyleSheet("font-size: 12px; font-weight: bold; padding: 8px;")
            group_layout.addWidget(label, row, 0)
            
            # Valor en un widget destacado
            value_widget = QtWidgets.QLabel(value_text)
            value_widget.setStyleSheet("""
                QLabel {
                    background: #f8f9fa;
                    border: 1px solid #dee2e6;
                    border-radius: 6px;
                    padding: 8px 12px;
                    font-size: 12px;
                    font-weight: bold;
                    color: #495057;
                }
            """)
            value_widget.setAlignment(QtCore.Qt.AlignCenter)
            group_layout.addWidget(value_widget, row, 1)
            
            row += 1
            
        layout.addWidget(group)
        
    def add_improved_recommendations(self, layout):
        """Agregar recomendaciones mejoradas"""
        group = QtWidgets.QGroupBox()
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                border: 2px solid #ddd;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 10px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px 0 8px;
                background: white;
            }
        """)
        group.setTitle("💡 Recomendaciones Profesionales")
        
        group_layout = QtWidgets.QVBoxLayout(group)
        group_layout.setSpacing(8)
        group_layout.setContentsMargins(15, 15, 15, 15)
        
        for i, recommendation in enumerate(self.analysis_result['recommendations']):
            rec_widget = QtWidgets.QWidget()
            rec_layout = QtWidgets.QHBoxLayout(rec_widget)
            rec_layout.setContentsMargins(0, 0, 0, 0)
            rec_layout.setSpacing(10)
            
            # Número de recomendación
            number_label = QtWidgets.QLabel(f"{i+1}")
            number_label.setFixedSize(25, 25)
            number_label.setStyleSheet("""
                QLabel {
                    background: #2196F3;
                    color: white;
                    border-radius: 12px;
                    font-weight: bold;
                    font-size: 12px;
                }
            """)
            number_label.setAlignment(QtCore.Qt.AlignCenter)
            rec_layout.addWidget(number_label)
            
            # Texto de recomendación
            rec_label = QtWidgets.QLabel(recommendation)
            rec_label.setWordWrap(True)
            rec_label.setStyleSheet("""
                QLabel {
                    font-size: 12px;
                    line-height: 1.4;
                    padding: 8px 12px;
                    background: #f8f9fa;
                    border-left: 4px solid #2196F3;
                    border-radius: 4px;
                }
            """)
            rec_layout.addWidget(rec_label)
            
            group_layout.addWidget(rec_widget)
            
        layout.addWidget(group)
        
    def add_improved_safe_alternatives(self, layout):
        """Agregar alternativas seguras mejoradas"""
        group = QtWidgets.QGroupBox()
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                border: 2px solid #ddd;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 10px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px 0 8px;
                background: white;
            }
        """)
        group.setTitle("🛡️ LPI Alternativos Seguros")
        
        group_layout = QtWidgets.QVBoxLayout(group)
        group_layout.setSpacing(8)
        group_layout.setContentsMargins(15, 15, 15, 15)
        
        alternatives = self.analysis_result['safe_alternatives']
        risk_level = self.analysis_result.get('risk_level', 'BAJO')
        
        if alternatives:
            for i, alt in enumerate(alternatives[:4]):  # Top 4
                alt_widget = QtWidgets.QWidget()
                alt_widget.setStyleSheet("""
                    QWidget {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                                   stop:0 #E8F5E8, stop:1 #F0FFF0);
                        border: 2px solid #4CAF50;
                        border-radius: 8px;
                        padding: 8px;
                        margin: 2px;
                    }
                """)
                
                alt_layout = QtWidgets.QHBoxLayout(alt_widget)
                alt_layout.setContentsMargins(10, 8, 10, 8)
                
                # Medalla/ranking
                rank_label = QtWidgets.QLabel(["🥇", "🥈", "🥉", "🏅"][i])
                rank_label.setStyleSheet("font-size: 20px;")
                alt_layout.addWidget(rank_label)
                
                # Información del LPI
                info_layout = QtWidgets.QVBoxLayout()
                info_layout.setSpacing(2)
                
                lpi_label = QtWidgets.QLabel(f"LPI {alt['lpi']}")
                lpi_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #2E7D32;")
                info_layout.addWidget(lpi_label)
                
                risk_label = QtWidgets.QLabel(f"Riesgo: {alt['risk_level']} ({alt['risk_score']:.1%})")
                risk_label.setStyleSheet("font-size: 11px; color: #666;")
                info_layout.addWidget(risk_label)
                
                alt_layout.addLayout(info_layout)
                alt_layout.addStretch()
                
                # Badge de recomendado
                if i == 0:
                    recommended_badge = QtWidgets.QLabel("⭐ RECOMENDADO")
                    recommended_badge.setStyleSheet("""
                        QLabel {
                            background: #4CAF50;
                            color: white;
                            padding: 4px 8px;
                            border-radius: 12px;
                            font-size: 10px;
                            font-weight: bold;
                        }
                    """)
                    alt_layout.addWidget(recommended_badge)
                
                group_layout.addWidget(alt_widget)
                
        elif risk_level == 'CRITICO':
            # Para casos críticos sin alternativas automáticas, dar sugerencias manuales
            critical_widget = QtWidgets.QWidget()
            critical_widget.setStyleSheet("""
                QWidget {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                               stop:0 #FFF3E0, stop:1 #FFECB3);
                    border: 2px solid #FF9800;
                    border-radius: 8px;
                    padding: 12px;
                    margin: 2px;
                }
            """)
            
            critical_layout = QtWidgets.QVBoxLayout(critical_widget)
            critical_layout.setSpacing(8)
            
            warning_title = QtWidgets.QLabel("🚨 CONFIGURACIÓN CRÍTICA DETECTADA")
            warning_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #E65100;")
            warning_title.setAlignment(QtCore.Qt.AlignCenter)
            critical_layout.addWidget(warning_title)
            
            current_ratio = self.mesh / self.lpi if self.lpi > 0 else 0
            
            suggestion_text = f"""
<b>📊 Problema actual:</b> LPI {self.lpi} con malla {self.mesh} (ratio: {current_ratio:.1f})<br>
<b>🔧 Sugerencias manuales:</b><br>
• <b>LPI 25:</b> Seguro para la mayoría de mallas<br>
• <b>LPI 30:</b> Buena calidad, compatible<br>
• <b>LPI 35:</b> Calidad superior, verificar compatibilidad<br><br>
<b>💡 Regla general:</b> Evita múltiplos exactos (malla ÷ LPI = número entero)
            """
            
            suggestion_label = QtWidgets.QLabel(suggestion_text)
            suggestion_label.setWordWrap(True)
            suggestion_label.setStyleSheet("font-size: 11px; line-height: 1.3; color: #333;")
            critical_layout.addWidget(suggestion_label)
            
            group_layout.addWidget(critical_widget)
            
        else:
            no_alt_label = QtWidgets.QLabel("❌ No se encontraron alternativas mejores en el rango analizado.")
            no_alt_label.setStyleSheet("""
                QLabel {
                    font-size: 12px;
                    color: #666;
                    background: #fff3cd;
                    border: 1px solid #ffeeba;
                    border-radius: 6px;
                    padding: 12px;
                }
            """)
            no_alt_label.setWordWrap(True)
            group_layout.addWidget(no_alt_label)
            
        layout.addWidget(group)
        
    def apply_recommended_lpi(self):
        """FUNCIÓN COMPLETAMENTE CORREGIDA: Aplicar LPI recomendado"""
        try:
            print("🔧 Intentando aplicar LPI recomendado...")
            
            # === PASO 1: Determinar LPI objetivo ===
            alternatives = self.analysis_result.get('safe_alternatives', [])
            risk_level = self.analysis_result.get('risk_level', 'BAJO')
            
            target_lpi = None
            
            if alternatives:
                # Usar la primera alternativa
                target_lpi = alternatives[0]['lpi']
                print(f"📋 LPI de alternativas: {target_lpi}")
            elif risk_level == 'CRITICO':
                # Para casos críticos, sugerir LPI basado en reglas
                if self.lpi > 0:
                    # Intentar LPIs seguros comunes
                    safe_lpis = [25, 30, 35, 40, 50, 60]
                    # Buscar el más cercano pero diferente
                    target_lpi = min(safe_lpis, key=lambda x: abs(x - self.lpi) if x != self.lpi else 999)
                    print(f"🚨 LPI crítico calculado: {target_lpi}")
            
            if not target_lpi:
                QtWidgets.QMessageBox.warning(self, "⚠️ Sin recomendación", 
                    "No se pudo determinar un LPI recomendado para esta configuración.")
                return
            
            # === PASO 2: Buscar ventana principal con múltiples métodos ===
            main_window = None
            
            # Método 1: parent_window directo
            if hasattr(self, 'parent_window') and self.parent_window:
                main_window = self.parent_window
                print("✅ Método 1: parent_window encontrado")
            
            # Método 2: Buscar por tipo de clase
            if not main_window:
                parent = self.parent()
                while parent:
                    if hasattr(parent, 'lpi_combo'):
                        main_window = parent
                        print("✅ Método 2: Ventana principal encontrada por tipo")
                        break
                    parent = parent.parent()
            
            # Método 3: Buscar en todas las ventanas abiertas
            if not main_window:
                from PyQt5.QtWidgets import QApplication
                for widget in QApplication.topLevelWidgets():
                    if hasattr(widget, 'lpi_combo') and hasattr(widget, 'update_moire_analysis'):
                        main_window = widget
                        print("✅ Método 3: Ventana principal encontrada globalmente")
                        break
            
            if not main_window:
                QtWidgets.QMessageBox.critical(self, "❌ Error", 
                    "No se pudo encontrar la ventana principal de la aplicación.")
                print("❌ No se encontró ventana principal")
                return
            
            # === PASO 3: Verificar que tiene el combo de LPI ===
            if not hasattr(main_window, 'lpi_combo'):
                QtWidgets.QMessageBox.critical(self, "❌ Error", 
                    "La ventana principal no tiene el control de LPI.")
                print("❌ No tiene lpi_combo")
                return
            
            combo = main_window.lpi_combo
            print(f"🎯 Combo encontrado con {combo.count()} opciones")
            
            # === PASO 4: Buscar mejor coincidencia ===
            best_match = None
            best_match_index = -1
            min_diff = float('inf')
            
            for i in range(combo.count()):
                item_text = combo.itemText(i)
                try:
                    # Extraer número del texto (varios formatos posibles)
                    # "25 LPI (malla 90)" -> 25
                    # "30 LPI" -> 30
                    # "25" -> 25
                    
                    parts = item_text.split()
                    lpi_value = int(parts[0])
                    
                    diff = abs(lpi_value - target_lpi)
                    
                    if diff < min_diff:
                        min_diff = diff
                        best_match = item_text
                        best_match_index = i
                        
                    print(f"   Opción {i}: '{item_text}' -> LPI {lpi_value}, diff: {diff}")
                        
                except (ValueError, IndexError) as e:
                    print(f"   Opción {i}: '{item_text}' -> Error parseando: {e}")
                    continue
            
            if best_match is None:
                QtWidgets.QMessageBox.warning(self, "⚠️ Aviso", 
                    f"No se encontró ningún LPI compatible en las opciones disponibles.")
                print("❌ No se encontró coincidencia")
                return
            
            # === PASO 5: Aplicar el cambio ===
            print(f"🎯 Aplicando LPI: {best_match} (índice {best_match_index})")
            
            old_lpi = combo.currentText()
            combo.setCurrentIndex(best_match_index)
            new_lpi = combo.currentText()
            
            print(f"🔄 Cambio: '{old_lpi}' -> '{new_lpi}'")
            
            # === PASO 6: Forzar actualización ===
            if hasattr(main_window, 'update_moire_analysis'):
                main_window.update_moire_analysis()
                print("🔄 Análisis de moiré actualizado")
            
            # Forzar actualización de cualquier preview si existe
            if hasattr(main_window, 'update_preview'):
                main_window.update_preview()
                print("🔄 Vista previa actualizada")
            
            # === PASO 7: Mensaje de éxito ===
            if min_diff == 0:
                exactitud = "✅ Coincidencia exacta"
            elif min_diff <= 2:
                exactitud = f"✅ Coincidencia muy cercana (diferencia: {min_diff})"
            else:
                exactitud = f"⚠️ Mejor opción disponible (diferencia: {min_diff})"
            
            success_msg = f"""🎉 LPI aplicado exitosamente

🎯 **LPI objetivo:** {target_lpi}
📊 **LPI aplicado:** {best_match}
📈 **Exactitud:** {exactitud}

⚡ **Cambios realizados:**
• Configuración de LPI actualizada
• Análisis de moiré recalculado
• Vista previa actualizada (si disponible)

💡 **Próximo paso:** Procesa tu imagen con esta nueva configuración optimizada."""
            
            QtWidgets.QMessageBox.information(self, "🎉 LPI Aplicado", success_msg)
            self.accept()  # Cerrar diálogo
            
            print("✅ Proceso completado exitosamente")
                
        except Exception as e:
            error_msg = f"Error interno al aplicar LPI recomendado:\n\n{str(e)}"
            print(f"❌ Error: {error_msg}")
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "❌ Error Técnico", 
                f"Error interno en la aplicación:\n\n{str(e)}\n\nRevisa la consola para más detalles.")
