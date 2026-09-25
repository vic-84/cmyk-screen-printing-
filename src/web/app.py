"""
Versión web: API sobre el mismo motor que la app de escritorio.

    python -m src.web            # http://127.0.0.1:8000

La imagen se sube una vez (/api/upload) y queda en memoria con un id; las
vistas previas y la exportación reutilizan ese id y reciben la configuración
del trabajo (JobSettings) en JSON.
"""

import base64
import io
import json
import os
import tempfile
import threading
import uuid
import zipfile
from collections import OrderedDict

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..core import input as doc_input
from ..core import mesh as mesh_rules
from ..core import icc
from ..core import output
from ..core import simulate as sim
from ..core.color import detect_palette, match_library, read_library
from ..core.job import JobSettings
from ..core.separation import layout, render
from ..core.spot import default_needs_base, order_light_to_dark
from ..utils.constants import (
    ANGLE_PRESETS, BASE_PRESETS, CMYK_ANGLES, LPI_OPTIONS, POINT_SHAPES, SEPARATION_MODES,
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_DOCUMENTS = 8
MAX_UPLOAD_MB = 200

PAPER_FORMATS = {
    "Personalizado": (300, 400),
    "A4": (210, 297), "A3": (297, 420), "A3+": (329, 483), "Tabloide": (279.4, 431.8),
    "Carta": (215.9, 279.4), "Pecho 30×40 cm": (300, 400), "Espalda 35×45 cm": (350, 450),
}
INK_COLORS = {'C': (0, 174, 239), 'M': (236, 0, 140), 'Y': (255, 242, 0), 'K': (35, 31, 32), 'W': (255, 255, 255)}

app = FastAPI(title="Separador de color para serigrafía")
_documents = OrderedDict()
_lock = threading.Lock()


def _store(document):
    doc_id = uuid.uuid4().hex
    with _lock:
        _documents[doc_id] = document
        while len(_documents) > MAX_DOCUMENTS:
            _documents.popitem(last=False)
    return doc_id


def _document(doc_id):
    with _lock:
        document = _documents.get(doc_id)
    if document is None:
        raise HTTPException(404, "La imagen ya no está en el servidor. Vuelve a subirla.")
    return document


def _settings(raw, document=None):
    try:
        data = json.loads(raw) if raw else {}
        job = JobSettings.from_dict(data)
        if document is not None:
            job.source_dpi = document.dpi   # para colocar la imagen a su tamaño real
        return job
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"Configuración inválida: {e}")


def _png_base64(rgb):
    ok, buffer = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _ink_colors(settings):
    colors = dict(INK_COLORS)
    colors['W'] = tuple(settings.base_rgb)
    for spot in settings.spot_colors:
        colors[spot["id"]] = tuple(spot["rgb"])
    return colors


def _profiles():
    return [{"name": p.name, "label": p.label(), "space": p.color_space, "md5": p.md5,
             "separation": p.usable_for_separation, "input": p.usable_as_input}
            for p in icc.list_profiles()]


@app.post("/api/profile")
async def upload_profile(file: UploadFile = File(...)):
    """Instala un perfil ICC (por ejemplo, el que exige una marca) en la carpeta de perfiles."""
    name = os.path.basename(file.filename or "")
    if not name.lower().endswith(icc.ICC_EXTENSIONS):
        raise HTTPException(415, "El perfil debe tener extensión .icc o .icm.")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(await file.read())
        try:
            info = icc.import_profile(path)
        except ValueError as e:
            raise HTTPException(422, str(e))
    tac = icc.total_ink_limit(info.name) if info.usable_for_separation else None
    return {"profile": {"name": info.name, "label": info.label(), "space": info.color_space, "md5": info.md5,
                        "separation": info.usable_for_separation, "input": info.usable_as_input,
                        "tac": tac}, "profiles": _profiles()}


@app.get("/api/profile-info")
def profile_info(name: str, intent: str = icc.DEFAULT_INTENT, bpc: bool = True):
    path = icc.find_profile(name)
    if not path:
        raise HTTPException(404, f"El perfil «{name}» no está instalado.")
    info = icc.read_profile_info(path)
    return {"description": info.description, "md5": info.md5, "space": info.color_space,
            "tac": icc.total_ink_limit(name, intent, bpc) if info.usable_for_separation else None}


@app.get("/api/options")
def options():
    return {
        "modes": SEPARATION_MODES, "shapes": POINT_SHAPES, "angle_presets": ANGLE_PRESETS,
        "default_angles": CMYK_ANGLES, "lpi_options": LPI_OPTIONS, "papers": PAPER_FORMATS,
        "substrates": sim.SUBSTRATE_PROFILES, "ink_types": sim.INK_TYPES,
        "profiles": _profiles(), "intents": list(icc.INTENTS.keys()), "bases": BASE_PRESETS,
        "defaults": JobSettings().to_dict(),
    }


