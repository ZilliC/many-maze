# B06 — Test schedule (programa de pruebas)

**Inventario:** «Test ▸ Test schedule — CINTA», «Test ▸ Test schedule», diálogos «Add tests from videos», «Create test
schedule», «Check video», «Import DeepLabCut track», «Test variables».
**Precondiciones:** `preparar_prueba.py` crea los dos experimentos: `$QA/experimentos/programa.mmaze` (copia del
demo) y `$QA/experimentos/videos.mmaze` (sin pruebas: aparato de campo abierto sobre la arena izquierda de
`OFT_macho_1.mp4`, animales R1, R2 y R3, etapas *Día 1* y *Día 2*). Oráculo de *Check video* en
`referencia/ORACULOS.txt`. La tabla de pruebas dice **Ready** en las pruebas por rastrear; «pending» solo sale en el
contador de la cabecera («N pending»).

## Añadir pruebas

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B06-01 | En `videos.mmaze`: *Add tests from videos* → los 3 OFT reales. En el diálogo, escribe en *Animal ID* R1, R2 y R3 (por defecto propone el nombre del archivo, que crearía animales nuevos) y elige la etapa *Día 1*. | 3 pruebas *Ready* con su video y su animal; no aparecen animales nuevos. | Seleccionar 0 archivos. Etapa vacía: se acepta (pruebas sin etapa); anótalo. |
| B06-02 | *Add tests from videos* con **cada** archivo de `videos_limite/` (uno por vez). | Válidos: prueba creada. Vacío/corrupto/truncado/texto: rechazo con mensaje claro **o** prueba creada que falla al rastrear con mensaje claro (anota cuál). Nombre con acentos: funciona en todo el flujo (rastrear, revisar, exportar). `lista_buena.m3u`: una prueba de 15 s continuos. `lista_con_archivo_que_falta.m3u` y el video vacío: el diálogo *Add tests from videos* avisa (texto naranja bajo la tabla) y nombra el archivo; al rastrear, el error dice en palabras claras qué pasa (era H-P03 / H-005: «Test 5: OSError: …»). | **Una fila de bitácora por archivo: los 15.** |
| B06-03 | *Add tests from videos ▾ Join video files into one test…* con `parte_1/2/3.mp4`. | Se crea una lista M3U en `videos/` del experimento; la prueba dura 15 s y se reproduce sin saltos. | Orden de nombres, una sola parte, partes de distinto tamaño (`vertical_240x320.mp4` + `parte_1.mp4`). |
| B06-04 | *Add test* (sin video). | Prueba sin video, para TakeNote o live. | |
| B06-05 | En `videos.mmaze`, *Schedule…*: R1–R3 × *Día 1*, *Día 2* × 2 ensayos con cada orden (*By animal*, *By trial*, *Randomised* con semilla, *Latin square*); *Counterbalance* aparato y una variable. El orden se comprueba en la vista previa del diálogo («N tests will be created — first: …») antes de aceptar. Para elegir *Randomised* escribe «Ra» en la lista (su posición cambia). | Orden correcto (anótalo); misma semilla → mismo orden; combinaciones existentes, animales retirados y etapas completadas se saltan. | 50 etapas × 99 ensayos (rendimiento, ¿avisa?); 0 ensayos. |
| B06-06 | *Schedule… ▾ Print schedule… / Save schedule… (CSV, TSV, Excel) / Copy schedule*. | Impresión con columna *Done* vacía (imprime a PDF); archivos válidos (`inspeccionar.py`); portapapeles con la tabla (`read_clipboard`). | |
| B06-07 **[P]** | Edita a mano animal, etapa, ensayo, aparato y notas en la tabla. | Persisten. | Etapa inexistente, ensayo 0/100, aparato que no existe. |
| B06-08 | *Duplicate*, *Set video file* (cambiar el video de una prueba rastreada), *Delete* (una, varias, todas, rastreadas). | Cambiar el video de una prueba rastreada avisa que el track ya no corresponde. Borrar pide confirmación. | |

## Rastrear y comprobar

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B06-10 | *Track selected* con 1 prueba; *Track all untracked* con el resto. Cronometra. | Progreso visible en un diálogo modal «Tracking N tests» (no se puede usar la app mientras tanto: es lo esperado); pasan a *tracked*. Con los videos reales (aparato sin calibrar) puede salir al final un aviso «N track looks wrong…»: léelo, anota qué dice y ciérralo. | Cancelar a mitad; cerrar el experimento durante el rastreo; `Cmd+Q` durante el rastreo (pregunta, y al reabrir no hay tracks corruptos). Rastrear de nuevo una ya rastreada. |
| B06-11 | *Check video* sobre la prueba de `fallos_cuadros_negros_y_repetidos.mp4`, y sobre una de un video real. | Negros y repetidos **iguales al oráculo** (números de cuadro desde 0). La columna *Findings* se lee entera (texto en varias líneas) y el diálogo se puede agrandar (era H-003). Real: sin fallos o pocos. Nada se modifica. | Sobre la M3U (numeración continua); sobre varias pruebas que comparten video (se comprueba una vez). |
| B06-12 | *Review test* con una seleccionada; doble clic en una fila. | Abre Review and score en esa prueba. | Sin selección. |
| B06-13 | *Import track* con `track_deeplabcut.csv` (elige partes del cuerpo y fps 25) en una prueba con `valido_10s.mp4`. | Prueba *tracked* con el track importado; Review muestra la trayectoria circular. | Partes del cuerpo inexistentes; fps 0; archivo que no es DLC. |
| B06-14 | *Import track data* con `track_tabulado_metros.txt` (escala 1000 px/m). | Asistente de columnas reconoce Time / Centre X/Y / Head X/Y; track importado. | Archivo vacío, columnas sin tiempo. |

## Estados

| Caso | Pasos | Esperado |
|---|---|---|
| B06-20 | *Skip* → *Resume* una prueba pendiente. | *skipped* y de vuelta a *Ready*; las saltadas no salen en resultados ni en *Track all untracked*. |
| B06-21 | *Re-perform* una rastreada. | Nueva prueba *attempt 2*; la anterior *superseded* y fuera de resultados/estadística. |
| B06-22 | *Exclude* una rastreada. | *excluded*: fuera de resultados; reversible con el mismo botón, que pasa a llamarse *Include*. |
| B06-23 | *Clear tracks*. | Track borrado, eventos puntuados y video intactos. |
| B06-24 | *End stage for animal* / *Reopen stage for animal*. | Pendientes saltadas con nota *stage ended*; reabrir las reanuda. |
| B06-25 | *Set user* para varias. | Columna *User* actualizada. |
| B06-26 | *Test variables* (objeto nuevo NOR, lado del estímulo social). | Se guardan por prueba; cambian resultados específicos (con tipo de test NOR / three-chamber). |
| B06-27 | Menú contextual (clic derecho) de la tabla: prueba cada entrada. | Mismas acciones que la cinta. |
| B06-28 **[P]** | Guarda, cierra, reabre: estados, usuarios, variables y videos persisten. | |
| B06-29 | Ordenar por cada columna; selección múltiple con `Shift`/`Cmd`; botones habilitados según selección (0, 1, varias). | Coherente. |
