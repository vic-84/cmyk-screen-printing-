# Análisis y mejoras — CMYK Separator

Revisión del 2026-09-25 sobre `main` (commit `105bbd2`). Todo lo marcado como
"verificado" se midió ejecutando la app sin interfaz (`QT_QPA_PLATFORM=offscreen`)
con `diseno-dtf39.png` en A3 a 300 dpi y 45 LPI.

---

## 1. Corregido en esta rama

| # | Problema | Efecto en el positivo | Antes → Después (verificado) |
|---|----------|----------------------|------------------------------|
| 1 | La trama se generaba a la resolución de la foto y luego se reescalaba al papel con `INTER_NEAREST` | El LPI real dependía de la foto y los puntos salían deformes | 45 LPI pedido → **17.9 LPI** reales → ahora **45.0 LPI** |
| 2 | `int(dpi / lpi)` truncaba la celda | 45 LPI salía a 50, 65 a 66.7 | La celda ahora es float (`300/45 = 6.67 px`) |
| 3 | DPI fijo en 300 en `_get_halftone_params` | La celda se calculaba con 300 aunque el formato pidiera otro DPI | Usa `dpi_recommended` del formato |
| 4 | Separación `(1-R-K)/(1-K)` con K×0.9 | En el negro puro C=M=Y=100 % + K 90 % = **390 % de tinta** | GCR por resta + límite de TAC (`TOTAL_INK_LIMIT = 260`) |
| 5 | PNG guardado con `cv2.imwrite` (sin DPI) | El RIP asumía 72/96 dpi y el positivo salía a otro tamaño | PNG con DPI incrustado (PIL) |
| 6 | PDF guardado sin `resolution` | Página de **1362×1875 mm** para un A3 | **327×450 mm** (A3 + 15 mm de margen por lado) |
| 7 | Ángulos 15/75/0/45 y blanco a 90° (o 30°, según la función) | 0°/90°/45° chocan con los hilos de la malla; con punto redondo 90° = 0°, así que el blanco chocaba con el amarillo | Una sola constante: C 22.5°, M 52.5°, Y 7.5°, K 82.5°, W 37.5° |
| 8 | "Diamante" era un diente de sierra; "Elipse" usaba media celda en Y (doble frecuencia vertical) | Formas de punto incorrectas | Diamante real (`|dx|+|dy|`); elipse en celda cuadrada |
| 9 | La base blanca se **expandía** 2 px | El blanco asoma por los bordes del color | Ahora se **contrae** (choke) 2 px |
| 10 | `.gitignore` guardado en UTF-16 (por el `echo` de PowerShell) | Git lo ignoraba y subió los `__pycache__` | `.gitignore` en UTF-8 y cachés fuera del repo |

Pruebas nuevas en `tests/test_core.py`:
- el negro puro respeta el límite de tinta
- la celda corresponde al LPI pedido
- "Ajustar formato" trama al tamaño de impresión

Correr las pruebas: `python -m unittest tests.test_core`

**Parámetros que debes validar con una impresión de prueba** (están en `src/utils/constants.py`):
- `GCR_AMOUNT = 0.8`: fracción del gris que pasa a negro. Súbelo si quieres menos tinta en las sombras; bájalo si el negro se ve "lavado".
- `TOTAL_INK_LIMIT = 260`: en textil lo habitual es 240–280 %. Plastisol sobre algodón aguanta más que base agua.
- `CMYK_ANGLES`: el ángulo del blanco (37.5°) es un punto de partida. Algunos talleres imprimen la base a otra lineatura para que no genere roseta con las tintas.

---

## 2. Pendiente: errores reales sin corregir

1. **Constantes duplicadas en `constants.py`.** `PRINT_FORMATS`, `MEASUREMENT_UNITS`, `REGISTRATION_GUIDE_SETTINGS`, `convert_units` y `format_dimension_display` se definen dos veces, y la segunda definición pisa a la primera.
   - Por eso los formatos de playera, A0–A2 y póster **nunca aparecen** en la interfaz.
   - Unificarlos requiere cambiar `get_current_print_format`: hoy busca la clave con `split(' ')[0]`, que no funciona con claves como `"Playera S (45×60cm)"`, y además tendría que convertir cm/in a mm.