@app.get("/api/mesh")
def mesh(mesh_tpi: float, lpi: float):
    level, message = mesh_rules.assess(mesh_tpi, lpi)
    low, high = mesh_rules.suggested_lpi_range(mesh_tpi)
    hold_min, hold_max = sim.holdable_range(mesh_tpi, lpi)
    return {"level": level, "message": message, "suggested": mesh_rules.suggested_lpi(mesh_tpi),
            "range": [round(low, 1), round(high, 1)], "hold": [round(hold_min, 1), round(hold_max, 1)]}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), dpi: float = Form(300), input_profile: str = Form("sRGB")):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"El archivo supera {MAX_UPLOAD_MB} MB.")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in doc_input.SUPPORTED_EXTENSIONS:
        raise HTTPException(415, f"Formato no admitido ({ext or 'sin extensión'}). "
                                 "Usa PNG, JPG, TIFF, PSD, PDF, AI, SVG o EPS.")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "entrada" + ext)
        with open(path, "wb") as f:
            f.write(data)
        try:
            document = doc_input.load_document(path, dpi, 0, input_profile)
        except Exception as e:
            raise HTTPException(422, f"No se pudo abrir el archivo: {e}")
    document.path = file.filename
    info = document.info()
    thumb = cv2.cvtColor(document.bgr, cv2.COLOR_BGR2RGB)
    scale = min(1.0, 900 / max(thumb.shape[:2]))
    if scale < 1:
        thumb = cv2.resize(thumb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return {"id": _store(document), "info": info, "thumbnail": _png_base64(thumb)}


@app.post("/api/palette")
def palette(doc_id: str = Form(...), count: int = Form(6), settings: str = Form("{}")):
    document = _document(doc_id)
    job = _settings(settings)
    found = detect_palette(document.bgr, max(1, min(count, 16)), document.alpha, job.garment_rgb)
    spots = [{"id": f"S{i}", "name": f"Tinta {i} ({entry['share']:.0%})", "rgb": entry["rgb"],
              "halftone": job.mode != "index", "opaque": True,
              "base": bool(default_needs_base(entry["rgb"], job.garment_rgb))}
             for i, entry in enumerate(found, 1)]
    return {"spot_colors": order_light_to_dark(spots)}


@app.post("/api/match")
async def match(library: UploadFile = File(...), spot_colors: str = Form("[]")):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, library.filename or "biblioteca.csv")
        with open(path, "wb") as f:
            f.write(await library.read())
        try:
            entries = read_library(path)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            raise HTTPException(422, f"No se pudo leer la biblioteca: {e}")
    spots = json.loads(spot_colors)
    for spot in spots:
        entry, distance = match_library(spot["rgb"], entries)
        if entry:
            spot.update(name=entry["name"], rgb=entry["rgb"], library=f"ΔE2000 {distance:.1f}")
    return {"spot_colors": spots, "library_size": len(entries)}


@app.post("/api/preview")
def preview(doc_id: str = Form(...), settings: str = Form("{}"), view: str = Form("print"),
            steps: int = Form(-1), misregister_mm: float = Form(0.3)):
    document = _document(doc_id)
    job = _settings(settings, document)
    channels, screens, scale = render(document.bgr, document.alpha, job, preview=True)
    printed = sim.simulate(channels, screens, job, _ink_colors(job), job.garment_rgb, scale=scale,
                           opacity=sim.INK_TYPES.get(job.ink_type, 0.25),
                           steps=None if steps < 0 or view != "print" else steps,
                           misregister_mm=misregister_mm if view == "registration" else 0.0)
    if view == "proof":
        if not job.icc_profile or not all(c in channels for c in "CMYK"):
            raise HTTPException(400, "La prueba de color ICC necesita un perfil CMYK en Gestión de color.")
        printed = icc.cmyk_to_srgb(channels, job.icc_profile, job.icc_intent, job.icc_bpc)
    elif view == "tac":
        printed = sim.tac_overlay(printed, channels, job)
    elif view == "dots":
        printed = sim.dot_risk_overlay(printed, channels, job, scale)
    report = sim.quality_report(channels, job, scale)
    advice = doc_input.resolution_advice(document.bgr.shape, document.dpi, job)
    placed = layout(document.bgr.shape, job)
    design = placed.mm(job.dpi)
    printed = output.canvas_preview(printed, job, scale, job.garment_rgb)
    return {"image": _png_base64(printed), "scale": scale, "design_mm": [round(design[0], 1), round(design[1], 1)],
            "canvas_mm": [job.paper_width_mm, job.paper_height_mm], "reduced": placed.reduced,
            "channels": [{"id": c, "name": job.channel_name(c), "angle": job.channel_angle(c),
                          "rgb": list(_ink_colors(job).get(c, (128, 128, 128)))} for c in job.channels()],
            "report": report, "resolution": {"level": advice[0], "message": advice[1]}}


@app.post("/api/export")
def export(doc_id: str = Form(...), settings: str = Form("{}")):
    document = _document(doc_id)
    job = _settings(settings, document)
    _, screens, _ = render(document.bgr, document.alpha, job, preview=False)
    buffer = io.BytesIO()
    base_name = os.path.splitext(os.path.basename(document.path or "trabajo"))[0] or "trabajo"
    with tempfile.TemporaryDirectory() as folder, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        films = []
        for channel in job.channels():
            film = output.finish_positive(screens[channel], channel, job)
            path = output.save_positive(os.path.join(folder, f"POSITIVO_{channel}"), film, job)
            bundle.write(path, os.path.basename(path))
            films.append(film)
        pdf_path = os.path.join(folder, f"{base_name}_positivos.pdf")
        output.save_pdf(pdf_path, films, job.dpi)
        bundle.write(pdf_path, os.path.basename(pdf_path))
        config = job.to_dict()
        if job.icc_profile and job.mode in ("cmyk", "cmyk_spot"):
            info = icc.read_profile_info(icc.find_profile(job.icc_profile))
            config["icc_profile_md5"] = info.md5
            config["icc_profile_description"] = info.description
            composite = os.path.join(folder, "compuesto_CMYK.tif")
            channels_full, _, _ = render(document.bgr, document.alpha, job, preview=False)
            icc.save_cmyk_tiff(composite, channels_full, job.icc_profile, job.dpi)
            bundle.write(composite, "compuesto_CMYK.tif")
        bundle.writestr("configuracion.json", json.dumps(config, indent=2, ensure_ascii=False))
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{base_name}_positivos.zip"'})


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
