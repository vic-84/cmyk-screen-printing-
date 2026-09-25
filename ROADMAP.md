# Hoja de ruta — Separador de color para serigrafía

Objetivo: separaciones precisas de **cuatricromía (CMYK)**, **semitono** y **color plano (spot)**, diseñadas para serigrafía y no adaptadas de offset.

Este documento cruza los requisitos del brief con lo que la app hace hoy (rama `claude/sleepy-thompson-taajrv`) y los ordena por fases. Cada fase entrega algo usable en el taller.

---

## Correcciones técnicas al brief

Antes de implementar, cuatro puntos del texto de referencia que no conviene programar tal cual:

1. **"Malla ÷ 2 = LPI" (malla 120 → 60 LPI) contradice la regla de 3.5–4.75 hilos por línea que el mismo texto cita después.** Con relación 2 el punto pequeño queda apoyado en unos 2 hilos y se cae por la abertura: en la práctica se pierden las luces. La app usará **malla ÷ 3.5 a ÷ 4.75** como rango recomendado (malla 120 → 25–34 LPI). La relación ÷ 2 solo se mostrará como "máximo absoluto, con pérdida de luces".
2. **"Evitar LPI múltiplos de la malla" (120 ÷ 4 = 30 → usar 28, 29, 31 o 32).** Es una recomendación muy difundida y se implementará como aviso. Pero el factor que más pesa en el moiré de malla es el **ángulo** entre la trama y los hilos, no solo la relación numérica: una trama a 0°/90°/45° choca con la malla aunque la relación no sea entera.
3. **Ángulos C 15°, M 75°, K 45°, Y 0°/90°.** Es el juego de offset. En serigrafía 0°, 90° y 45° coinciden con la geometría de la malla. La app ofrecerá **juegos de ángulos seleccionables**:
   - Offset (15/75/0/45)
   - Serigrafía, offset + 7.5° (22.5/52.5/7.5/82.5): valor por defecto actual
   - Monocromo: 22.5° o 25°
   - Personalizado
4. **Pantone.** Las bibliotecas Pantone tienen licencia; no se pueden incluir en la app. Se podrán **importar** las que el usuario ya tenga (ASE/ACB exportadas de su software) o definir colores propios en Lab/CMYK.

---

## Estado actual frente al brief

| Área | Ya existe | Falta |
|---|---|---|
| CMYK | GCR y límite de tinta total, base blanca proporcional con choke, trama por canal (LPI, ángulo, forma), umbral por canal, orden de impresión | Rango tonal mín/máx, compensación de ganancia de punto, densidad por canal, juegos de ángulos, perfiles ICC |
| Semitono | Trama por canal | Modo monocromo, salida 1 bit, rango tonal |
| Color plano | — | Todo |
| Archivos | PNG, JPG, BMP, TIFF, PDF | PSD, AI, EPS, entrada CMYK con perfil |
| Salida | PNG y PDF con DPI, guías de registro | TIFF 1 bit, espejo, etiqueta de canal, orden, LPI y ángulo en cada película, tira de control, impresión directa |
| Simulación | Color de prenda, colores de tinta, vista por canal | Opacidad de tinta, perfiles de sustrato, previsualización paso a paso |
| Control de calidad | Detector de moiré, compatibilidad de resolución | Mapa de tinta total, aviso de puntos que se pierden, prueba virtual de calce |
| Perfiles | `inks.json`, `squeegees.json` | Perfiles malla + tinta + sustrato con LPI, ángulos y rango |

---

## Fases

### Fase 0: Base corregida *(hecha: PR #1)*
- LPI real correcto, límite de tinta, DPI en la salida, transparencia, base blanca proporcional, interfaz nueva.
- Pruebas automáticas (11) y hook de sesión que instala las dependencias.

### Fase 1: Motor separado de la interfaz *(hecha)*
- `src/core/job.py`: `JobSettings`, la configuración completa del trabajo, que se guarda y carga en JSON (menú Archivo → Guardar/Abrir configuración). Cada exportación incluye su `configuracion_*.json`.
- `src/core/separation.py`: preparación a la resolución de salida, CMYK con GCR y límite de tinta, base blanca y vista previa reducida.
- `src/core/screening.py`: trama (celda = DPI/LPI), formas de punto y umbral por canal.
- `src/core/output.py`: colocación en el papel sin reescalar la trama, guías de registro, etiqueta por película (orden, canal, LPI, ángulo, DPI), PNG y PDF con DPI.
- Se eliminaron de `main_window.py` 21 métodos muertos o duplicados (860 líneas) y `core/halftone.py`.
- Medido con `prueba.jpg` en A3 a 30 LPI:

  | | Antes | Ahora |
  |---|---|---|
  | Separar (vista previa) | ~14 s | **1.4 s** |
  | Cambiar el umbral de un canal | ~2.5 s | **0.3 s** |
  | Exportar a resolución completa | — | 13 s |

  La exportación da 29.9–30.1 LPI en los 5 positivos.
