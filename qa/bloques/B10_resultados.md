# B10 — Results ▸ Data: hoja, exportaciones, informes, gráficos y video

**Inventario:** «Results ▸ Data — CINTA», «Results ▸ Data», sub-páginas *Spreadsheet*, *Track plots*, *Heat maps*,
*Charts*, *Video export*, diálogos «HTML report», «E-mail report», «Export video with overlays».
**Precondiciones:** copia **limpia** del demo `$QA/experimentos/resultados.mmaze` (sin cambios: debe coincidir con
`referencia/`). Servidor de correo falso en marcha (plan §3.4). Toda salida va a `$QA/salidas/B10/`.

## La comparación clave

Para **cada** exportación de tabla:

```bash
cd ~/Downloads/many-maze
.venv/bin/python qa/herramientas/inspeccionar.py "$QA/salidas/B10/<archivo>"
.venv/bin/python qa/herramientas/comparar_con_referencia.py "$QA/salidas/B10/<archivo>" "$QA/referencia/demo_resultados.csv"
```

Con la selección de datos por defecto el resultado debe ser **IGUALES**. Si difiere, hallazgo **S1** con la salida
del comparador pegada.

## Hoja de resultados

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B10-01 | Abre Results ▸ Data ▸ *Spreadsheet*. Revisa 6 filas (C1–C3, A1–A3). | Coincide con la referencia (mira 5 celdas a ojo y luego exporta). | |
| B10-02 | *Select data*: recorre **todas** las categorías de medidas (whole apparatus, por zona, por punto, por línea, secuencias, grids, social, claves, I/O, calculadas, columnas de información), filtros y periodos. Marca y desmarca grupos enteros. | La tabla cambia en consecuencia. | Desmarcar todo (¿tabla vacía con mensaje?); marcar todo (rendimiento: anota el tiempo). |
| B10-03 | Guarda la selección como **informe** (*Report* ▸ guardar con nombre), cámbiala, vuelve a cargar el informe; márcalo por defecto; *Clear settings*. | Ida y vuelta exacta. | Nombre repetido, vacío. |
| B10-04 | *Set segment length* 10 s. | Filas por segmento = `referencia/demo_resultados_por_intervalos.csv` (compara). | 0, 0.01, 1000. |
| B10-05 | *Recalculate*, *Back*, *Forward*, *View spreadsheet*. | Coherente. | |
| B10-06 | *Copy* (sin selección y con una fila), *Copy selection* (rango rectangular), clic derecho ▸ *Copy without headers* / *Save selected cells…*. Lee el portapapeles con `read_clipboard`. | Texto tabulado correcto. | |
| B10-07 | *Print*: imprime a PDF (diálogo de macOS ▸ PDF ▸ Guardar como PDF en `$QA/salidas/B10/`). | PDF legible. | |
| B10-08 **[O]** | Ordena por columnas, redimensiona columnas, desplázate a la columna 579. | Fluido; encabezados legibles en claro y oscuro. | |

## Exportaciones (Spreadsheet ▸ *Save* / *Export*)

Una fila de bitácora por formato, cada una con `inspeccionar.py` + `comparar_con_referencia.py` cuando aplique:

| Caso | Formato | Esperado |
|---|---|---|
| B10-10 | CSV | IGUALES a la referencia |
| B10-11 | Tab-separated text | IGUALES |
| B10-12 | Excel workbook | IGUALES (hoja 1) + hojas de periodos, animales, pruebas, ajustes |
| B10-13 | SYLK `.slk` | IGUALES; números como números |
| B10-14 | dBase `.dbf` | Nombres de campo ≤ 10 caracteres (`TOTAL_DIST`, `TOTAL_DI_2`…); valores iguales (compara a mano 5 celdas) |
| B10-15 | *Selected cells…* en cada extensión | solo el rango elegido |
| B10-16 | *Experiment as XML (with raw tracks)* | XML válido, con animales, pruebas, tracks |
| B10-17 | *Raw data per test (CSV)* | un archivo por prueba y animal, con tiempo y parámetros por cuadro |
| B10-18 | *One row per animal* | una fila por animal, columnas `medida [Etapa · Ensayo]` |
| B10-19 | *Mean of each animal's trials per stage* | una fila por animal y etapa con el nº de ensayos |
| B10-20 | *Event log of the shown tests* | entradas/salidas de zonas, teclas, pausas, en orden temporal |
| B10-21 | Límites: destino `solo_lectura`; nombre con acentos y espacios; sobrescribir un archivo existente (debe preguntar); archivo abierto en otra app (si puedes); extensión no soportada (`.pdf`). | Mensajes claros, nunca un archivo a medias sin aviso |

## Informes y correo

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B10-30 | *HTML report*: con escala de mapa de calor común, con charts de parámetros marcados en Charts. | HTML válido (`inspeccionar.py`), con estadística de las medidas principales, gráficos, sin `nan`/`Traceback`. Compara con `referencia/demo_reporte.html` (tamaño y número de imágenes similares). | Sin datos (filtro vacío). |
| B10-31 | *E-mail report*: crea antes un dispositivo de alertas (B09-12: `127.0.0.1:2525`). Envía Excel + HTML a `qa@example.org`. | Llega a `$QA/salidas/correos/` con los adjuntos (revisa la consola del servidor). | Con track plots y mapas (mensaje grande); servidor apagado (error claro y nada enviado); sin dispositivo de alertas (mensaje que explica cómo configurarlo). |

## Track plots, mapas de calor, charts, video

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B10-40 | *Track plots*: cada prueba de la lista; pestañas *Track*, *Heat map*, *Speed*, *Groups*; colorear por velocidad / tiempo / parámetro; marcas de comportamiento; paneles por periodo. | Gráficos correctos. | |
| B10-41 | Reproducción animada: *Play the track*, velocidades 0.25–16×, *Trail*, barra de tiempo; *Save playback* a MP4. | MP4 válido (`inspeccionar.py`) con el video de la prueba debajo del track. | Cancelar la exportación; destino de solo lectura. |
| B10-42 | *Save figure* (PNG/SVG/PDF), *Copy* (figura al portapapeles), *Open test*, *Export video*. | Archivos válidos. | |
| B10-43 | *Heat maps*: normalizado, por comportamiento, *Treatment heat maps* (promedio por grupo con alineación), *Add point here* (punto en el lugar más caliente). | Mapas correctos; el punto se crea en el aparato. | |
| B10-44 | *Charts*: hasta 10 parámetros (elige 11), bandas de ocupación de zona, eventos puntuados, zoom con rueda, *Home / Back / Forward / Pan / Zoom / Subplots / Customize / Save / Reset zoom*, *Measure interval*, *Find peaks*, *Export data*. | Todo funciona; exportación de datos válida. | |
| B10-45 | *Video export*: cada casilla (zonas, centro/cabeza/cola, comportamientos, marca de tiempo, track de los últimos N s), colores, velocidad 0.5–8×, tamaño 100/75/50 %; *Export video…*. | MP4 válido con las superposiciones elegidas (mira un cuadro con una captura de la reproducción en Review o con QuickTime si tienes acceso). | Prueba sin video; cancelar a mitad. |
| B10-46 | Con ciego activo, exporta CSV y HTML. | *Group* = código, nunca el nombre real. | |
| B10-47 | Periodos y cálculos de B03 presentes en la hoja y en las exportaciones. | | |
