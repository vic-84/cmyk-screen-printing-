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

### Fase 2: Control de trama profesional *(hecha)*
- **Malla independiente** en hilos/pulgada o hilos/cm (×2.54) (`src/core/mesh.py`):
  - rango recomendado malla ÷ 4.75 a ÷ 3.5
  - botón **Sugerida**: el LPI más cercano a malla ÷ 4 sin relación entera (malla 120 → 29 LPI)
  - aviso de malla muy abierta y de relación entera
- **Lineatura libre**: acepta cualquier valor escrito, no solo la lista.
- **Técnica**: cuatricromía o **semitono de una tinta**.
- **Formas de punto**: redonda, elíptica, **cuadrada**, diamante y lineal, todas **calibradas**: la cobertura en película es igual al tono pedido (±1 %). Antes, con punto redondo, un 25 % salía al 15 % y un 75 % al 89 %.
- **Juegos de ángulos**: serigrafía (offset + 7.5°), offset (15/75/0/45), monocromo 22.5° y 25°, y personalizado (se edita por canal).
- **Tono** (`src/core/tone.py`):
  - punto mínimo y máximo
  - **compensación de ganancia de punto** según la ganancia medida al 50 %
  - **densidad** por canal
- **La simulación muestra lo impreso**: la película compensada más la ganancia de la prensa.
- **Ajustes al instante**: los cambios de tono, forma, ángulos y LPI vuelven a tramar la vista previa sin repetir la separación.
- Pruebas: 23.

### Fase 3: Salida de fotolitos *(hecha)*
- **Plantilla de ganancia de punto** (Herramientas): 9 lineaturas (20–60 LPI) × 11 porcentajes (5–95 %), con la forma y el DPI del trabajo, más un archivo de instrucciones.
- **Curva de ganancia medida** (Herramientas o botón en Tono): se anota el % impreso de cada % de película y la trama se compensa con esa curva. Reemplaza el valor único al 50 %.
- **Cada película** lleva:
  - cruces de registro y marcas de centro
  - etiqueta con orden, canal, LPI, ángulo y DPI
  - **tira de control** 5/10/25/50/75/90/95 % tramada igual que el canal
- **Resolución de salida**: según formato, 300, 600, 720 o 1200 dpi. La trama y la separación se procesan por franjas para que los tamaños grandes quepan en memoria; un canal A3 a 300 dpi pasó de 2 s a 0.7 s.
- **Formato**: PNG o **TIFF 1 bit** (CCITT G4), ambos con DPI incrustado. Opciones **espejo** y **negativo**.
- **Impresión directa** (Archivo → Imprimir, Ctrl+P): una página por película a tamaño físico real; la app hace la trama, así que funciona sin PostScript.
- Pruebas: 28.

### Fase 4: Color plano (spot) *(hecha)*
- Técnica **Color plano (spot)** con panel propio (`src/core/spot.py`, `src/core/color.py`).
- **Detección de colores**: k-means en Lab. Descarta el color de la prenda (ΔE < 8), porque esa zona no se imprime.
- **Paleta editable**: nombre, color (doble clic en la muestra), agregar, quitar, **unir** (promedio en Lab) y exportar a ASE.
- Opciones por tinta:
  - **Sólido o con semitono**: con semitono, la tinta sigue la cercanía al color y los degradados salen en puntos.
  - **Cubriente o transparente**: cambia la simulación.
  - **Base**: se marca sola en prenda oscura para colores más claros que la prenda; desmarcarla la quita (underbase removal).
- **Knockout**: con asignación exclusiva los colores sólidos no se solapan.
- **Trapping** en mm: cada color se expande solo bajo los colores más oscuros vecinos, nunca hacia la prenda.
- **Orden** automático: base primero y luego de claro a oscuro; se puede reordenar.
- **Bibliotecas de color**: importa ASE (RGB/CMYK/Lab/gris) y CSV. **Igualar** asigna a cada tinta la muestra más cercana por **ΔE2000**, verificado con los datos de referencia de Sharma 2005. Pantone no se incluye por licencia; se importa la que tengas exportada.
- Pruebas: 35.

