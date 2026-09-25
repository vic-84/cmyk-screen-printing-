"""
Carga de documentos de entrada.

- Mapa de bits (PNG, JPEG, TIFF, BMP, WebP): con Pillow, respetando el perfil
  ICC incrustado. Los archivos CMYK se convierten a sRGB con su perfil (o con
  GRACoL si no traen uno); OpenCV los convertía sin gestión de color.
- PSD: imagen compuesta con psd-tools.
- Vectoriales y documentos (PDF, AI compatible con PDF, SVG): con PyMuPDF,
  rasterizados al DPI pedido (normalmente el de salida).
- EPS: con Ghostscript (si está instalado).
"""

import io
import os

import numpy as np
from PIL import Image, ImageCms

from . import icc

PROFILES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ui', 'profiles')
DEFAULT_CMYK_PROFILE = os.path.join(PROFILES_DIR, 'GRACoL2006_Coated1v2.icc')

RASTER_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'}
VECTOR_EXTENSIONS = {'.pdf', '.ai', '.svg'}
SUPPORTED_EXTENSIONS = RASTER_EXTENSIONS | VECTOR_EXTENSIONS | {'.psd', '.psb', '.eps'}
OPEN_FILTER = ("Imágenes y documentos (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.psd *.psb "
               "*.pdf *.ai *.svg *.eps);;Todos los archivos (*.*)")


class LoadedDocument:
    """Imagen lista para separar: BGR uint8, alfa opcional y metadatos."""

    def __init__(self, rgba, dpi, source_type, path, notes=None, page=0, page_count=1):
        rgb = rgba[..., :3]
        self.bgr = np.ascontiguousarray(rgb[..., ::-1])
        self.alpha = np.ascontiguousarray(rgba[..., 3]) if rgba.shape[2] == 4 else None
        if self.alpha is not None and self.alpha.min() == 255:
            self.alpha = None
        self.dpi = float(dpi)
        self.source_type = source_type
        self.path = path
        self.notes = notes or []
        self.page = page
        self.page_count = page_count

    @property
    def bgra(self):
        """BGRA (si hay alfa) o BGR, como lo espera _set_loaded_image."""
        if self.alpha is None:
            return self.bgr
        return np.dstack([self.bgr, self.alpha])

    def info(self):
        h, w = self.bgr.shape[:2]
        return {
            'file_path': self.path, 'source_type': self.source_type,
            'width_px': w, 'height_px': h, 'dpi_x': self.dpi, 'dpi_y': self.dpi,
            'width_cm': w / self.dpi * 2.54, 'height_cm': h / self.dpi * 2.54,
            'page': self.page + 1, 'page_count': self.page_count, 'notes': list(self.notes),
        }