2. **Relación malla/LPI demasiado baja.**
   - `calculate_optimal_lpi` usa malla ÷ 2.5 y `MESH_SPECIFICATIONS` asigna 55–65 LPI a malla 200.
   - La regla práctica es **malla ≈ 4 × LPI** (rango 3.5–5). Con menos malla, el punto pequeño cae en la abertura del hilo y se pierde.
   - Referencia: malla 110 → 25–30 LPI · 160 → 40 · 200 → 45–50 · 230 → 55 · 305 → 65–75.
   - Para CMYK en textil faltan las mallas 230, 280 y 305.
3. **Mallas en dos unidades distintas.** `inks.json` (`malla_recomendada: [45, 85]`) parece usar **hilos/cm**, mientras que `MESH_SPECIFICATIONS` usa **hilos/pulgada** (90–200). Hay que elegir una unidad (o convertir: hilos/cm × 2.54 = hilos/pulgada) y mostrarla en la interfaz.
4. **El detector de moiré da falsa seguridad.** Con 45 LPI y malla 120 (ratio 2.67) reporta riesgo "BAJO", cuando ese ratio no sostiene el punto. Debería:
   - advertir cuando malla/LPI < 3.5
   - considerar el ángulo de la trama respecto al hilo, no solo el ratio
5. **Código duplicado o muerto en `main_window.py` (4 857 líneas).**
   - Duplicados: `apply_calculated_lpi` (×2) y `get_current_resolution_settings` (×2); la segunda definición anula a la primera.
   - Tres generadores de trama sin uso: `generate_halftone_pattern`, `generate_simple_halftone_pattern` (con un tamaño de celda distinto por canal) y `generate_quick_halftone`.
   - `create_circular_dots` no tiene `self`.
   - `_resize_and_center_for_output` y las funciones de `core/halftone.py` no se usan.
   - `src/ui/main_window.txt` es una versión antigua: bórrala para no confundirte.
6. **Scripts auxiliares rotos o desactualizados.**
   - `contar_lpi.py` busca `LPI_VALUES = {` en `main_window.py`; ya no existe ahí y el script termina con "No se pudo encontrar".
   - `RESUMEN_EXPANSION_LPI.md` habla de 35 valores (lista 34), pero la app genera 21, **con LPI repetidos** (35 LPI aparece con malla 90, 110 y 120).
7. **El archivo de especificaciones afirma cosas que no hace.** Por ejemplo "Valores binarios preservados": el blanco lleva desenfoque gaussiano y deja grises en los bordes.
8. **Rendimiento.**
   - Tramar a tamaño real (A3 a 300 dpi ≈ 17 Mpx) tarda **~13 s y ~1.8 GB de RAM** (medido).
   - Cada movimiento del slider de umbral regenera el canal completo.
   - Solución: generar la **vista previa a resolución reducida** (por ejemplo 1500 px de lado) y la trama completa solo al guardar.
9. **Archivos pesados en el repo.** `diseno-dtf39.png` (24 MB), `debug_mask_*.png` y `outputs/` están versionados. Si no los necesitas en el historial: `git rm -r --cached outputs debug_mask_*.png` y agrégalos al `.gitignore`.

---

## 3. Recomendaciones de proceso (serigrafía CMYK en textil)