### Fase 5: Simulación y control de calidad *(hecha)*
- Simulación en el motor (`src/core/simulate.py`), probada sin interfaz.
- **Tipo de tinta** con su opacidad: plastisol de proceso, base agua, plastisol cubriente y transparente. Una tinta transparente sobre prenda negra casi desaparece; una cubriente tapa.
- **Perfiles de sustrato**: algodón blanco, negro y de color, poliéster claro y papel. Cada uno fija color de prenda, base blanca, límite de tinta, rango tonal y ganancia.
- **Límite de tinta total** editable en la interfaz.
- **Vistas** de la simulación:
  - **Impreso**, con **control pasada por pasada** en el orden de impresión
  - **Tinta total**: ámbar cerca del límite, rojo por encima
  - **Puntos en riesgo**: naranja para luces que la malla no sostiene, azul para sombras que se cierran
  - **Prueba de calce**: corre cada tinta 0.05–2 mm en otra dirección para ver dónde asoma la base o la prenda
- **Rango sostenible por la malla**: el punto mínimo es el que mide ~1.5 pasos de hilo de diámetro (malla 120 a 29 LPI → 10–90 %). El botón **Según malla** lo aplica al punto mínimo y máximo.
- **Resumen de calidad** en la barra de estado al separar: tinta total máxima, área sobre el límite, % con puntos que se pierden o se cierran.
- Pruebas: 41.

### Fase 6: Formatos de entrada *(hecha)*
- Cargador único (`src/core/input.py`) para PNG, JPEG, TIFF, BMP, WebP, **PSD/PSB**, **PDF**, **AI** (compatible con PDF), **SVG** y **EPS** (con Ghostscript).
- **Gestión de color**: se respeta el perfil ICC incrustado. Las imágenes **CMYK** se convierten a sRGB con su perfil, o con GRACoL 2006 si no traen uno (antes OpenCV las convertía sin perfil). AdobeRGB y otros perfiles RGB pasan a sRGB.
- **Vectoriales** rasterizados directamente al DPI de salida, con fondo transparente: fuera del arte no va tinta ni base.
- PDF de varias páginas: selector de página.
- **Aviso de resolución**: resolución efectiva al tamaño final frente al mínimo de 2 × LPI (verde, ámbar o rojo en el panel Imagen). Se recalcula al cambiar LPI, formato o ajuste.
- Corregido: abrir un PDF después de un PNG con transparencia dejaba el alfa del PNG, de otro tamaño, y la separación fallaba.
- Dependencias: `psd-tools`; Ghostscript opcional para EPS.
- Pruebas: 47.

### Fase 7: Técnicas combinadas y automatización *(hecha)*
- **Color índice**: la imagen se reduce a la paleta de tintas con tramado ordenado (Bayer 8×8) en una rejilla de píxeles cuadrados (100–200 ppp configurable). Cada píxel lleva una sola tinta: películas sólidas sin solapes.
- **Proceso simulado** (botón en color plano): tintas cubrientes con semitono, base blanca primero y **blanco de luces** al final.
- **Cuatricromía + planos**: CMYK más tintas planas (por ejemplo un Pantone de logo). Cada plana cubre los píxeles cercanos a su color (alcance en ΔE) y se **quita de C, M, Y, K** en esa zona (knockout); se imprime después de la cuatricromía.
- **Lotes desde la línea de comandos**:
  ```
  python -m src.cli trabajo.json imagenes/ otra.psd -o salida/
  ```
  Usa la configuración guardada por la app. Por cada archivo genera una carpeta con positivos, PDF, configuración y `resumen.json` (tamaño, tinta total, puntos que se pierden, tiempo). Si la técnica es plano o índice sin paleta, detecta los colores de cada imagen (`--colores N`). Un archivo con error no detiene el lote.
- Pruebas: 51.

### Fase 8: Versión web
- Reutiliza el motor de la fase 1 detrás de una API. Interfaz web aparte.

---

## Requisitos de sistema (actual)
- Python 3.10+, Windows, macOS o Linux.
- `pip install -r requirements.txt`
- Separación A3 a 300 dpi: unos 14 s y 1.8 GB de RAM. La vista previa a baja resolución (fase 1) debe bajar esto a menos de 1 s por ajuste.
