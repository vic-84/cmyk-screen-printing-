# Análisis de video: cuatricromía CMYK en textil

- **Video:** https://youtu.be/halShrPHZ2w
- **Fecha del análisis:** 2026-09-27
- **Método:** análisis automático con Gemini (gemini-3.7-flash), que vio el video completo. Las marcas de tiempo `[mm:ss]` remiten al video. No se revisaron los fotogramas manualmente: verifica cualquier dato antes de usarlo en producción.
- **Pendientes:** los videos https://youtu.be/ShIhuBYk2gg y https://youtu.be/dAhoh4a7neA no se analizaron (cuota de Gemini agotada y YouTube bloqueado en el entorno en la nube).

---

### 1. Técnica empleada
* **Cuatricromía textil (CMYK):** Técnica de reproducción de imágenes a todo color mediante la superposición de tramas de medios tonos (*halftones*) con cuatro colores de tinta transparentes/semitraslúcidas: Amarillo (*Yellow*), Cian (*Cyan*), Magenta y Negro (*Black*) [00:40 - 01:00].
* **Separación de color y tramado digital:** Realizado en Adobe Photoshop mediante separación por canales y conversión a mapa de bits [06:40 - 13:15].
* **Impresión serigráfica:** Estampado textil manual en pulpo o carrusel de serigrafía [13:38 - 20:00].

---

### 2. Materiales e insumos
* **Sustrato:** Retal/corte de tela o playera blanca de algodón fijada a la base de impresión [15:30, 20:02]. *(El expositor señala a las 16:51 que al ser tintas altamente traslúcidas, en telas oscuras requerirían una base blanca opaca).*
* **Herramientas de impresión:** 
  * Pulpo serigráfico manual de varios brazos con micrométricos/mariposas de ajuste [13:38, 14:24].
  * Rasero / racleta con mango de aluminio y tira de poliuretano [15:22, 16:14, 17:58].
* **Tintas:**
  * **Plastisol Process (demostración en vivo):** Tintas de cuatricromía directa plastisol formuladas de fábrica [14:50, 20:00].
  * **Tintas base agua pigmentadas (muestra comparativa):** Formulación de base *AQP Plus Neutral EK-Max* pigmentada con concentrados (Azul Navy, Violeta, Rojo NM, Limón) de la marca *AQP Pigmentos* [20:15 - 20:44].

---

### 3. Parámetros técnicos

#### A. Parámetros digitales en Photoshop (Audio y Visual)
* **Perfil de color:** Proceso *Wilflex* cargado en los ajustes de color [03:30].
* **Ganancia de punto (*Dot Gain*):** Ajustado en *Custom* a **30% - 35%** para compensar la dispersión y engrosamiento del punto en impresión manual [04:51].
* **Generación de negro (*Black Generation*):** Ajustada en **Medium** para aislar los bordes y sombras en el canal negro sin sobrecargar la mezcla de CMY [05:10 - 05:25].
* **Ajuste cromático previo:** Corrección selectiva de color (*Selective Color*) antes de separar canales [07:20 - 08:15].
* **Resolución de salida:** **300 DPI** [12:10].
* **Lineatura (LPI):** Se visualiza en pantalla configurado en **45 líneas por pulgada (LPI)** [13:00 - 13:10]. *(El expositor menciona que este valor depende de la malla utilizada [12:15]).*
* **Forma de punto:** Redondo (*Round*) [12:40].
* **Ángulos de trama configurados:**
  * **Amarillo (Yellow):** 90° [12:30].
  * **Cian (Cyan):** 15° [12:53].
  * **Magenta:** 75° [13:00].
  * **Negro (Black):** 45° [13:10].

#### B. Parámetros de taller / serigrafía
* **Malla (lineatura de tela / hilos):** No se especifica verbalmente ni en texto el número exacto de hilos (ej. 90, 120 hilos/cm) del bastidor en uso; el expositor solo menciona que debe ser acorde a la lineatura elegida [12:15, 19:30].
* **Emulsión y exposición:** **No se muestran ni se mencionan** en el video; los bastidores ya se presentan emulsionados y revelados montados en el pulpo [13:38].
* **Fuera de contacto (*Off-contact*):** Visible en el soporte y topes del pulpo [14:24], pero no se proporciona una medida milimétrica específica.
* **Pre-secado / Curado (*Flash cure* y termofijado):** En la estampación se observa que imprime directo en el pulpo húmedo sobre húmedo / pase a pase [15:30 - 19:56]. El video **no muestra** paso por unidad de pre-secado intermedio (*flash*) ni el curado final en horno túnel o plancha térmica.

---

### 4. Pasos cronológicos del proceso con marcas de tiempo

