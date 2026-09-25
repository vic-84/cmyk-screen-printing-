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
import math
from PyQt5 import QtWidgets, QtGui, QtCore

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

from ..core.halftone import apply_simple_halftone, apply_cmyk_halftone, apply_floyd_steinberg_halftone
from ..core.image_processing import (
    enhance_image_resolution, generate_white_base, detect_image_complexity,
    prepare_image_for_processing, resize_to_print_format
)
from ..utils.constants import *
from ..utils.helpers import convert_units, format_dimension_display
from .pdf_selector import PDFPageSelector


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
        self.image_label.setStyleSheet("border: 1px solid #ccc;")
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
        """Crear controles de zoom superpuestos"""
        # Widget contenedor para controles
        self.controls_widget = QtWidgets.QWidget(self)
        self.controls_widget.setFixedSize(200, 80)
        self.controls_widget.setStyleSheet("""
            QWidget {
                background-color: rgba(255, 255, 255, 230);
                border: 1px solid #ccc;
                border-radius: 5px;
            }
        """)
        
        # Layout de controles
        controls_layout = QtWidgets.QVBoxLayout(self.controls_widget)
        controls_layout.setContentsMargins(5, 5, 5, 5)
        
        # Etiqueta de zoom
        self.zoom_label = QtWidgets.QLabel("Zoom: 100%")
        self.zoom_label.setAlignment(QtCore.Qt.AlignCenter)
        self.zoom_label.setStyleSheet("font-weight: bold; font-size: 11px;")
        controls_layout.addWidget(self.zoom_label)
        
        # Botones de zoom
        button_layout = QtWidgets.QHBoxLayout()
        
        self.zoom_out_btn = QtWidgets.QPushButton("➖")
        self.zoom_out_btn.setFixedSize(25, 25)
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        button_layout.addWidget(self.zoom_out_btn)
        
        self.zoom_fit_btn = QtWidgets.QPushButton("📐")
        self.zoom_fit_btn.setFixedSize(25, 25)
        self.zoom_fit_btn.setToolTip("Ajustar al tamaño")
        self.zoom_fit_btn.clicked.connect(self.zoom_to_fit)
        button_layout.addWidget(self.zoom_fit_btn)
        
        self.zoom_100_btn = QtWidgets.QPushButton("1:1")
        self.zoom_100_btn.setFixedSize(25, 25)
        self.zoom_100_btn.setToolTip("Zoom 100%")
        self.zoom_100_btn.clicked.connect(self.zoom_to_100)
        button_layout.addWidget(self.zoom_100_btn)
        
        self.zoom_in_btn = QtWidgets.QPushButton("➕")
        self.zoom_in_btn.setFixedSize(25, 25)
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        button_layout.addWidget(self.zoom_in_btn)
        
        controls_layout.addLayout(button_layout)
        
        # Posicionar controles en esquina superior derecha
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
        self.zoom_label.setText(f"Zoom: {zoom_percent}%")
        
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
        self.image_info = None
        self.preview_cache = {}
        self.channel_arrays = {}
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
        self.PURE_CMYK_COLORS = {
            'C': QtGui.QColor(0, 255, 255), 'M': QtGui.QColor(255, 0, 255),
            'Y': QtGui.QColor(255, 255, 0), 'K': QtGui.QColor(0, 0, 0),
            'W': QtGui.QColor(255, 255, 255)
        }
        self.PURE_DEFAULT_THRESHOLDS = {
            'C': 128, 'M': 128, 'Y': 128, 'K': 64, 'W': 128
        }

        # --- 4. Reset a Valores por Defecto ---
        self.reset_all_to_defaults()
        print("🚀 Aplicación iniciada con valores puros.")

        # --- 5. Inicialización de la Interfaz Gráfica (SIEMPRE AL FINAL) ---
        self.init_ui()
        print("✅ Interfaz inicializada.")

    def init_ui(self):
        """Construye interfaz reorganizada y optimizada."""
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QHBoxLayout(central_widget)

        # -- PANEL IZQUIERDO CON SCROLL --
        controls_scroll = QtWidgets.QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        controls_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        controls_scroll.setMaximumWidth(480)  # Más ancho para evitar recortes
        controls_scroll.setMinimumWidth(460)

        # Widget contenedor para el scroll
        controls_container = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls_container)
        controls_layout.setAlignment(QtCore.Qt.AlignTop)
        controls_layout.setSpacing(6)  # Espaciado más compacto
        controls_layout.setContentsMargins(8, 8, 8, 8)  # Márgenes reducidos

        # === SECCIÓN 1: BOTONES PRINCIPALES (SIEMPRE VISIBLES) ===
        main_actions_group = QtWidgets.QGroupBox("🚀 Acciones Principales")
        main_actions_layout = QtWidgets.QVBoxLayout(main_actions_group)
        
        # Botones en fila para ahorrar espacio - MEJORADO
        buttons_row1 = QtWidgets.QHBoxLayout()
        buttons_row1.setSpacing(8)
        self.load_btn = QtWidgets.QPushButton("📂 Cargar")
        self.load_btn.clicked.connect(self.load_image_or_pdf)
        self.load_btn.setMinimumHeight(40)  # Más altura
        self.load_btn.setStyleSheet("font-weight: bold; font-size: 11px;")
        buttons_row1.addWidget(self.load_btn)
        
        self.wizard_btn = QtWidgets.QPushButton("🧙‍♂️ Asistente")
        self.wizard_btn.clicked.connect(self.show_setup_wizard)
        self.wizard_btn.setMinimumHeight(40)  # Más altura
        self.wizard_btn.setStyleSheet("font-weight: bold; font-size: 11px;")
        buttons_row1.addWidget(self.wizard_btn)
        
        buttons_row2 = QtWidgets.QHBoxLayout()
        buttons_row2.setSpacing(8)
        self.process_btn = QtWidgets.QPushButton("🚀 Procesar CMYK")
        self.process_btn.clicked.connect(self.process_cmyk)
        self.process_btn.setMinimumHeight(45)  # Más altura para botón principal
        self.process_btn.setStyleSheet("font-weight: bold; background-color: #4CAF50; color: white; font-size: 12px;")
        buttons_row2.addWidget(self.process_btn)
        
        self.save_btn = QtWidgets.QPushButton("💾 Guardar")
        self.save_btn.clicked.connect(self.save_results)
        self.save_btn.setMinimumHeight(45)  # Más altura para botón principal
        self.save_btn.setStyleSheet("font-weight: bold; background-color: #2196F3; color: white; font-size: 12px;")
        buttons_row2.addWidget(self.save_btn)
        
        main_actions_layout.addLayout(buttons_row1)
        main_actions_layout.addLayout(buttons_row2)
        controls_layout.addWidget(main_actions_group)

        # === SECCIÓN 2: INFORMACIÓN DE IMAGEN (COMPACTA) ===
        image_info_group = QtWidgets.QGroupBox("📐 Información de Imagen")
        image_info_layout = QtWidgets.QVBoxLayout(image_info_group)
        image_info_layout.setSpacing(2)  # Espaciado mínimo
        image_info_layout.setContentsMargins(6, 6, 6, 6)  # Márgenes reducidos
        
        self.image_res_label = QtWidgets.QLabel("📐 Sin imagen")
        self.image_res_label.setStyleSheet("font-size: 10px; color: #666; padding: 2px;")
        self.image_res_label.setWordWrap(True)
        image_info_layout.addWidget(self.image_res_label)
        
        self.complexity_label = QtWidgets.QLabel("🔍 Complejidad: N/A")
        self.complexity_label.setStyleSheet("font-size: 10px; color: #666; padding: 2px;")
        self.complexity_label.setWordWrap(True)
        image_info_layout.addWidget(self.complexity_label)
        
        controls_layout.addWidget(image_info_group)

        # === SECCIÓN 3: PARÁMETROS TÉCNICOS (COMPACTA) ===
        params_group = QtWidgets.QGroupBox("⚙️ Parámetros Técnicos")
        params_layout = QtWidgets.QGridLayout(params_group)
        params_layout.setSpacing(4)  # Espaciado compacto
        params_layout.setContentsMargins(6, 6, 6, 6)  # Márgenes reducidos
        params_layout.setVerticalSpacing(4)  # Espaciado vertical reducido
        
        # Forma y LPI con labels compactos
        form_label = QtWidgets.QLabel("Forma:")
        form_label.setStyleSheet("font-weight: bold; font-size: 10px;")
        params_layout.addWidget(form_label, 0, 0)
        self.shape_combo = QtWidgets.QComboBox()
        self.shape_combo.addItems(list(POINT_SHAPES.keys()))
        self.shape_combo.setMinimumHeight(26)  # Altura reducida
        params_layout.addWidget(self.shape_combo, 0, 1)
        
        lpi_label = QtWidgets.QLabel("LPI:")
        lpi_label.setStyleSheet("font-weight: bold; font-size: 10px;")
        params_layout.addWidget(lpi_label, 1, 0)
        self.lpi_combo = QtWidgets.QComboBox()
        self.lpi_combo.addItems(list(LPI_VALUES.keys()))
        self.lpi_combo.setMinimumHeight(26)  # Altura reducida
        params_layout.addWidget(self.lpi_combo, 1, 1)
        
        res_label = QtWidgets.QLabel("Resolución:")
        res_label.setStyleSheet("font-weight: bold; font-size: 10px;")
        params_layout.addWidget(res_label, 2, 0)
        self.resolution_combo = QtWidgets.QComboBox()
        self.resolution_combo.addItems(list(RESOLUTION_ENHANCEMENT.keys()))
        self.resolution_combo.setMinimumHeight(26)  # Altura reducida
        params_layout.addWidget(self.resolution_combo, 2, 1)
        
        controls_layout.addWidget(params_group)

        # === SECCIÓN 4: DETECTOR DE MOIRÉ (COMPACTO) ===
        moire_group = QtWidgets.QGroupBox("🔍 Detector de Moiré")
        moire_layout = QtWidgets.QVBoxLayout(moire_group)
        moire_layout.setContentsMargins(4, 4, 4, 4)  # Márgenes mínimos
        moire_layout.setSpacing(2)
        self.moire_warning = MoireWarningWidget(self)
        self.moire_warning.setFixedHeight(65)  # Altura fija compacta
        moire_layout.addWidget(self.moire_warning)
        controls_layout.addWidget(moire_group)

        # === SECCIÓN 5: FORMATO (COMPACTO) ===
        format_group = QtWidgets.QGroupBox("📄 Formato de Impresión")
        format_layout = QtWidgets.QVBoxLayout(format_group)
        format_layout.setSpacing(4)  # Espaciado reducido
        format_layout.setContentsMargins(6, 6, 6, 6)
        
        # Combo de formato
        self.print_format_combo = QtWidgets.QComboBox()
        self.print_format_combo.addItems(list(PRINT_FORMATS.keys()) + ["Personalizado"])
        self.print_format_combo.currentIndexChanged.connect(self.on_print_format_changed)
        self.print_format_combo.setMinimumHeight(26)
        format_layout.addWidget(self.print_format_combo)
        
        # Info de formato compacta
        self.format_info_label = QtWidgets.QLabel("Selecciona un formato")
        self.format_info_label.setStyleSheet("font-size: 9px; padding: 2px; border: 1px solid #ccc; background: #f9f9f9;")
        self.format_info_label.setWordWrap(True)
        self.format_info_label.setMaximumHeight(35)  # Altura limitada
        format_layout.addWidget(self.format_info_label)
        
        # Widget personalizado (más compacto)
        self.custom_size_widget = QtWidgets.QWidget()
        custom_layout = QtWidgets.QGridLayout(self.custom_size_widget)
        custom_layout.setSpacing(2)
        
        self.unit_combo = QtWidgets.QComboBox()
        self.unit_combo.addItems([f"{k} ({v['label']})" for k, v in MEASUREMENT_UNITS.items()])
        custom_layout.addWidget(QtWidgets.QLabel("Unidad:"), 0, 0)
        custom_layout.addWidget(self.unit_combo, 0, 1)
        
        self.custom_width = QtWidgets.QDoubleSpinBox()
        self.custom_height = QtWidgets.QDoubleSpinBox()
        self.custom_dpi = QtWidgets.QSpinBox()
        self.custom_dpi.setRange(72, 600)
        self.custom_dpi.setValue(300)
        
        custom_layout.addWidget(QtWidgets.QLabel("Ancho:"), 1, 0)
        custom_layout.addWidget(self.custom_width, 1, 1)
        custom_layout.addWidget(QtWidgets.QLabel("Alto:"), 2, 0)
        custom_layout.addWidget(self.custom_height, 2, 1)
        custom_layout.addWidget(QtWidgets.QLabel("DPI:"), 3, 0)
        custom_layout.addWidget(self.custom_dpi, 3, 1)
        
        self.custom_size_widget.setVisible(False)
        format_layout.addWidget(self.custom_size_widget)
        
        controls_layout.addWidget(format_group)

        # === SECCIÓN 6: OPCIONES DE PROCESAMIENTO (COMPACTAS) ===
        options_group = QtWidgets.QGroupBox("🎛️ Opciones")
        options_layout = QtWidgets.QGridLayout(options_group)
        options_layout.setSpacing(4)  # Espaciado reducido
        options_layout.setContentsMargins(6, 6, 6, 6)  # Márgenes reducidos
        options_layout.setVerticalSpacing(4)  # Espaciado vertical mínimo
        
        self.white_base_cb = QtWidgets.QCheckBox("Base Blanca")
        self.white_base_cb.setStyleSheet("font-size: 10px; font-weight: bold;")
        self.fit_format_cb = QtWidgets.QCheckBox("Ajustar Formato")
        self.fit_format_cb.setStyleSheet("font-size: 10px; font-weight: bold;")
        self.guides_cb = QtWidgets.QCheckBox("Guías Registro")
        self.guides_cb.setStyleSheet("font-size: 10px; font-weight: bold;")
        self.show_halftones_cb = QtWidgets.QCheckBox("Ver Halftones")
        self.show_halftones_cb.setStyleSheet("font-size: 10px; font-weight: bold;")
        self.show_halftones_cb.setChecked(True)
        self.show_halftones_cb.stateChanged.connect(self.update_preview)
        
        options_layout.addWidget(self.white_base_cb, 0, 0)
        options_layout.addWidget(self.fit_format_cb, 0, 1)
        options_layout.addWidget(self.guides_cb, 1, 0)
        options_layout.addWidget(self.show_halftones_cb, 1, 1)
        
        # Botón de color de prenda compacto
        self.garment_color_btn = QtWidgets.QPushButton("🎨 Color Prenda")
        self.garment_color_btn.clicked.connect(self.select_garment_color)
        self.garment_color_btn.setMinimumHeight(28)  # Altura reducida
        self.garment_color_btn.setStyleSheet("font-weight: bold; font-size: 10px;")
        options_layout.addWidget(self.garment_color_btn, 2, 0, 1, 2)
        
        controls_layout.addWidget(options_group)

        # === SECCIÓN 7: CANALES (COMPACTA) ===
        channels_group = QtWidgets.QGroupBox("🎨 Ajustes de Canales")
        channels_layout = QtWidgets.QVBoxLayout(channels_group)
        channels_layout.setContentsMargins(4, 4, 4, 4)  # Márgenes mínimos
        channels_layout.setSpacing(2)
        
        # Pestañas compactas
        channels_tabs = QtWidgets.QTabWidget()
        channels_tabs.setMinimumHeight(280)  # Altura controlada
        channels_tabs.setStyleSheet("QTabBar::tab { min-width: 70px; padding: 6px; font-weight: bold; font-size: 10px; }")
        
        # PESTAÑA 1: Lista de canales - COMPACTA
        channels_tab = QtWidgets.QWidget()
        channels_tab_layout = QtWidgets.QVBoxLayout(channels_tab)
        channels_tab_layout.setSpacing(4)  # Espaciado reducido
        channels_tab_layout.setContentsMargins(4, 4, 4, 4)
        
        self.view_individual_channel_cb = QtWidgets.QCheckBox("Ver canal individual")
        self.view_individual_channel_cb.setStyleSheet("font-weight: bold; font-size: 10px;")
        self.view_individual_channel_cb.stateChanged.connect(self.update_preview)
        channels_tab_layout.addWidget(self.view_individual_channel_cb)
        
        help_label = QtWidgets.QLabel("<i>Arrastra para reordenar. Clic para ajustar.</i>")
        help_label.setStyleSheet("font-size: 9px; color: #666; padding: 2px;")
        help_label.setWordWrap(True)
        channels_tab_layout.addWidget(help_label)
        
        self.channel_list = DraggableChannelList(self)
        self.channel_list.orderChanged.connect(self.set_channel_order)
        self.channel_list.itemSelectionChanged.connect(self.on_channel_selection_changed)
        self.channel_list.setMinimumHeight(120)  # Altura controlada
        self.channel_list.setMaximumHeight(120)  # Altura máxima
        channels_tab_layout.addWidget(self.channel_list)
        
        # Umbral del canal seleccionado - COMPACTO
        self.threshold_label = QtWidgets.QLabel("Umbral:")
        self.threshold_label.setStyleSheet("font-weight: bold; font-size: 10px;")
        channels_tab_layout.addWidget(self.threshold_label)
        
        self.threshold_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.threshold_slider.setRange(0, 255)
        self.threshold_slider.setMinimumHeight(22)  # Altura reducida
        self.threshold_slider.sliderMoved.connect(self.on_threshold_slider_changed)
        self.threshold_slider.sliderReleased.connect(self._delayed_threshold_update)
        channels_tab_layout.addWidget(self.threshold_slider)
        
        self.threshold_value_label = QtWidgets.QLabel("Selecciona un canal")
        self.threshold_value_label.setAlignment(QtCore.Qt.AlignCenter)
        self.threshold_value_label.setStyleSheet("font-size: 10px; font-weight: bold; padding: 2px; background: #f0f0f0; border-radius: 3px;")
        channels_tab_layout.addWidget(self.threshold_value_label)
        
        channels_tabs.addTab(channels_tab, "📋 Lista")
        
        # PESTAÑA 2: Colores de canales - COMPACTA
        colors_tab = QtWidgets.QWidget()
        colors_tab_layout = QtWidgets.QGridLayout(colors_tab)
        colors_tab_layout.setSpacing(4)  # Espaciado reducido
        colors_tab_layout.setContentsMargins(4, 4, 4, 4)
        colors_tab_layout.setVerticalSpacing(4)
        
        all_channels = ['C', 'M', 'Y', 'K', 'W']
        channel_names = {'C': 'Cian', 'M': 'Magenta', 'Y': 'Amarillo', 'K': 'Negro', 'W': 'Blanco'}
        
        for i, ch in enumerate(all_channels):
            label = QtWidgets.QLabel(f"{channel_names[ch]}")
            label.setStyleSheet("font-size: 10px; font-weight: bold;")
            colors_tab_layout.addWidget(label, i, 0)
            
            color_btn = QtWidgets.QPushButton("Color")
            color_btn.clicked.connect(lambda _, c=ch: self.pick_channel_color(c))
            color_btn.setMinimumHeight(26)  # Altura reducida
            color_btn.setStyleSheet("font-weight: bold; font-size: 9px;")
            self.channel_color_buttons[ch] = color_btn
            colors_tab_layout.addWidget(color_btn, i, 1)
            
            restore_btn = QtWidgets.QPushButton("↺")
            restore_btn.clicked.connect(lambda _, c=ch: self.restore_channel_color_default(c))
            restore_btn.setMinimumWidth(35)  # Ancho reducido
            restore_btn.setMinimumHeight(26)  # Altura reducida
            restore_btn.setToolTip("Restaurar color por defecto")
            restore_btn.setStyleSheet("font-weight: bold; font-size: 11px;")
            colors_tab_layout.addWidget(restore_btn, i, 2)
        
        channels_tabs.addTab(colors_tab, "🎨 Colores")
        
        channels_layout.addWidget(channels_tabs)
        controls_layout.addWidget(channels_group)

        # === NO ESPACIADOR FINAL para aprovechar mejor el espacio ===
        # controls_layout.addStretch()  # Comentado para evitar espacios vacíos

        # Configurar el scroll
        controls_scroll.setWidget(controls_container)

        # -- PANEL DERECHO (VISTAS) --
        views_panel = QtWidgets.QWidget()
        views_layout = QtWidgets.QHBoxLayout(views_panel)
        
        original_group = QtWidgets.QGroupBox("Imagen Original")
        original_layout = QtWidgets.QVBoxLayout(original_group)
        self.original_label = AspectRatioPixmapLabel("Cargue una imagen")
        original_layout.addWidget(self.original_label)
        
        preview_group = QtWidgets.QGroupBox("Vista Previa de Impresión")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        self.preview_label = ZoomablePreviewLabel()
        self.preview_label.setMinimumSize(400, 300)
        preview_layout.addWidget(self.preview_label)
        
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(original_group)
        splitter.addWidget(preview_group)
        splitter.setSizes([200, 200])
        views_layout.addWidget(splitter)
        
        # -- ENSAMBLAJE FINAL OPTIMIZADO --
        main_layout.addWidget(controls_scroll)  # Panel con scroll
        main_layout.addWidget(views_panel)
        main_layout.setStretch(0, 0)  # Panel izquierdo tamaño fijo pero más ancho
        main_layout.setStretch(1, 1)  # Panel derecho se expande
        main_layout.setSpacing(8)  # Espacio entre paneles

        # Barra de estado y menú
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        
        menu_bar = self.menuBar()
        help_menu = menu_bar.addMenu("&Ayuda")
        troubleshoot_action = QtWidgets.QAction("🩺 Solucionador", self)
        troubleshoot_action.triggered.connect(self.show_troubleshooter)
        help_menu.addAction(troubleshoot_action)
        
        lpi_calc_action = QtWidgets.QAction("🧮 Calculadora LPI", self)
        lpi_calc_action.triggered.connect(self.show_lpi_calculator)
        help_menu.addAction(lpi_calc_action)

        # Estado inicial
        self.threshold_slider.setEnabled(False)
        self.view_individual_channel_cb.setEnabled(False)
        
        # Inicializar UI
        self.update_channel_list_ui()
        self.update_all_color_buttons_ui()
        
        # Conectar señales del detector de moiré
        self.lpi_combo.currentTextChanged.connect(self.update_moire_analysis)
        self.shape_combo.currentTextChanged.connect(self.update_moire_analysis)
        
        # Análisis inicial
        self.update_moire_analysis()

        print("✅ Interfaz COMPACTA y optimizada cargada - espacios vacíos eliminados")

    def get_current_resolution_settings(self):
        """
        Este método ahora funcionará porque self.resolution_combo existe.
        """
        if hasattr(self, 'resolution_combo'):
            return RESOLUTION_ENHANCEMENT.get(self.resolution_combo.currentText(), {"factor": 1.0, "method": "INTER_CUBIC"})
        return {"factor": 1.0, "method": "INTER_CUBIC"} # Valor por defecto seguro


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
        for ch in self.channel_order:
            item = QtWidgets.QListWidgetItem(ch)
            item.setBackground(QtGui.QBrush(self.channel_colors[ch]))
            # Lógica para texto legible (negro o blanco)
            if self.channel_colors[ch].lightness() > 128:
                item.setForeground(QtGui.QBrush(QtGui.QColor("black")))
            else:
                item.setForeground(QtGui.QBrush(QtGui.QColor("white")))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setTextAlignment(QtCore.Qt.AlignCenter)
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
                        item.setBackground(QtGui.QBrush(color))
                        print(f"✅ Item {channel} actualizado en lista")
            for i in range(self.channel_list.count()):
                item = self.channel_list.item(i)
                if item:
                    channel = item.text()
                    if channel in self.channel_colors:
                        color = self.channel_colors[channel]
                        item.setBackground(QtGui.QBrush(color))
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
        """Actualiza el orden de los canales y refresca la vista."""
        self.channel_order = new_order
        print(f"Nuevo orden de impresión: {self.channel_order}")
        self.update_preview()

    def select_garment_color(self):
        """Permite al usuario seleccionar el color de fondo para la simulación."""
        color = QtWidgets.QColorDialog.getColor(self.garment_color, self)
        if color.isValid():
            self.garment_color = color
            self.preview_label.setStyleSheet(f"background-color: {self.garment_color.name()};")
            self.update_preview()

    def load_image_or_pdf(self):
        """
        Abre un diálogo para que el usuario seleccione un archivo de imagen o PDF,
        lo carga y actualiza la interfaz.
        Esta versión corregida maneja correctamente las rutas con caracteres especiales.
        """
        if PDF_SUPPORT:
            file_types = "Imágenes y PDFs (*.png *.jpg *.jpeg *.bmp *.tiff *.pdf);;Todas (*.*)"
        else:
            file_types = "Imágenes (*.png *.jpg *.jpeg *.bmp *.tiff);;Todas (*.*)"

        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Seleccionar Archivo", "", file_types
        )

        if not file_path:
            return # El usuario canceló

        try:
            self.status_bar.showMessage(f"📂 Cargando: {os.path.basename(file_path)}...")
            
            self.image = None
            self.image_info = {}

            if file_path.lower().endswith('.pdf') and PDF_SUPPORT:
                # --- Lógica para cargar PDFs ---
                selector = PDFPageSelector(file_path, self)
                if selector.exec_() == QtWidgets.QDialog.Accepted:
                    page_num = selector.get_selected_page()
                    resolution = selector.get_resolution()
                    
                    pdf_doc = fitz.open(file_path)
                    page = pdf_doc.load_page(page_num)
                    pix = page.get_pixmap(matrix=fitz.Matrix(resolution/72, resolution/72), alpha=False)
                    
                    img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
                    self.image = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                    
                    self.image_info = {
                        'file_path': file_path,
                        'width_px': self.image.shape[1], 'height_px': self.image.shape[0],
                        'dpi_x': resolution, 'dpi_y': resolution,
                        'width_cm': (self.image.shape[1] / resolution) * 2.54,
                        'height_cm': (self.image.shape[0] / resolution) * 2.54
                    }
                else:
                    self.status_bar.clearMessage()
                    return
            else:
                # --- Lógica para cargar Imágenes (CORREGIDA para caracteres especiales) ---
                # Este método lee el archivo a un buffer de memoria primero,
                # lo que evita problemas con caracteres en la ruta del archivo.
                with open(file_path, "rb") as stream:
                    bytes_array = bytearray(stream.read())
                    numpy_array = np.asarray(bytes_array, dtype=np.uint8)
                    img = cv2.imdecode(numpy_array, cv2.IMREAD_UNCHANGED)

                if img is None:
                    raise ValueError("OpenCV no pudo decodificar el archivo. Puede que esté corrupto o en un formato no soportado.")
                
                # Si la imagen tiene canal alfa (transparencia), se convierte a BGR
                if len(img.shape) > 2 and img.shape[2] == 4:
                    self.image = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                else:
                    self.image = img

                # Poblar image_info completamente
                dpi_x, dpi_y = 72, 72
                try:
                    with Image.open(file_path) as pil_img:
                        dpi_x, dpi_y = pil_img.info.get('dpi', (72, 72))
                except Exception:
                    pass # Usar DPI por defecto si no se puede leer

                self.image_info = {
                    'file_path': file_path,
                    'width_px': self.image.shape[1], 'height_px': self.image.shape[0],
                    'dpi_x': dpi_x, 'dpi_y': dpi_y,
                    'width_cm': (self.image.shape[1] / dpi_x) * 2.54,
                    'height_cm': (self.image.shape[0] / dpi_y) * 2.54
                }

            # --- Actualizaciones de la UI ---
            self.display_original()
            self.analyze_image_complexity()
            self.update_print_format_compatibility() 
            self.status_bar.showMessage(f"✅ Archivo cargado: {os.path.basename(file_path)}", 5000)

        except Exception as e:
            error_msg = f"Error al cargar el archivo: {e}"
            print(f"❌ {error_msg}")
            QtWidgets.QMessageBox.critical(self, "Error de Carga", error_msg)
            self.status_bar.showMessage("❌ Falló la carga del archivo.")


    def load_pdf(self, pdf_path):
        """Cargar y procesar PDF"""
        try:
            print(f"📄 Cargando PDF: {pdf_path}")

            # Mostrar selector de página
            selector = PDFPageSelector(pdf_path, self)
            if selector.exec_() == QtWidgets.QDialog.Accepted:
                page_num = selector.get_selected_page()
                resolution = selector.get_resolution()
                color_mode = selector.get_color_mode()

                print(f"📖 Procesando página {page_num + 1} a {resolution} DPI")

                # Convertir página específica
                if 'fitz' in sys.modules:
                    import fitz
                    pdf_doc = fitz.open(pdf_path)
                    page = pdf_doc[page_num]

                    zoom = resolution / 72.0
                    matrix = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=matrix, alpha=False)

                    img_data = pix.tobytes("ppm")
                    pil_img = Image.open(io.BytesIO(img_data))

                    pdf_doc.close()
                else:
                    from pdf2image import convert_from_path
                    pages = convert_from_path(
                        pdf_path,
                        dpi=resolution,
                        first_page=page_num + 1,
                        last_page=page_num + 1
                    )
                    pil_img = pages[0]

                # Convertir a formato OpenCV
                img_array = np.array(pil_img)
                if len(img_array.shape) == 3:
                    self.image = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                else:
                    self.image = cv2.cvtColor(img_array, cv2.COLOR_GRAY2BGR)

                # Actualizar información adicional para PDF
                self.image_info = {
                    'width_px': img_array.shape[1],
                    'height_px': img_array.shape[0],
                    'dpi_x': resolution,
                    'dpi_y': resolution,
                    'width_cm': (img_array.shape[1] / resolution) * 2.54,
                    'height_cm': (img_array.shape[0] / resolution) * 2.54,
                    'file_size_mb': os.path.getsize(pdf_path) / (1024 * 1024),
                    'file_path': pdf_path,
                    'source_type': 'PDF',
                    'pdf_page': page_num + 1,
                    'pdf_resolution': resolution
                }

                # Actualizar análisis de complejidad
                self.analyze_image_complexity()

                self.display_original()
                # IMPORTANTE: Actualizar compatibilidad después de cargar PDF
                self.update_print_format_compatibility()

                # Actualizar etiqueta de información
                res_text = f"📄 PDF p.{page_num + 1} | {img_array.shape[1]}×{img_array.shape[0]}px | {resolution} DPI | {self.image_info['width_cm']:.1f}×{self.image_info['height_cm']:.1f}cm"
                self.image_res_label.setText(res_text)

                self.status_bar.showMessage(f"✅ PDF cargado: página {page_num + 1}")
                print("✅ PDF procesado correctamente")

        except Exception as e:
            error_msg = f"Error al cargar PDF: {str(e)}"
            print(f"❌ {error_msg}")
            QtWidgets.QMessageBox.critical(self, "Error", error_msg)

    def load_image_file(self, file_path):
        """Cargar archivo de imagen"""
        try:
            print(f"📂 Cargando: {file_path}")

            # Cargar imagen con OpenCV
            img = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)
            if img is None:
                raise ValueError("No se pudo cargar la imagen")

            self.image = prepare_image_for_processing(img)

            # Detectar resolución de la imagen
            self.detect_image_resolution(file_path)
            
            # Analizar complejidad
            self.analyze_image_complexity()

            self.display_original()
            self.update_print_format_compatibility()

            self.status_bar.showMessage(f"✅ Imagen cargada: {os.path.basename(file_path)}")
            print("✅ Imagen cargada correctamente")

        except Exception as e:
            error_msg = f"Error al cargar imagen: {str(e)}"
            print(f"❌ {error_msg}")
            QtWidgets.QMessageBox.critical(self, "Error", error_msg)



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

    def update_moire_analysis(self):
        """
        VERSIÓN SIMPLE CON DEBUG - Para verificar que se ejecuta
        """
        print("🔍 === UPDATE MOIRE ANALYSIS CALLED ===")
        
        try:
            # Verificar que el detector existe
            if not hasattr(self, 'moire_detector'):
                print("❌ No hay moire_detector")
                return
                
            if not hasattr(self, 'moire_warning'):
                print("❌ No hay moire_warning widget")
                return
                
            # Obtener LPI actual
            lpi_text = self.lpi_combo.currentText()
            print(f"📏 LPI text: '{lpi_text}'")
            
            if not lpi_text:
                print("❌ No hay texto de LPI")
                return
                
            # Extraer número de LPI
            lpi = int(lpi_text.split()[0])
            mesh = 120
            
            print(f"🔢 LPI: {lpi}, Malla: {mesh}, Ratio: {mesh/lpi:.3f}")
            
            # Realizar análisis
            analysis = self.moire_detector.analyze_moire_risk(lpi, mesh, {})
            
            print(f"📊 Resultado: {analysis['risk_level']} ({analysis['risk_score']:.1%})")
            
            # Actualizar widget
            self.moire_warning.update_moire_status(lpi, mesh, analysis)
            
            print("✅ Widget actualizado")
            
        except Exception as e:
            print(f"❌ Error en update_moire_analysis: {e}")
            import traceback
            traceback.print_exc()


    def detect_image_resolution(self, file_path):
        """Detectar resolución y características de la imagen"""
        try:
            # Información básica con OpenCV
            h, w = self.image.shape[:2]
            file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB

            # Intentar obtener DPI con PIL
            dpi_x, dpi_y = 72, 72  # Valores por defecto
            try:
                with Image.open(file_path) as pil_img:
                    if hasattr(pil_img, 'info') and 'dpi' in pil_img.info:
                        dpi_x, dpi_y = pil_img.info['dpi']
                    elif hasattr(pil_img, 'info') and 'resolution' in pil_img.info:
                        dpi_x, dpi_y = pil_img.info['resolution']
            except:
                pass

            # Calcular tamaño de impresión en cm
            if dpi_x > 0 and dpi_y > 0:
                width_cm = (w / dpi_x) * 2.54
                height_cm = (h / dpi_y) * 2.54
            else:
                # Asumir 300 DPI si no se puede detectar
                width_cm = (w / 300) * 2.54
                height_cm = (h / 300) * 2.54
                dpi_x = dpi_y = 300

            # Actualizar información en la interfaz
            res_text = f"📐 {w}×{h}px | {dpi_x:.0f} DPI | {width_cm:.1f}×{height_cm:.1f}cm | {file_size:.1f}MB"
            self.image_res_label.setText(res_text)

            # Guardar información para uso posterior
            self.image_info = {
                'width_px': w,
                'height_px': h,
                'dpi_x': dpi_x,
                'dpi_y': dpi_y,
                'width_cm': width_cm,
                'height_cm': height_cm,
                'file_size_mb': file_size,
                'file_path': file_path
            }

            print(f"📊 Información de imagen detectada:")
            print(f"   Dimensiones: {w}×{h} px")
            print(f"   DPI: {dpi_x}×{dpi_y}")
            print(f"   Tamaño físico: {width_cm:.1f}×{height_cm:.1f} cm")
            print(f"   Archivo: {file_size:.1f} MB")

        except Exception as e:
            print(f"⚠️ Error detectando resolución: {e}")
            self.image_res_label.setText("📐 Resolución no detectada")
            self.image_info = None

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

            complexity_text = f"🔍 Complejidad: {complexity.upper()} - {recommendation}"
            self.complexity_label.setText(complexity_text)
            self.complexity_label.setStyleSheet(f"color: {color}; font-size: 10px; padding: 2px; font-weight: bold;")

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
            self.format_info_label.setText(f"🎯 {dimension_text} @ {dpi} DPI")
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
                self.format_info_label.setText(f"🎯 {dpi_rec} DPI recomendado para {format_display}")

            # Actualizar compatibilidad si hay imagen cargada
            if hasattr(self, 'image_info') and self.image_info:
                self.update_print_format_compatibility()
                
        except Exception as e:
            print(f"⚠️ Error en cambio de formato: {e}")
            self.format_info_label.setText("🎯 300 DPI recomendado")

    def update_print_format_compatibility(self):
        """Verificar compatibilidad entre imagen y formato de impresión - VERSIÓN CORREGIDA"""
        if not hasattr(self, 'image_info') or not self.image_info:
            # Reset label if no image is loaded
            if self.print_format_combo.currentText() != "Personalizado":
                try:
                    print_format = self.get_current_print_format()
                    dpi_rec = print_format.get('dpi_recommended', 300)
                    format_name = print_format.get('name', 'Formato desconocido')
                    self.format_info_label.setText(f"🎯 {dpi_rec} DPI recomendado para {format_name}")
                    self.format_info_label.setStyleSheet("color: #666; font-size: 11px; padding: 5px;")
                except Exception as e:
                    print(f"⚠️ Error actualizando formato: {e}")
                    self.format_info_label.setText("🎯 300 DPI recomendado")
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
            self.format_info_label.setStyleSheet(f"color: {color}; font-size: 11px; padding: 5px; font-weight: bold;")

        except Exception as e:
            print(f"⚠️ Error calculando compatibilidad: {e}")
            self.format_info_label.setText("🎯 300 DPI recomendado")
            self.format_info_label.setStyleSheet("color: #666; font-size: 11px; padding: 5px;")

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
                self.format_info_label.setStyleSheet(f"color: {color}; font-size: 11px; padding: 5px; font-weight: bold;")

            except Exception as e:
                print(f"Error calculando compatibilidad: {e}")

    def add_registration_guides(self, img, channel_name, dpi=300):
        """
        VERSIÓN CORREGIDA: Agrega guías de registro profesionales.
        """
        if not self.guides_cb.isChecked():
            print(f"   ❌ Guías desactivadas - retornando imagen original")
            return img
            
        try:
            print(f"   🎯 === AGREGANDO GUÍAS DE REGISTRO A {channel_name} ===")
            
            # Obtener configuración actual
            print_format = self.get_current_print_format()
            
            # Configuración de guías (en píxeles)
            margin_px = int((REGISTRATION_GUIDE_SETTINGS["margin_mm"] / 25.4) * dpi)
            cross_size_px = int((REGISTRATION_GUIDE_SETTINGS["cross_size_mm"] / 25.4) * dpi)
            line_thickness = REGISTRATION_GUIDE_SETTINGS["line_thickness"]
            
            print(f"   📏 DPI: {dpi}, Margen: {margin_px}px, Cruz: {cross_size_px}px")
            
            # Dimensiones de la imagen original
            orig_h, orig_w = img.shape[:2]
            print(f"   📐 Imagen original: {orig_w}x{orig_h}")
            
            # Calcular dimensiones del papel (formato de impresión)
            paper_width_mm = print_format["width"]
            paper_height_mm = print_format["height"]
            paper_width_px = int((paper_width_mm / 25.4) * dpi)
            paper_height_px = int((paper_height_mm / 25.4) * dpi)
            
            print(f"   📄 Papel: {paper_width_mm}x{paper_height_mm}mm = {paper_width_px}x{paper_height_px}px")
            
            # Crear canvas expandido (papel + márgenes para guías)
            final_width = paper_width_px + (margin_px * 2)
            final_height = paper_height_px + (margin_px * 2)
            
            print(f"   🖼️ Canvas final: {final_width}x{final_height}")
            
            # Crear canvas blanco
            final_canvas = np.ones((final_height, final_width), dtype=np.uint8) * 255
            
            # PASO 1: Centrar la imagen original en el área del papel
            if self.fit_format_cb.isChecked():
                # Escalar para ajustar al formato manteniendo proporción
                scale_x = paper_width_px / orig_w
                scale_y = paper_height_px / orig_h
                scale = min(scale_x, scale_y)  # Usar el menor para que quepa
                
                new_w = int(orig_w * scale)
                new_h = int(orig_h * scale)
                
                # Redimensionar usando INTER_NEAREST para preservar halftones
                img_resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
                print(f"   🔄 Imagen redimensionada: {new_w}x{new_h} (escala: {scale:.3f})")
            else:
                # Usar imagen original si cabe
                if orig_w <= paper_width_px and orig_h <= paper_height_px:
                    img_resized = img
                    new_w, new_h = orig_w, orig_h
                    print(f"   ✅ Usando tamaño original: {new_w}x{new_h}")
                else:
                    # Si no cabe, escalar para que quepa
                    scale_x = paper_width_px / orig_w
                    scale_y = paper_height_px / orig_h
                    scale = min(scale_x, scale_y)
                    
                    new_w = int(orig_w * scale)
                    new_h = int(orig_h * scale)
                    img_resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
                    print(f"   🔄 Imagen forzada a caber: {new_w}x{new_h}")
            
            # Centrar imagen en el área del papel
            paper_start_x = margin_px
            paper_start_y = margin_px
            
            img_start_x = paper_start_x + (paper_width_px - new_w) // 2
            img_start_y = paper_start_y + (paper_height_px - new_h) // 2
            
            # Colocar imagen en el canvas
            final_canvas[img_start_y:img_start_y+new_h, img_start_x:img_start_x+new_w] = img_resized
            
            print(f"   📍 Imagen colocada en: ({img_start_x}, {img_start_y})")
            
            # PASO 2: Agregar guías de registro
            guide_color = 0  # Negro para las guías
            
            # Cruces en las esquinas del papel
            corners = [
                (margin_px, margin_px),                                    # Superior izquierda
                (margin_px + paper_width_px, margin_px),                   # Superior derecha  
                (margin_px, margin_px + paper_height_px),                  # Inferior izquierda
                (margin_px + paper_width_px, margin_px + paper_height_px)  # Inferior derecha
            ]
            
            for cx, cy in corners:
                # Cruz horizontal
                cv2.line(final_canvas, 
                        (cx - cross_size_px//2, cy), 
                        (cx + cross_size_px//2, cy), 
                        guide_color, line_thickness)
                # Cruz vertical
                cv2.line(final_canvas, 
                        (cx, cy - cross_size_px//2), 
                        (cx, cy + cross_size_px//2), 
                        guide_color, line_thickness)
                # Círculo exterior
                cv2.circle(final_canvas, (cx, cy), cross_size_px//2 + 2, guide_color, 1)
            
            # Marcas centrales en los bordes
            center_x = margin_px + paper_width_px // 2
            center_y = margin_px + paper_height_px // 2
            
            # Marca central superior
            cv2.line(final_canvas, 
                    (center_x - cross_size_px//3, margin_px - cross_size_px//3), 
                    (center_x + cross_size_px//3, margin_px - cross_size_px//3), 
                    guide_color, line_thickness)
            
            # Marca central inferior  
            cv2.line(final_canvas, 
                    (center_x - cross_size_px//3, margin_px + paper_height_px + cross_size_px//3), 
                    (center_x + cross_size_px//3, margin_px + paper_height_px + cross_size_px//3), 
                    guide_color, line_thickness)
            
            # Marca central izquierda
            cv2.line(final_canvas, 
                    (margin_px - cross_size_px//3, center_y - cross_size_px//3), 
                    (margin_px - cross_size_px//3, center_y + cross_size_px//3), 
                    guide_color, line_thickness)
            
            # Marca central derecha
            cv2.line(final_canvas, 
                    (margin_px + paper_width_px + cross_size_px//3, center_y - cross_size_px//3), 
                    (margin_px + paper_width_px + cross_size_px//3, center_y + cross_size_px//3), 
                    guide_color, line_thickness)
            
            # PASO 3: Agregar información del canal (opcional)
            if REGISTRATION_GUIDE_SETTINGS["text_info"]:
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.6
                text_thickness = 1
                
                # Información del canal en la esquina superior izquierda
                text = f"CANAL {channel_name}"
                text_size = cv2.getTextSize(text, font, font_scale, text_thickness)[0]
                text_x = 10
                text_y = text_size[1] + 10
                
                cv2.putText(final_canvas, text, (text_x, text_y), font, font_scale, guide_color, text_thickness)
                
                # Información técnica en la esquina inferior
                tech_info = f"LPI: {self.lpi_combo.currentText()} | DPI: {dpi}"
                tech_y = final_height - 10
                cv2.putText(final_canvas, tech_info, (text_x, tech_y), font, 0.4, guide_color, 1)
            
            print(f"   ✅ Guías de registro agregadas exitosamente")
            print(f"   📊 Canvas final: {final_canvas.shape}, valores: {final_canvas.min()}-{final_canvas.max()}")
            
            return final_canvas
            
        except Exception as e:
            print(f"   ❌ ERROR agregando guías: {e}")
            import traceback
            traceback.print_exc()
            return img  # Retornar imagen original si falla
   

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
            res_text = f"📐 {info['width_px']}×{info['height_px']}px | {info['dpi_x']:.0f} DPI | {info['width_cm']:.1f}×{info['height_cm']:.1f}cm"
            self.image_res_label.setText(res_text)

    def show_channel(self, channel):
        print(f"DEBUG: Mostrando canal {channel}")
        """
        Muestra el canal seleccionado en la vista previa.
        Genera un QPixmap para el canal y lo muestra en el label de previsualización.
        """
        if channel not in self.preview_cache:
            self.status_bar.showMessage(f"❌ Canal {channel} no disponible")
            return

        pixmap = self.generate_channel_pixmap(channel)
        if pixmap is None:
            self.status_bar.showMessage(f"❌ No se pudo generar vista previa para el canal {channel}")
            return
        
        # Sincroniza slider con el umbral de ese canal
        threshold = self.channel_thresholds.get(channel, 64)
        self.threshold_slider.blockSignals(True)
        self.threshold_slider.setValue(threshold)
        self.threshold_slider.blockSignals(False)

        self.preview_label.setPixmap(pixmap)
        self.status_bar.showMessage(f"Mostrando canal: {channel}")

    def generate_channel_pixmap(self, channel):
        """
        ACTUALIZADO: Genera un pixmap del canal individual APLICANDO EL HALFTONE.
        """
        if channel not in self.channel_arrays:
            return QtGui.QPixmap()

        gray_channel_np = self.channel_arrays[channel]
        threshold = self.channel_thresholds.get(channel, 128)
        _, thresholded_channel = cv2.threshold(gray_channel_np, threshold, 255, cv2.THRESH_BINARY)

        scale, shape, angles = self._get_halftone_params()
        
        halftoned_mask = self.generate_quick_halftone(
            thresholded_channel, 
            angles.get(channel, 0), 
            scale
        )

        # Verificar que los datos son correctos
        unique_vals = len(np.unique(halftoned_mask))
        print(f"📊 Halftone {channel}: valores únicos = {unique_vals}, rango = {halftoned_mask.min()}-{halftoned_mask.max()}")

        if unique_vals > 1:
            print(f"✅ Halftone con ángulo {angles.get(channel, 0)}° aplicado al canal {channel}")
        else:
            print(f"⚠️ Halftone {channel} sin variación, revisa parámetros")

        h, w = halftoned_mask.shape
        rgba_data = np.zeros((h, w, 4), dtype=np.uint8)
        target_color = self.channel_colors[channel]
        r, g, b = target_color.red(), target_color.green(), target_color.blue()
        
        # Pinta los puntos (donde la máscara es negra) con el color de la tinta
        ink_mask = halftoned_mask == 0
        rgba_data[ink_mask] = [r, g, b, 255]
        
        q_image = QtGui.QImage(rgba_data.data, w, h, 4 * w, QtGui.QImage.Format_ARGB32)
        return QtGui.QPixmap.fromImage(q_image)
    
    def on_threshold_slider_changed(self, value):
        """
        VERSIÓN OPTIMIZADA LIGERA: UI instantánea + procesamiento con delay
        """
        try:
            if not self.current_channel or self.current_channel == "COMPOSITE":
                return
                
            # 1. ACTUALIZACIÓN INSTANTÁNEA de la UI (sin cálculos)
            self.channel_thresholds[self.current_channel] = value
            self.threshold_value_label.setText(f"Valor: {value}")
            
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

    def _get_halftone_params(self):
        """
        Método auxiliar para obtener los parámetros de halftone actuales desde la UI.
        """
        try:
            lpi = int(self.lpi_combo.currentText().split()[0])
            dpi = self.custom_dpi.value() if self.print_format_combo.currentText() == "Personalizado" else 300
            scale = max(2, int(dpi / lpi))
        except (ValueError, IndexError):
            scale = 6 # Valor por defecto si falla la lectura

        shape = POINT_SHAPES[self.shape_combo.currentText()]
        angles = {'C': 15, 'M': 75, 'Y': 0, 'K': 45, 'W': 90} # Ángulos fijos profesionales
        
        return scale, shape, angles
    
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

    def generate_halftone_pattern(self, channel_data, threshold, channel_name):
        """
        Halftones equilibrados para simulación natural
        """
        try:
            print(f"🎯 Generando halftone equilibrado para {channel_name}")
            
            # Normalizar datos del canal
            normalized_data = channel_data.astype(np.float32) / 255.0
            threshold_norm = threshold / 255.0
            
            # Crear patrón de puntos equilibrado
            height, width = channel_data.shape
            dot_size = self.get_halftone_dot_size(channel_name)
            angle = self.get_halftone_angle(channel_name)
            
            # Crear grilla de coordenadas con rotación
            y, x = np.mgrid[0:height, 0:width]
            
            # Aplicar rotación
            angle_rad = np.radians(angle)
            x_rot = x * np.cos(angle_rad) - y * np.sin(angle_rad)
            y_rot = x * np.sin(angle_rad) + y * np.cos(angle_rad)
            
            # Crear patrón de puntos usando coordenadas rotadas
            pattern_x = (x_rot % dot_size) - dot_size/2
            pattern_y = (y_rot % dot_size) - dot_size/2
            
            # Distancia al centro del punto
            distance = np.sqrt(pattern_x**2 + pattern_y**2)
            
            # Normalizar el patrón de forma equilibrada
            max_distance = dot_size / 2
            pattern = np.clip(distance / max_distance, 0.0, 1.0)
            
            # Aplicar función de transferencia para puntos más naturales
            pattern = np.power(pattern, 0.8)  # Menos agresivo que 0.7
            
            # Aplicar umbral de intensidad equilibrado
            intensity_adjusted = np.clip(
                (normalized_data - threshold_norm) * 2.5 + 0.2,  # Menos agresivo que 3.0
                0.0, 1.0
            )
            
            # Combinar patrón con intensidad
            halftone_mask = (pattern < intensity_adjusted).astype(np.float32)
            
            # Suavizado muy sutil
            try:
                from scipy import ndimage
                halftone_mask = ndimage.gaussian_filter(halftone_mask, sigma=0.3)  # Menos suavizado
            except ImportError:
                pass
            
            print(f"✅ Halftone equilibrado para {channel_name} - densidad: {np.mean(halftone_mask):.3f}")
            return halftone_mask
            
        except Exception as e:
            print(f"⚠️ Error en halftone equilibrado: {e}")
            # Fallback mejorado
            normalized_data = channel_data.astype(np.float32) / 255.0
            threshold_norm = threshold / 255.0
            return np.clip((normalized_data - threshold_norm) * 1.8 + 0.4, 0.0, 1.0)

    def get_halftone_angle(self, channel_name):
        """
        Ángulos estándar para evitar interferencias (moiré)
        """
        angles = {
            'C': 15,   # Cian: 15°
            'M': 75,   # Magenta: 75°
            'Y': 0,    # Amarillo: 0°
            'K': 45,   # Negro: 45°
            'W': 30    # Blanco: 30°
        }
        return angles.get(channel_name, 15)

    def create_dot_pattern(self, width, height, dot_size, angle):
        """
        Crea patrón de puntos para halftone
        """
        # Crear grilla de coordenadas
        x = np.arange(width)
        y = np.arange(height)
        X, Y = np.meshgrid(x, y)
        
        # Aplicar rotación
        angle_rad = np.radians(angle)
        X_rot = X * np.cos(angle_rad) - Y * np.sin(angle_rad)
        Y_rot = X * np.sin(angle_rad) + Y * np.cos(angle_rad)
        
        # Crear patrón de puntos circulares
        grid_x = (X_rot % dot_size) - dot_size/2
        grid_y = (Y_rot % dot_size) - dot_size/2
        
        # Distancia al centro del punto
        distance = np.sqrt(grid_x**2 + grid_y**2)
        
        # Normalizar (0.0 = centro del punto, 1.0 = borde)
        pattern = distance / (dot_size/2)
        pattern = np.clip(pattern, 0.0, 1.0)
        
        return pattern

    def apply_halftone_absorption(self, canvas, halftone_mask, channel_name):
        """
        Aplica absorción CMYK pura con patrón de halftone
        """
        if channel_name == 'W':
            # Blanco: aplicar sobre áreas con tinta
            white_areas = halftone_mask > 0.1
            canvas[white_areas, 0] = 1.0
            canvas[white_areas, 1] = 1.0
            canvas[white_areas, 2] = 1.0
            
        elif channel_name == 'C':
            # CIAN PURO: absorbe SOLO rojo con halftone
            canvas[:, :, 0] *= (1.0 - halftone_mask * 0.70)
            # Verde y azul quedan intactos
            
        elif channel_name == 'M':
            # MAGENTA PURO: absorbe SOLO verde con halftone
            canvas[:, :, 1] *= (1.0 - halftone_mask * 0.70)
            # Rojo y azul quedan intactos
            
        elif channel_name == 'Y':
            # AMARILLO PURO: absorbe SOLO azul con halftone
            canvas[:, :, 2] *= (1.0 - halftone_mask * 0.75)
            # Rojo y verde quedan intactos
            
        elif channel_name == 'K':
            # Negro: absorbe todos los canales por igual con halftone
            absorption = halftone_mask * 0.82
            canvas[:, :, 0] *= (1.0 - absorption)
            canvas[:, :, 1] *= (1.0 - absorption)
            canvas[:, :, 2] *= (1.0 - absorption)
        
        return canvas
    
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

    def _generate_halftone_pattern(self, image_data, scale, shape, angle):
        """
        Genera un patrón de semitonos con una forma y ángulo específicos.
        Este método interno reemplaza la llamada a la función externa.
        """
        # Crear una grilla de coordenadas
        h, w = image_data.shape
        x_coords, y_coords = np.meshgrid(np.arange(w), np.arange(h))

        # Rotar las coordenadas según el ángulo
        angle_rad = np.radians(angle)
        cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)
        x_rot = x_coords * cos_a + y_coords * sin_a
        y_rot = -x_coords * sin_a + y_coords * cos_a

        # Normalizar la imagen de entrada (0.0 a 1.0)
        image_norm = image_data.astype(np.float32) / 255.0

        # Generar el patrón de puntos basado en la forma seleccionada
        if shape == 'line':
            pattern = (y_rot % scale) / scale
        elif shape == 'ellipse':
            pattern = np.sqrt(((x_rot % scale) - scale / 2)**2 + ((y_rot % (scale/2)) - scale / 4)**2)
            pattern /= np.sqrt((scale/2)**2 + (scale/4)**2)
        else: # Círculo o Diamante (usamos círculo como base)
            pattern = np.sqrt(((x_rot % scale) - scale / 2)**2 + ((y_rot % scale) - scale / 2)**2)
            pattern /= (scale / np.sqrt(2))
            if shape == 'diamond':
                pattern = (pattern + ((x_rot % scale) / scale + (y_rot % scale) / scale))/2


        # Crear la máscara de semitonos comparando la imagen con el patrón
        # La distancia radial crece más rápido que el área del punto. Esta
        # corrección conserva mejor los medios tonos al convertirlos a puntos.
        ink_level = np.power(image_norm, 0.85)
        halftone_mask = (ink_level < pattern).astype(np.uint8) * 255
        return halftone_mask
    
    def _regenerate_single_halftone(self, channel_name):
        """
        VERSIÓN OPTIMIZADA de tu función existente - REEMPLAZA la que tienes
        """
        if channel_name not in self.channel_arrays:
            return
            
        try:
            # Obtener parámetros (tu código existente)
            scale, shape, angles = self._get_halftone_params()
            angle = angles.get(channel_name, 0)
            threshold = self.channel_thresholds[channel_name]
            
            # Obtener datos originales
            original_channel_data = self.channel_arrays[channel_name]
            
            # Aplicar ajuste de niveles (tu función existente)
            adjusted_channel_data = self._adjust_levels(original_channel_data, threshold)
            
            # Generar halftone (tu función existente)
            self.preview_cache[channel_name] = self._generate_halftone_pattern(
                adjusted_channel_data, scale, shape, angle
            )
            
        except Exception as e:
            print(f"❌ Error regenerando halftone: {e}")

    def _adjust_levels(self, image, threshold):
        """
        Ajusta los niveles de una imagen en escala de grises usando una corrección gamma.
        El 'threshold' del slider controla el punto medio de la curva de intensidad.
        """
        # El valor del slider (0-255) se convierte a un factor gamma.
        # El valor 128 es el punto neutro (gamma = 1.0).
        # Un valor más bajo en el slider resulta en una imagen más oscura (más tinta).
        # Un valor más alto en el slider resulta en una imagen más clara (menos tinta).
        
        # Se invierte la lógica para que sea intuitivo: slider a la izquierda = más oscuro.
        gamma = threshold / 128.0
        if gamma == 0: gamma = 0.01 # Evitar división por cero
        
        # La corrección gamma se aplica con la fórmula: O = I ^ (1/gamma)
        inv_gamma = 1.0 / gamma
        
        # Se crea una tabla de consulta para aplicar la corrección de forma eficiente.
        table = np.array([
            ((i / 255.0) ** inv_gamma) * 255
            for i in np.arange(0, 256)
        ]).astype("uint8")
        
        # Se aplica la tabla de corrección a la imagen.
        return cv2.LUT(image, table)
    
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
        """
        VERSIÓN LIGERAMENTE OPTIMIZADA de tu función existente - REEMPLAZA la que tienes
        """
        try:
            if not self.preview_cache:
                return

            h, w = next(iter(self.preview_cache.values())).shape
            
            # Crear canvas más eficientemente
            garment_color_float = np.array(self.garment_color.getRgbF()[:3], dtype=np.float32)
            canvas = np.full((h, w, 3), garment_color_float, dtype=np.float32)

            # Determinar canales a mostrar (tu lógica existente)
            channels_to_show = []
            if self.white_base_cb.isChecked() and 'W' in self.preview_cache:
                channels_to_show.append('W')
            for ch in ['C', 'M', 'Y', 'K']:
                if ch in self.preview_cache:
                    channels_to_show.append(ch)

            # Aplicar canales según orden de impresión (tu código existente)
            for channel in self.channel_order:
                if channel in channels_to_show:
                    mask = (255 - self.preview_cache[channel]).astype(np.float32) / 255.0
                    mask = mask[:, :, np.newaxis]
                    ink_rgb = np.array(self.channel_colors[channel].getRgbF()[:3], dtype=np.float32)
                    
                    if channel == 'W':
                        canvas = (canvas * (1.0 - mask)) + (ink_rgb * mask)
                    else:
                        light_passing_through = 1.0 - ((1.0 - ink_rgb) * mask)
                        canvas = canvas * light_passing_through
            
            # Convertir resultado final
            final_image = np.clip(canvas * 255, 0, 255).astype(np.uint8)
            qimage = QtGui.QImage(final_image.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
            self.preview_label.setPixmap(QtGui.QPixmap.fromImage(qimage))

        except Exception as e:
            print(f"❌ Error en vista previa compuesta: {e}")

    def generate_quick_halftone(self, channel_data, angle, scale):
        """
        CORREGIDO: Genera un patrón de halftone clásico con puntos circulares.
        Esta versión crea puntos cuyo tamaño depende de la intensidad de la imagen y 
        cuya frecuencia (LPI) depende del 'scale' correctamente.
        """
        try:
            h, w = channel_data.shape
            
            # 1. Crear una grilla de coordenadas
            y_coords, x_coords = np.mgrid[0:h, 0:w].astype(np.float32)

            # 2. Rotar la grilla según el ángulo del canal
            angle_rad = np.radians(angle)
            cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)
            x_rot = x_coords * cos_a - y_coords * sin_a
            y_rot = x_coords * sin_a + y_coords * cos_a

            # 3. Crear el patrón de puntos usando la grilla rotada y la escala
            # El operador módulo (%) crea una grilla repetitiva (la base del halftone)
            dot_pattern = np.sqrt(
                ((x_rot % scale) - scale / 2) ** 2 +
                ((y_rot % scale) - scale / 2) ** 2
            )
            
            # 4. Normalizar el patrón (0 = centro del punto, 1 = borde)
            max_dist = scale / 2 * np.sqrt(2)
            dot_pattern_norm = np.clip(dot_pattern / (scale / 2), 0, 1)

            # 5. Normalizar la imagen del canal (0 = negro, 1 = blanco)
            image_norm = channel_data.astype(np.float32) / 255.0
            
            # 6. Generar el halftone: un píxel se convierte en punto si su
            # intensidad en la imagen es mayor que su valor en el patrón de puntos.
            halftone = (image_norm > dot_pattern_norm).astype(np.uint8) * 255
            
            # Devolvemos la máscara invertida: los puntos son negros (0) y el fondo blanco (255)
            return cv2.bitwise_not(halftone)

        except Exception as e:
            print(f"❌ Error en generate_quick_halftone: {e}")
            # Si falla, devuelve la imagen original sin procesar
            return channel_data
        
    def create_circular_dots(dot_pattern):
        """Crear puntos más circulares"""
        return np.sin(dot_pattern * np.pi) * 0.8
    

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
                self.threshold_value_label.setText(f"Valor: {new_threshold}")
            
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
            self.threshold_label.setText(f"Umbral del Canal <b>{self.current_channel}</b>:")
            self.threshold_value_label.setText(f"Valor: {current_threshold}")
        else:
            self.current_channel = None
            self.threshold_label.setText("Umbral del Canal:")
            self.threshold_value_label.setText("Selecciona un canal para ajustar")
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

    def generate_simple_halftone_pattern(self, channel_data, threshold, channel_name):
        """
        Genera halftones simples pero efectivos - CON UMBRAL FUNCIONAL
        """
        try:
            print(f"🎯 Generando halftone para {channel_name} con umbral {threshold}")
            
            # Normalizar datos del canal
            normalized_data = channel_data.astype(np.float32) / 255.0
            
            # Crear patrón de puntos
            height, width = channel_data.shape
            dot_size = self.get_simple_dot_size(channel_name)
            angle = self.get_halftone_angle(channel_name)
            
            # Crear grilla de coordenadas
            y, x = np.mgrid[0:height, 0:width]
            
            # Aplicar rotación
            angle_rad = np.radians(angle)
            x_rot = x * np.cos(angle_rad) - y * np.sin(angle_rad)
            y_rot = x * np.sin(angle_rad) + y * np.cos(angle_rad)
            
            # Crear patrón de puntos
            pattern_x = (x_rot % dot_size) - dot_size/2
            pattern_y = (y_rot % dot_size) - dot_size/2
            distance = np.sqrt(pattern_x**2 + pattern_y**2)
            
            # Normalizar patrón
            max_distance = dot_size / 2
            pattern = np.clip(distance / max_distance, 0.0, 1.0)
            
            # APLICAR UMBRAL - El umbral modifica el tamaño de los puntos
            if threshold != 64 and channel_name == 'K':  # Para canal K
                default_threshold = 64
            elif threshold != 128:  # Para otros canales
                default_threshold = 128
            else:
                default_threshold = threshold
                
            if threshold != default_threshold:
                # EFECTO MÁS DRAMÁTICO Y VISIBLE
                if threshold > default_threshold:
                    # Umbral alto = puntos MUY pequeños (imagen mucho más clara)
                    intensity_factor = 0.3 + (threshold - default_threshold) / 255.0 * 1.2
                    adjusted_intensity = np.power(normalized_data, intensity_factor)
                else:
                    # Umbral bajo = puntos MUY grandes (imagen mucho más oscura)  
                    intensity_factor = 2.5 - threshold / 255.0 * 1.8
                    adjusted_intensity = np.power(normalized_data, 1.0/intensity_factor)
                    
                print(f"   🎯 Umbral {threshold} aplicado - factor DRAMÁTICO: {intensity_factor:.2f}")
            else:
                adjusted_intensity = normalized_data
            
            # Crear máscara de halftones con intensidad ajustada
            halftone_mask = (pattern < adjusted_intensity).astype(np.float32)
            
            # Suavizado mínimo
            try:
                from scipy import ndimage
                halftone_mask = ndimage.gaussian_filter(halftone_mask, sigma=0.2)
            except ImportError:
                pass
            
            print(f"✅ Halftone para {channel_name} - densidad: {np.mean(halftone_mask):.3f}")
            return halftone_mask
        
        except Exception as e:
            print(f"⚠️ Error en halftone: {e}")
            # Fallback: usar datos originales normalizados
            return channel_data.astype(np.float32) / 255.0
        
    def get_simple_dot_size(self, channel_name):
        """
        Tamaños de punto simples para halftones efectivos
        """
        sizes = {
            'C': 8,    # Cian
            'M': 9,    # Magenta  
            'Y': 6,    # Amarillo
            'K': 10,   # Negro
            'W': 7     # Blanco
        }
        return sizes.get(channel_name, 8)

        
    def auto_adjust_based_on_image(self, canvas, selected_channels):
        """
        Ajustes automáticos basados en el contenido de la imagen
        """
        # Analizar el contenido de la imagen
        avg_brightness = np.mean(canvas)
        avg_saturation = np.std(canvas)
        
        print(f"📊 Análisis automático - Brillo: {avg_brightness:.3f}, Saturación: {avg_saturation:.3f}")
        
        # Ajustes automáticos basados en contenido
        if avg_brightness < 0.3:
            # Imagen oscura - aumentar brillo
            canvas = np.clip(canvas + 0.1, 0.0, 1.0)
            print("🔆 Imagen oscura detectada - aumentando brillo")
        elif avg_brightness > 0.8:
            # Imagen muy clara - reducir ligeramente
            canvas = np.clip(canvas * 0.95, 0.0, 1.0)
            print("🔅 Imagen muy clara detectada - reduciendo brillo")
        
        if avg_saturation > 0.3:
            # Imagen muy contrastada - suavizar ligeramente
            canvas = np.clip((canvas - 0.5) * 0.9 + 0.5, 0.0, 1.0)
            print("🎨 Alto contraste detectado - suavizando")
        
        return canvas

    def get_halftone_dot_size(self, channel_name):
        """
        Tamaños de punto optimizados para equilibrio visual
        """
        sizes = {
            'C': 7,    # Cian: puntos medianos
            'M': 8,    # Magenta: puntos medianos-grandes  
            'Y': 5,    # Amarillo: puntos pequeños (evita interferencia)
            'K': 9,    # Negro: puntos grandes (más definición)
            'W': 6     # Blanco: puntos medianos-pequeños
        }
        return sizes.get(channel_name, 7)

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
        """Generar plantilla de ganancia de punto"""
        try:
            # Crear imagen de plantilla simple
            template_size = 800
            template_img = np.ones((template_size, template_size), dtype=np.uint8) * 255

            # Generar matriz de prueba
            lpi_values = [30, 40, 50, 60]
            percent_values = [20, 40, 60, 80]
            cell_size = 150

            print("🎯 Generando plantilla de ganancia de punto...")

            for i, percent in enumerate(percent_values):
                for j, lpi in enumerate(lpi_values):
                    x_start = 50 + j * cell_size
                    y_start = 50 + i * cell_size

                    # Crear celda de prueba
                    test_value = int((percent / 100.0) * 255)
                    test_cell = np.full((cell_size, cell_size), test_value, dtype=np.uint8)

                    # Aplicar halftone con forma redonda por defecto
                    scale = max(3, int(300 / lpi))
                    halftoned_cell = apply_simple_halftone(test_cell, scale, 'circle')

                    # Colocar en plantilla
                    template_img[y_start:y_start+cell_size, x_start:x_start+cell_size] = halftoned_cell

            # Guardar plantilla
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"plantilla_ganancia_{timestamp}.png"
            filepath = os.path.join(self.output_dir, filename)
            cv2.imwrite(filepath, template_img)

            # Crear archivo de instrucciones
            info_filename = f"instrucciones_ganancia_{timestamp}.txt"
            info_filepath = os.path.join(self.output_dir, info_filename)

            with open(info_filepath, 'w', encoding='utf-8') as f:
                f.write("PLANTILLA DE VERIFICACIÓN DE GANANCIA DE PUNTO\n")
                f.write("=" * 50 + "\n\n")
                f.write("INSTRUCCIONES:\n")
                f.write("1. Imprime esta plantilla\n")
                f.write("2. Graba la malla\n")
                f.write("3. Estampa en tela blanca\n")
                f.write("4. Compara con lupa los resultados\n")
                f.write("5. Ajusta tu proceso según diferencias\n\n")
                f.write(f"MATRIZ: LPI {lpi_values} x Porcentajes {percent_values}\n")
                f.write(f"FORMA DE PUNTO: Redonda (estándar)\n")
                f.write(f"FORMAS DISPONIBLES EN LA APLICACIÓN:\n")
                f.write("• Redonda: Punto circular clásico\n")
                f.write("• Elipse: Punto ovalado horizontal\n")
                f.write("• Diamante: Punto en forma de rombo\n")
                f.write("• Lineal: Efecto de líneas paralelas\n")

            msg = f"✅ Plantilla generada:\n{filename}\n{info_filename}\n\nUbicación: {self.output_dir}"
            QtWidgets.QMessageBox.information(self, "Plantilla Generada", msg)
            self.status_bar.showMessage(f"✅ Plantilla generada: {filename}", 5000)

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Error al generar plantilla:\n{str(e)}")


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

    def apply_calculated_lpi(self, dialog):
        """Aplicar LPI calculado al proyecto actual"""
        try:
            # Obtener el LPI calculado de la pestaña activa
            optimal_lpi = None

            # Desde pestaña de malla
            if hasattr(self, 'lpi_result_label'):
                text = self.lpi_result_label.text()
                import re
                match = re.search(r'LPI Calculado: (\d+)', text)
                if match:
                    optimal_lpi = int(match.group(1))

            if optimal_lpi:
                # Buscar en LPI_VALUES la opción más cercana
                closest_option = None
                min_diff = float('inf')

                for lpi_option in LPI_VALUES.keys():
                    lpi_num = int(lpi_option.split()[0])
                    diff = abs(lpi_num - optimal_lpi)
                    if diff < min_diff:
                        min_diff = diff
                        closest_option = lpi_option

                if closest_option:
                    # Aplicar al combo box principal
                    self.lpi_combo.setCurrentText(closest_option)
                    dialog.accept()

                    msg = f"✅ LPI aplicado al proyecto:\n{closest_option}\n\nPuedes procesar la imagen con esta configuración optimizada."
                    QtWidgets.QMessageBox.information(self, "LPI Aplicado", msg)
                    self.status_bar.showMessage(f"✅ LPI optimizado aplicado: {closest_option}", 5000)
                else:
                    QtWidgets.QMessageBox.warning(self, "Aviso", "No se encontró una opción LPI compatible en el sistema.")
            else:
                QtWidgets.QMessageBox.warning(self, "Aviso", "No se pudo obtener el LPI calculado.")

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Error al aplicar LPI:\n{str(e)}")

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

    def _create_specifications_file(self, folder_path, timestamp, saved_files, print_format, output_dpi):
        """
        NUEVA FUNCIÓN: Crear archivo de especificaciones mejorado
        """
        spec_filename = f"especificaciones_{timestamp}.txt"
        spec_filepath = os.path.join(folder_path, spec_filename)
        
        # Obtener información del formato
        format_name = self.print_format_combo.currentText()
        if format_name == "Personalizado":
            format_display = "Formato Personalizado"
        else:
            format_display = format_name
        
        with open(spec_filepath, 'w', encoding='utf-8') as f:
            f.write("ESPECIFICACIONES DE TRABAJO SERIGRÁFICO\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Formato: {format_display}\n")
            f.write(f"Dimensiones: {print_format['width']}x{print_format['height']}mm\n")
            f.write(f"DPI de salida: {output_dpi}\n")
            f.write(f"Lineatura: {self.lpi_combo.currentText()}\n")
            f.write(f"Forma de punto: {self.shape_combo.currentText()}\n")
            f.write(f"Guías de registro: {'SÍ' if self.guides_cb.isChecked() else 'NO'}\n")
            f.write(f"Base blanca: {'SÍ' if self.white_base_cb.isChecked() else 'NO'}\n")
            f.write(f"Ajustar al formato: {'SÍ' if self.fit_format_cb.isChecked() else 'NO'}\n")
            f.write(f"Color de prenda: {self.garment_color.name()}\n\n")
            
            # Información de imagen original
            if hasattr(self, 'image_info') and self.image_info:
                f.write("IMAGEN ORIGINAL:\n")
                f.write("-" * 20 + "\n")
                f.write(f"Archivo: {os.path.basename(self.image_info.get('file_path', 'N/A'))}\n")
                f.write(f"Resolución original: {self.image_info.get('width_px', 'N/A')}x{self.image_info.get('height_px', 'N/A')}px\n")
                f.write(f"DPI original: {self.image_info.get('dpi_x', 'N/A')}\n")
                f.write(f"Tamaño original: {self.image_info.get('width_cm', 'N/A'):.1f}x{self.image_info.get('height_cm', 'N/A'):.1f}cm\n\n")
            
            f.write("ORDEN DE IMPRESIÓN:\n")
            f.write("-" * 20 + "\n")
            for i, channel in enumerate(self.channel_order, 1):
                if channel in self.preview_cache:
                    color = self.channel_colors[channel]
                    threshold = self.channel_thresholds.get(channel, 128)
                    f.write(f"{i}. Canal {channel} - Color: {color.name()} - Umbral: {threshold}\n")
            
            f.write("\nARCHIVOS GENERADOS:\n")
            f.write("-" * 20 + "\n")
            for filename in saved_files:
                f.write(f"• {filename}\n")
                
            f.write("\nNOTAS TÉCNICAS:\n")
            f.write("-" * 15 + "\n")
            f.write("• Los positivos están listos para grabado de pantallas\n")
            f.write("• Verificar orientación correcta al grabar\n")
            f.write("• Usar emulsión fotopolimérica de calidad\n")
            f.write("• Comprobar registro antes de producción masiva\n")

    def _resize_and_center_for_output(self, image, target_width, target_height):
        """
        NUEVA FUNCIÓN: Redimensiona y centra la imagen para el formato de salida
        """
        try:
            print(f"📐 Redimensionando de {image.shape} a {target_width}x{target_height}")
            
            # Obtener dimensiones actuales
            current_height, current_width = image.shape[:2]
            
            # Calcular factor de escala para ajustar manteniendo proporción
            scale_x = target_width / current_width
            scale_y = target_height / current_height
            
            # Usar el factor menor para que quepa completo
            scale_factor = min(scale_x, scale_y)
            
            # Solo escalar si es necesario hacer más pequeño o si queremos agrandar
            if self.fit_format_cb.isChecked() or scale_factor < 1.0:
                # Redimensionar manteniendo proporción
                new_width = int(current_width * scale_factor)
                new_height = int(current_height * scale_factor)
                
                resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
                print(f"🔄 Imagen redimensionada a {new_width}x{new_height} (factor: {scale_factor:.3f})")
            else:
                # Mantener tamaño original si cabe y no se fuerza ajuste
                resized = image
                new_width, new_height = current_width, current_height
                print(f"📏 Manteniendo tamaño original {new_width}x{new_height}")
            
            # Crear canvas del tamaño final
            final_canvas = np.ones((target_height, target_width), dtype=np.uint8) * 255  # Fondo blanco
            
            # Calcular posición para centrar
            start_x = (target_width - new_width) // 2
            start_y = (target_height - new_height) // 2
            
            # Asegurar que la imagen cabe
            end_x = start_x + new_width
            end_y = start_y + new_height
            
            if end_x <= target_width and end_y <= target_height:
                final_canvas[start_y:end_y, start_x:end_x] = resized
                print(f"✅ Imagen centrada en posición ({start_x}, {start_y})")
            else:
                # Si no cabe, recortar o ajustar
                final_canvas = cv2.resize(resized, (target_width, target_height), interpolation=cv2.INTER_AREA)
                print(f"⚠️ Imagen ajustada forzosamente al tamaño objetivo")
            
            return final_canvas
            
        except Exception as e:
            print(f"❌ Error en redimensionado: {e}")
            # Fallback: redimensionar directo
            return cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_AREA)
        
    def verify_halftone_quality(self, image_data, description=""):
        """
        NUEVA FUNCIÓN: Verificar calidad de halftone
        """
        try:
            print(f"\n🔍 === VERIFICACIÓN DE CALIDAD: {description} ===")
            
            # Análisis básico
            unique_values = np.unique(image_data)
            print(f"Valores únicos: {len(unique_values)}")
            print(f"Rango: {image_data.min()} - {image_data.max()}")
            
            # Para halftones, deberíamos tener solo 0 y 255
            if len(unique_values) == 2 and 0 in unique_values and 255 in unique_values:
                print(f"✅ Halftone binario correcto (solo 0 y 255)")
            else:
                print(f"⚠️ Halftone con valores intermedios: {unique_values}")
            
            # Análisis de distribución de puntos
            black_pixels = np.sum(image_data == 0)
            white_pixels = np.sum(image_data == 255)
            total_pixels = image_data.size
            
            black_percentage = (black_pixels / total_pixels) * 100
            
            print(f"Distribución: {black_percentage:.1f}% negro, {100-black_percentage:.1f}% blanco")
            
            # Verificar patrones de halftone
            if black_percentage > 0 and black_percentage < 100:
                print(f"✅ Distribución de puntos normal")
            elif black_percentage == 0:
                print(f"⚠️ Imagen completamente blanca")
            elif black_percentage == 100:
                print(f"⚠️ Imagen completamente negra")
            
            return {
                'unique_values': len(unique_values),
                'is_binary': len(unique_values) == 2,
                'black_percentage': black_percentage,
                'quality_score': 'GOOD' if len(unique_values) == 2 else 'POOR'
            }
            
        except Exception as e:
            print(f"❌ Error en verificación: {e}")
            return {'quality_score': 'ERROR'}

    def save_results(self):
        """
        VERSIÓN TOTALMENTE CORREGIDA: Guarda respetando medidas y guías
        """
        if not self.preview_cache:
            QtWidgets.QMessageBox.warning(self, "Aviso", "Procesa la imagen primero")
            return
            
        try:
            folder_path = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Seleccionar Carpeta para Guardar", self.output_dir)
            if not folder_path:
                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_files = []
            
            print(f"\n💾 === GUARDADO CORREGIDO CON GUÍAS ===")
            
            # Obtener configuración de formato
            print_format = self.get_current_print_format()
            output_dpi = print_format["dpi_recommended"]
            
            guides_enabled = self.guides_cb.isChecked()
            fit_format_enabled = self.fit_format_cb.isChecked()
            
            print(f"🎯 Guías de registro: {'✅ ACTIVADAS' if guides_enabled else '❌ DESACTIVADAS'}")
            print(f"📏 Ajustar formato: {'✅ ACTIVADO' if fit_format_enabled else '❌ DESACTIVADO'}")
            print(f"📐 Formato: {self.print_format_combo.currentText()}")
            print(f"📊 DPI de salida: {output_dpi}")
            print(f"📄 Dimensiones papel: {print_format['width']}x{print_format['height']}mm")
            
            # PROCESAR CADA CANAL
            for i, channel in enumerate(self.channel_order):
                if channel not in self.preview_cache:
                    continue
                    
                print(f"\n🔄 === PROCESANDO CANAL {channel} ({i+1}/{len(self.channel_order)}) ===")
                
                # PASO 1: Obtener datos del canal (calidad perfecta)
                channel_img = self.preview_cache[channel].copy()
                original_shape = channel_img.shape
                unique_before = len(np.unique(channel_img))
                
                print(f"   📊 Original: {original_shape}, valores únicos: {unique_before}")
                
                # PASO 2: Aplicar gu��as de registro ANTES de cualquier redimensionado
                if guides_enabled:
                    print(f"   🎯 APLICANDO GUÍAS DE REGISTRO...")
                    final_img = self.add_registration_guides(channel_img, channel, output_dpi)
                    
                    # Verificar que las guías se aplicaron
                    if final_img.shape != original_shape:
                        print(f"   ✅ GUÍAS APLICADAS: {original_shape} → {final_img.shape}")
                    else:
                        print(f"   ⚠️ ADVERTENCIA: Las guías no cambiaron el tamaño")
                else:
                    print(f"   ⏭️ Sin guías - usando imagen original")
                    final_img = channel_img
                
                # PASO 3: Verificar calidad final
                unique_after = len(np.unique(final_img))
                print(f"   📈 Calidad final: valores únicos = {unique_after}")
                
                if unique_after <= 3:  # Esperamos: 0 (negro), 255 (blanco), y posibles grises de anti-aliasing
                    print(f"   ✅ Calidad preservada")
                else:
                    print(f"   ⚠️ Valores adicionales detectados (normal para texto/guías)")
                
                # PASO 4: Guardar archivo
                filename = f"POSITIVO_{channel}_{timestamp}.png"
                filepath = os.path.join(folder_path, filename)
                
                success = cv2.imwrite(filepath, final_img)
                if success:
                    saved_files.append(filename)
                    file_size = os.path.getsize(filepath) / (1024 * 1024)
                    print(f"   ✅ GUARDADO: {filename} ({file_size:.1f}MB)")
                    
                    # Verificar archivo guardado
                    verification_img = cv2.imread(filepath, cv2.IMREAD_GRAYSCALE)
                    if verification_img is not None:
                        saved_shape = verification_img.shape
                        saved_unique = len(np.unique(verification_img))
                        print(f"   🔍 Verificación: {saved_shape}, valores únicos: {saved_unique}")
                        
                        if guides_enabled and saved_shape == final_img.shape:
                            print(f"   ✅ ARCHIVO GUARDADO CORRECTAMENTE CON GUÍAS")
                        elif not guides_enabled:
                            print(f"   ✅ ARCHIVO GUARDADO SIN GUÍAS (como esperado)")
                        else:
                            print(f"   ⚠️ Posible problema en el guardado")
                    else:
                        print(f"   ❌ ERROR: No se pudo verificar el archivo guardado")
                else:
                    print(f"   ❌ ERROR: Falló el guardado de {filename}")
            
            # CREAR PDFs
            print(f"\n📄 === CREANDO PDFs ===")
            
            # PDF limpio (sin guías) - SIEMPRE
            images_clean = []
            for channel in self.channel_order:
                if channel in self.preview_cache:
                    clean_img = self.preview_cache[channel]
                    pil_img = Image.fromarray(clean_img).convert("L")
                    images_clean.append(pil_img)
            
            if images_clean:
                pdf_path_clean = os.path.join(folder_path, f"cmyk_limpio_{timestamp}.pdf")
                images_clean[0].save(pdf_path_clean, save_all=True, append_images=images_clean[1:])
                saved_files.append(f"cmyk_limpio_{timestamp}.pdf")
                print(f"✅ PDF limpio creado: cmyk_limpio_{timestamp}.pdf")

            # PDF con guías (solo si están habilitadas)
            if guides_enabled:
                print(f"📄 Creando PDF con guías...")
                images_with_guides = []
                for channel in self.channel_order:
                    if channel in self.preview_cache:
                        img_with_guides = self.add_registration_guides(
                            self.preview_cache[channel], channel, output_dpi)
                        pil_img = Image.fromarray(img_with_guides).convert("L")
                        images_with_guides.append(pil_img)
                        
                if images_with_guides:
                    pdf_path_guides = os.path.join(folder_path, f"cmyk_con_guias_{timestamp}.pdf")
                    images_with_guides[0].save(pdf_path_guides, save_all=True, append_images=images_with_guides[1:])
                    saved_files.append(f"cmyk_con_guias_{timestamp}.pdf")
                    print(f"✅ PDF con guías creado: cmyk_con_guias_{timestamp}.pdf")
            
            # CREAR ESPECIFICACIONES
            spec_filename = f"especificaciones_{timestamp}.txt"
            spec_filepath = os.path.join(folder_path, spec_filename)
            
            with open(spec_filepath, 'w', encoding='utf-8') as f:
                f.write("ESPECIFICACIONES DE TRABAJO SERIGRÁFICO\n")
                f.write("=" * 50 + "\n\n")
                f.write(f"Fecha y hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Aplicación: Asistente de Serigrafía Profesional\n\n")
                
                f.write("CONFIGURACIÓN DE FORMATO:\n")
                f.write("-" * 30 + "\n")
                f.write(f"Formato seleccionado: {self.print_format_combo.currentText()}\n")
                f.write(f"Dimensiones del papel: {print_format['width']} x {print_format['height']} mm\n")
                f.write(f"DPI de salida: {output_dpi}\n")
                f.write(f"Ajustar al formato: {'SÍ' if fit_format_enabled else 'NO'}\n")
                f.write(f"Guías de registro: {'SÍ' if guides_enabled else 'NO'}\n")
                if guides_enabled:
                    f.write(f"Margen para guías: {REGISTRATION_GUIDE_SETTINGS['margin_mm']} mm\n")
                    f.write(f"Tamaño de cruces: {REGISTRATION_GUIDE_SETTINGS['cross_size_mm']} mm\n")
                f.write("\n")
                
                f.write("CONFIGURACIÓN DE HALFTONE:\n")
                f.write("-" * 30 + "\n")
                f.write(f"Lineatura (LPI): {self.lpi_combo.currentText()}\n")
                f.write(f"Forma de punto: {self.shape_combo.currentText()}\n")
                f.write(f"Mejora de resolución: {self.resolution_combo.currentText()}\n")
                f.write(f"Base blanca incluida: {'SÍ' if self.white_base_cb.isChecked() else 'NO'}\n")
                f.write(f"Color de prenda: {self.garment_color.name()}\n\n")
                
                f.write("ORDEN DE IMPRESIÓN Y UMBRALES:\n")
                f.write("-" * 35 + "\n")
                for i, channel in enumerate(self.channel_order, 1):
                    if channel in self.preview_cache:
                        threshold = self.channel_thresholds.get(channel, 128)
                        color = self.channel_colors[channel].name()
                        f.write(f"{i}. Canal {channel} - Color: {color} - Umbral: {threshold}\n")
                f.write("\n")
                
                if hasattr(self, 'image_info') and self.image_info:
                    f.write("INFORMACIÓN DE IMAGEN ORIGINAL:\n")
                    f.write("-" * 35 + "\n")
                    f.write(f"Archivo: {os.path.basename(self.image_info.get('file_path', 'N/A'))}\n")
                    f.write(f"Dimensiones originales: {self.image_info.get('width_px', 'N/A')} x {self.image_info.get('height_px', 'N/A')} px\n")
                    f.write(f"DPI original: {self.image_info.get('dpi_x', 'N/A')}\n")
                    f.write(f"Tamaño físico original: {self.image_info.get('width_cm', 'N/A'):.1f} x {self.image_info.get('height_cm', 'N/A'):.1f} cm\n\n")
                
                f.write("ARCHIVOS GENERADOS:\n")
                f.write("-" * 20 + "\n")
                for filename in saved_files:
                    f.write(f"• {filename}\n")
                    
                f.write("\nINSTRUCCIONES DE USO:\n")
                f.write("-" * 20 + "\n")
                f.write("1. Los archivos PNG están listos para grabado de pantallas\n")
                f.write("2. Usar emulsión fotopolimérica de calidad profesional\n")
                f.write("3. Verificar orientación correcta al exponer\n")
                if guides_enabled:
                    f.write("4. Las guías de registro ayudan en el centrado y alineación\n")
                    f.write("5. Las cruces en esquinas son para registro preciso\n")
                    f.write("6. Las marcas centrales facilitan el posicionamiento\n")
                f.write("7. Realizar prueba de impresión antes de producción masiva\n")
                f.write("8. Los PDFs contienen todos los canales en páginas separadas\n")
                
                f.write("\nDATOS TÉCNICOS:\n")
                f.write("-" * 15 + "\n")
                f.write("• Halftones optimizados para calidad profesional\n")
                f.write("• Valores binarios preservados (0 y 255)\n")
                f.write("• Interpolación INTER_NEAREST para redimensionado\n")
                f.write("• Ángulos de canal anti-moiré aplicados automáticamente\n")
                if guides_enabled:
                    f.write("• Guías de registro incluidas según estándares profesionales\n")
                    f.write(f"• Margen de {REGISTRATION_GUIDE_SETTINGS['margin_mm']}mm para manipulación\n")
            
            saved_files.append(spec_filename)
            
            # MENSAJE FINAL DETALLADO
            success_msg = f"✅ GUARDADO COMPLETADO EXITOSAMENTE\n\n"
            success_msg += f"📁 Ubicación: {folder_path}\n\n"
            success_msg += f"📊 Archivos generados: {len(saved_files)}\n\n"
            
            success_msg += "📋 Lista de archivos:\n"
            for i, filename in enumerate(saved_files, 1):
                success_msg += f"   {i}. {filename}\n"
            
            if guides_enabled:
                success_msg += f"\n🎯 LAS GUÍAS DE REGISTRO HAN SIDO APLICADAS CORRECTAMENTE\n"
                success_msg += f"   • Cruces de registro en las 4 esquinas\n"
                success_msg += f"   • Marcas de centrado en los bordes\n"
                success_msg += f"   • Información del canal en cada positivo\n"
                success_msg += f"   • Margen de {REGISTRATION_GUIDE_SETTINGS['margin_mm']}mm para manipulación\n"
            
            if fit_format_enabled:
                success_msg += f"\n📏 FORMATO RESPETADO:\n"
                success_msg += f"   • Papel: {print_format['width']} x {print_format['height']} mm\n"
                success_msg += f"   • DPI: {output_dpi}\n"
                success_msg += f"   • Imagen centrada y escalada apropiadamente\n"
            
            success_msg += f"\n💡 NOTAS IMPORTANTES:\n"
            success_msg += f"   • Los archivos PNG están listos para grabado\n"
            success_msg += f"   • Los PDFs contienen páginas separadas por canal\n"
            success_msg += f"   • Revisa las especificaciones técnicas en el archivo .txt\n"
            
            QtWidgets.QMessageBox.information(self, "🎉 Guardado Exitoso", success_msg)
            self.status_bar.showMessage(f"✅ {len(saved_files)} archivos guardados con guías", 10000)
            
            print(f"\n🎉 === GUARDADO COMPLETADO ===")
            print(f"📁 Archivos: {len(saved_files)}")
            print(f"🎯 Guías: {'✅ INCLUIDAS' if guides_enabled else '❌ SIN GUÍAS'}")
            print(f"📏 Formato: {'✅ RESPETADO' if fit_format_enabled else '❌ TAMAÑO ORIGINAL'}")
            
        except Exception as e:
            error_msg = f"❌ ERROR DURANTE EL GUARDADO:\n\n{str(e)}\n\nDetalles en la consola."
            print(f"\n❌ ERROR CRÍTICO: {error_msg}")
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "❌ Error de Guardado", error_msg)

    def debug_preview_cache(self):
        """
        NUEVA FUNCIÓN: Para verificar qué hay exactamente en preview_cache
        Agrégala a tu clase y puedes llamarla manualmente
        """
        try:
            print(f"\n🔍 === DEBUG PREVIEW CACHE ===")
            
            if not self.preview_cache:
                print(f"❌ Preview cache está vacío")
                return
            
            for channel, data in self.preview_cache.items():
                print(f"\n📊 Canal {channel}:")
                print(f"   Tipo: {type(data)}")
                print(f"   Shape: {data.shape}")
                print(f"   Dtype: {data.dtype}")
                print(f"   Rango: {data.min()} - {data.max()}")
                
                unique_vals = np.unique(data)
                print(f"   Valores únicos ({len(unique_vals)}): {unique_vals}")
                
                # Contar distribución
                if len(unique_vals) <= 10:
                    for val in unique_vals:
                        count = np.sum(data == val)
                        percentage = (count / data.size) * 100
                        print(f"      Valor {val}: {count} píxeles ({percentage:.1f}%)")
                
                # Verificar si es binario correcto
                if len(unique_vals) == 2 and 0 in unique_vals and 255 in unique_vals:
                    print(f"   ✅ Halftone binario correcto")
                else:
                    print(f"   ⚠️ Problema: No es halftone binario puro")
            
        except Exception as e:
            print(f"❌ Error en debug: {e}")
    
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
            if hasattr(self, 'show_channel'):
                self.show_channel(canal)
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
        Motor principal para la separación de colores y generación de semitonos.
        Esta versión incluye control de Generación de Negro (Black Generation)
        para evitar negros sobresaturados y mejorar el detalle en sombras.
        """
        if self.image is None:
            return

        QtWidgets.QApplication.processEvents()

        try:
            resolution_settings = self.get_current_resolution_settings()
            working_image = enhance_image_resolution(self.image, **resolution_settings)
            rgb = cv2.cvtColor(working_image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

            # --- CORRECCIÓN: Control de Generación de Negro (Black Generation) ---
            # 1. Se calcula el canal K (Negro) de forma estándar.
            K_heavy = 1 - np.max(rgb, axis=2)

            # 2. Se aplica un factor de reducción para que el negro no sea tan dominante.
            #    Un valor de 0.9 significa que usamos el 90% de la fuerza del negro,
            #    dejando más espacio para los otros colores en las sombras.
            black_generation_factor = 0.9
            K = K_heavy * black_generation_factor

            # 3. Se calculan C, M, Y usando el nuevo canal K (menos intenso).
            #    Esto hace que los otros canales retengan más color.
            C = (1 - rgb[..., 0] - K) / (1 - K + 1e-10)
            M = (1 - rgb[..., 1] - K) / (1 - K + 1e-10)
            Y = (1 - rgb[..., 2] - K) / (1 - K + 1e-10)
            
            # 5. Se guardan los canales finales.
            self.channel_arrays = {
                'C': (np.clip(C, 0, 1) * 255).astype(np.uint8),
                'M': (np.clip(M, 0, 1) * 255).astype(np.uint8),
                'Y': (np.clip(Y, 0, 1) * 255).astype(np.uint8),
                'K': (np.clip(K, 0, 1) * 255).astype(np.uint8)
            }
            
            if self.white_base_cb.isChecked():
                self.channel_arrays['W'] = generate_white_base(working_image)

            # --- Generación de Semitonos ---
            scale, shape, angles = self._get_halftone_params()
            self.preview_cache = {}
            for channel_name, channel_data in self.channel_arrays.items():
                angle = angles.get(channel_name, 0)
                self.preview_cache[channel_name] = self._generate_halftone_pattern(channel_data, scale, shape, angle)
            
            # --- Actualización Final de la UI ---
            self.update_preview()

        except Exception as e:
            print(f"❌ Error durante el ajuste: {e}")
            import traceback
            traceback.print_exc()
    
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
        self.setFixedHeight(65)  # Altura fija para evitar cambios de layout
        self.setMinimumWidth(300)  # Ancho mínimo para evitar cortes
        self.risk_level = 'BAJO'
        self.risk_score = 0.0
        self.current_lpi = 0
        self.current_mesh = 0
        self.analysis_result = {}
        self.parent_window = parent  # Referencia a la ventana principal
        
        self.setup_ui()
        
    def setup_ui(self):
        """Configurar interfaz del widget de alerta - MEJORADA Y CORREGIDA"""
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)  # Márgenes ajustados
        layout.setSpacing(8)
        
        # === ICONO DE ESTADO AJUSTADO ===
        self.status_icon = QtWidgets.QLabel("🟢")
        self.status_icon.setFixedSize(30, 30)  # Tamaño reducido
        self.status_icon.setAlignment(QtCore.Qt.AlignCenter)
        self.status_icon.setStyleSheet("font-size: 20px;")  # Tamaño de fuente ajustado
        layout.addWidget(self.status_icon)
        
        # === INFORMACIÓN DE ESTADO AJUSTADA ===
        info_layout = QtWidgets.QVBoxLayout()
        info_layout.setSpacing(1)  # Espaciado mínimo
        info_layout.setContentsMargins(0, 0, 0, 0)
        
        self.status_label = QtWidgets.QLabel("✅ Sin riesgo de moiré")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #2E7D32; margin: 0; padding: 0;")
        self.status_label.setWordWrap(True)  # Permitir wrap
        info_layout.addWidget(self.status_label)
        
        self.detail_label = QtWidgets.QLabel("LPI y malla compatibles")
        self.detail_label.setStyleSheet("font-size: 9px; color: #666; margin: 0; padding: 0;")
        self.detail_label.setWordWrap(True)
        info_layout.addWidget(self.detail_label)
        
        layout.addLayout(info_layout, 1)  # Dar prioridad de espacio
        
        # === BOTÓN DE ANÁLISIS AJUSTADO ===
        self.analyze_btn = QtWidgets.QPushButton("📊 Análisis")
        self.analyze_btn.setFixedSize(75, 40)  # Tamaño reducido
        self.analyze_btn.setStyleSheet("""
            QPushButton {
                font-size: 10px; 
                font-weight: bold;
                border: 2px solid #2196F3;
                border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                           stop:0 #E3F2FD, stop:1 #BBDEFB);
                color: #1976D2;
                padding: 2px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                           stop:0 #BBDEFB, stop:1 #90CAF9);
                border-color: #1976D2;
            }
            QPushButton:pressed {
                background: #90CAF9;
            }
        """)
        self.analyze_btn.clicked.connect(self.show_detailed_analysis)
        layout.addWidget(self.analyze_btn)
        
        # No usar addStretch() para evitar problemas de layout
        
    def update_moire_status(self, lpi, mesh, analysis_result):
        """Actualizar estado visual del moiré - AJUSTE CORREGIDO"""
        self.current_lpi = lpi
        self.current_mesh = mesh
        self.risk_level = analysis_result['risk_level']
        self.risk_score = analysis_result['risk_score']
        self.analysis_result = analysis_result
        
        # === CONFIGURACIONES DE RIESGO AJUSTADAS ===
        risk_configs = {
            'CRITICO': {
                'icon': '🔴', 
                'text': '🚨 RIESGO CRÍTICO', 
                'color': '#D32F2F',
                'bg_color': '#FFEBEE',
                'border_color': '#F44336'
            },
            'ALTO': {
                'icon': '🟠', 
                'text': '⚠️ ALTO riesgo', 
                'color': '#F57C00',
                'bg_color': '#FFF3E0',
                'border_color': '#FF9800'
            },
            'MEDIO': {
                'icon': '🟡', 
                'text': '⚠️ Riesgo moderado', 
                'color': '#F9A825',
                'bg_color': '#FFFDE7',
                'border_color': '#FBC02D'
            },
            'BAJO': {
                'icon': '🟢', 
                'text': '✅ Bajo riesgo', 
                'color': '#388E3C',
                'bg_color': '#E8F5E8',
                'border_color': '#4CAF50'
            }
        }
        
        config = risk_configs[self.risk_level]
        
        # === ACTUALIZAR ELEMENTOS VISUALES ===
        self.status_icon.setText(config['icon'])
        self.status_label.setText(config['text'])
        self.status_label.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {config['color']}; margin: 0; padding: 0;")
        
        # Detalle con información técnica más compacta
        ratio = mesh / lpi if lpi > 0 else 0
        detail_text = f"LPI {lpi} vs Malla {mesh} (R:{ratio:.1f}) - {self.risk_score:.0%}"
        self.detail_label.setText(detail_text)
        
        # === ESTILO DEL WIDGET AJUSTADO ===
        widget_style = f"""
            MoireWarningWidget {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                           stop:0 {config['bg_color']}, stop:1 {config['bg_color']}E0);
                border: 2px solid {config['border_color']};
                border-radius: 8px;
                margin: 1px;
                min-height: 65px;
                max-height: 65px;
            }}
        """
        
        self.setStyleSheet(widget_style)
        
        # === ACTUALIZAR ESTILO DEL BOTÓN SEGÚN RIESGO ===
        if self.risk_level in ['CRITICO', 'ALTO']:
            self.analyze_btn.setText("🚨 ¡Fix!")
            self.analyze_btn.setStyleSheet("""
                QPushButton {
                    font-size: 10px; 
                    font-weight: bold;
                    border: 2px solid #F44336;
                    border-radius: 6px;
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                               stop:0 #FFEBEE, stop:1 #FFCDD2);
                    color: #C62828;
                    padding: 2px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                               stop:0 #FFCDD2, stop:1 #EF9A9A);
                    border-color: #C62828;
                }
                QPushButton:pressed {
                    background: #EF9A9A;
                }
            """)
        else:
            self.analyze_btn.setText("📊 Info")
            self.analyze_btn.setStyleSheet("""
                QPushButton {
                    font-size: 10px; 
                    font-weight: bold;
                    border: 2px solid #2196F3;
                    border-radius: 6px;
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                               stop:0 #E3F2FD, stop:1 #BBDEFB);
                    color: #1976D2;
                    padding: 2px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                               stop:0 #BBDEFB, stop:1 #90CAF9);
                    border-color: #1976D2;
                }
                QPushButton:pressed {
                    background: #90CAF9;
                }
            """)
        
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
