"""
Utilidades de color: conversión a Lab, diferencia de color (ΔE), detección de
colores planos y bibliotecas de color importadas (ASE / CSV).

Las bibliotecas Pantone tienen licencia y no se incluyen: se importan las que
el usuario ya tenga exportadas de su software de diseño.
"""

import csv
import struct

import cv2
import numpy as np


# ---------------------------------------------------------------- conversión

def rgb_to_lab(rgb):
    """RGB (…, 3) en 0-255 → Lab (L 0-100, a/b ≈ ±128), float32."""
    arr = np.asarray(rgb, dtype=np.float32).reshape(-1, 1, 3) / 255.0
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    return lab.reshape(np.shape(rgb))


def lab_to_rgb(lab):
    arr = np.asarray(lab, dtype=np.float32).reshape(-1, 1, 3)
    rgb = cv2.cvtColor(arr, cv2.COLOR_LAB2RGB)
    return np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8).reshape(np.shape(lab))


def cmyk_to_rgb(c, m, y, k):
    """Conversión simple (sin perfil) para muestras de bibliotecas en CMYK, valores 0-1."""
    return [round(255 * (1 - c) * (1 - k)), round(255 * (1 - m) * (1 - k)), round(255 * (1 - y) * (1 - k))]


def delta_e76(lab1, lab2):
    return np.linalg.norm(np.asarray(lab1, dtype=np.float64) - np.asarray(lab2, dtype=np.float64), axis=-1)


def delta_e2000(lab1, lab2):
    """CIEDE2000 (Sharma et al. 2005). Acepta arreglos (…, 3)."""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    c_mean = (c1 + c2) / 2
    g = 0.5 * (1 - np.sqrt(c_mean**7 / (c_mean**7 + 25.0**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360

    dL = L2 - L1
    dC = c2p - c1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, dh)
    dh = np.where(dh < -180, dh + 360, dh)
    dh = np.where(c1p * c2p == 0, 0, dh)
    dH = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh / 2))

    L_mean = (L1 + L2) / 2
    cp_mean = (c1p + c2p) / 2
    h_sum = h1p + h2p
    h_mean = np.where(np.abs(h1p - h2p) > 180, (h_sum + 360) / 2, h_sum / 2)
    h_mean = np.where(c1p * c2p == 0, h_sum, h_mean) % 360

    t = (1 - 0.17 * np.cos(np.radians(h_mean - 30)) + 0.24 * np.cos(np.radians(2 * h_mean))
         + 0.32 * np.cos(np.radians(3 * h_mean + 6)) - 0.20 * np.cos(np.radians(4 * h_mean - 63)))
    d_theta = 30 * np.exp(-(((h_mean - 275) / 25) ** 2))
    rc = 2 * np.sqrt(cp_mean**7 / (cp_mean**7 + 25.0**7))
    sl = 1 + 0.015 * (L_mean - 50) ** 2 / np.sqrt(20 + (L_mean - 50) ** 2)
    sc = 1 + 0.045 * cp_mean
    sh = 1 + 0.015 * cp_mean * t
    rt = -np.sin(np.radians(2 * d_theta)) * rc
    return np.sqrt((dL / sl) ** 2 + (dC / sc) ** 2 + (dH / sh) ** 2 + rt * (dC / sc) * (dH / sh))


# ---------------------------------------------------------------- detección

