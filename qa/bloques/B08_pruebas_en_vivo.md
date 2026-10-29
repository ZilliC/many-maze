# B08 — Run tests (pruebas en vivo con cámaras simuladas)

**Inventario:** todas las secciones «Test ▸ Run tests …» (los 3 modos y sus pestañas *Setup*, *Monitor*,
*Procedures*, *Results*, *Log*), diálogos «Adjust calibration», «Adjust apparatus», «Panel settings», «Recorded video
file names», «Industrial cameras», «Weigh animal … before test …».
**Precondiciones:** copia del demo `$QA/experimentos/vivo.mmaze`. No hay cámara: se usa **Simulate a camera with a
video file** con los videos del demo (`<demo>/videos/C1.avi`…) y los de `videos_reales/`. Todo lo que exige una
cámara real: `N/A (sin cámara)`.

## Una prueba

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B08-01 | Modo *One test*. Setup: *Simulate a camera with a video file* → `C1.avi`, *Play it at* 1×. Animal/etapa/ensayo/aparato. *Show the camera image*. | Se ve el video. | *Use a camera* + *Scan*: sin cámaras → mensaje claro. Video corrupto/vacío. |
| B08-02 | *Test duration* 20 s, *Immediately when armed*, *Arm / Start test*. | Corre 20 s; Monitor muestra posición, zonas, estadísticas en tiempo real; al terminar, prueba nueva en Test schedule con *Reason for test end* = *Test duration reached*. | Duración 0 (= hasta parar). |
| B08-03 | *Pause* / *Resume* a mitad; *Stop* → *Save*; otra vez → *Discard*. | Pausa congela el reloj (nota con su duración); *Save* guarda (*Stopped by user*); *Discard* no deja prueba. | Pausar 5 veces seguidas. |
| B08-04 | Cada modo de inicio: *When the animal is detected*, *When the experimenter leaves the view* (con el demo no hay mano: debe quedarse esperando con el texto *WAITING…* — anótalo), *On a start key* (espacio, PageDown, F5; parar con B, PageUp), *On a start switch* (sin dispositivo: mensaje), *At a clock time* (pon la hora actual + 1 min). *Delay after the start switch* 5 s. | Cada uno se comporta como en la guía §7. *Start now* fuerza el inicio. | Hora pasada; formato de hora inválido; tecla de inicio que también es de puntuación. |
| B08-05 | Simulación hasta el final del video (duración mayor que el video). | Termina con *End of the video*. | |
| B08-06 | *Record a video of the test*, *Burn the time and events*, *Start a new video file every* 10 s, *File names…*. | Grabación en `recordings/` (`inspeccionar.py`), con texto quemado; partes de 10 s + lista. | Nombre de archivo con `/`, vacío. Carpeta de grabaciones en `solo_lectura` (si se puede elegir). |
| B08-07 | Puntuación durante la prueba (teclas y botones; *Undo the last scored event*). | Eventos en la prueba guardada. | |
| B08-08 | *Capture backgrounds* / *Capture the empty-arena background*; *Background*: *Empty-arena image* vs *Adaptive*. | Captura; la detección sigue funcionando. | |
| B08-09 | *Camera options* (con video simulado), *Camera image*, *Options of this test*. | Opciones no soportadas se informan como tales. | |
| B08-10 | Durante una prueba: *Adjust calibration* (longitud real y px/cm) y *Adjust apparatus* (mover, rotar, escalar, mover una zona). | Efecto inmediato en estadísticas en vivo; guardado con la prueba y anotado en sus notas; el aparato del experimento no cambia. | Valores 0, negativos. |
| B08-11 | Pestañas *Monitor* (gráficos en vivo de parámetros, advertencias), *Results* (*Open in Review and score*, *Next test*), *Log* (*Clear*). | Contenido coherente. | |
| B08-12 | *Warn if the animal is lost for* 1 s con un video donde el animal desaparece (usa `fallos_cuadros_negros_y_repetidos.mp4` como cámara). | Advertencia en Monitor y en la prueba. | |
| B08-13 | *Weigh the animal before each live test* (B03-08) sin balanza. | Pide el peso (escribirlo a mano) o explica; no se queda bloqueado. | |

## Varias pruebas a la vez

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B08-20 | Modo *Several tests*: *Add source* ×4 (videos del demo), *Add test panel* ×4, asigna fuente/aparato/animal a cada panel en la tabla *Test panels*. | 4 paneles con su imagen. | Dos paneles con el mismo animal; panel sin fuente. |
| B08-21 | *Start all*, *Pause all*, *Resume all*, *Stop all*. | Todos a la vez; cada prueba se guarda al terminar. | *Stop all* durante el retardo de inicio. |
| B08-22 | *Remove panel*, *Next test*, *Apparatus layout*, *Scale to fit*, *Tracking indicators*, *Hide report*, *Hide apparatus*, *Panel settings*. | Cada uno hace lo que dice. | |
| B08-23 | Escala: 12 fuentes y 12 paneles (repite videos), luego intenta 49 cámaras / 41 pruebas. | Funciona hasta 48/40 (anota fluidez y CPU con `top -l 1 | head`); más allá avisa. | |
| B08-24 | *At a clock time* + *every day* (no esperes al día siguiente: comprueba que se arma y anota). | | |

## Solo observación y recuperación

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B08-30 | Modo *Observation only*: *Start observation*, puntúa, *Pause*, *Undo*, *Stop and save*. | Prueba *scored*. | Cerrar la app con la observación en marcha. |
| B08-31 | **Recuperación tras cierre**: inicia una prueba de 60 s, a los 20 s mata la app con `kill -9 $(pgrep -f mANY-MAZE)`. Relanza y abre el experimento. | La prueba se recupera, marcada *Interrupted (recovered after a crash)*, con ~20 s de track; grabación reproducible hasta el último fragmento. | |
| B08-32 **[P]** | Ajustes de Setup (duración, inicio, teclas, grabación) persisten tras cerrar y reabrir. | | |

## Larga duración

| Caso | Pasos | Esperado |
|---|---|---|
| B08-40 | Sin interfaz (Bash, en segundo plano): `.venv/bin/python qa/herramientas/prueba_larga.py --horas 4 --sesiones 8 --salida "$QA/salidas/B08_larga"` (≈ 1 h de reloj). Lee `prueba_larga.txt`. | Todos los criterios `OK` salvo el de 40 × 24 h mientras H-P08 siga abierto. Anota la memoria por hora y sesión y el coste por cuadro. |
| B08-41 | En la interfaz: una prueba en vivo de 60 min con un video del demo como cámara (*Play it at* 1×; repite el video con una lista M3U de 120 copias de `C1.avi` si hace falta: créala con Bash), *Record a video* activado, con *Monitor* abierto. Cada 15 min anota la memoria de la app (`ps -o rss= -p $(pgrep -f mANY-MAZE)`) y si la interfaz sigue fluida. | La prueba termina por duración, se guarda con 60 min de trayectoria y la grabación se reproduce entera. Memoria: crecimiento parecido al de B08-40 (sin saltos bruscos). |
| B08-42 | Igual que B08-41 pero 8 pruebas a la vez (*Several tests*), 30 min. | Las 8 terminan y se guardan; la interfaz no se congela; anota la memoria. |
