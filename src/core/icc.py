"""
Gestión de color con perfiles ICC (LittleCMS a través de Pillow).

- Biblioteca de perfiles: los incluidos con la app (src/ui/profiles) y los
  que importa el usuario (carpeta de perfiles del usuario). Cada perfil se
  valida al importarlo y se identifica por nombre de archivo y MD5, para dejar
  constancia exacta en las especificaciones del trabajo.
- Separación CMYK con el perfil de salida exigido (por ejemplo, el de una
  marca o el de la prensa del taller): sRGB → CMYK con el intento elegido y
  compensación de punto negro. El GCR y la tinta total los define el perfil.
- Prueba de color en pantalla: CMYK → sRGB con el mismo perfil.
- TIFF CMYK compuesto con el perfil incrustado.
"""

import hashlib
import io
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageCms

BUNDLED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ui', 'profiles')
SRGB = 'sRGB'  # perfil integrado, no es un archivo

INTENTS = {
    'Colorimétrico relativo': ImageCms.Intent.RELATIVE_COLORIMETRIC,
    'Perceptual': ImageCms.Intent.PERCEPTUAL,
    'Saturación': ImageCms.Intent.SATURATION,
    'Colorimétrico absoluto': ImageCms.Intent.ABSOLUTE_COLORIMETRIC,
}
DEFAULT_INTENT = 'Colorimétrico relativo'
ICC_EXTENSIONS = ('.icc', '.icm')


def user_profiles_dir():
    """Carpeta de perfiles importados (se puede cambiar con SERIGRAFIA_PROFILES_DIR)."""
    folder = os.environ.get('SERIGRAFIA_PROFILES_DIR') or os.path.join(
        os.path.expanduser('~'), '.serigrafia', 'perfiles')
    os.makedirs(folder, exist_ok=True)
    return folder


@dataclass(frozen=True)
class ProfileInfo:
    name: str            # nombre de archivo: identificador en la configuración
    path: str
    description: str
    color_space: str     # 'CMYK', 'RGB', 'GRAY', 'LAB'…
    device_class: str    # 'prtr' (salida), 'mntr' (monitor), 'scnr', 'spac'…
    version: str
    md5: str
    bundled: bool

    @property
    def usable_for_separation(self):
        return self.color_space == 'CMYK'

    @property
    def usable_as_input(self):
        return self.color_space == 'RGB'

    def label(self):
        origin = '' if self.bundled else ' (importado)'
        return f"{self.description}{origin}"


def _md5(path):
    digest = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_profile_info(path, bundled=False):
    """Lee y valida un perfil. Lanza ValueError con un mensaje claro si no sirve."""
    try:
        profile = ImageCms.getOpenProfile(path)
    except (OSError, ImageCms.PyCMSError) as e:
        raise ValueError(f"{os.path.basename(path)} no es un perfil ICC válido ({e}).") from e
    core = profile.profile
    space = core.xcolor_space.strip().upper()
    description = (ImageCms.getProfileDescription(profile) or os.path.basename(path)).strip()
    return ProfileInfo(name=os.path.basename(path), path=path, description=description,
                       color_space=space, device_class=core.device_class.strip(),
                       version=f"{core.version:.1f}", md5=_md5(path), bundled=bundled)


def list_profiles():
    """Perfiles disponibles: primero los importados por el usuario, luego los incluidos."""
    found, seen = [], set()
    for folder, bundled in ((user_profiles_dir(), False), (BUNDLED_DIR, True)):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(ICC_EXTENSIONS) or name in seen:
                continue
            try:
                found.append(read_profile_info(os.path.join(folder, name), bundled))
                seen.add(name)
            except ValueError:
                continue
    return found


def find_profile(name):
    """Ruta de un perfil por nombre de archivo (el importado tiene prioridad)."""
    if not name or name == SRGB:
        return None
    for folder in (user_profiles_dir(), BUNDLED_DIR):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def import_profile(source_path):
    """Valida y copia un perfil a la carpeta del usuario. Devuelve su ProfileInfo."""
    if not source_path.lower().endswith(ICC_EXTENSIONS):
        raise ValueError("El archivo debe tener extensión .icc o .icm.")
    info = read_profile_info(source_path)
    if info.color_space not in ('CMYK', 'RGB'):
        raise ValueError(f"El perfil es de espacio {info.color_space}; la app usa perfiles RGB (entrada) o CMYK (separación).")
    destination = os.path.join(user_profiles_dir(), os.path.basename(source_path))
    if os.path.abspath(destination) != os.path.abspath(source_path):
        shutil.copy2(source_path, destination)
    return read_profile_info(destination)