def detect_palette(bgr, count, alpha=None, garment_rgb=None, max_samples=250_000, seed=7):
    """
    Colores dominantes por k-means en Lab. Devuelve una lista de dicts
    {'rgb': [r, g, b], 'share': fracción de píxeles}, de mayor a menor
    presencia. Los grupos casi iguales al color de la prenda (ΔE < 8) se
    descartan: esa zona no se imprime.
    """
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    pixels = rgb.reshape(-1, 3)
    if alpha is not None:
        pixels = pixels[alpha.reshape(-1) >= 128]
    if len(pixels) == 0:
        return []
    rng = np.random.default_rng(seed)
    if len(pixels) > max_samples:
        pixels = pixels[rng.choice(len(pixels), max_samples, replace=False)]

    lab = rgb_to_lab(pixels).astype(np.float32)
    k = int(min(count + (1 if garment_rgb is not None else 0), len(lab)))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    cv2.setRNGSeed(seed)
    _, labels, centers = cv2.kmeans(lab, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    shares = np.bincount(labels.ravel(), minlength=k) / len(labels)

    garment_lab = rgb_to_lab([garment_rgb])[0] if garment_rgb is not None else None
    palette = []
    for index in np.argsort(-shares):
        if garment_lab is not None and delta_e76(centers[index], garment_lab) < 8:
            continue
        palette.append({'rgb': [int(v) for v in lab_to_rgb([centers[index]])[0]],
                        'share': float(shares[index])})
    return palette[:count]


# ---------------------------------------------------------------- bibliotecas

def read_ase(path):
    """
    Adobe Swatch Exchange (.ase). Devuelve [{'name', 'rgb'}]. Admite muestras
    RGB, CMYK, Lab y gris.
    """
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'ASEF':
        raise ValueError("No es un archivo ASE (falta la firma ASEF)")
    (blocks,) = struct.unpack('>I', data[8:12])
    pos, colors = 12, []
    for _ in range(blocks):
        block_type, length = struct.unpack('>HI', data[pos:pos + 6])
        body = data[pos + 6:pos + 6 + length]
        pos += 6 + length
        if block_type != 0x0001:
            continue
        (name_len,) = struct.unpack('>H', body[:2])
        name = body[2:2 + name_len * 2].decode('utf-16-be').rstrip('\x00')
        offset = 2 + name_len * 2
        model = body[offset:offset + 4].decode('ascii')
        offset += 4
        if model == 'RGB ':
            values = struct.unpack('>3f', body[offset:offset + 12])
            rgb = [round(v * 255) for v in values]
        elif model == 'CMYK':
            rgb = cmyk_to_rgb(*struct.unpack('>4f', body[offset:offset + 16]))
        elif model == 'LAB ':
            L, a, b = struct.unpack('>3f', body[offset:offset + 12])
            rgb = [int(v) for v in lab_to_rgb([[L * 100, a, b]])[0]]
        elif model == 'Gray':
            (gray,) = struct.unpack('>f', body[offset:offset + 4])
            rgb = [round(gray * 255)] * 3
        else:
            continue
        colors.append({'name': name, 'rgb': [int(np.clip(v, 0, 255)) for v in rgb]})
    return colors


def write_ase(path, colors):
    """Escribe una biblioteca ASE en RGB (útil para exportar la paleta del trabajo)."""
    blocks = b''
    for color in colors:
        name = (color['name'] + '\x00').encode('utf-16-be')
        body = struct.pack('>H', len(name) // 2) + name + b'RGB '
        body += struct.pack('>3f', *[v / 255 for v in color['rgb']]) + struct.pack('>H', 2)
        blocks += struct.pack('>HI', 0x0001, len(body)) + body
    with open(path, 'wb') as f:
        f.write(b'ASEF' + struct.pack('>HHI', 1, 0, len(colors)) + blocks)


def read_csv_library(path):
    """
    CSV con una muestra por fila: nombre,#RRGGBB  o  nombre,R,G,B.
    Se ignoran filas vacías y encabezados.
    """
    colors = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.reader(f):
            row = [cell.strip() for cell in row if cell.strip()]
            if len(row) == 2 and row[1].lstrip('#').isalnum() and len(row[1].lstrip('#')) == 6:
                hex_value = row[1].lstrip('#')
                try:
                    rgb = [int(hex_value[i:i + 2], 16) for i in (0, 2, 4)]
                except ValueError:
                    continue
            elif len(row) >= 4:
                try:
                    rgb = [int(float(v)) for v in row[1:4]]
                except ValueError:
                    continue
            else:
                continue
            colors.append({'name': row[0], 'rgb': rgb})
    return colors


def read_library(path):
    if path.lower().endswith('.ase'):
        return read_ase(path)
    return read_csv_library(path)


def match_library(rgb, library):
    """Muestra de la biblioteca más cercana por ΔE2000: (muestra, ΔE)."""
    if not library:
        return None, None
    target = rgb_to_lab([rgb])[0]
    labs = rgb_to_lab([entry['rgb'] for entry in library])
    distances = delta_e2000(labs, target)
    best = int(np.argmin(distances))
    return library[best], float(distances[best])