- **Resolución del positivo:** lo que importa es el DPI de la impresora de película, no el de la foto. Con celda = DPI/LPI, los niveles de gris por punto son (DPI/LPI)² + 1. A 300 dpi y 45 LPI salen ~45 niveles. Para degradados limpios, exporta a **600–720 dpi** (malla 200, 45 LPI → ~180–250 niveles).
- **Punto mínimo y máximo:** en textil los puntos por debajo de ~5–10 % no se sostienen en la malla y los de más de ~85–90 % se cierran por ganancia de punto. Conviene un recorte (cutback) por canal antes de tramar.
- **Ganancia de punto:** en plastisol sobre algodón es de 15–30 %. Una curva de compensación por tinta y malla es la mejora de calidad más grande que falta.
- **Orden de impresión:** la app usa W → Y → C → M → K. Es un orden habitual; en húmedo sobre húmedo, las tintas claras van primero.
- **Base blanca en prenda oscura:** además del choke, la base debería ser **proporcional a la luminosidad** (más blanco en las luces, nada en sombras profundas) en lugar de una máscara sólida por umbral. Normalmente se imprime, se flashea y luego se imprime el color.
- **Base agua frente a plastisol:** la base agua penetra más y gana menos punto, pero cubre menos sobre oscuro. Conviene guardar el límite de tinta total por tipo de tinta.

---

## 4. Funciones que la app podría agregar

| Prioridad | Función | Por qué |
|-----------|---------|---------|
| Alta | Vista previa a baja resolución + exportación completa | Respuesta inmediata del slider; hoy tarda segundos por cambio |
| Alta | Curvas de ganancia de punto y recorte de punto mín/máx por canal | La calidad real en prenda depende de esto más que del LPI |
| Alta | Selector de DPI de salida (300 / 600 / 720 / 1200) independiente del formato | Positivo acorde a la impresora de película |
| Alta | Espejo (emulsión abajo) y negativo al exportar | Requisito según cómo expongas la pantalla |
| Media | Perfil ICC (ya tienes `GRACoL2006_Coated1v2.icc` en `src/ui/profiles/`) con `PIL.ImageCms` | Separación colorimétrica en vez de la fórmula ingenua |
| Media | Base blanca proporcional + opción de "highlight white" final | Mejores luces en prenda oscura |
| Media | Tira de control dentro de cada positivo: 5/10/25/50/75/90/95 % + nombre del canal, LPI y ángulo (hoy `generate_dot_gain_template` genera la plantilla como archivo aparte) | Calibrar exposición y ganancia en la misma pantalla que se imprime |
| Media | Reporte de TAC: mapa de calor donde se supera el límite | Ver dónde habrá lodo antes de quemar pantallas |
| Media | Guardar/cargar preajustes (malla, tinta, prenda, LPI, ángulos) en JSON | Repetir trabajos |
| Baja | Separación por índices de color (simulated process) | Técnica más usada en textil oscuro que el CMYK puro |
| Baja | Trama estocástica (FM) como opción para el blanco | Elimina el moiré entre la base y las tintas |
| Baja | Exportar TIFF 1-bit con DPI | Formato estándar para RIP de película |

---

## 5. Interfaz (rediseño aplicado)

Criterio: la interfaz es gris neutro y el único color saturado en pantalla es la tinta de cada canal, para que la interfaz no altere cómo percibes el color. La simulación se ve sobre gris medio, el entorno estándar de preprensa. El tema completo está en `src/ui/theme.py`.

- **Orden del panel:** Imagen → Trama → Salida → Canales, que es el orden del trabajo.
- **Acciones fijas:** "Separar colores" (Ctrl+R) y "Exportar positivos…" (Ctrl+S) quedan fijas abajo; antes se perdían al hacer scroll.
- **Lista de canales:** cada pantalla muestra su muestra de tinta, nombre y ángulo de trama. Antes el texto blanco sobre amarillo era ilegible.
- **Colores de simulación:** aproximaciones sRGB de tintas de cuatricromía en vez de primarios RGB puros, que sobresaturaban la vista previa.
- **Detector de moiré:**
  - Usaba malla 120 fija aunque el combo dijera otra; ahora lee la malla del LPI elegido.
  - Advierte "Malla muy abierta" cuando la relación malla/LPI es menor de 3.5.
- **Textos:** sin emojis, en minúscula inicial y con verbos que dicen lo que hace cada botón.

Pendiente de interfaz:
- Los diálogos (asistente, calculadora de LPI, análisis de moiré) siguen con su estilo anterior, con estilos en línea y degradados.
- Mostrar en la barra de estado el tamaño físico y el LPI real del positivo después de separar.
