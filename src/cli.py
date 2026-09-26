"""
Separación por lotes desde la línea de comandos, sin interfaz.

    python -m src.cli trabajo.json imagen_o_carpeta [más rutas] -o salida/

trabajo.json es la configuración que guarda la app (Archivo → Guardar
configuración) o la que acompaña cada exportación. Cada imagen genera su
carpeta con los positivos, el PDF, la configuración usada y un resumen.
"""

import argparse
import json
import os
import sys
import time

from .core import icc
from .core import input as doc_input
from .core import output
from .core.color import detect_palette
from .core.job import JobSettings
from .core.separation import design_size_mm, layout, render
from .core.simulate import quality_report
from .core.spot import default_needs_base


def iter_inputs(paths):
    for path in paths:
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                if os.path.isfile(full) and os.path.splitext(name)[1].lower() in doc_input.SUPPORTED_EXTENSIONS:
                    yield full
        elif os.path.isfile(path):
            yield path
        else:
            print(f"Aviso: no existe {path}", file=sys.stderr)


def auto_palette(document, settings, count):
    """Para técnicas con paleta sin colores definidos: detecta la paleta de cada imagen."""
    palette = detect_palette(document.bgr, count, document.alpha, settings.garment_rgb)
    settings.spot_colors = [
        {'id': f"S{i}", 'name': f"Tinta {i}", 'rgb': entry['rgb'], 'halftone': settings.mode != 'index',
         'opaque': True, 'base': bool(default_needs_base(entry['rgb'], settings.garment_rgb))}
        for i, entry in enumerate(palette, 1)]


def process_file(path, settings, out_dir, colors=6):
    """Separa un archivo y guarda sus positivos. Devuelve el resumen del trabajo."""
    started = time.time()
    document = doc_input.load_document(path, settings.dpi, 0, settings.input_profile)
    job = JobSettings.from_dict(settings.to_dict())
    job.source_dpi = document.dpi
    if job.mode in ('spot', 'index') and not job.spot_colors:
        auto_palette(document, job, colors)

    channels, screens, _ = render(document.bgr, document.alpha, job, preview=False)
    base_name = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.join(out_dir, base_name)
    os.makedirs(folder, exist_ok=True)

    films, files = [], []
    finished, guide_plan = output.finish_positives(screens, job)
    for channel in job.channels():
        film = finished[channel]
        saved = output.save_positive(os.path.join(folder, f"POSITIVO_{channel}"), film, job)
        films.append(film)
        files.append(os.path.basename(saved))
    output.save_pdf(os.path.join(folder, f"{base_name}_positivos.pdf"), films, job.dpi)
    job.save(os.path.join(folder, "configuracion.json"))

    report = quality_report(channels, job)
    summary = {
        'archivo': path,
        'canales': [job.channel_name(c) for c in job.channels()],
        'positivos': files,
        'tamano_mm': [round(films[0].shape[1] / job.dpi * 25.4, 1), round(films[0].shape[0] / job.dpi * 25.4, 1)],
        'lienzo_mm': [job.paper_width_mm, job.paper_height_mm],
        'diseno_mm': [round(v, 1) for v in design_size_mm(document.bgr.shape, job)],
        'diseno_reducido_para_caber': layout(document.bgr.shape, job).reduced,
        'guias_sin_lugar': guide_plan.missing if guide_plan else [],
        'lpi': job.lpi, 'dpi': job.dpi,
        'tinta_total_max': round(report['tac_max'], 1),
        'puntos_que_se_pierden': round(report['lost'], 4),
        'perfil_icc': job.icc_profile or 'fórmula GCR',
        'perfil_icc_md5': icc.read_profile_info(icc.find_profile(job.icc_profile)).md5 if job.icc_profile else None,
        'notas': document.notes,
        'segundos': round(time.time() - started, 1),
    }
    with open(os.path.join(folder, "resumen.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separación de color por lotes para serigrafía.")
    parser.add_argument("config", help="configuración del trabajo (.json guardado por la app)")
    parser.add_argument("inputs", nargs="+", help="imágenes o carpetas")
    parser.add_argument("-o", "--output", default="salida_lote", help="carpeta de salida")
    parser.add_argument("--colores", type=int, default=6,
                        help="tintas a detectar en color plano/índice si la configuración no trae paleta")
    args = parser.parse_args(argv)

    settings = JobSettings.load(args.config)
    if settings.icc_profile and not icc.find_profile(settings.icc_profile):
        print(f"ERROR  El trabajo usa el perfil ICC «{settings.icc_profile}», que no está instalado "
              f"(cópialo en {icc.user_profiles_dir()}).", file=sys.stderr)
        return 2
    os.makedirs(args.output, exist_ok=True)
    results, failures = [], 0
    for path in iter_inputs(args.inputs):
        try:
            summary = process_file(path, settings, args.output, args.colores)
            results.append(summary)
            print(f"OK  {os.path.basename(path)}: {len(summary['positivos'])} positivos "
                  f"({summary['tamano_mm'][0]}×{summary['tamano_mm'][1]} mm) en {summary['segundos']} s")
        except Exception as e:  # un archivo malo no detiene el lote
            failures += 1
            print(f"ERROR  {os.path.basename(path)}: {e}", file=sys.stderr)
    with open(os.path.join(args.output, "lote.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"{len(results)} archivos procesados, {failures} con error. Resultados en {args.output}")
    return 1 if failures and not results else 0


if __name__ == "__main__":
    sys.exit(main())