def _open(name_or_path):
    if not name_or_path or name_or_path == SRGB:
        return ImageCms.createProfile('sRGB')
    path = name_or_path if os.path.isfile(name_or_path) else find_profile(name_or_path)
    if path is None:
        raise ValueError(f"No se encontró el perfil «{name_or_path}». Impórtalo de nuevo en Gestión de color.")
    return ImageCms.getOpenProfile(path)


@lru_cache(maxsize=16)
def _transform(source, target, in_mode, out_mode, intent, bpc):
    flags = ImageCms.Flags.BLACKPOINTCOMPENSATION if bpc else ImageCms.Flags.NONE
    try:
        return ImageCms.buildTransform(_open(source), _open(target), in_mode, out_mode,
                                       INTENTS.get(intent, INTENTS[DEFAULT_INTENT]), flags)
    except ImageCms.PyCMSError as e:
        raise ValueError(f"LittleCMS no pudo crear la transformación con «{target or source}»: {e}") from e


def rgb_to_profile_cmyk(bgr, profile, intent=DEFAULT_INTENT, bpc=True, band_rows=512):
    """
    Separa una imagen sRGB (BGR uint8) con el perfil CMYK de salida.
    Devuelve {'C','M','Y','K'} uint8 (255 = 100 % de tinta).
    """
    h, w = bgr.shape[:2]
    transform = _transform(SRGB, profile, 'RGB', 'CMYK', intent, bool(bpc))
    channels = {name: np.empty((h, w), dtype=np.uint8) for name in 'CMYK'}
    for top in range(0, h, band_rows):
        bottom = min(h, top + band_rows)
        rgb = Image.fromarray(np.ascontiguousarray(bgr[top:bottom, :, ::-1]), 'RGB')
        cmyk = np.asarray(ImageCms.applyTransform(rgb, transform))
        for i, name in enumerate('CMYK'):
            channels[name][top:bottom] = cmyk[..., i]
    return channels


def cmyk_to_srgb(channels, profile, intent=DEFAULT_INTENT, bpc=True):
    """Prueba de color: canales CMYK (uint8) → RGB uint8 con el perfil de salida."""
    cmyk = np.dstack([channels[name] for name in 'CMYK'])
    transform = _transform(profile, SRGB, 'CMYK', 'RGB', intent, bool(bpc))
    return np.asarray(ImageCms.applyTransform(Image.fromarray(cmyk, 'CMYK'), transform)).copy()


def convert_to_srgb(image, source_profile, intent=DEFAULT_INTENT):
    """Convierte una imagen PIL RGB con un perfil de entrada dado (p. ej. Adobe RGB) a sRGB."""
    transform = _transform(source_profile, SRGB, 'RGB', 'RGB', intent, True)
    return ImageCms.applyTransform(image.convert('RGB'), transform)


def total_ink_limit(profile, intent=DEFAULT_INTENT, bpc=True):
    """Tinta total (%) que el perfil pone en el negro más profundo (su TAC)."""
    black = np.zeros((1, 1, 3), dtype=np.uint8)
    channels = rgb_to_profile_cmyk(black, profile, intent, bpc)
    return sum(int(channels[name][0, 0]) for name in 'CMYK') / 2.55


def save_cmyk_tiff(path, channels, profile, dpi):
    """TIFF CMYK compuesto con el perfil incrustado (para pruebas o para el cliente)."""
    cmyk = np.dstack([channels[name] for name in 'CMYK'])
    image = Image.fromarray(cmyk, 'CMYK')
    profile_path = find_profile(profile) if not os.path.isfile(profile or '') else profile
    icc_bytes = open(profile_path, 'rb').read() if profile_path else None
    image.save(path, compression='tiff_lzw', dpi=(dpi, dpi), icc_profile=icc_bytes)


def embedded_description(icc_bytes):
    try:
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc_bytes))).strip()
    except (OSError, ImageCms.PyCMSError):
        return None