* **[00:00 - 00:37]** Introducción teórica a la cuatricromía textil, ventajas y limitaciones.
* **[00:38 - 03:15]** Análisis de gama en Photoshop: uso de *Gamut Warning* para identificar tonos fuera de registro CMYK (fucsias, turquesas, flúor).
* **[03:16 - 06:30]** Configuración del perfil de color, ajuste de ganancia de punto (*Dot Gain* 30-35%) y modo de generación de negro en *Medium*. Conversión a CMYK.
* **[06:31 - 10:30]** Ajustes tonales mediante *Selective Color* para evitar saturación antes de pasar a modo multicanal. Reordenamiento visual (Y-C-M-K).
* **[10:31 - 11:45]** Aplicación de efecto de borde tramado al contorno de la imagen para evitar un marco rígido/cuadrado en la tela.
* **[11:46 - 13:35]** Separación de canales (*Split Channels*) y conversión individual a mapa de bits con medios tonos (resolución 300 dpi, 45 LPI, punto redondo y ángulos: Y: 90°, C: 15°, M: 75°, K: 45°).
* **[13:36 - 15:19]** Explicación en taller de la técnica manual, control de presión, ángulo del rasero y orden de tirada.
* **[15:20 - 15:35]** **Impresión del 1.ᵉʳ color (Amarillo):** Realiza pases de carga/impresión con rasero de aluminio.
* **[15:36 - 16:47]** **Impresión del 2.º color (Cian):** Comprobación del tono azul/cian y su mezcla formando los primeros verdes.
* **[17:52 - 18:18]** **Impresión del 3.ᵉʳ color (Magenta):** Estampado del magenta generando la piel, naranjas y sombras rojizas.
* **[19:42 - 20:03]** **Impresión del 4.º color (Negro):** Pase final del negro para generar delineados, contrastes y sombras profundas.
* **[20:04 - 22:15]** Evaluación del resultado final y comparación entre la muestra impresa con plastisol directo frente a la formulada con tintas al agua.

---

### 5. Errores, malas prácticas o riesgos visibles

1. **Manipulación táctil directa sobre el estampado fresco:**
   * Entre cada pase de color y al final [16:30 - 17:44, 18:16 - 19:35, 20:20 - 20:56], el operador pasa repetidamente las manos y yemas de los dedos sobre la tinta húmeda para señalar zonas, con riesgo inminente de correr la tinta, ensuciar áreas blancas o contaminar los colores.
2. **Falta de equipo de protección (EPI):**
   * Manipulación de tintas y componentes químicos sin guantes protectores de nitrilo/látex.
3. **Múltiples pasadas / sobrecarga en el amarillo:**
   * El impresor da varias pasadas sucesivas ("tres fases" [15:20]) en el color base amarillo para aumentar opacidad. En cuatricromía, sobrecargar una tinta distorsiona el balance de grises y ensucia las mezclas posteriores (haciendo los verdes demasiado oliva o la piel excesivamente amarillenta).
4. **Variabilidad en el rasero manual:**
   * Se aprecian cambios en el ángulo de inclinación y velocidad de arrastre entre pasadas [15:24, 16:16, 18:00, 19:48], lo que incrementa notablemente la ganancia de punto no controlada.

---

### 6. Consejos y recomendaciones técnicas aplicables

* **Compensación previa en diseño (*Dot Gain*):** Si se imprime de forma manual, nunca calibrar la separación digital al 100% de saturación; dejar margen de pérdida (30-35% de ganancia) para que la presión humana no empaste los medios tonos [04:51, 08:20 - 08:40].
* **Uso del perfil del fabricante de tintas:** Utilizar siempre perfiles de color ICC específicos del fabricante de la tinta que se va a estampar, ya que los tonos de cian o magenta varían sustancialmente entre marcas y respecto a la previsualización genérica digital [02:51 - 03:30, 20:30 - 21:50].
* **Orden de impresión adecuado:** Respetar la secuencia estándar de menor a mayor opacidad/fuerza lumínica (generalmente Amarillo → Cian/Magenta → Negro) para facilitar la lectura de registro y evitar que el negro opaque las capas subyacentes [10:15 - 10:25, 13:40].
* **Aislamiento del canal negro:** Mantener la generación del negro en nivel medio/alto (*Medium*) para asegurar que las sombras y contornos queden limpios en una sola pantalla y no se formen por saturación excesiva de CMY en el textil [05:00 - 05:25, 18:50 - 19:20].
* **Consistencia del rasero:** Mantener una inclinación uniforme (alrededor de 70°–75°), presión constante y un solo pase uniforme por color para minimizar el efecto de aplastamiento del punto.

---

### 7. Comparación con esta app

| Paso del video | ¿La app lo hace? |
|---|---|
| Perfil de color del fabricante de tintas | Sí: gestión de color ICC (`src/core/icc.py`) |
| Compensación de ganancia de punto 30–35 % | Sí: curva de ganancia calibrable |
| Generación de negro (GCR) media | Sí: GCR y límite de tinta total en `separate_cmyk` |
| Ángulos por canal y punto redondo | Sí |
| Resolución 300 dpi, LPI según malla | Sí |
| Corrección selectiva de color antes de separar | No: la mejora de imagen solo reduce ruido y enfoca (`enhance.py`); no hay corrección por tono |
| Borde tramado (difuminado) para que la imagen no quede en cuadro [10:31] | **No como función.** Solo se respeta si el PNG ya trae la alfa difuminada (`separation.py`: cada canal se multiplica por la alfa). El control *Borde desgastado* es otro efecto (manchas irregulares, borde duro). |

**Mejora propuesta:** control *Borde tramado (mm)* que genere una alfa en rampa del 100 % al 0 % siguiendo la silueta del diseño (reutilizando la transformada de distancia de `enhance.distress_edges`). La multiplicación por alfa existente ya convierte esa rampa en puntos que se achican en todos los canales y en la base.

### 8. Notas de taller (criterio propio, no del video)

- Un solo pase por color en cuatricromía. Las tres pasadas del amarillo [15:20] desbalancean los grises; la opacidad se corrige con la curva de ganancia, no con más tinta.
- Amarillo a 90° es el ángulo que menos se nota si genera moiré, pero con 45 LPI sobre malla textil conviene probar la interferencia con los hilos (regla práctica: malla ≥ 4–5 × LPI).
- El video no da malla, emulsión, exposición, fuera de contacto ni curado: no sirve como referencia para esos parámetros.