def page_count(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VECTOR_EXTENSIONS:
        import fitz
        with fitz.open(path, filetype=_fitz_type(ext)) as doc:
            return doc.page_count
    return 1


def load_document(path, dpi=300, page=0, input_profile=None):
    """
    Carga cualquier formato admitido y devuelve un LoadedDocument.
    input_profile: perfil RGB que se asume en las imágenes sin perfil
    incrustado (None o 'sRGB' = sRGB).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.psd', '.psb'):
        return _load_psd(path, input_profile)
    if ext in VECTOR_EXTENSIONS:
        return _load_vector(path, dpi, page)
    if ext == '.eps':
        return _load_eps(path, dpi, input_profile)
    return _load_raster(path, input_profile)


def _to_srgb(image, notes, input_profile=None):
    """Convierte a sRGB (RGB o RGBA) usando el perfil ICC incrustado."""
    alpha = image.getchannel('A') if image.mode in ('RGBA', 'LA', 'PA') or 'transparency' in image.info else None
    if image.mode == 'P':
        image = image.convert('RGBA')
        alpha = image.getchannel('A')
    embedded = image.info.get('icc_profile')
    srgb = ImageCms.createProfile('sRGB')
    if image.mode == 'CMYK':
        source = ImageCms.ImageCmsProfile(io.BytesIO(embedded)) if embedded else ImageCms.getOpenProfile(DEFAULT_CMYK_PROFILE)
        notes.append("CMYK convertido a RGB con " + ("su perfil ICC" if embedded else "GRACoL 2006 (sin perfil incrustado)"))
        rgb = ImageCms.profileToProfile(image, source, srgb, outputMode='RGB',
                                        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
    else:
        base = image.convert('RGB')
        if embedded:
            try:
                source = ImageCms.ImageCmsProfile(io.BytesIO(embedded))
                rgb = ImageCms.profileToProfile(base, source, srgb, outputMode='RGB',
                                                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
                description = ImageCms.getProfileDescription(source).strip()
                if 'srgb' not in description.lower():
                    notes.append(f"Convertido de {description} a sRGB")
            except (OSError, ImageCms.PyCMSError):
                rgb = base
                notes.append("Perfil ICC ilegible: se usó como sRGB")
        elif input_profile and input_profile != icc.SRGB:
            rgb = icc.convert_to_srgb(base, input_profile)
            notes.append(f"Sin perfil incrustado: se asumió {input_profile}")
        else:
            rgb = base
    array = np.asarray(rgb, dtype=np.uint8)
    if alpha is not None:
        array = np.dstack([array, np.asarray(alpha, dtype=np.uint8)])
    return array


def _dpi_of(image, default=72.0):
    dpi = image.info.get('dpi')
    try:
        value = float(dpi[0]) if dpi else default
    except (TypeError, ValueError):
        value = default
    return value if value > 1 else default


def _load_raster(path, input_profile=None):
    notes = []
    with Image.open(path) as image:
        image.load()
        if getattr(image, 'n_frames', 1) > 1:
            notes.append("Archivo con varias páginas o capas: se usó la primera")
        if image.mode in ('I;16', 'I;16B', 'I', 'F'):
            image = image.point(lambda v: v / 256).convert('L')
            notes.append("Imagen de 16 bits reducida a 8 bits")
        array = _to_srgb(image, notes, input_profile)
        return LoadedDocument(array, _dpi_of(image), 'Imagen', path, notes)


def _load_psd(path, input_profile=None):
    from psd_tools import PSDImage
    psd = PSDImage.open(path)
    composite = psd.composite()
    notes = [f"PSD {psd.width}×{psd.height} px, {len(list(psd.descendants()))} capas: se usó la imagen compuesta"]
    if psd.color_mode.name == 'CMYK':
        notes.append("PSD en CMYK: composición convertida a RGB por psd-tools")
    icc_bytes = psd.image_resources.get_data(1039) if psd.image_resources else None  # perfil ICC del PSD
    if icc_bytes:
        composite.info['icc_profile'] = icc_bytes
    array = _to_srgb(composite, notes, input_profile)
    dpi = 72.0
    try:
        resolution = psd.image_resources.get_data(1005)  # ResolutionInfo
        if resolution is not None:
            dpi = float(resolution.horizontal)
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return LoadedDocument(array, dpi, 'PSD', path, notes)


def _fitz_type(ext):
    return {'.ai': 'pdf', '.pdf': 'pdf', '.svg': 'svg'}.get(ext)


def _load_vector(path, dpi, page):
    import fitz
    ext = os.path.splitext(path)[1].lower()
    notes = [f"Vectorial rasterizado a {dpi:g} dpi"]
    try:
        doc = fitz.open(path, filetype=_fitz_type(ext))
    except (RuntimeError, ValueError) as e:
        if ext == '.ai':
            raise ValueError("El archivo AI no es compatible con PDF. En Illustrator, "
                             "guárdalo con «Crear archivo compatible con PDF» activado.") from e
        raise
    with doc:
        page = min(max(page, 0), doc.page_count - 1)
        pix = doc.load_page(page).get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=True)
        array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n).copy()
        count = doc.page_count
    if array.shape[2] == 2:
        array = np.dstack([np.repeat(array[..., :1], 3, axis=2), array[..., 1:]])
    if count > 1:
        notes.append(f"Página {page + 1} de {count}")
    return LoadedDocument(array, dpi, ext[1:].upper(), path, notes, page, count)


def _load_eps(path, dpi, input_profile=None):
    from PIL import EpsImagePlugin
    if not EpsImagePlugin.has_ghostscript():
        raise ValueError("Para abrir EPS hace falta Ghostscript instalado (https://ghostscript.com).")
    with Image.open(path) as image:
        scale = max(1, round(dpi / 72))
        image.load(scale=scale, transparency=True)
        notes = [f"EPS rasterizado a {72 * scale} dpi con Ghostscript"]
        array = _to_srgb(image, notes, input_profile)
        return LoadedDocument(array, 72.0 * scale, 'EPS', path, notes)


def effective_dpi(image_shape, image_dpi, settings):
    """
    Resolución efectiva de la imagen al tamaño final. Con «Ajustar al papel»
    la imagen se escala; sin él se imprime a su tamaño original.
    """
    h, w = image_shape[:2]
    if settings.fit_to_paper:
        return min(w / (settings.paper_width_mm / 25.4), h / (settings.paper_height_mm / 25.4))
    return image_dpi * settings.resolution_factor


def resolution_advice(image_shape, image_dpi, settings):
    """(nivel, mensaje): la imagen debería tener al menos 2 × LPI al tamaño final."""
    effective = effective_dpi(image_shape, image_dpi, settings)
    needed = 2 * settings.lpi
    if effective >= needed:
        return 'ok', f"{effective:.0f} dpi al tamaño final (mínimo recomendado {needed:.0f} para {settings.lpi:g} LPI)."
    if effective >= 1.5 * settings.lpi:
        return 'aviso', (f"{effective:.0f} dpi al tamaño final: justo para {settings.lpi:g} LPI "
                         f"(ideal {needed:.0f}). Los detalles finos se suavizarán.")
    return 'riesgo', (f"Solo {effective:.0f} dpi al tamaño final: poca resolución para {settings.lpi:g} LPI "
                      f"(mínimo {needed:.0f}). Usa una imagen más grande o un tamaño menor.")