- Corregido al mover la lógica:
  - Los umbrales por canal no se aplicaban al separar, solo al mover el slider.
  - El umbral estaba invertido (bajarlo daba *menos* tinta); ahora bajar = más tinta y 128 = sin cambio (antes K arrancaba en 64).
  - Con guías, sin "Ajustar al formato" y la imagen más grande que el papel, la trama se reescalaba con `INTER_NEAREST`.
- Pruebas: 16 (5 nuevas del motor).

### Fase 2: Control de trama profesional
- **Selector de malla independiente**, en hilos/pulgada o hilos/cm (×2.54), con el LPI sugerido (malla ÷ 3.5–4.75) y aviso de relación entera.
- **Rango tonal** por canal (por defecto 10–90 %, editable; por ejemplo 6–97 %).
- **Curva de ganancia de punto** por perfil. Se mide con la plantilla de la fase 3 y se compensa antes de tramar.
- **Densidad** por canal.
- **Juegos de ángulos** seleccionables (ver correcciones).
- Formas de punto: redonda, elíptica, cuadrada, diamante y línea.

### Fase 3: Salida de fotolitos
- **Plantilla de ganancia de punto**: rejilla de LPI (10–60) × carga (10–90 %) para imprimir, grabar, estampar y comparar con lupa. Los resultados se capturan en la app y generan la curva de la fase 2.
- **Cada película** lleva:
  - cruces de registro
  - nombre y color del canal
  - número de orden
  - LPI y ángulo
  - tira de control 5–95 %
- Opciones de salida: **TIFF 1 bit** con DPI, **espejo**, positivo/negativo y **DPI de salida** seleccionable (300/600/720/1200).
- **Impresión directa** (QPrinter), con la trama ya calculada por la app para impresoras sin PostScript.

### Fase 4: Color plano (spot)
- **Detección de colores** (agrupamiento en espacio Lab) con paleta editable: unir, quitar y asignar tinta.
- Separación **sólida** y **sólido + semitono** para sombreado dentro del mismo color.
- **Trapping** automático o manual: spread del color de encima y choke de la base, en mm.
- **Eliminación de solapamientos** (knockout) y **retiro de base** bajo colores que no la necesitan.
- Tinta **cubriente o transparente**: cambia la simulación y la necesidad de base.
- Bibliotecas de color importables (ASE/ACB/CSV).

### Fase 5: Simulación y control de calidad
- Modelo de **opacidad de tinta** (plastisol, base agua, cubriente, transparente) sobre el color del sustrato.
- **Perfiles de sustrato** (claro/oscuro, algodón, poliéster, papel) que activan base blanca, límite de tinta y rango tonal.
- **Previsualización paso a paso** en el orden de impresión.
- **Mapa de tinta total**, aviso de **puntos por debajo del mínimo** de la malla y **prueba virtual de calce** (desplazar cada canal ±0.x mm para ver dónde asoma la base).

### Fase 6: Formatos de entrada
- PSD (`psd-tools`), AI y PDF (PyMuPDF), EPS (Ghostscript).
- Imágenes **CMYK** de entrada con su perfil ICC (`PIL.ImageCms`; ya hay perfiles GRACoL y AdobeRGB en `src/ui/profiles/`).
- Vectoriales rasterizados directamente al DPI de salida.
- Verificación de resolución: DPI efectivo al tamaño final y recomendación mínima para el LPI elegido.

### Fase 7: Técnicas combinadas y automatización
- **Color índice** (punto cuadrado/difusión) y **proceso simulado** para prenda oscura.
- CMYK + colores planos en un mismo trabajo.
- **Lotes** desde la línea de comandos: `python -m src.cli trabajo.json imagenes/`.

### Fase 8: Versión web
- Reutiliza el motor de la fase 1 detrás de una API. Interfaz web aparte.

---

## Requisitos de sistema (actual)
- Python 3.10+, Windows, macOS o Linux.
- `pip install -r requirements.txt`
- Separación A3 a 300 dpi: unos 14 s y 1.8 GB de RAM. La vista previa a baja resolución (fase 1) debe bajar esto a menos de 1 s por ajuste.
