# Origen: claude_test_fixed_lpi.py
# Sección: Clase PDFPageSelector

import sys
import os
import io
import numpy as np
from PyQt5 import QtWidgets, QtGui, QtCore
import cv2
from PIL import Image

class PDFPageSelector(QtWidgets.QDialog):
    """Diálogo para seleccionar página y configuración de PDF"""
    def __init__(self, pdf_path, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.setWindowTitle("Configuración de PDF")
        self.setModal(True)
        self.resize(650, 500)

        # Variables
        self.pdf_info = None
        self.page_previews = []

        self.init_ui()
        self.load_pdf_info()

    def init_ui(self):
        """Crear interfaz del selector de PDF"""
        layout = QtWidgets.QVBoxLayout(self)

        # Información del archivo
        info_label = QtWidgets.QLabel(f"""
        <h3>Procesamiento de PDF</h3>
        <p><b>Archivo:</b> {os.path.basename(self.pdf_path)}</p>
        <p>Selecciona la página y configuración para la separación CMYK.</p>
        """)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Área principal con pestañas
        tabs = QtWidgets.QTabWidget()

        # === PESTAÑA 1: Selección de página ===
        page_tab = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page_tab)

        # Información del PDF
        self.pdf_info_label = QtWidgets.QLabel("Cargando información del PDF...")
        page_layout.addWidget(self.pdf_info_label)

        # Selector de página
        page_selector_layout = QtWidgets.QHBoxLayout()
        page_selector_layout.addWidget(QtWidgets.QLabel("Página:"))

        self.page_spinbox = QtWidgets.QSpinBox()
        self.page_spinbox.setMinimum(1)
        self.page_spinbox.valueChanged.connect(self.on_page_changed)
        page_selector_layout.addWidget(self.page_spinbox)

        self.page_info_label = QtWidgets.QLabel()
        page_selector_layout.addWidget(self.page_info_label)
        page_selector_layout.addStretch()

        page_layout.addLayout(page_selector_layout)

        # Vista previa de la página
        preview_group = QtWidgets.QGroupBox("Vista Previa")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)

        self.page_preview_label = QtWidgets.QLabel("Selecciona una página para ver la vista previa")
        self.page_preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.page_preview_label.setMinimumSize(400, 300)
        self.page_preview_label.setStyleSheet("border: 1px solid #ccc; background: white;")
        preview_layout.addWidget(self.page_preview_label)

        page_layout.addWidget(preview_group)
        tabs.addTab(page_tab, "Selección de Página")
        # === PESTAÑA 2: Configuración ===
        config_tab = QtWidgets.QWidget()
        config_layout = QtWidgets.QFormLayout(config_tab)

        # Resolución de salida
        self.resolution_combo = QtWidgets.QComboBox()
        self.resolution_combo.addItems([
            "150 DPI (Vista previa rápida)",
            "200 DPI (Calidad estándar)",
            "300 DPI (Impresión profesional)",
            "400 DPI (Alta calidad)",
            "600 DPI (Máxima calidad)"
        ])
        self.resolution_combo.setCurrentIndex(2)  # 300 DPI por defecto
        config_layout.addRow("Resolución de conversión:", self.resolution_combo)

        # Información adicional
        info_text = QtWidgets.QLabel("""
        <h4>Consejos de configuración:</h4>
        <ul>
            <li><b>150-200 DPI:</b> Para pruebas rápidas y vistas previas</li>
            <li><b>300 DPI:</b> Estándar para impresión profesional</li>
            <li><b>400-600 DPI:</b> Para detalles extremos y ampliaciones</li>
        </ul>
        <p><b>Nota:</b> Mayor resolución = archivos más grandes y procesamiento más lento.</p>
        """)
        info_text.setWordWrap(True)
        config_layout.addRow(info_text)

        tabs.addTab(config_tab, "Configuración")

        layout.addWidget(tabs)

        # Botones
        button_layout = QtWidgets.QHBoxLayout()

        self.preview_btn = QtWidgets.QPushButton("Vista Previa")
        self.preview_btn.clicked.connect(self.generate_preview)
        button_layout.addWidget(self.preview_btn)

        button_layout.addStretch()

        process_btn = QtWidgets.QPushButton("Procesar Página")
        process_btn.clicked.connect(self.accept)
        process_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        button_layout.addWidget(process_btn)

        cancel_btn = QtWidgets.QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def load_pdf_info(self):
        """Cargar información del PDF"""
        try:
            if 'fitz' in sys.modules:
                import fitz
                pdf_doc = fitz.open(self.pdf_path)
                page_count = len(pdf_doc)

                # Información del documento
                metadata = pdf_doc.metadata
                title = metadata.get('title', 'Sin título')
                creator = metadata.get('creator', 'Desconocido')

                pdf_doc.close()
            else:
                # Fallback con pdf2image (menos información)
                from pdf2image import convert_from_path
                page_count = len(convert_from_path(self.pdf_path, dpi=72))  # Aproximado
                title = "PDF"
                creator = "Desconocido"

            # Actualizar interfaz
            info_text = f"""
            <b>Información del PDF:</b><br>
            • Páginas: {page_count}<br>
            • Título: {title}<br>
            • Creador: {creator}<br>
            • Tamaño: {os.path.getsize(self.pdf_path) / (1024*1024):.1f} MB
            """
            self.pdf_info_label.setText(info_text)

            # Configurar selector de página
            self.page_spinbox.setMaximum(page_count)
            self.page_spinbox.setValue(1)

            self.page_info_label.setText(f"de {page_count}")

            print(f"PDF analizado: {page_count} páginas")

        except Exception as e:
            print(f"Error cargando info PDF: {e}")
            self.pdf_info_label.setText(f"Error: {str(e)}")

    def on_page_changed(self):
        """Responder a cambio de página"""
        current_page = self.page_spinbox.value()
        self.page_info_label.setText(f"de {self.page_spinbox.maximum()}")

        # Limpiar vista previa anterior
        self.page_preview_label.setText(f"Página {current_page} - Haz clic en 'Vista Previa' para ver")

    def generate_preview(self):
        """Generar vista previa de la página seleccionada"""
        try:
            page_num = self.page_spinbox.value() - 1  # Base 0

            self.page_preview_label.setText("Generando vista previa...")

            # Generar preview a baja resolución (150 DPI)
            if 'fitz' in sys.modules:
                import fitz
                pdf_doc = fitz.open(self.pdf_path)
                page = pdf_doc[page_num]

                # Vista previa a 150 DPI
                zoom = 150 / 72.0
                matrix = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=matrix, alpha=False)

                img_data = pix.tobytes("ppm")
                pil_img = Image.open(io.BytesIO(img_data))

                pdf_doc.close()
            else:
                from pdf2image import convert_from_path
                pages = convert_from_path(
                    self.pdf_path,
                    dpi=150,
                    first_page=page_num + 1,
                    last_page=page_num + 1
                )
                pil_img = pages[0]

            # Convertir a QPixmap para mostrar
            img_array = np.array(pil_img)
            if len(img_array.shape) == 3:
                img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                h, w, ch = img_array.shape
                bytes_per_line = ch * w
                qt_image = QtGui.QImage(img_array.data, w, h, bytes_per_line, QtGui.QImage.Format_BGR888)
            else:
                h, w = img_array.shape
                qt_image = QtGui.QImage(img_array.data, w, h, w, QtGui.QImage.Format_Grayscale8)

            pixmap = QtGui.QPixmap.fromImage(qt_image)

            # Escalar para mostrar en el label
            scaled_pixmap = pixmap.scaled(
                self.page_preview_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            )

            self.page_preview_label.setPixmap(scaled_pixmap)
            print(f"Vista previa generada para página {page_num + 1}")

        except Exception as e:
            error_msg = f"Error generando vista previa: {str(e)}"
            print(f"{error_msg}")
            self.page_preview_label.setText(f"{error_msg}")

    def get_selected_page(self):
        """Obtener página seleccionada (base 0)"""
        return self.page_spinbox.value() - 1

    def get_resolution(self):
        """Obtener resolución seleccionada"""
        resolution_text = self.resolution_combo.currentText()
        resolution = int(resolution_text.split()[0])  # Extraer número
        return resolution
