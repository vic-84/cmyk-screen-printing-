"""Ventana principal de la app de separación de color para serigrafía (PyQt5)."""

import os
import json
from datetime import datetime
import numpy as np
import cv2
from PyQt5 import QtWidgets, QtGui, QtCore

from ..core.image_processing import prepare_image_for_processing
from ..utils.constants import *
from ..utils.helpers import convert_units
from .pdf_selector import PDFPageSelector
from . import theme
from ..core.job import CHANNEL_NAMES, JobSettings
from ..core.screening import screen_channel
from ..core.separation import design_size_mm, layout, render
from ..core import mesh as mesh_rules
from ..core import output
from ..core import icc
from ..core import input as doc_input
from ..core import simulate as sim
from ..core.simulate import INK_TYPES, SUBSTRATE_PROFILES
from ..core.color import detect_palette, lab_to_rgb, match_library, read_library, rgb_to_lab, write_ase
from ..core.spot import default_needs_base, order_light_to_dark


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


class ChannelScreenDelegate(QtWidgets.QStyledItemDelegate):
    """
    Pinta cada canal como una pantalla de la prensa: muestra de tinta, nombre y
    ángulo de trama. El texto del item sigue siendo la letra del canal ('C', 'M'...)
    porque el resto del código lo usa como identificador.
    """
    ROW_HEIGHT = 30

    def __init__(self, color_for_channel, angle_for_channel=None, parent=None, name_for_channel=None,
                 density_for_channel=None, own_tone_for_channel=None):
        super().__init__(parent)
        self._own_tone_for_channel = own_tone_for_channel or (lambda ch: "")
        self._density_for_channel = density_for_channel or (lambda ch: 100.0)
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
        # Densidad cambiada: se avisa en la lista (una tinta al 0 % no se imprime)
        density = self._density_for_channel(channel)
        marks = self._own_tone_for_channel(channel)
        if marks and density == 100:
            painter.setPen(QtGui.QColor(theme.EMULSION))
            painter.drawText(text_rect.adjusted(0, 0, -52, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                             marks)
        if density != 100:
            painter.setPen(QtGui.QColor(theme.ESTADO_RIESGO if density == 0 else theme.ESTADO_ALERTA))
            painter.drawText(text_rect.adjusted(0, 0, -52, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                             f"densidad {density:g} %")
        painter.restore()


class WheelGuard(QtCore.QObject):
    """
    La rueda del mouse solo cambia casillas, menús y deslizadores que tienen el
    foco (al hacer clic). Sin foco, la rueda desplaza el panel: al bajar por los
    controles no se cambian valores sin querer.
    """

    def eventFilter(self, widget, event):
        if event.type() == QtCore.QEvent.Wheel and not widget.hasFocus():
            parent = widget.parentWidget()
            while parent is not None and not isinstance(parent, QtWidgets.QAbstractScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                QtWidgets.QApplication.sendEvent(parent.verticalScrollBar(), event)
            return True
        return False

    def protect(self, root):
        for widget in root.findChildren((QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QAbstractSlider)):
            if isinstance(widget, QtWidgets.QScrollBar):
                continue
            widget.setFocusPolicy(QtCore.Qt.StrongFocus)
            widget.installEventFilter(self)


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
        self.mm_per_px = None

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
            QPushButton {{ padding: 0 8px; min-height: 0; border-radius: 4px; }}
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

        self.zoom_100_btn = QtWidgets.QPushButton("Tamaño real")
        self.zoom_100_btn.setFixedHeight(24)
        self.zoom_100_btn.setToolTip("Muestra el diseño al tamaño en que se imprime (el % del zoom es respecto a ese tamaño)")
        self.zoom_100_btn.clicked.connect(self.zoom_to_100)
        controls_layout.addWidget(self.zoom_100_btn)

        for button in (self.zoom_fit_btn, self.zoom_100_btn):
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(button.text()) + 36)
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

    def setPixmap(self, pixmap, mm_per_px=None):
        """
        Nueva imagen. Solo se ajusta a la ventana con la primera imagen o si
        cambia su tamaño; al retocar un ajuste se conservan zoom y posición.
        mm_per_px: milímetros impresos por píxel de la imagen (para el tamaño real).
        """
        if isinstance(pixmap, QtGui.QPixmap) and not pixmap.isNull():
            same_size = not self.original_pixmap.isNull() and self.original_pixmap.size() == pixmap.size()
            self.original_pixmap = pixmap
            self.mm_per_px = mm_per_px
            if same_size:
                h, v = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
                self.update_display()
                self.horizontalScrollBar().setValue(h)
                self.verticalScrollBar().setValue(v)
            else:
                self.zoom_to_fit()
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

        # El % es respecto al tamaño impreso: 100 % = lo que mide en la prenda
        real = self.real_size_zoom()
        self.zoom_label.setText(f"{self.zoom_factor / real:.0%}" if real else f"{self.zoom_factor:.0%}")

    def real_size_zoom(self):
        """Zoom con el que la imagen se ve en pantalla al tamaño impreso (None si no se sabe)."""
        if not self.mm_per_px:
            return None
        # ponytail: el DPI físico lo informa el monitor (EDID); si una regla
        # sobre la pantalla no coincide, el monitor informa mal su tamaño
        screen_dpi = self.screen().physicalDotsPerInch()
        return screen_dpi / 25.4 * self.mm_per_px

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
        """Zoom al tamaño impreso real (o 1:1 en píxeles si no se conoce la medida)."""
        self.zoom_factor = min(self.max_zoom, self.real_size_zoom() or 1.0)
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
        self._guide_plan_cache = None
        self.current_channel = None

        # Timer para umbrales
        self.threshold_timer = QtCore.QTimer()
        self.threshold_timer.setSingleShot(True)
        self.threshold_timer.timeout.connect(self._delayed_threshold_update)

        self.moire_warning = None

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

        # --- 5. Inicialización de la Interfaz Gráfica (SIEMPRE AL FINAL) ---
        self.init_ui()
        self.wheel_guard = WheelGuard(self)
        self.wheel_guard.protect(self)

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

        # Un paso del trabajo por pestaña: no hay que bajar por todo el panel
        # para llegar a los canales, que es lo que más se retoca
        self.step_tabs = QtWidgets.QTabWidget()
        self.step_tabs.setObjectName("pasos")
        self.step_tabs.setDocumentMode(True)

        def step_page(title, tooltip):
            scroll = QtWidgets.QScrollArea()
            scroll.setObjectName("panelControles")
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            container = QtWidgets.QWidget()
            container.setObjectName("contenedorControles")
            page = QtWidgets.QVBoxLayout(container)
            page.setSpacing(10)
            page.setContentsMargins(12, 8, 12, 12)
            scroll.setWidget(container)
            self.step_tabs.addTab(scroll, title)
            self.step_tabs.setTabToolTip(self.step_tabs.count() - 1, tooltip)
            step_pages.append(page)
            return page

        step_pages = []
        self.step_tabs.tabBar().setExpanding(True)

        design_page = step_page("Diseño", "Imagen, prenda y tinta")
        separation_page = step_page("Separación", "Trama, colores planos, tono y gestión de color")
        channels_page = step_page("Canales", "Orden, color, umbral y curva de cada tinta")
        output_page = step_page("Salida", "Película, tamaño, guías y archivo")

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

        quality_grid = QtWidgets.QGridLayout()
        quality_grid.setHorizontalSpacing(10)
        quality_grid.setColumnStretch(1, 1)
        quality_grid.addWidget(field_label("Limpiar ruido"), 0, 0)
        self.denoise_combo = compact_combo(QtWidgets.QComboBox())
        for label, value in (("No", "off"), ("Suave", "light"), ("Fuerte", "strong")):
            self.denoise_combo.addItem(label, value)
        self.denoise_combo.setToolTip("Quita ruido y halos de compresión JPEG antes de separar "
                                      "(en color plano evita motas y bordes sucios)")
        quality_grid.addWidget(self.denoise_combo, 0, 1)
        quality_grid.addWidget(field_label("Enfoque"), 1, 0)
        self.sharpen_spin = QtWidgets.QDoubleSpinBox()
        self.sharpen_spin.setRange(0, 150)
        self.sharpen_spin.setDecimals(0)
        self.sharpen_spin.setSuffix(" %")
        self.sharpen_spin.setToolTip("Máscara de enfoque tras ampliar: recupera nitidez en fotos de poca resolución")
        quality_grid.addWidget(self.sharpen_spin, 1, 1)
        self.smooth_edges_cb = QtWidgets.QCheckBox("Suavizar bordes de tintas sólidas")
        self.smooth_edges_cb.setChecked(True)
        self.smooth_edges_cb.setToolTip("Al ampliar una imagen chica, quita los escalones de los bordes "
                                        "sin crear solapes ni huecos entre tintas")
        quality_grid.addWidget(self.smooth_edges_cb, 2, 0, 1, 2)
        image_layout.addLayout(quality_grid)

        self.image_res_label = secondary_label("Ninguna imagen abierta")
        image_layout.addWidget(self.image_res_label)
        self.complexity_label = secondary_label("")
        image_layout.addWidget(self.complexity_label)
        design_page.addWidget(image_group)

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
        separation_page.addWidget(params_group)

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

        spot_grid.addWidget(field_label("Limpiar motas"), 5, 0)
        self.despeckle_spin = QtWidgets.QDoubleSpinBox()
        self.despeckle_spin.setRange(0, 2)
        self.despeckle_spin.setSingleStep(0.05)
        self.despeckle_spin.setDecimals(2)
        self.despeckle_spin.setValue(0.25)
        self.despeckle_spin.setSuffix(" mm")
        self.despeckle_spin.setToolTip("Elimina manchas de tinta sólida más chicas que este diámetro "
                                       "(ruido JPEG, píxeles sueltos). 0 = no limpiar")
        spot_grid.addWidget(self.despeckle_spin, 5, 1)
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
        separation_page.addWidget(self.spot_group)

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
        separation_page.addWidget(tone_group)

        # === SUSTRATO ===
        substrate_group = QtWidgets.QGroupBox("Sustrato y tinta")
        substrate_layout = QtWidgets.QGridLayout(substrate_group)
        substrate_layout.setHorizontalSpacing(10)
        substrate_layout.setVerticalSpacing(6)
        substrate_layout.setColumnStretch(1, 1)
        substrate_layout.addWidget(field_label("Sustrato"), 0, 0)
        self.substrate_combo = compact_combo(QtWidgets.QComboBox())
        self.substrate_combo.addItems(["Personalizado"] + list(SUBSTRATE_PROFILES.keys()))
        self.substrate_combo.setToolTip("Material: pone límite de tinta, rango tonal, ganancia, base y un color de prenda típico.\n"
                                        "El color exacto de tu prenda se elige abajo, en Prenda.")
        self.substrate_combo.currentTextChanged.connect(self.apply_substrate_profile)
        substrate_layout.addWidget(self.substrate_combo, 0, 1)
        substrate_layout.addWidget(field_label("Tinta"), 2, 0)
        self.ink_type_combo = compact_combo(QtWidgets.QComboBox())
        self.ink_type_combo.addItems(list(INK_TYPES.keys()))
        self.ink_type_combo.setToolTip("Opacidad de la tinta en la simulación: la transparente filtra el color de abajo")
        self.ink_type_combo.currentTextChanged.connect(lambda *_: self.update_preview())
        substrate_layout.addWidget(self.ink_type_combo, 2, 1)
        substrate_layout.addWidget(field_label("Base"), 4, 0)
        self.base_combo = compact_combo(QtWidgets.QComboBox())
        for name, rgb in BASE_PRESETS.items():
            self.base_combo.addItem(name, rgb)
        self.base_combo.addItem("Personalizada…", None)
        self.base_combo.setToolTip("Blanca: color más brillante en prenda oscura. Gris: cubre con menos tinta y "
                                   "tacto más suave. Gris bloqueadora: frena la migración del teñido en poliéster")
        self.base_combo.activated.connect(self.on_base_changed)
        substrate_layout.addWidget(self.base_combo, 4, 1)
        self.base_rgb = list(BASE_PRESETS["Base blanca"])
        substrate_layout.addWidget(field_label("Límite de tinta"), 3, 0)
        self.ink_limit_spin = QtWidgets.QDoubleSpinBox()
        self.ink_limit_spin.setRange(100, 400)
        self.ink_limit_spin.setDecimals(0)
        self.ink_limit_spin.setSuffix(" %")
        self.ink_limit_spin.setValue(TOTAL_INK_LIMIT)
        self.ink_limit_spin.setToolTip("Suma máxima de C+M+Y+K. Textil: 240–280 %")
        substrate_layout.addWidget(self.ink_limit_spin, 3, 1)
        self.white_base_cb = QtWidgets.QCheckBox("Imprimir base")
        self.white_base_cb.setToolTip("Para prenda oscura: la base se imprime primero y se contrae en los bordes")
        substrate_layout.addWidget(self.white_base_cb, 5, 1)
        self.garment_color_btn = QtWidgets.QPushButton("Color de la prenda…")
        self.garment_color_btn.clicked.connect(self.select_garment_color)
        self.garment_color_btn.setToolTip("Color real de la tela: fondo de la simulación y qué zonas no llevan tinta")
        substrate_layout.addWidget(field_label("Prenda"), 1, 0)
        substrate_layout.addWidget(self.garment_color_btn, 1, 1)
        # Cuatricromía en prenda oscura con 4 estaciones: la tela hace de negro
        self.garment_black_cb = QtWidgets.QCheckBox("Usar la prenda como negro (sin película K)")
        self.garment_black_cb.setToolTip(
            "Para pulpos de 4 estaciones en prenda oscura: base + C + M + Y. No se imprime el negro: "
            "la tela hace de negro y la base dibuja los grises con su trama.\n"
            "Haz una prueba en prenda antes de producir.")
        substrate_layout.addWidget(self.garment_black_cb, 6, 0, 1, 2)
        self.garment_black_label = field_label("Sin base bajo")
        substrate_layout.addWidget(self.garment_black_label, 7, 0)
        self.garment_black_spin = QtWidgets.QDoubleSpinBox()
        self.garment_black_spin.setRange(0, 60)
        self.garment_black_spin.setDecimals(0)
        self.garment_black_spin.setSuffix(" % de luz")
        self.garment_black_spin.setValue(12)
        self.garment_black_spin.setToolTip(
            "Por debajo de esta luz no se imprime base: ahí queda el negro de la tela.\n"
            "Más bajo: más detalle en sombras (cabello, humo). Más alto: sombras más negras y limpias.")
        substrate_layout.addWidget(self.garment_black_spin, 7, 1)
        self.garment_boost_label = field_label("Refuerzo de grises")
        substrate_layout.addWidget(self.garment_boost_label, 8, 0)
        self.garment_boost_spin = QtWidgets.QDoubleSpinBox()
        self.garment_boost_spin.setRange(0, 100)
        self.garment_boost_spin.setDecimals(0)
        self.garment_boost_spin.setSuffix(" %")
        self.garment_boost_spin.setToolTip("Aclara los grises intermedios de la base (0 % = proporcional a la imagen).")
        substrate_layout.addWidget(self.garment_boost_spin, 8, 1)
        self.garment_boost_spin.valueChanged.connect(self.schedule_reseparation)
        for control in (self.garment_black_cb, self.white_base_cb):
            control.stateChanged.connect(self.on_garment_black_changed)
        self.garment_black_spin.valueChanged.connect(self.schedule_reseparation)
        design_page.addWidget(substrate_group)

        # === GESTIÓN DE COLOR ===
        color_group = QtWidgets.QGroupBox("Gestión de color")
        color_layout = QtWidgets.QGridLayout(color_group)
        color_layout.setHorizontalSpacing(10)
        color_layout.setVerticalSpacing(6)
        color_layout.setColumnStretch(1, 1)
        color_layout.addWidget(field_label("Separación CMYK"), 0, 0)
        self.icc_profile_combo = compact_combo(QtWidgets.QComboBox())
        self.icc_profile_combo.setToolTip("Con un perfil ICC la separación la define el perfil (GCR y tinta total). "
                                          "Úsalo cuando la marca o el cliente exija su perfil")
        color_layout.addWidget(self.icc_profile_combo, 0, 1, 1, 2)
        color_layout.addWidget(field_label("Intento"), 1, 0)
        self.icc_intent_combo = compact_combo(QtWidgets.QComboBox())
        self.icc_intent_combo.addItems(list(icc.INTENTS.keys()))
        self.icc_intent_combo.setToolTip("Colorimétrico relativo: colores exactos dentro de gama (lo habitual para marcas). "
                                         "Perceptual: comprime toda la imagen para conservar degradados")
        color_layout.addWidget(self.icc_intent_combo, 1, 1, 1, 2)
        color_layout.addWidget(field_label("Imágenes sin perfil"), 2, 0)
        self.input_profile_combo = compact_combo(QtWidgets.QComboBox())
        self.input_profile_combo.setToolTip("Perfil RGB que se asume cuando la imagen no trae uno incrustado")
        color_layout.addWidget(self.input_profile_combo, 2, 1, 1, 2)
        self.icc_bpc_cb = QtWidgets.QCheckBox("Compensar punto negro")
        self.icc_bpc_cb.setChecked(True)
        self.icc_limit_cb = QtWidgets.QCheckBox("Aplicar también el límite de tinta")
        self.icc_limit_cb.setToolTip("Recorta la tinta total del perfil al límite del sustrato. "
                                     "Desactívalo si la marca exige el perfil sin modificar")
        color_layout.addWidget(self.icc_bpc_cb, 3, 0, 1, 3)
        color_layout.addWidget(self.icc_limit_cb, 4, 0, 1, 3)
        profile_buttons = QtWidgets.QHBoxLayout()
        import_profile_btn = QtWidgets.QPushButton("Importar perfil…")
        import_profile_btn.clicked.connect(self.import_icc_profile)
        manage_profiles_btn = QtWidgets.QPushButton("Perfiles instalados…")
        manage_profiles_btn.clicked.connect(self.show_icc_profiles)
        profile_buttons.addWidget(import_profile_btn)
        profile_buttons.addWidget(manage_profiles_btn)
        color_layout.addLayout(profile_buttons, 5, 0, 1, 3)
        self.icc_info_label = secondary_label("")
        color_layout.addWidget(self.icc_info_label, 6, 0, 1, 3)
        separation_page.addWidget(color_group)

        # === SALIDA ===
        # Tres bloques en el orden en que se decide: el lienzo (la película),
        # dónde va el diseño dentro de él y qué lleva la película además del diseño.
        format_group = QtWidgets.QGroupBox("Salida")
        out = QtWidgets.QGridLayout(format_group)
        out.setHorizontalSpacing(10)
        out.setVerticalSpacing(6)
        out.setColumnStretch(1, 1)
        row = 0

        def subtitle(text):
            nonlocal row
            label = QtWidgets.QLabel(text)
            label.setProperty("rol", "subtitulo")
            out.addWidget(label, row, 0, 1, 2)
            row += 1

        def field(text, widget):
            nonlocal row
            label = field_label(text)
            out.addWidget(label, row, 0)
            out.addWidget(widget, row, 1)
            row += 1
            return label

        def full(widget):
            nonlocal row
            out.addWidget(widget, row, 0, 1, 2)
            row += 1

        subtitle("Lienzo")
        self.print_format_combo = compact_combo(QtWidgets.QComboBox())
        self.print_format_combo.addItems(list(PRINT_FORMATS.keys()) + ["Personalizado"])
        self.print_format_combo.setToolTip("Medida de la película. El diseño se coloca dentro y nunca se corta")
        self.print_format_combo.currentIndexChanged.connect(self.on_print_format_changed)
        field("Formato", self.print_format_combo)

        self.custom_size_widget = QtWidgets.QWidget()
        custom_layout = QtWidgets.QGridLayout(self.custom_size_widget)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setHorizontalSpacing(10)
        custom_layout.setVerticalSpacing(6)
        custom_layout.setColumnStretch(1, 1)
        self.unit_combo = compact_combo(QtWidgets.QComboBox())
        self.unit_combo.addItems([f"{k} ({v['label']})" for k, v in MEASUREMENT_UNITS.items()])
        self.custom_width = QtWidgets.QDoubleSpinBox()
        self.custom_height = QtWidgets.QDoubleSpinBox()
        self.custom_dpi = QtWidgets.QSpinBox()
        self.custom_dpi.setRange(72, 1200)
        self.custom_dpi.setValue(300)
        self.custom_dpi.setSuffix(" dpi")
        for i, (text, widget) in enumerate((("Unidad", self.unit_combo), ("Ancho", self.custom_width),
                                            ("Alto", self.custom_height), ("DPI sugerido", self.custom_dpi))):
            custom_layout.addWidget(field_label(text), i, 0)
            custom_layout.addWidget(widget, i, 1)
        # Rango, decimales y unidad reales: sin esto Qt limita a 0–99.99 y
        # un formato de 300 × 400 mm se recortaba a 99.99 mm
        self.setup_dimension_spinbox(self.custom_width, "mm")
        self.setup_dimension_spinbox(self.custom_height, "mm")
        self.custom_width.setValue(300)
        self.custom_height.setValue(400)
        self.unit_combo.currentIndexChanged.connect(self.on_unit_changed)
        for control in (self.custom_width, self.custom_height, self.custom_dpi):
            control.valueChanged.connect(self.on_output_size_changed)
        self.custom_size_widget.setVisible(False)
        full(self.custom_size_widget)

        self.output_dpi_combo = compact_combo(QtWidgets.QComboBox())
        self.output_dpi_combo.addItem("Según formato", 0)
        for dpi in (300, 600, 720, 1200):
            self.output_dpi_combo.addItem(f"{dpi} dpi", dpi)
        self.output_dpi_combo.setToolTip("DPI de la impresora de película. Más DPI = más niveles de gris por punto")
        field("Resolución", self.output_dpi_combo)
        self.format_info_label = secondary_label("")
        full(self.format_info_label)

        subtitle("Diseño en el lienzo")
        self.placement_combo = compact_combo(QtWidgets.QComboBox())
        self.placement_combo.addItem("Ajustar al lienzo", "fit")
        self.placement_combo.addItem("Tamaño real", "real")
        self.placement_combo.addItem("Ancho del diseño", "width")
        self.placement_combo.setToolTip(
            "Ajustar: llena el área útil conservando la proporción. Tamaño real: según los DPI de la imagen. "
            "Ancho del diseño: el ancho que indiques. Si no cabe, se reduce; nunca se corta.")
        field("Imagen", self.placement_combo)
        self.design_width_spin = QtWidgets.QDoubleSpinBox()
        self.design_width_spin.setRange(10, 2000)
        self.design_width_spin.setDecimals(1)
        self.design_width_spin.setSuffix(" mm")
        self.design_width_spin.setValue(280)
        self.design_width_label = field("Ancho", self.design_width_spin)
        self.design_width_spin.setVisible(False)
        self.design_width_label.setVisible(False)
        self.align_combo = compact_combo(QtWidgets.QComboBox())
        self.align_combo.addItem("Centrada", "center")
        self.align_combo.addItem("Arriba al centro", "top")
        self.align_combo.setToolTip("Arriba al centro: la posición habitual de un estampado de pecho")
        field("Posición", self.align_combo)
        self.distress_spin = QtWidgets.QDoubleSpinBox()
        self.distress_spin.setRange(0, 80)
        self.distress_spin.setDecimals(0)
        self.distress_spin.setSuffix(" mm")
        self.distress_spin.setSpecialValueText("Sin desgaste")
        self.distress_spin.setToolTip("Rompe el borde del diseño en manchas irregulares a lo largo de esta distancia, "
                                      "para que no quede cuadrado. Deja espacio en blanco donde van las guías")
        field("Borde desgastado", self.distress_spin)
        self.design_size_label = secondary_label("")
        full(self.design_size_label)
        self.placement_combo.currentIndexChanged.connect(self.on_placement_changed)
        self.design_width_spin.valueChanged.connect(self.on_output_size_changed)
        self.align_combo.currentIndexChanged.connect(self.on_output_size_changed)
        self.distress_spin.valueChanged.connect(self.on_output_size_changed)

        subtitle("Guías y película")
        self.guides_cb = QtWidgets.QCheckBox("Guías de registro")
        self.guides_cb.setToolTip("Cruces, datos del canal y tira de control dentro del lienzo: la película no crece")
        full(self.guides_cb)
        self.guide_position_combo = compact_combo(QtWidgets.QComboBox())
        self.guide_position_combo.addItem("Automática", "auto")
        self.guide_position_combo.addItem("En margen", "margin")
        self.guide_position_combo.addItem("En espacios en blanco", "blank")
        self.guide_position_combo.setToolTip(
            "En margen: el diseño se reduce para dejar el margen de las guías.\n"
            "En espacios en blanco: el diseño usa todo el lienzo y las guías van donde no hay tinta "
            "(por ejemplo, el borde desgastado).\n"
            "Automática: en blanco si hay borde desgastado, en margen si no.")
        field("Colocación", self.guide_position_combo)
        self.guide_margin_spin = QtWidgets.QDoubleSpinBox()
        self.guide_margin_spin.setRange(5, 50)
        self.guide_margin_spin.setDecimals(1)
        self.guide_margin_spin.setSuffix(" mm por lado")
        self.guide_margin_spin.setValue(15)
        self.guide_margin_spin.setToolTip("Cuánto se reduce el diseño en cada lado para dejar lugar a las guías")
        field("Margen", self.guide_margin_spin)
        self.guide_position_combo.currentIndexChanged.connect(self.on_output_size_changed)
        self.guide_margin_spin.valueChanged.connect(self.on_output_size_changed)

        self.output_format_combo = compact_combo(QtWidgets.QComboBox())
        self.output_format_combo.addItem("PNG", "png")
        self.output_format_combo.addItem("TIFF 1 bit (RIP)", "tiff")
        field("Archivo", self.output_format_combo)

        film_options = QtWidgets.QGridLayout()
        film_options.setHorizontalSpacing(12)
        self.control_strip_cb = QtWidgets.QCheckBox("Tira de control")
        self.control_strip_cb.setChecked(True)
        self.control_strip_cb.setToolTip("Parches 5–95 % en cada película, para revisar exposición y ganancia")
        self.mirror_cb = QtWidgets.QCheckBox("Espejo")
        self.mirror_cb.setToolTip("Invierte la película de izquierda a derecha (emulsión abajo)")
        self.negative_cb = QtWidgets.QCheckBox("Negativo")
        self.cmyk_composite_cb = QtWidgets.QCheckBox("TIFF CMYK con perfil")
        self.cmyk_composite_cb.setToolTip("Exporta además la separación compuesta en un TIFF CMYK con el perfil incrustado")
        film_options.addWidget(self.control_strip_cb, 0, 0)
        film_options.addWidget(self.mirror_cb, 0, 1)
        film_options.addWidget(self.negative_cb, 1, 0)
        film_options.addWidget(self.cmyk_composite_cb, 1, 1)
        out.addLayout(film_options, row, 0, 1, 2)
        row += 1
        output_page.addWidget(format_group)

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
            "Orden de impresión de arriba abajo: ▲ ▼ o arrastra para cambiarlo. "
            "Selecciona un canal para su umbral y su curva; «Color» o doble clic para su color de tinta."))

        self.channel_list = DraggableChannelList(self)
        self.channel_list.setItemDelegate(
            ChannelScreenDelegate(
                lambda ch: self.channel_colors.get(ch, QtGui.QColor("white")),
                self.channel_angle_for_list,
                self.channel_list,
                self.channel_display_name,
                lambda ch: self.density_spins[ch].value() if ch in self.density_spins else 100.0,
                self.channel_adjust_marks))
        self.channel_list.orderChanged.connect(self.set_channel_order)
        self.channel_list.itemSelectionChanged.connect(self.on_channel_selection_changed)
        self.channel_list.setFixedHeight(ChannelScreenDelegate.ROW_HEIGHT * 5 + 4)
        self.channel_list.itemDoubleClicked.connect(lambda item: self.pick_channel_color(item.text()))
        # Orden de impresión como capas: la de arriba se imprime primero
        list_row = QtWidgets.QHBoxLayout()
        list_row.setSpacing(6)
        list_row.addWidget(self.channel_list, 1)
        order_buttons = QtWidgets.QVBoxLayout()
        order_buttons.setSpacing(4)
        self.move_up_btn = QtWidgets.QPushButton("▲")
        self.move_up_btn.setToolTip("Imprimir este color antes (sube en el orden)")
        self.move_down_btn = QtWidgets.QPushButton("▼")
        self.move_down_btn.setToolTip("Imprimir este color después (baja en el orden)")
        self.color_btn = QtWidgets.QPushButton("Color")
        self.color_btn.setToolTip("Cambiar el color de la tinta de este canal (también con doble clic)")
        for button in (self.move_up_btn, self.move_down_btn, self.color_btn):
            button.setFixedWidth(60)
            button.setStyleSheet("QPushButton { padding: 4px 2px; }")
            button.setEnabled(False)
            order_buttons.addWidget(button)
        order_buttons.addStretch(1)
        self.move_up_btn.clicked.connect(lambda: self.move_channel(-1))
        self.move_down_btn.clicked.connect(lambda: self.move_channel(1))
        self.color_btn.clicked.connect(lambda: self.current_channel and self.pick_channel_color(self.current_channel))
        list_row.addLayout(order_buttons)
        channels_tab_layout.addLayout(list_row)

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

        # Curva de color del positivo seleccionado: sube o baja la tinta en
        # luces, medios y sombras sin mover el umbral
        self.curve_box = QtWidgets.QWidget()
        curve_grid = QtWidgets.QGridLayout(self.curve_box)
        curve_grid.setContentsMargins(0, 4, 0, 0)
        curve_grid.setHorizontalSpacing(8)
        curve_grid.setVerticalSpacing(2)
        self.curve_title = field_label("Curva de color")
        curve_grid.addWidget(self.curve_title, 0, 0, 1, 2)
        reset_curve_btn = QtWidgets.QPushButton("Restablecer")
        reset_curve_btn.setToolTip("Deja la curva de este canal sin cambios")
        reset_curve_btn.clicked.connect(self.reset_channel_curve)
        curve_grid.addWidget(reset_curve_btn, 0, 2)
        self.channel_color_curves = {}
        self.curve_sliders, self.curve_value_labels = [], []
        for row, (name, tip) in enumerate((("Luces", "Tonos claros (25 %)"), ("Medios", "Tonos medios (50 %)"),
                                           ("Sombras", "Tonos oscuros (75 %)")), 1):
            curve_grid.addWidget(field_label(name), row, 0)
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(-50, 50)
            slider.setToolTip(f"{tip}: + más tinta de este color, − menos tinta")
            slider.valueChanged.connect(self.on_channel_curve_changed)
            curve_grid.addWidget(slider, row, 1)
            value_label = secondary_label("0")
            value_label.setWordWrap(False)
            value_label.setMinimumWidth(34)
            value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            curve_grid.addWidget(value_label, row, 2)
            self.curve_sliders.append(slider)
            self.curve_value_labels.append(value_label)
        curve_grid.setColumnStretch(1, 1)
        self.curve_box.setEnabled(False)
        channels_tab_layout.addWidget(self.curve_box)

        channels_tabs.addTab(channels_tab, "Orden y umbral")

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

        # Tono por canal: cada tinta gana distinto (la base de plastisol crece más
        # que un cian de proceso). «General» = usa el valor del grupo Tono.
        tone_tab = QtWidgets.QWidget()
        tone_grid = QtWidgets.QGridLayout(tone_tab)
        tone_grid.setContentsMargins(8, 8, 8, 8)
        tone_grid.setHorizontalSpacing(6)
        tone_grid.setVerticalSpacing(6)
        for col, text in enumerate(("Canal", "Mín.", "Máx.", "Ganancia", "")):
            tone_grid.addWidget(field_label(text), 0, col)
        self.channel_tone_spins = {}
        self.channel_curves = {}
        self.channel_curve_buttons = {}

        def general_spin(maximum, tooltip):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(-1, maximum)
            spin.setDecimals(0)
            spin.setSuffix(" %")
            spin.setSpecialValueText("General")
            spin.setValue(-1)
            # Que la tabla quepa en el panel: las casillas se reparten el ancho
            spin.setMinimumWidth(60)
            spin.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            spin.setAlignment(QtCore.Qt.AlignCenter)
            spin.setStyleSheet("QDoubleSpinBox { padding: 2px 2px; }")
            spin.setToolTip(tooltip + "\n«General» usa el valor del grupo Tono.")
            spin.valueChanged.connect(self.on_channel_tone_changed)
            return spin

        for col in (1, 2, 3):
            tone_grid.setColumnStretch(col, 1)
        for i, ch in enumerate(['C', 'M', 'Y', 'K', 'W'], 1):
            tone_grid.addWidget(QtWidgets.QLabel("Base" if ch == 'W' else CHANNEL_NAMES[ch]), i, 0)
            spins = {
                'min_dot': general_spin(50, "Punto mínimo de esta tinta"),
                'max_dot': general_spin(100, "Punto máximo de esta tinta"),
                'dot_gain': general_spin(45, "Ganancia al 50 % medida con esta tinta"),
            }
            for col, key in enumerate(('min_dot', 'max_dot', 'dot_gain'), 1):
                tone_grid.addWidget(spins[key], i, col)
            curve_btn = QtWidgets.QPushButton("Curva")
            curve_btn.setFixedWidth(64)
            curve_btn.setStyleSheet("QPushButton { padding: 2px 4px; }")
            curve_btn.setToolTip("Curva de ganancia medida con esta tinta (reemplaza la ganancia)")
            curve_btn.clicked.connect(lambda _, c=ch: self.edit_gain_curve(c))
            tone_grid.addWidget(curve_btn, i, 4)
            self.channel_tone_spins[ch] = spins
            self.channel_curves[ch] = []
            self.channel_curve_buttons[ch] = curve_btn
        tone_hint = secondary_label("Calibra cada tinta con la plantilla de ganancia estampada con esa tinta. "
                                    "Los canales en «General» usan los valores del grupo Tono.")
        tone_grid.addWidget(tone_hint, 6, 0, 1, 5)
        tone_grid.setRowStretch(7, 1)
        channels_tabs.addTab(tone_tab, "Tono por canal")
        channels_layout.addWidget(channels_tabs)
        # Densidad, ángulo y tono por canal: para calibrar, no para el día a día
        advanced_cb = QtWidgets.QCheckBox("Ajustes avanzados por canal (densidad, ángulo, calibración)")
        advanced_cb.toggled.connect(lambda on: [channels_tabs.setTabVisible(i, on) for i in (1, 2)])
        advanced_cb.setChecked(False)
        for i in (1, 2):
            channels_tabs.setTabVisible(i, False)
        channels_layout.addWidget(advanced_cb)
        channels_page.addWidget(channels_group)
        for page in step_pages:
            page.addStretch(1)      # las cajas quedan arriba, sin estirarse
        left_layout.addWidget(self.step_tabs, 1)

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
        self.view_mode_combo.addItem("Prueba de color ICC", "proof")
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
        self.show_halftones_cb = QtWidgets.QCheckBox("Ver trama")
        self.show_halftones_cb.setChecked(True)
        self.show_halftones_cb.setToolTip("Muestra los puntos de la trama; sin ella, el tono continuo")
        self.show_halftones_cb.stateChanged.connect(self.update_preview)
        view_bar.addWidget(self.show_halftones_cb)
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
        lpi_calc_action.triggered.connect(self.show_mesh_lpi_dialog)
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

        self.lpi_combo.currentTextChanged.connect(self.update_moire_analysis)
        self.mesh_spin.valueChanged.connect(self.update_moire_analysis)
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        self.refresh_icc_profiles()
        self.icc_profile_combo.currentIndexChanged.connect(self.on_icc_profile_changed)
        for control in (self.icc_intent_combo,):
            control.currentIndexChanged.connect(self.on_icc_profile_changed)
        self.icc_bpc_cb.stateChanged.connect(self.on_icc_profile_changed)
        self.icc_limit_cb.stateChanged.connect(self.schedule_reseparation)
        self.denoise_combo.currentIndexChanged.connect(self.schedule_reseparation)
        self.sharpen_spin.valueChanged.connect(self.schedule_reseparation)
        self.smooth_edges_cb.stateChanged.connect(self.schedule_reseparation)
        self.input_profile_combo.currentIndexChanged.connect(self.on_input_profile_changed)
        self.lpi_combo.currentTextChanged.connect(lambda *_: self.update_resolution_advice())
        self.guides_cb.stateChanged.connect(self.on_output_size_changed)
        self.output_dpi_combo.currentIndexChanged.connect(self.on_output_size_changed)
        self.print_format_combo.currentIndexChanged.connect(lambda *_: self.update_resolution_advice())
        self.ink_limit_spin.valueChanged.connect(self.schedule_reseparation)
        for control in (self.trap_spin, self.spot_softness_spin, self.spot_tolerance_spin, self.index_resolution_spin,
                        self.despeckle_spin):
            control.valueChanged.connect(self.schedule_reseparation)
        self.spot_angle_spin.valueChanged.connect(self.schedule_rescreen)
        for spin in self.density_spins.values():
            spin.valueChanged.connect(lambda *_: self.channel_list.viewport().update())
        for control in (self.min_dot_spin, self.max_dot_spin, self.dot_gain_spin, *self.density_spins.values()):
            control.valueChanged.connect(self.schedule_rescreen)
        for control in (self.shape_combo, self.angle_preset_combo, self.lpi_combo):
            control.currentTextChanged.connect(self.schedule_rescreen)
        for control in self.angle_spins.values():
            control.valueChanged.connect(self.schedule_rescreen)
        self.mesh_unit_combo.currentIndexChanged.connect(self.on_mesh_unit_changed)
        self.shape_combo.currentTextChanged.connect(self.update_moire_analysis)
        self.update_moire_analysis()
        self.update_guide_controls()
        self.update_garment_black_controls()
        self.update_format_info()

    # =====================================================================
    # == MÉTODOS DE LÓGICA Y EVENTOS
    # =====================================================================

    def show_troubleshooter(self):
        """Problemas de taller (malla, emulsión, tinta…): causas y soluciones."""
        import re
        path = os.path.join(os.path.dirname(__file__), '..', 'data', 'troubleshooting.json')
        with open(path, encoding='utf-8') as f:
            categories = json.load(f).get('categorías', [])

        def clean(text):
            # Quita las marcas de cita del material de origen
            return re.sub(r'\s*\[cite(?:_start)?[^\]]*\]', '', text).strip()

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Solucionador de problemas de taller")
        dialog.resize(820, 520)
        layout = QtWidgets.QHBoxLayout(dialog)
        tree = QtWidgets.QTreeWidget()
        tree.setHeaderHidden(True)
        details = QtWidgets.QTextBrowser()
        splitter = QtWidgets.QSplitter()
        splitter.addWidget(tree)
        splitter.addWidget(details)
        splitter.setSizes([300, 520])
        layout.addWidget(splitter)
        for category in categories:
            parent = QtWidgets.QTreeWidgetItem(tree, [category.get('nombre', '')])
            for problem in category.get('problemas', []):
                item = QtWidgets.QTreeWidgetItem(parent, [problem.get('titulo', '')])
                item.setData(0, QtCore.Qt.UserRole, problem)

        def show(item, _column=0):
            problem = item.data(0, QtCore.Qt.UserRole)
            if not problem:
                return
            rows = "".join(f"<li><b>{clean(c.get('descripcion', ''))}</b><br>{clean(c.get('solucion', ''))}</li>"
                           for c in problem.get('causas', []))
            details.setHtml(f"<h3>{problem.get('titulo', '')}</h3><p>Causas y soluciones:</p><ol>{rows}</ol>")

        tree.itemClicked.connect(show)
        tree.expandAll()
        dialog.exec_()

    def update_channel_list_ui(self):
        """Puebla o actualiza los items en la lista de canales."""
        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        for ch in self.list_channels():
            item = QtWidgets.QListWidgetItem(ch)
            item.setToolTip(f"{self.channel_display_name(ch)}: arrastra o usa ▲ ▼ para cambiar el orden de "
                            "impresión; doble clic para cambiar su color")
            self.channel_list.addItem(item)
            # Rehacer la lista no debe perder el canal que se estaba editando
            item.setSelected(ch == self.current_channel)
        self.channel_list.blockSignals(False)
        self.on_channel_selection_changed()

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
        # La lista muestra el orden real (la base siempre va primero)
        selected = self.current_channel
        self.update_channel_list_ui()
        if selected:
            for i in range(self.channel_list.count()):
                if self.channel_list.item(i).text() == selected:
                    self.channel_list.setCurrentRow(i)
        self.update_preview()

    def move_channel(self, step):
        """Sube (−1) o baja (+1) el canal seleccionado en el orden de impresión."""
        channel = self.current_channel
        order = [self.channel_list.item(i).text() for i in range(self.channel_list.count())]
        if channel not in order or channel == 'W':
            return
        i = order.index(channel)
        j = i + step
        if j < 0 or j >= len(order) or order[j] == 'W':
            return
        order[i], order[j] = order[j], order[i]
        self.set_channel_order(order)

    def channel_angle_for_list(self, channel):
        if channel in getattr(self, 'angle_spins', {}):
            return self.angle_spins[channel].value()
        if hasattr(self, 'spot_angle_spin'):
            return self.spot_angle_spin.value()
        return CMYK_ANGLES.get(channel)

    def list_channels(self):
        """Canales que muestra la lista: los que se imprimen (la base solo con «Imprimir base»)."""
        return self.job_settings().channels()

    def channel_display_name(self, channel):
        spot = next((sp for sp in self.spot_colors if sp['id'] == channel), None)
        if spot:
            return spot['name']
        if channel == 'W' and hasattr(self, 'base_combo'):
            return self.current_base_name()
        return CHANNEL_NAMES.get(channel, channel)

    def select_garment_color(self):
        """Permite al usuario seleccionar el color de fondo para la simulación."""
        def preview(color):
            self.garment_color = QtGui.QColor(color)
            self.update_preview()
        color = self.pick_color_live(QtGui.QColor(self.garment_color), "Color de la prenda", preview)
        if color:
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
            document = doc_input.load_document(file_path, dpi or self.job_settings().dpi, page,
                                               self.input_profile_combo.currentData())
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
        self.update_resolution_advice()
        notes = "; ".join(document.notes)
        self.status_bar.showMessage(f"Abierto: {os.path.basename(file_path)}" + (f". {notes}" if notes else ""), 10000)

    def on_output_size_changed(self, *_):
        """El tamaño del papel cambió: aviso de resolución, tamaño del diseño y vista previa."""
        self.update_guide_controls()
        self.update_format_info()
        self.update_resolution_advice()
        if self.preview_cache:
            self.schedule_reseparation()

    def on_placement_changed(self, *_):
        by_width = self.placement_combo.currentData() == 'width'
        self.design_width_spin.setVisible(by_width)
        self.design_width_label.setVisible(by_width)
        self.on_output_size_changed()

    def update_guide_controls(self):
        """Solo se puede editar lo que aplica: colocación y tira con guías; margen si van en margen."""
        guides = self.guides_cb.isChecked()
        position = self.guide_position_combo.currentData()
        in_blank = position == 'blank' or (position == 'auto' and self.distress_spin.value() > 0)
        self.guide_position_combo.setEnabled(guides)
        self.guide_margin_spin.setEnabled(guides and not in_blank)
        self.control_strip_cb.setEnabled(guides)

    def design_size_text(self, settings=None):
        if self.image is None:
            return ""
        settings = settings or self.job_settings()
        placed = layout(self.image.shape, settings)
        width, height = placed.mm(settings.dpi)
        area_w, area_h = placed.area[2] / settings.dpi * 25.4, placed.area[3] / settings.dpi * 25.4
        in_margin = settings.registration_guides and not settings.guides_in_blank
        text = f"El diseño sale de {width:.1f} × {height:.1f} mm"
        if in_margin:
            text += f", dentro del área útil de {area_w:.0f} × {area_h:.0f} mm que dejan las guías."
        elif settings.registration_guides:
            text += "; las guías van en los espacios en blanco del diseño."
        else:
            text += "."
        if placed.reduced:
            text += " No cabía al tamaño pedido: se redujo al área útil para no cortarlo."
        return text

    def update_resolution_advice(self):
        """Resolución efectiva al tamaño final frente a la lineatura elegida."""
        if self.image is None or not self.image_info:
            return
        settings = self.job_settings()
        if hasattr(self, 'design_size_label'):
            self.design_size_label.setText(self.design_size_text(settings))
        level, message = doc_input.resolution_advice(self.image.shape, self.image_info.get('dpi_x', 72), settings)
        color = {'ok': theme.ESTADO_OK, 'aviso': theme.ESTADO_ALERTA, 'riesgo': theme.ESTADO_RIESGO}[level]
        self.complexity_label.setText(message)
        self.complexity_label.setStyleSheet(f"color: {color}; font-size: 9pt;")

    def load_databases(self):
        """Carga las bases de datos de tintas y racletas (JSON en src/data)."""
        data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        try:
            with open(os.path.join(data_dir, 'inks.json'), encoding='utf-8') as f:
                self.db_inks = json.load(f)
            with open(os.path.join(data_dir, 'squeegees.json'), encoding='utf-8') as f:
                self.db_squeegees = json.load(f)
        except FileNotFoundError as e:
            QtWidgets.QMessageBox.critical(self, "Error de carga", f"No se encontró {os.path.basename(e.filename)} "
                                           "en la carpeta 'data'.")
        except json.JSONDecodeError as e:
            QtWidgets.QMessageBox.critical(self, "Error de carga", f"Error de formato en un archivo JSON: {e}")

    # ------------------------------------------------------------ base

    def current_base_name(self):
        data = self.base_combo.currentData()
        if data is None:
            return f"Base {QtGui.QColor(*self.base_rgb).name().upper()}"
        return self.base_combo.currentText()

    def set_base(self, name, rgb):
        """Selecciona la base por nombre (o la deja personalizada con su color)."""
        self.base_rgb = [int(v) for v in rgb]
        index = self.base_combo.findText(name) if name else -1
        self.base_combo.setCurrentIndex(index if index >= 0 else self.base_combo.count() - 1)
        self.channel_colors['W'] = QtGui.QColor(*self.base_rgb)
        self.channel_list.viewport().update()

    def on_base_changed(self, *_):
        rgb = self.base_combo.currentData()
        if rgb is None:
            color = self.pick_color_live(QtGui.QColor(*self.base_rgb), "Color de la base",
                                         lambda c: self.preview_channel_color('W', c))
            if not color:
                return
            rgb = list(color.getRgb()[:3])
        self.set_base(self.base_combo.currentText(), rgb)
        self.update_preview()

    # ------------------------------------------------------------ gestión de color

    def refresh_icc_profiles(self, select=None):
        """Recarga la lista de perfiles instalados en los combos."""
        current = select if select is not None else (self.icc_profile_combo.currentData() or '')
        current_input = self.input_profile_combo.currentData() or icc.SRGB
        profiles = icc.list_profiles()
        for combo in (self.icc_profile_combo, self.input_profile_combo):
            combo.blockSignals(True)
            combo.clear()
        self.icc_profile_combo.addItem("Fórmula de la app (GCR + límite de tinta)", "")
        self.input_profile_combo.addItem("sRGB (estándar)", icc.SRGB)
        for info in profiles:
            if info.usable_for_separation:
                self.icc_profile_combo.addItem(info.label(), info.name)
            elif info.usable_as_input:
                self.input_profile_combo.addItem(info.label(), info.name)
        self.icc_profile_combo.setCurrentIndex(max(self.icc_profile_combo.findData(current), 0))
        self.input_profile_combo.setCurrentIndex(max(self.input_profile_combo.findData(current_input), 0))
        for combo in (self.icc_profile_combo, self.input_profile_combo):
            combo.blockSignals(False)
        self.update_icc_info()

    def select_icc_profile(self, name):
        index = self.icc_profile_combo.findData(name or "")
        if index < 0 and name:
            QtWidgets.QMessageBox.warning(
                self, "Perfil no instalado",
                f"El trabajo usa el perfil «{name}», que no está instalado en este equipo.\n\n"
                "Impórtalo en Gestión de color → Importar perfil… para separar igual que el original.")
            index = 0
        self.icc_profile_combo.setCurrentIndex(max(index, 0))

    def update_icc_info(self):
        name = self.icc_profile_combo.currentData()
        uses_formula = not name
        self.icc_intent_combo.setEnabled(not uses_formula)
        self.icc_bpc_cb.setEnabled(not uses_formula)
        self.icc_limit_cb.setEnabled(not uses_formula)
        if uses_formula:
            self.icc_info_label.setText("Separación con fórmula: GCR 0.8 y el límite de tinta del sustrato.")
            return
        try:
            info = icc.read_profile_info(icc.find_profile(name))
            tac = icc.total_ink_limit(name, self.icc_intent_combo.currentText(), self.icc_bpc_cb.isChecked())
            self.icc_info_label.setText(f"{info.description}: tinta total máxima del perfil {tac:.0f} %. "
                                        f"MD5 {info.md5[:12]}…")
        except (ValueError, TypeError) as e:
            self.icc_info_label.setText(str(e))

    def on_icc_profile_changed(self, *_):
        self.update_icc_info()
        self.schedule_reseparation()

    def on_input_profile_changed(self, *_):
        """El perfil de entrada cambia cómo se lee la imagen: se vuelve a abrir."""
        if self.image_info and self.image_info.get('file_path') and os.path.isfile(self.image_info['file_path']):
            self.load_image_file(self.image_info['file_path'], page=self.image_info.get('page', 1) - 1)
            if self.preview_cache == {}:
                self.process_cmyk()

    def import_icc_profile(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Importar perfil ICC", "", "Perfiles ICC (*.icc *.icm)")
        if not path:
            return
        try:
            info = icc.import_profile(path)
        except ValueError as e:
            QtWidgets.QMessageBox.critical(self, "Perfil no válido", str(e))
            return
        self.refresh_icc_profiles(select=info.name if info.usable_for_separation else None)
        kind = "separación CMYK" if info.usable_for_separation else "entrada RGB"
        self.status_bar.showMessage(f"Perfil importado ({kind}): {info.description}", 10000)
        if info.usable_for_separation:
            self.on_icc_profile_changed()

    def show_icc_profiles(self):
        """Lista de perfiles instalados con su espacio, clase y MD5."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Perfiles ICC instalados")
        dialog.resize(760, 320)
        layout = QtWidgets.QVBoxLayout(dialog)
        table = QtWidgets.QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["Perfil", "Espacio", "Uso", "Versión", "MD5"])
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        for info in icc.list_profiles():
            row = table.rowCount()
            table.insertRow(row)
            use = "Separación" if info.usable_for_separation else ("Entrada" if info.usable_as_input else "—")
            for column, text in enumerate([info.label(), info.color_space, use, info.version, info.md5]):
                item = QtWidgets.QTableWidgetItem(text)
                item.setToolTip(info.path)
                table.setItem(row, column, item)
        layout.addWidget(table)
        layout.addWidget(QtWidgets.QLabel(f"Perfiles importados en: {icc.user_profiles_dir()}"))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec_()

    # ------------------------------------------------------------ color plano

    def on_mode_changed(self, *_):
        mode = SEPARATION_MODES.get(self.mode_combo.currentText(), 'cmyk')
        self.spot_group.setVisible(mode in SPOT_PALETTE_MODES)
        self.spot_group.setTitle({'index': "Color índice", 'cmyk_spot': "Tintas planas adicionales"}.get(mode, "Color plano"))
        self.index_resolution_spin.setEnabled(mode == 'index')
        self.spot_tolerance_spin.setEnabled(mode == 'cmyk_spot')
        self.simulated_btn.setVisible(mode == 'spot')
        self.set_spot_colors(self.spot_colors)
        self.update_garment_black_controls()
        self.update_channel_list_ui()
        if self.image is not None and self.preview_cache:
            self.process_cmyk()

    def update_garment_black_controls(self):
        """La opción solo aplica en cuatricromía con base."""
        mode = SEPARATION_MODES.get(self.mode_combo.currentText(), 'cmyk')
        available = mode == 'cmyk' and self.white_base_cb.isChecked()
        self.garment_black_cb.setEnabled(available)
        active = available and self.garment_black_cb.isChecked()
        for widget in (self.garment_black_spin, self.garment_black_label,
                       self.garment_boost_spin, self.garment_boost_label):
            widget.setEnabled(active)

    def on_garment_black_changed(self, *_):
        self.update_garment_black_controls()
        self.update_channel_list_ui()
        self.schedule_reseparation()

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
        level, message = mesh_rules.assess(mesh, lpi)
        low, high = mesh_rules.suggested_lpi_range(mesh)
        self.moire_warning.update_moire_status(level, message, f"Recomendado: {low:.0f}–{high:.0f} LPI")

    def show_mesh_lpi_dialog(self):
        MeshLpiDialog(self).exec_()

    def set_lpi(self, lpi):
        self.lpi_combo.setCurrentText(f"{lpi:g} LPI")

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
        """Convierte ancho y alto a la nueva unidad: la medida del lienzo no cambia."""
        symbol = self.custom_width.suffix().strip()
        old_unit = next((k for k, v in MEASUREMENT_UNITS.items() if v['symbol'] == symbol), 'mm')
        new_unit = self.get_current_unit()
        for spin in (self.custom_width, self.custom_height):
            value = convert_units(spin.value(), old_unit, new_unit)
            self.setup_dimension_spinbox(spin, new_unit)
            spin.setValue(value)
        self.update_format_info()

    def update_format_info(self):
        """Medida del lienzo elegido y DPI recomendado."""
        canvas = self.get_current_print_format()
        dpi = self.output_dpi_combo.currentData() or canvas['dpi_recommended']
        self.format_info_label.setText(f"Película de {canvas['width']:g} × {canvas['height']:g} mm a {dpi} DPI.")

    def get_current_print_format(self):
        """Medida (mm) y DPI recomendado del lienzo elegido."""
        name = self.print_format_combo.currentText()
        if name == "Personalizado":
            unit = self.get_current_unit()
            return {'width': convert_units(self.custom_width.value(), unit, 'mm'),
                    'height': convert_units(self.custom_height.value(), unit, 'mm'),
                    'dpi_recommended': self.custom_dpi.value(), 'name': name}
        return {**PRINT_FORMATS[name], 'name': name}

    def get_current_resolution_settings(self):
        return RESOLUTION_ENHANCEMENT[self.resolution_combo.currentText()]

    def on_print_format_changed(self):
        self.custom_size_widget.setVisible(self.print_format_combo.currentText() == "Personalizado")
        self.update_format_info()

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
        """Actualiza la etiqueta al instante y retrama el canal cuando el slider se detiene."""
        if not self.current_channel or self.current_channel == "COMPOSITE":
            return
        self.channel_thresholds[self.current_channel] = value
        self.threshold_value_label.setText(f"{value} / 255")
        self.threshold_timer.start(100)

    def _delayed_threshold_update(self):
        if self.current_channel in self.channel_arrays:
            self._regenerate_single_halftone(self.current_channel)
            self.update_preview()

    def pick_color_live(self, initial, title, preview):
        """
        Diálogo de color que se ve en la simulación mientras se elige, sin dar OK.
        preview(color) se llama con cada color (y con el inicial si se cancela).
        Devuelve el color aceptado o None.
        """
        dialog = QtWidgets.QColorDialog(initial, self)
        dialog.setWindowTitle(title)
        pending = [initial]
        timer = QtCore.QTimer(dialog)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: preview(pending[-1]))
        # Se agrupan los cambios seguidos (arrastrar por el cuadro) en un solo redibujo
        dialog.currentColorChanged.connect(lambda color: (pending.append(QtGui.QColor(color)), timer.start(60)))
        accepted = dialog.exec_() == QtWidgets.QDialog.Accepted
        timer.stop()
        color = dialog.selectedColor() if accepted else initial
        preview(color)
        return color if accepted and color.isValid() else None

    def preview_channel_color(self, channel, color):
        self.channel_colors[channel] = QtGui.QColor(color)
        self.channel_list.viewport().update()
        self.update_preview()

    def pick_channel_color(self, channel):
        """Abre el diálogo de color; la simulación muestra el color mientras se elige."""
        if channel not in self.channel_colors:
            return
        color = self.pick_color_live(QtGui.QColor(self.channel_colors[channel]),
                                     f"Color de {self.channel_display_name(channel).lower()}",
                                     lambda c: self.preview_channel_color(channel, c))
        if color:
            if channel == 'W':
                # La base tiene un solo color: el del combo Base (también se exporta)
                self.set_base(None, color.getRgb()[:3])
            else:
                self.channel_colors[channel] = color
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
            channel_tone=self.current_channel_tone(),
            channel_curve={ch: list(v) for ch, v in self.channel_color_curves.items() if any(v)},
            dpi=int(self.output_dpi_combo.currentData() or print_format["dpi_recommended"]),
            output_format=self.output_format_combo.currentData(),
            mirror=self.mirror_cb.isChecked(),
            negative=self.negative_cb.isChecked(),
            control_strip=self.control_strip_cb.isChecked(),
            paper_width_mm=float(print_format["width"]),
            paper_height_mm=float(print_format["height"]),
            placement=self.placement_combo.currentData(),
            design_width_mm=self.design_width_spin.value(),
            align=self.align_combo.currentData(),
            source_dpi=float((self.image_info or {}).get('dpi_x') or 0),
            registration_guides=self.guides_cb.isChecked(),
            guide_position=self.guide_position_combo.currentData(),
            guide_margin_mm=self.guide_margin_spin.value(),
            distress_mm=self.distress_spin.value(),
            denoise=self.denoise_combo.currentData(),
            sharpen=self.sharpen_spin.value(),
            smooth_edges=self.smooth_edges_cb.isChecked(),
            base_name=self.current_base_name(),
            base_rgb=list(self.base_rgb),
            resolution_factor=resolution.get("factor", 1.0),
            resolution_method=resolution.get("method"),
            white_base=self.white_base_cb.isChecked(),
            garment_as_black=self.garment_black_cb.isChecked(),
            garment_black_shadow=self.garment_black_spin.value(),
            garment_black_boost=self.garment_boost_spin.value(),
            ink_limit=self.ink_limit_spin.value(),
            icc_profile=self.icc_profile_combo.currentData() or '',
            icc_intent=self.icc_intent_combo.currentText(),
            icc_bpc=self.icc_bpc_cb.isChecked(),
            icc_ink_limit=self.icc_limit_cb.isChecked(),
            input_profile=self.input_profile_combo.currentData() or icc.SRGB,
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
            despeckle_mm=self.despeckle_spin.value(),
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
        self.channel_color_curves = {ch: list(v) for ch, v in settings.channel_curve.items()}
        for ch, spins in self.channel_tone_spins.items():
            own = settings.channel_tone.get(ch, {})
            for key, spin in spins.items():
                spin.blockSignals(True)
                spin.setValue(own.get(key, -1))
                spin.blockSignals(False)
            self.set_gain_curve(own.get('dot_gain_curve', []), ch)
        dpi_index = self.output_dpi_combo.findData(settings.dpi)
        self.output_dpi_combo.setCurrentIndex(max(dpi_index, 0))
        self.output_format_combo.setCurrentIndex(max(self.output_format_combo.findData(settings.output_format), 0))
        self.mirror_cb.setChecked(settings.mirror)
        self.negative_cb.setChecked(settings.negative)
        self.control_strip_cb.setChecked(settings.control_strip)
        self.denoise_combo.setCurrentIndex(max(self.denoise_combo.findData(settings.denoise), 0))
        self.sharpen_spin.setValue(settings.sharpen)
        self.smooth_edges_cb.setChecked(settings.smooth_edges)
        self.set_base(settings.base_name, settings.base_rgb)
        self.ink_limit_spin.setValue(settings.ink_limit)
        self.select_icc_profile(settings.icc_profile)
        self.icc_intent_combo.setCurrentText(settings.icc_intent)
        self.icc_bpc_cb.setChecked(settings.icc_bpc)
        self.icc_limit_cb.setChecked(settings.icc_ink_limit)
        index = self.input_profile_combo.findData(settings.input_profile)
        self.input_profile_combo.setCurrentIndex(max(index, 0))
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
        self.despeckle_spin.setValue(settings.despeckle_mm)
        self.index_resolution_spin.setValue(settings.index_resolution)
        self.set_spot_colors(settings.spot_colors)
        for ch, spin in self.density_spins.items():
            spin.setValue(settings.density.get(ch, 100.0))
        self.placement_combo.setCurrentIndex(max(0, self.placement_combo.findData(settings.placement)))
        if settings.design_width_mm > 0:
            self.design_width_spin.setValue(settings.design_width_mm)
        self.align_combo.setCurrentIndex(max(0, self.align_combo.findData(settings.align)))
        self.guides_cb.setChecked(settings.registration_guides)
        self.guide_position_combo.setCurrentIndex(max(0, self.guide_position_combo.findData(settings.guide_position)))
        self.guide_margin_spin.setValue(settings.guide_margin_mm)
        self.distress_spin.setValue(settings.distress_mm)
        self.white_base_cb.setChecked(settings.white_base)
        self.garment_black_cb.setChecked(settings.garment_as_black)
        self.garment_black_spin.setValue(settings.garment_black_shadow)
        self.garment_boost_spin.setValue(settings.garment_black_boost)
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

    def generate_single_channel_preview(self, channel_name):
        """Un solo canal: sus puntos con el color de la tinta sobre la prenda."""
        if channel_name not in self.preview_cache:
            return
        garment_rgb = self.garment_color.getRgb()[:3]
        halftone_mask = self.preview_cache[channel_name]
        canvas = np.empty(halftone_mask.shape + (3,), dtype=np.uint8)
        canvas[:] = garment_rgb
        canvas[halftone_mask == 0] = self.channel_colors[channel_name].getRgb()[:3]
        self.show_on_canvas(canvas, self.job_settings(), garment_rgb)

    def _regenerate_single_halftone(self, channel_name):
        """Vuelve a tramar un canal de la vista previa tras cambiar su umbral."""
        if channel_name not in self.channel_arrays:
            return
        settings = self.job_settings()
        self.preview_cache[channel_name] = screen_channel(
            self.channel_arrays[channel_name], channel_name, settings, self.preview_scale)

    def update_preview(self):
        """Vista de un canal o simulación completa, según «Ver solo el canal seleccionado»."""
        if self.is_preview_updating or not self.preview_cache:
            return
        self.is_preview_updating = True
        try:
            if self.view_individual_channel_cb.isChecked() and self.current_channel:
                self.generate_single_channel_preview(self.current_channel)
            else:
                self.generate_composite_preview()
        finally:
            self.is_preview_updating = False

    def show_on_canvas(self, rgb, settings, garment_rgb):
        """Muestra la vista previa dentro del lienzo, con las guías donde van a caer."""
        image = np.ascontiguousarray(output.canvas_preview(rgb, settings, self.preview_scale, garment_rgb,
                                                           plan=self.preview_guide_plan(settings)))
        h, w = image.shape[:2]
        qimage = QtGui.QImage(image.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
        self.preview_label.setPixmap(QtGui.QPixmap.fromImage(qimage.copy()), settings.paper_width_mm / w)

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
        icc_proof = (mode == 'proof' and settings.icc_profile
                     and all(ch in self.channel_arrays for ch in 'CMYK'))
        if icc_proof:
            # La prueba ICC no usa la simulación: se evita calcularla
            self.status_bar.showMessage("Prueba de color ICC: no muestra curvas, umbrales ni colores de tinta. "
                                        "Para verlos usa Ver → Impreso.", 8000)
            self.show_on_canvas(icc.cmyk_to_srgb(self.channel_arrays, settings.icc_profile,
                                                 settings.icc_intent, settings.icc_bpc), settings, garment)
            return
        printed = sim.simulate(
            self.channel_arrays, self.preview_cache, settings, ink_rgb, garment,
            scale=self.preview_scale, show_screen=self.show_halftones_cb.isChecked(),
            opacity=INK_TYPES.get(self.ink_type_combo.currentText(), 0.25),
            steps=None if mode != 'print' else steps,
            misregister_mm=self.misregister_spin.value() if mode == 'registration' else 0.0)
        if mode == 'proof':
            image = printed
            self.status_bar.showMessage("La prueba de color ICC necesita un perfil CMYK en Gestión de color.", 8000)
        elif mode == 'tac':
            image = sim.tac_overlay(printed, self.channel_arrays, settings)
        elif mode == 'dots':
            image = sim.dot_risk_overlay(printed, self.channel_arrays, settings, self.preview_scale)
        else:
            image = printed
        self.show_on_canvas(image, settings, garment)

    def preview_guide_plan(self, settings):
        """Dónde caen las guías en espacios en blanco (con todas las tramas de la vista previa)."""
        if not (settings.registration_guides and settings.guides_in_blank and self.preview_cache):
            return None
        # Se recalcula solo si cambian las tramas o los ajustes que mueven las guías
        key = (tuple((name, id(screen)) for name, screen in self.preview_cache.items()), self.preview_scale,
               settings.dpi, settings.paper_px, settings.guide_cross_mm, settings.control_strip,
               settings.align, tuple(output.channel_label(ch, settings) for ch in settings.channels()))
        if self._guide_plan_cache is None or self._guide_plan_cache[0] != key:
            self._guide_plan_cache = (key, output.plan_guides(self.preview_cache, settings, self.preview_scale))
        return self._guide_plan_cache[1]

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
        base = profile.get("base", "Base blanca")
        self.set_base(base, BASE_PRESETS[base])
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
        report = sim.quality_report(self.channel_arrays, settings, self.preview_scale)
        parts = [f"{settings.lpi:g} LPI a {settings.dpi} DPI"]
        off = [settings.channel_name(ch) for ch in settings.channels() if settings.density.get(ch, 100) == 0]
        if off:
            parts.insert(0, f"{' y '.join(off)} con densidad 0 %: no se imprimen (Canales → Densidad y ángulo)")
        if settings.mode == 'cmyk':
            parts.append(f"tinta total máx. {report['tac_max']:.0f} %"
                         + (f" ({report['tac_over']:.1%} del área sobre el límite)" if report['tac_over'] > 0.001 else ""))
        if report['lost'] > 0.005:
            parts.append(f"{report['lost']:.1%} con puntos < {report['hold_min']:.0f} % que la malla no sostiene")
        if report['plugged'] > 0.005:
            parts.append(f"{report['plugged']:.1%} con sombras > {report['hold_max']:.0f} % que se cerrarán")
        for channel, thin in report['thin'].items():
            if thin['lines'] > 0.002:
                parts.append(f"{thin['lines']:.1%} de {self.channel_display_name(channel)} son líneas de menos de "
                             f"{report['min_line_mm']:.2f} mm que la malla no sostiene")
            if thin.get('specks'):
                parts.append(f"{thin['specks']} motas sueltas en {self.channel_display_name(channel)} "
                             f"(sube «Limpiar motas»)")
            if thin['gaps'] > 0.002:
                parts.append(f"{self.channel_display_name(channel)} tiene huecos de menos de "
                             f"{report['min_line_mm']:.2f} mm que se taparán")
        if self.image is not None:
            width, height = design_size_mm(self.image.shape, settings)
            parts.insert(0, f"diseño {width:.1f} × {height:.1f} mm en lienzo de "
                            f"{settings.paper_width_mm:g} × {settings.paper_height_mm:g} mm")
        plan = self.preview_guide_plan(settings)
        if plan and plan.missing:
            parts.append(f"sin espacio en blanco para: {', '.join(plan.missing)} (sube el desgaste o usa guías en margen)")
        parts.append(f"vista previa al {self.preview_scale:.0%}")
        self.status_bar.showMessage("Separado: " + "; ".join(parts) + ".")

    def show_channel_curve(self, channel):
        """Pone en los deslizadores la curva de color del canal seleccionado."""
        self.curve_box.setEnabled(channel is not None)
        shifts = self.channel_color_curves.get(channel, [0, 0, 0]) if channel else [0, 0, 0]
        self.curve_title.setText(f"Curva de {self.channel_display_name(channel).lower()}" if channel
                                 else "Curva de color")
        for slider, label, value in zip(self.curve_sliders, self.curve_value_labels, shifts):
            slider.blockSignals(True)
            slider.setValue(int(value))
            slider.blockSignals(False)
            label.setText(f"{value:+d}" if value else "0")

    def on_channel_curve_changed(self, *_):
        channel = self.current_channel
        if not channel:
            return
        shifts = [slider.value() for slider in self.curve_sliders]
        self.channel_color_curves[channel] = shifts
        for label, value in zip(self.curve_value_labels, shifts):
            label.setText(f"{value:+d}" if value else "0")
        self.channel_list.viewport().update()
        self.threshold_timer.start(100)       # retrama este canal al soltar

    def reset_channel_curve(self):
        if self.current_channel:
            self.channel_color_curves.pop(self.current_channel, None)
            self.show_channel_curve(self.current_channel)
            self.channel_list.viewport().update()
            self.threshold_timer.start(0)

    def on_channel_selection_changed(self):
        """
        Maneja la selección en la lista de canales. Activa el slider si se
        selecciona un solo canal.
        """
        selected_items = self.channel_list.selectedItems()

        is_single_selection = len(selected_items) == 1

        self.threshold_slider.setEnabled(is_single_selection)
        self.view_individual_channel_cb.setEnabled(is_single_selection)
        movable = is_single_selection and selected_items[0].text() != 'W'
        self.move_up_btn.setEnabled(movable)
        self.move_down_btn.setEnabled(movable)
        self.color_btn.setEnabled(is_single_selection)

        if is_single_selection:
            self.current_channel = selected_items[0].text()
            current_threshold = self.channel_thresholds.get(self.current_channel, 128)
            self.threshold_slider.blockSignals(True)
            self.threshold_slider.setValue(current_threshold)
            self.threshold_slider.blockSignals(False)
            self.threshold_label.setText(f"Umbral de {CHANNEL_NAMES.get(self.current_channel, self.current_channel).lower()}")
            self.threshold_value_label.setText(f"{current_threshold} / 255")
            self.show_channel_curve(self.current_channel)
        else:
            self.current_channel = None
            self.threshold_label.setText("Umbral")
            self.threshold_value_label.setText("Selecciona un canal")
            self.show_channel_curve(None)
            # Si no hay un solo canal seleccionado, forzamos la vista de composición
            self.view_individual_channel_cb.setChecked(False)

        self.update_preview()

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

    def set_gain_curve(self, curve, channel=None):
        """Curva medida general (channel=None) o de una tinta."""
        curve = [list(point) for point in curve]
        if channel is None:
            self.dot_gain_curve = curve
            self.dot_gain_spin.setEnabled(not curve)
            self.gain_curve_btn.setText(f"Curva ({len(curve)} pts)" if curve else "Curva medida…")
        else:
            self.channel_curves[channel] = curve
            self.channel_tone_spins[channel]['dot_gain'].setEnabled(not curve)
            self.channel_curve_buttons[channel].setText(f"{len(curve)} pts" if curve else "Curva")
        self.schedule_rescreen()

    def current_channel_tone(self):
        """Ajustes propios de cada canal (solo los que no están en «General»)."""
        tone = {}
        for ch, spins in self.channel_tone_spins.items():
            own = {key: spin.value() for key, spin in spins.items() if spin.value() >= 0}
            if self.channel_curves.get(ch):
                own['dot_gain_curve'] = [list(point) for point in self.channel_curves[ch]]
                own.pop('dot_gain', None)
            if own:
                tone[ch] = own
        return tone

    def channel_adjust_marks(self, channel):
        """Texto para la lista de canales: qué ajustes propios lleva el positivo."""
        marks = []
        if any(self.channel_color_curves.get(channel, [])):
            marks.append("curva")
        if channel in self.current_channel_tone():
            marks.append("tono propio")
        return ", ".join(marks)

    def on_channel_tone_changed(self, *_):
        self.channel_list.viewport().update()
        self.schedule_rescreen()

    def edit_gain_curve(self, channel=None):
        """Tabla para anotar el % impreso medido de cada % de película (general o de una tinta)."""
        dialog = QtWidgets.QDialog(self)
        name = CHANNEL_NAMES.get(channel, channel) if channel else None
        dialog.setWindowTitle(f"Curva de ganancia: {name}" if name else "Curva de ganancia medida")
        layout = QtWidgets.QVBoxLayout(dialog)
        intro = QtWidgets.QLabel("Anota el porcentaje que imprimió cada parche de la plantilla, "
                                 "en la fila de tu lineatura"
                                 + (f", estampada con la tinta {name.lower()}" if name else "")
                                 + ". Deja 0 en los que no mediste.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QtWidgets.QFormLayout()
        existing = self.channel_curves.get(channel, []) if channel else self.dot_gain_curve
        current = {round(f): p for f, p in existing}
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
            self.set_gain_curve([[t, spin.value()] for t, spin in spins.items() if spin.value() > 0], channel)

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
        films, _ = output.finish_positives(screens, settings)
        return [films[ch] for ch in settings.channels()]

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

            channels_full, screens, _ = render(self.image, self.image_alpha, settings, preview=False)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_files = []

            positives, clean_pages = [], []
            films, guide_plan = output.finish_positives(screens, settings)
            for channel in settings.channels():
                screen = screens[channel]
                positive = films[channel]
                path = output.save_positive(os.path.join(folder_path, f"POSITIVO_{channel}_{timestamp}"),
                                            positive, settings)
                saved_files.append(os.path.basename(path))
                positives.append(positive)
                clean_pages.append(output.place_on_paper(screen, settings))

            pdf_name = f"cmyk_limpio_{timestamp}.pdf"
            output.save_pdf(os.path.join(folder_path, pdf_name), clean_pages, settings.dpi)
            saved_files.append(pdf_name)
            if settings.registration_guides:
                pdf_name = f"cmyk_con_guias_{timestamp}.pdf"
                output.save_pdf(os.path.join(folder_path, pdf_name), positives, settings.dpi)
                saved_files.append(pdf_name)

            if self.cmyk_composite_cb.isChecked() and settings.icc_profile and settings.mode in ('cmyk', 'cmyk_spot'):
                composite_name = f"compuesto_CMYK_{timestamp}.tif"
                icc.save_cmyk_tiff(os.path.join(folder_path, composite_name), channels_full,
                                   settings.icc_profile, settings.dpi)
                saved_files.append(composite_name)

            config_name = f"configuracion_{timestamp}.json"
            settings.save(os.path.join(folder_path, config_name))
            saved_files.append(config_name)

            spec_name = f"especificaciones_{timestamp}.txt"
            self._write_specifications(os.path.join(folder_path, spec_name), settings, saved_files, positives)
            saved_files.append(spec_name)

            height_px, width_px = positives[0].shape
            size_mm = f"{width_px / settings.dpi * 25.4:.1f}×{height_px / settings.dpi * 25.4:.1f} mm"
            design_w, design_h = design_size_mm(self.image.shape, settings)
            self.status_bar.showMessage(
                f"Exportados {len(settings.channels())} positivos de {size_mm} a {settings.dpi} DPI en {folder_path}",
                15000)
            QtWidgets.QMessageBox.information(
                self, "Positivos exportados",
                f"{len(saved_files)} archivos en:\n{folder_path}\n\n"
                f"Película de {size_mm} a {settings.dpi} DPI, {settings.lpi:g} LPI.\n"
                f"Diseño impreso: {design_w:.1f} × {design_h:.1f} mm.\n"
                + (f"Sin espacio en blanco para: {', '.join(guide_plan.missing)}. Sube el desgaste "
                   f"o usa guías en margen.\n" if guide_plan and guide_plan.missing else "")
                + "\n"
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
            f.write(f"  Lienzo: {settings.paper_width_mm:g} × {settings.paper_height_mm:g} mm\n")
            f.write(f"  Positivo: {width_px} × {height_px} px = "
                    f"{width_px / settings.dpi * 25.4:.1f} × {height_px / settings.dpi * 25.4:.1f} mm\n")
            design_w, design_h = design_size_mm(self.image.shape, settings)
            f.write(f"  Diseño impreso: {design_w:.1f} × {design_h:.1f} mm\n")
            f.write(f"  Resolución: {settings.dpi} DPI\n")
            placement = {'fit': 'ajustada al lienzo', 'real': 'tamaño real',
                         'width': f'ancho fijo {settings.design_width_mm:g} mm'}.get(settings.placement, settings.placement)
            f.write(f"  Imagen: {placement}, {'arriba al centro' if settings.align == 'top' else 'centrada'}\n")
            guides = 'no'
            if settings.registration_guides:
                guides = ('en los espacios en blanco del diseño' if settings.guides_in_blank
                          else f'en margen de {settings.guide_margin_mm:g} mm')
            f.write(f"  Guías de registro: {guides}\n")
            if settings.uses_garment_as_black:
                f.write(f"  Prenda como negro: sin película K; base por luminosidad, sin base bajo "
                        f"{settings.garment_black_shadow:g} % de luz, refuerzo de grises "
                        f"{settings.garment_black_boost:g} %\n")
            if settings.distress_mm > 0:
                f.write(f"  Efecto desgastado: {settings.distress_mm:g} mm de borde\n")
            f.write("\nTRAMA\n")
            f.write(f"  Lineatura: {settings.lpi:g} LPI (celda {settings.cell_px:.2f} px)\n")
            f.write(f"  Forma de punto: {self.shape_combo.currentText()}\n")
            f.write("\nSEPARACIÓN\n")
            if settings.icc_profile:
                info = icc.read_profile_info(icc.find_profile(settings.icc_profile))
                f.write(f"  Perfil ICC de salida: {info.description} ({info.name})\n")
                f.write(f"  MD5 del perfil: {info.md5}\n")
                f.write(f"  Intento: {settings.icc_intent}; compensación de punto negro: "
                        f"{'sí' if settings.icc_bpc else 'no'}; límite de tinta de la app: "
                        f"{f'{settings.ink_limit:g} %' if settings.icc_ink_limit else 'no (el del perfil)'}\n")
            else:
                f.write(f"  GCR: {settings.gcr:g}   Límite de tinta total: {settings.ink_limit:g} %\n")
            f.write(f"  Perfil de entrada asumido: {settings.input_profile}\n")
            if settings.white_base:
                f.write(f"  Base blanca proporcional, choke {settings.white_base_choke_px} px\n")
            f.write(f"  Color de prenda: {self.garment_color.name()}\n")
            f.write("\nORDEN DE IMPRESIÓN\n")
            for i, channel in enumerate(settings.channels(), 1):
                tone = settings.tone_for(channel)
                gain = (f"curva de {len(tone['dot_gain_curve'])} puntos" if tone['dot_gain_curve']
                        else f"ganancia {tone['dot_gain']:g} %")
                own = " (propios)" if channel in settings.channel_tone else ""
                curve = settings.channel_curve.get(channel)
                if curve and any(curve):
                    own += f", curva luces {curve[0]:+d} medios {curve[1]:+d} sombras {curve[2]:+d}"
                f.write(f"  {i}. {settings.channel_name(channel)}: ángulo "
                        f"{settings.channel_angle(channel):g}°, umbral {settings.thresholds.get(channel, 128)}, "
                        f"densidad {settings.density.get(channel, 100):g} %, punto {tone['min_dot']:g}–"
                        f"{tone['max_dot']:g} %, {gain}{own}\n")
            f.write("\nARCHIVOS\n")
            for name in saved_files:
                f.write(f"  {name}\n")

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
            self.update_resolution_advice()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "No se pudo separar", str(e))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()


# Malla y lineatura: franja de estado y diálogo (reglas de core/mesh.py)

class MoireWarningWidget(QtWidgets.QWidget):
    """Franja de estado malla/LPI: barra de color, veredicto, detalle y botón."""

    VERDICTS = {'ok': "Malla y lineatura compatibles",
                'aviso': "Revisa la lineatura",
                'riesgo': "Malla muy abierta para esta lineatura"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(52)
        self.parent_window = parent
        self.setObjectName("detectorMoire")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(8)

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
        self.analyze_btn.setToolTip("Hilos por línea, puntos que la malla sostiene y LPI alternativos")
        self.analyze_btn.clicked.connect(lambda: MeshLpiDialog(self.parent_window).exec_())
        layout.addWidget(self.analyze_btn)

    def update_moire_status(self, level='ok', message='', recommendation=''):
        """Muestra la evaluación malla/LPI del motor (mesh.assess)."""
        color = {'ok': theme.ESTADO_OK, 'aviso': theme.ESTADO_ALERTA, 'riesgo': theme.ESTADO_RIESGO}[level]
        self.status_label.setText(self.VERDICTS[level])
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


class MeshLpiDialog(QtWidgets.QDialog):
    """
    Malla y lineatura con las reglas del motor (core/mesh.py): hilos por
    línea, rango recomendado, puntos que la malla sostiene y LPI alternativos
    sin relación entera. Sirve de «Detalles» y de calculadora de LPI.
    """

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Malla y lineatura")
        self.setMinimumWidth(420)
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.mesh_spin = QtWidgets.QDoubleSpinBox()
        self.mesh_spin.setRange(20, 500)
        self.mesh_spin.setDecimals(0)
        self.mesh_spin.setSuffix(" hilos/pulg")
        self.mesh_spin.setValue(window.mesh_tpi())
        self.lpi_spin = QtWidgets.QDoubleSpinBox()
        self.lpi_spin.setRange(5, 150)
        self.lpi_spin.setDecimals(1)
        self.lpi_spin.setSuffix(" LPI")
        self.lpi_spin.setValue(window.current_lpi() or 45)
        form.addRow("Malla", self.mesh_spin)
        form.addRow("Lineatura", self.lpi_spin)
        layout.addLayout(form)

        self.verdict = QtWidgets.QLabel()
        self.verdict.setWordWrap(True)
        layout.addWidget(self.verdict)
        self.facts = QtWidgets.QLabel()
        self.facts.setWordWrap(True)
        self.facts.setProperty("rol", "secundario")
        layout.addWidget(self.facts)

        layout.addWidget(QtWidgets.QLabel("LPI alternativos (sin relación entera con la malla):"))
        self.alternatives = QtWidgets.QHBoxLayout()
        layout.addLayout(self.alternatives)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.apply_btn = buttons.addButton("Usar esta lineatura", QtWidgets.QDialogButtonBox.AcceptRole)
        self.apply_btn.clicked.connect(lambda: self.apply(self.lpi_spin.value()))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.mesh_spin.valueChanged.connect(self.refresh)
        self.lpi_spin.valueChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        mesh, lpi = self.mesh_spin.value(), self.lpi_spin.value()
        level, message = mesh_rules.assess(mesh, lpi)
        color = {'ok': theme.ESTADO_OK, 'aviso': theme.ESTADO_ALERTA, 'riesgo': theme.ESTADO_RIESGO}[level]
        self.verdict.setText(f"<b style='color:{color}'>{MoireWarningWidget.VERDICTS[level]}</b><br>{message}")
        low, high = mesh_rules.suggested_lpi_range(mesh)
        hold_min, hold_max = sim.holdable_range(mesh, lpi)
        self.facts.setText(
            f"{mesh_rules.threads_per_line(mesh, lpi):.2f} hilos por línea "
            f"(recomendado {mesh_rules.MIN_THREADS_PER_LINE}–{mesh_rules.MAX_THREADS_PER_LINE}).\n"
            f"Rango para esta malla: {low:.0f}–{high:.0f} LPI; sugerido {mesh_rules.suggested_lpi(mesh)} LPI.\n"
            f"La malla sostiene puntos de {hold_min:.0f} % a {hold_max:.0f} %; "
            f"fuera de ese rango se pierden o se tapan.")
        while self.alternatives.count():
            item = self.alternatives.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for value in mesh_rules.alternatives(mesh, lpi):
            button = QtWidgets.QPushButton(f"{value} LPI")
            button.clicked.connect(lambda _, v=value: self.apply(v))
            self.alternatives.addWidget(button)
        self.alternatives.addStretch(1)

    def apply(self, lpi):
        self.window.set_lpi(lpi)
        self.accept()
