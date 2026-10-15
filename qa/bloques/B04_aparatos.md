# B04 — Diseñador de aparatos (Protocol ▸ Apparatus)

**Inventario:** «Protocol ▸ Apparatus — CINTA», sus pestañas *Zones*, *Points*, *Lines*, *Groups*, *Sequences*,
«Protocol ▸ Apparatus», diálogos «Create apparatus from template», «Add grid», «Calibrate».
**Precondiciones:** experimento nuevo `$QA/experimentos/aparatos.mmaze` (protocolo *Custom*) y una copia del demo.
Fondo: los recortes de `$QA/entradas/videos_reales/`.

## Plantillas y fondo

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B04-01 | *Background ▸ Video file* → `OFT_macho_1.mp4`. Cambia el tiempo del cuadro de fondo. | Se muestra el cuadro; el control de tiempo lo cambia. | `vacio_0_bytes.mp4`, `corrupto_bytes_aleatorios.mp4`, `texto_disfrazado.mp4`, `un_solo_cuadro.mp4`, `vertical_240x320.mp4`, el de nombre con acentos: mensaje claro o funciona. Tiempo > duración del video. |
| B04-02 | *From template* ▸ *Open field*: arrastra un rectángulo sobre la arena, tamaño real 100 cm. | Zonas centro/periferia/esquinas colocadas y calibradas (píxeles por cm ≈ ancho del rectángulo/100). | Rectángulo de 1 px; fuera de la imagen; tamaño 0, negativo, 1e6. |
| B04-03 | Repite *From template* con **cada** plantilla (23) en un aparato nuevo cada vez (usa EPM sobre `EPM_macho_1`, water maze sobre `MWM_hembra_1`, forced swim sobre `FST_hembra_1`; el resto sobre cualquier fondo). Opciones de cada plantilla (nº de brazos, pocillos, agujeros…). | Cada una crea zonas/puntos/líneas con nombres sensatos. Captura de cada mapa. | Radial con 2 y con 16 brazos; multi-well con 6 y 96. |
| B04-04 | *Background ▸ Test video* (en el demo) y *Use a test's video…*. | Muestra el cuadro de la prueba elegida. | |
| B04-05 | *Lens correction…*: deslizador de barril, calibración con tablero de ajedrez (sin imagen de tablero: mensaje claro), ámbito (fondo / pruebas de este aparato / todas). | El fondo se muestra corregido («lens corrected»). Deshacer volviendo a 0. | |

## Herramientas de dibujo

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B04-10 | *Rectangle tool*, *Ellipse tool*, *Multiline tool* (polígono), *Line tool*: dibuja uno de cada. | Aparecen en las pestañas Zones / Lines con nombre por defecto. | Polígono de 2 puntos; que se cruza consigo mismo; doble clic para cerrar; Esc a mitad. Forma de tamaño 0. Forma fuera del arena / fuera de la imagen. |
| B04-11 | *Select objects*: mover, redimensionar, mover vértices; *Select all*; *Delete selection*; Supr. | Funciona; el mapa se redibuja. | Mover con flechas del teclado (si existe). |
| B04-12 | *Points attract* activado/desactivado al dibujar cerca de otra forma. | Ajusta a vértices cercanos solo si está activo. | |
| B04-13 | Copiar/pegar (`Cmd+C`/`Cmd+V`) una zona en el mismo aparato y en otro. | En el mismo: desplazada; en otro: misma posición. | Pegar sin haber copiado; pegar texto del portapapeles. |
| B04-14 | *Define ▸ Arena*, *Point*, *Zone grid*, *Zone group*, *Sequence*. | Cada uno crea su objeto. | |
| B04-15 | Deshacer (`Cmd+Z`) tras cada tipo de cambio. | Si no hay deshacer en el mapa, Observación (anota qué esperaría un usuario). | |

## Propiedades

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B04-20 **[P]** | Pestaña *Zones*: para una zona cambia nombre, color, *Moveable*, regla de entrada (Default, Centre, Head, Tail base, Proportion of the body con 0/50/100 %, Not in any other zone), *Investigate* distancia, *Entry only when facing* ángulo, *Whishaw's corridor*, *Hidden zone*. Botones *Duplicate*, *Use as arena*, *Delete*. | Cada propiedad persiste; en el demo, cambiar la regla de *Centre* cambia *Centre: entries* en Results. | Nombre vacío, duplicado con otra zona, 300 caracteres, `{`, `}` (rompería fórmulas de cálculos). |
| B04-21 | Pestaña *Points*: nombre, color, distancia «near», punto de mapa de calor (cada opción). | Medidas de punto en Results. | Distancia negativa. |
| B04-22 | Pestaña *Lines*: nombre, color, *Delete line*. | Cruces de línea en Results. | |
| B04-23 | Pestaña *Groups*: *Add group* `Periferia = Arena − Centre`; zonas incluidas y excluidas. | Medidas del grupo = las de *Periphery* de la plantilla (compara). | Grupo vacío; que se excluye a sí mismo; que incluye y excluye la misma zona. |
| B04-24 | Pestaña *Sequences*: *Add sequence* A→B→C, *Add step*, *Up*, *Down*, *Remove*, cada casilla (cualquier paso, ambas direcciones, solapadas, otras zonas entre pasos, completar al volver al primero), límite de tiempo. | Medidas de secuencia en Results. | Secuencia de 1 paso, con la misma zona repetida, con un grupo. |
| B04-25 | *Zone grid*: cuadrada N×M, tamaño en cm, anillos concéntricos, sectores radiales, anillos×sectores; *Delete the whole grid*. | Celdas `A1…`, `Ring 1…`, `Sector 1…` y su grupo. | 1×1, 50×50 (rendimiento), tamaño de celda mayor que la arena, sin calibrar. |

## Calibración, varios aparatos, importar/exportar, imagen

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B04-30 | *Calibration ▸ Ruler*: dibuja una línea sobre algo de tamaño conocido, escribe la longitud (mm, cm, m). *Calibrate with the ruler*, *Clear*. Cambia la unidad del experimento. | Píxeles por cm coherentes; resultados en la nueva unidad (las columnas se renombran: `(cm)` → `(mm)`). | Longitud 0, negativa, texto; regla de 0 px. Sin calibrar: resultados en píxeles. |
| B04-31 **[P]** | *New*, *Duplicate*, *Rename*, *Delete* aparatos. | Explorador actualizado. | Borrar un aparato usado por pruebas (debe avisar). Renombrar a uno existente. Dos aparatos sobre el mismo video. |
| B04-32 | *Export* el aparato a `$QA/salidas/aparato.json`; *Import* en otro experimento (desde ese JSON y desde el `project.json` del demo). | Igual en ambos. | JSON roto (edita con Bash), JSON de otra cosa, experimento cifrado (B02). |
| B04-33 | *Map image* a PNG, SVG y PDF, con *Show labels* activado y desactivado; `inspeccionar.py`. | Archivos válidos con zonas en colores, nombres, calibración en la esquina. | Destino `solo_lectura`. |
| B04-34 | *Scale to fit*, zoom con rueda/gestos, *Show labels*. | Vista correcta. | |
| B04-35 **[O]** | Repite una captura del mapa en modo oscuro. | Legible. | |
