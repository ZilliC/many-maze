# Hallazgos previos (encontrados al construir los oráculos, 2026-10-12)

Antes de abrir un hallazgo nuevo, mira si es uno de estos. Si lo es, **no lo dupliques**: en la bitácora anota
`FALLA H-P0x (sigue)` o `OK H-P0x (ya no ocurre)` en el caso que lo verifica. Copia estos hallazgos al principio de
`bitacora/HALLAZGOS.md` de cada ejecución (lo hace `preparar_prueba.py`).

## H-P01 — En video real de rata, la cabeza rastreada apuntaba hacia atrás en ~25–60 % de la marcha

- **Severidad:** S1 para todas las medidas por la cabeza (exploración de objetos, entradas y tiempo de la cabeza en
  zonas, orientación hacia puntos, cabeceos, *head turn*, visitas de Barnes/hole board). Las medidas por el centro no
  se ven afectadas.
- **Estado:** CORREGIDO en los videos del laboratorio (2026-10-12; commits 5b8cbd8 y deb1ac8, integrados en la rama
  `validacion-qa`). Causas: (1) el voto por la dirección del movimiento no votaba a velocidad de marcha lenta (60
  cuadros/s); (2) al retroceder (bajar de un rearing, apartarse de la pared) el voto ponía la cabeza en la cola y
  la continuidad la mantenía mientras la rata estaba quieta; ahora, con la cola bien visible, la cola decide; (3)
  con luz roja se perdían la cola y los bordes (H-P11). Comprobado también a ojo, sin saber qué versión era cada
  punto: en los cuadros en que la versión vieja y la nueva discrepan, la nueva acierta en 73 de 77.
- **Evidencia** (`qa/herramientas/cabeza_real.py`, 150 s de cada video, cada arena por separado; «sostenida» = tramos
  de ≥ 1 s seguidos con la cabeza contra la marcha, que una rata no hace: es la inversión; ≤ 5 % = OK):

  | Video original (arena) | `mac-ci`: sostenida (contra la marcha) | arreglada: sostenida (contra la marcha) |
  |---|---|---|
  | EPM macho `2025-04-23 20-00-49.mov` | **23,3 %** (37,4 %) | 0,0 % (7,3 %) |
  | EPM hembra `2025-04-24 20-06-44.mov` | **30,7 %** (40,8 %) | 0,0 % (4,7 %) |
  | OFT `2025-04-22 20-10-36.mov` izquierda | 0,0 % (27,0 %; 890 cuadros de saltos) | 0,0 % (11,2 %) |
  | OFT `2025-04-22 20-10-36.mov` derecha | 0,0 % (1,7 %) | 0,0 % (0,7 %) |
  | OFT `2025-04-22 20-32-43.mov` izquierda | **31,8 %** (49,0 %) | 0,0 % (4,8 %) |
  | OFT `2025-04-22 20-32-43.mov` derecha | **24,6 %** (62,4 %) | 0,0 % (24,1 %: brazos del experimentador, 444 cuadros de saltos) |
  | OFT `2025-04-22 22-35-14.mov` izquierda | 4,9 % (12,8 %) | 0,0 % (7,5 %) |
  | OFT `2025-04-22 22-35-14.mov` derecha | **60,3 %** (71,0 %) | 0,0 % (2,0 %) |

  «Contra la marcha» incluye los ratos en que la rata retrocede de verdad y los saltos de posición que quedan, por
  eso no baja a 0.

  **Agua (MWM y FST), con recorte a la piscina / al cilindro** (2026-10-12; recortes en `ARENAS` de
  `cabeza_real.py`; `mac-ci` medido en un *worktree* aparte con `--codigo`). Las dos primeras columnas son la
  herramienta tal cual (≥ 2 px/cuadro; sostenida / contra la marcha); la tercera y la cuarta, una medición
  suplementaria hecha a mano para el MWM (abajo):

  | Video (recorte) | `mac-ci`: sostenida (contra) | arreglada: sostenida (contra) | `mac-ci`: sostenida (contra), rastreo afinado | arreglada: sostenida (contra), rastreo afinado |
  |---|---|---|---|---|
  | MWM `2025-04-29 20-00-56.mov` (piscina) | 0,0 % (77,2 %; 92 cuadros) | 0,0 % (21,4 %; 98 cuadros) | 0,0 % (24,2 %; 219 cuadros) | 0,0 % (2,5 %; 158 cuadros) |
  | MWM `2025-04-29 20-01-42.mov` (piscina) | 0,0 % (37,7 %; 191 cuadros) | 0,0 % (14,6 %; 151 cuadros) | 0,0 % (0,0 %; 276 cuadros) | 0,0 % (2,1 %; 235 cuadros) |
  | MWM `2025-04-29 20-11-06.mov` (piscina) | 0,0 % (27,3 %; 11 cuadros) | 0,0 % (16,7 %; 12 cuadros) | **37,0 %** (53,4 %; 219 cuadros) | 0,0 % (5,3 %; 228 cuadros) |
  | FST `2025-06-05 09-19-18.mov` (cilindro) | 0,0 % (49,2 %; 2162 cuadros) | 0,0 % (53,0 %; 2067 cuadros) | no aplica | no aplica |
  | FST `2025-06-05 09-31-36.mov` (cilindro) | 0,0 % (45,9 %; 1382 cuadros) | 0,0 % (44,0 %; 1324 cuadros) | no aplica | no aplica |

  - **MWM (vista cenital, una piscina por video; la rata nada con la cabeza por delante, visto a ojo en 5 cuadros
    al azar por video):** el criterio *sí* aplica, pero la herramienta tal cual casi no mide: a 60 cuadros/s una rata
    que nada avanza 0,6–1,5 px/cuadro, así que solo 11–191 de ~1 600 cuadros pasan el umbral de 2 px/cuadro, y en el
    24–35 % de los cuadros el rastreo no es la rata sino un reflejo brillante del agua (saltos de cientos de px de un
    cuadro) o, al final, la mano y el pelo del experimentador que la saca. Por eso el «sostenida 0,0 %» de la herramienta
    tal cual es en buena parte vacío: no demuestra nada. **Medición suplementaria** (a
    mano, con el mismo rastreador): detección afinada para el agua (`threshold=40, min_area_px=500, max_area_px=2500`,
    deja 4–17 % de cuadros con reflejos o sin detección en vez de 24–35 %), dirección de marcha sobre 15 cuadros con
    desplazamiento > 15 px (≥ 1 px/cuadro), cuadros con saltos descartados con una mediana móvil. Con eso: la versión
    arreglada da 2–5 % contra la marcha y 0 % sostenida en los tres videos (158–235 cuadros); `mac-ci` da 24 %, 0 %
    y 53 %, y en el tercer video un 37 % sostenido: la misma inversión de H-P01 también ocurre en el agua. La
    diferencia entre versiones es real pero la muestra es pequeña (tres videos de ~27 s, una rata cada uno); y el
    rastreo por defecto en piscina (pendiente, ver abajo) es peor que lo que sugiere la cifra.
  - **FST (vista lateral de un cilindro de vidrio, rata erguida o colgando, cola hacia abajo):** el criterio *no
    aplica*. El rastreador modela la rata como un animal visto desde arriba (cabeza y cola sobre el plano del
    suelo) y aquí se ve de lado: el centro se mueve poco y sobre todo en vertical, mientras la rata bracea sin
    avanzar, y la «dirección de marcha» no tiene relación con hacia dónde mira. Las dos versiones dan 44–53 % contra
    la marcha (cara o cruz) y 0,0 % sostenida solo porque el movimiento no avanza ≥ 1 s seguido. En el FST la medida
    pertinente es la inmovilidad, no la cabeza. A ojo, en 5 cuadros al azar por video, la cabeza unas veces apunta
    hacia arriba, otras de lado, y otras no se ve (la rata de espaldas a la cámara).
  - **Hallazgo aparte (agua, no H-P01; abierto y corregido como H-P13):** con los ajustes por defecto el rastreo sobre la
    piscina del MWM salta a reflejos del agua en 24–35 % de los cuadros (la rata, pequeña, pierde frente a las
    manchas brillantes). Con `threshold=40` y área 500–2 500 px baja a 4–17 %.
- **Por qué el oráculo sintético no lo veía:** los animales sintéticos (incluida una «rata» de 110 px que avanza 2,5
  px/cuadro, `rata_grande_lenta`) salían con 0 % de inversiones en las dos versiones; solo una media vuelta en **un**
  cuadro (`novel_object_giro_instantaneo`) lo reproducía (la exploración del objeto nuevo salía 0 s; con el
  arreglo sale bien). La forma de una rata real (cola gruesa en la base, sombra, reflejos, luz roja) no la imita el
  dibujo sintético: **este hallazgo solo se verifica con video real.**
- **Casos que lo verifican:** B13-54 (video real, el decisivo), B13-51 (inversiones frente a la nariz anotada a mano) y
  B17-04 (sintético).

## H-P02 — Dunnett da un p distinto cada vez que se calcula

- **Estado:** CORREGIDO (2026-10-11, commit 8018dcb; integrado en la rama `validacion-qa`). El p de Dunnett ahora se calcula por
  integración numérica determinista (exacta para n iguales o distintos), sin Monte Carlo: el mismo dato da siempre el
  mismo p y coincide con el oráculo. Prueba automática: `tests/test_dunnett.py`. En la ejecución, B11-05 debe dar `OK
  H-P02 (ya no ocurre)`: cinco cálculos iguales y Control–HFD ≈ 7,0e-5.
- **Severidad:** S3 (los resultados no son reproducibles; con p pequeños la variación relativa llega al ±30 %)
- **Dónde se ve:** Results ▸ Statistics, *Compare groups*, post-hoc *Dunnett test (vs control)*, con diferencias
  grandes (p < 0,001). Experimento del oráculo `estadistica.mmaze`, *Total distance (cm)* por Group, Día 1.
- **Observado:** cinco cálculos seguidos de Control–HFD: 5,7e-5 · 5,6e-5 · 6,5e-5 · 8,2e-5 · 6,1e-5. El valor exacto
  (integración numérica, validada con las tablas de Dunnett 1955) es 7,0e-5. Con p moderados (≈ 0,97) coincide con
  el exacto en 4 cifras.
- **Causa probable (para quien lo arregle, no para el ejecutor):** `scipy.stats.dunnett` integra por Monte Carlo
  sin semilla fija.
- **Caso que lo verifica:** B11-05.

## H-P03 — Mensajes de error técnicos (nombres de excepciones de Python) para el usuario

- **Estado:** CORREGIDO (2026-10-11, commit 8018dcb, y 2026-10-12, rama `validacion-qa`) para los dos casos de abajo y, en general,
  para todos los cuadros de error (`error_box` y los trabajos en segundo plano): los errores de archivos, zip/JSON
  dañados, falta de memoria o de disco se explican en lenguaje de usuario, y los errores internos inesperados dicen
  «Something went wrong unexpectedly…»; el detalle técnico va a la consola. Abrir una carpeta que no es un experimento
  → «“…” is not a mANY-MAZE experiment…»; convertir un archivo que no es un modelo → «“…” is not a PyTorch
  model checkpoint…». Pruebas: `tests/gui/test_error_messages_gui.py`. La lista de errores por test al rastrear
  («Test 5: OSError: Cannot open video source …», visto en el piloto) también dice ya en palabras claras qué pasa
  («Test 5: “x.mp4” was not found…», «… cannot be opened: it is damaged, empty or in a format …»; 2026-10-12,
  `tests/gui/test_pilot_fixes_gui.py`). Si la prueba encuentra otro mensaje técnico, se
  sigue anotando aquí en «También ocurre en».
- **Severidad:** S3 (el usuario no sabe qué pasó ni qué hacer)
- **Dónde se ve:** lo encontró la exploración automática del inventario (sección «Mensajes que aparecen al pulsar»).
  Ejemplos:
  - *File ▸ Open experiment…* eligiendo una carpeta que no es un experimento → «FileNotFoundError: [Errno 2] No such
    file or directory: '…/project.json'».
  - *Protocol ▸ Animal tracking ▸ Convert DeepLabCut model…* con un archivo que no es un modelo → «BadZipFile: File
    is not a zip file».
- **Esperado:** un mensaje en lenguaje de usuario («Esta carpeta no contiene un experimento de mANY-MAZE…»).
- **Casos que lo verifican:** B01-13, B03-23, B14-40, B14-42. Cada mensaje técnico nuevo que aparezca en la prueba se
  añade a este hallazgo («También ocurre en»), no como hallazgo aparte.

## H-P04 — Las rotaciones de la trayectoria cambian con diferencias numéricas invisibles

- **Estado:** CORREGIDO (2026-10-11, commit 3e7fdff; rama `validacion-qa`): un cambio de la dirección de marcha de más de 90° en
  un cuadro (marcha atrás) ya no es un giro de las rotaciones de la trayectoria. En el oráculo quedan 2 diferencias
  (funciones 3 horarias, open_field 1 antihoraria, verdad 0), que son temblor con el animal quieto (H-P05).
- **Severidad:** S3 (resultado no reproducible entre ordenadores o versiones de librerías)
- **Dónde:** *Path rotations clockwise / anticlockwise* (y las rotaciones cuando se siguen por la dirección de
  desplazamiento). Encontrado al construir la capa B del oráculo (2026-10-12).
- **Observado:** con dos series de rumbos que difieren como mucho en 1e-9 grados, el recuento pasa de 5/6 a 8/7. Cuando
  el animal da marcha atrás (~180° entre dos cuadros), el sentido del giro es ambiguo y lo decide el signo de un cero
  en coma flotante (−0,0 frente a +0,0 en `atan2`), y `unwrap` lo convierte en media vuelta a un lado o al otro.
- **Esperado:** que una marcha atrás no cuente como media rotación (como ya hace la orientación de la cabeza con los
  saltos > 90°), o al menos que el resultado no dependa del redondeo.
- **Caso que lo verifica:** B17 (el oráculo usa como tolerancia el número de marchas atrás ambiguas).

## H-P05 — Medidas infladas por el temblor del rastreo (aunque el cálculo es correcto)

- **Severidad:** S2 para las medidas de giro; S3 para el resto (afecta a la validez científica de esas medidas)
- **Dónde:** oráculo de verdad conocida, capa A frente a capa B (2026-10-12). La capa B (las mismas fórmulas sobre la
  trayectoria que rastreó la app) coincide en todo: **el cálculo es correcto**; la diferencia la pone el temblor del
  centro rastreado (≈ 0,1–0,4 px por cuadro con el animal quieto en video sintético limpio; en video real, más).
- **Magnitud (mediana sobre los 23 escenarios):**

  | Medida | Verdad → app |
  |---|---|
  | *Absolute turn angle*, *Angular velocity*, *Meander* | ≈ ×1,9 (p. ej. 1296° → 2441°) |
  | *Mean turn angle* | +74 % |
  | *Time getting closer / further away* (zonas) | +60 % a +74 % (quieto, el temblor «acerca» y «aleja») |
  | *Time moving towards / away* (zonas y puntos) | +50 % a +60 % |
  | *Path rotations* | 0 → 5–6 |
  | Distancia y velocidad media **dentro de una zona** donde el animal se para | +20 % |
  | *Time immobile* a baja resolución (EPM, 6,9 px/cm) | −18 % (el temblor supera el umbral de 2 cm/s) |

- **Estado (2026-10-15, rama `auto/hp05-jitter`):** CORREGIDO en lo que depende de la dirección: el rumbo se mide
  sobre un recorrido en tramos de al menos 0,5 cm (`series.simplify_path`, ajuste *Smallest movement that has a
  direction*, guía §9). Mediana app/verdad: giro absoluto ×1,63 → ×1,28; velocidad angular ×1,89 → ×1,32; *Meander*
  ×1,86 → ×1,26; giro medio ×1,79 → ×1,27; acercarse/alejarse (zonas) ×1,69 → ×1,15; ir hacia/alejándose ×1,40 →
  ×1,13; rotaciones del camino 78 esperadas 14 → 101 → 19. NO cambia: distancia y velocidad dentro de una zona, ni
  *Time immobile* a baja resolución (son de distancia/velocidad, no de dirección).
- **Esperado:** que las medidas de giro y de acercamiento ignoren los cambios de dirección por debajo de un
  desplazamiento mínimo (o suavicen más), y que la guía advierta de su sensibilidad al ruido.
- **Cómo lo usa la prueba:** `calibracion_capa_A.csv` de cada experimento del oráculo guarda estos efectos; en B17
  salen como `CONOCIDO (H-P05)`. Si una corrección los reduce, saldrán `OK`; si aparecen otros, son hallazgos nuevos.

## H-P06 — Decisiones de cálculo que la guía no documenta (o documenta distinto)

- **Estado:** 4 de la lista CORREGIDOS (2026-10-12, rama `validacion-qa`, `tests/test_guide_rules.py`): contradecían la regla de la
  propia guía o el ejemplo publicado de ANY-maze (auditoría independiente del oráculo, `AUDITORIA_ORACULO.md`). Son
  el n.º 5 (rotaciones: la siguiente vuelta cuenta desde el extremo), el 9 (distancia de la cabeza en una zona: el
  paso que sale, como el centro), el 12 (mínima distancia de la cabeza al borde: 0 una vez ha salido, como el
  centro) y el 13 (corredor de Whishaw del laberinto acuático: el paso que sale, como el de una zona). El oráculo
  ya no los acepta como alternativa. Los demás (1–4, 6–8, 10, 11, 14–17) y los valores por defecto que la guía no
  da (umbral y tiempo de inmovilidad, freezing mínimo de 1 s frente a 2 s de ANY-maze, latencia de lo que no ocurre
  = duración frente a «indefinida», suavizado, banda de tigmotaxis) **son decisiones del laboratorio**: siguen
  abiertos hasta que el laboratorio decida si deben coincidir con ANY-maze.
- **Severidad:** Observación (documentación). La app es coherente; quien compare con ANY-maze o reproduzca un método
  necesita saberlo.
- **Lista** (cada una sale como `ALTERNATIVA` en B17; ver `qa/herramientas/oraculo_medidas.py`):
  1. Móvil / inmóvil: la velocidad se promedia **además** 0,5 s antes de compararla con el umbral.
  2. Velocidad máxima: la velocidad (ya con posiciones suavizadas 0,2 s) se promedia otra vez 0,2 s.
  3. Giro de la cabeza: la dirección acumulada se suaviza 0,2 s.
  4. *Time facing* de una zona: el ángulo se mide desde la **cabeza** (la guía no dice desde dónde).
  5. Rotaciones completas: tras volver atrás > 90°, la cuenta sigue desde ese punto y no desde el extremo (los giros
     parciales sí cuentan desde el extremo): una vuelta en sentido contrario necesita hasta 450°.
  6. *Moving towards / away*: con el estado móvil (episodios de ≥ 2 s), no cuadro a cuadro.
  7. Puntos: *time near*, *approaches* y la latencia se miden con la **cabeza**; *exploring* incluye la cabeza a menos
     de medio radio aunque no mire al punto.
  8. *Zone transitions*: no cuenta los tramos fuera de toda zona.
  9. *Head distance* en una zona: cuenta el paso que **llega** a la zona, al revés que la distancia del centro (que
     cuenta el que sale, como dice la guía).
  10. *Door sequence* (RAPC): solo las entradas a los brazos.
  11. CPP *Preference index* = (A − B)/(A + B) (también se publica A/(A + B)). La EPM *Open arm time (%)* sí usa la
      definición de Pellow (abiertos / (abiertos + cerrados)).
  12. *Min head distance to border when inside*: sin la regla «0 una vez ha salido» que sí aplica al centro.
  13. Laberinto acuático, *Whishaw corridor distance / path*: cuenta el paso que **entra** en el corredor; el corredor
      de una zona cuenta el que **sale** (como la guía dice para las zonas).
  14. Periodos que empiezan con el animal ya en una zona: esa visita no es una entrada del periodo y la latencia sale
      «nunca» (la duración del periodo) aunque el tiempo en la zona sea el periodo entero.
  15. *Platform crossings*: empezar encima de la plataforma no es un cruce (aunque sí una entrada).
  16. Línea base de una entrada analógica: la DE es la poblacional (n), no la muestral (n − 1).
  17. Punto, *exploration bouts*: los tramos de menos de 0,2 s no cuentan.

## H-P07 — El rumbo inicial puede ser la dirección del temblor del rastreo (error de rumbo aleatorio)

- **Estado:** CORREGIDO (2026-10-11, commit 3e7fdff; rama `validacion-qa`): si tras «1 s móvil» el animal sigue a ≤ 0,5 cm del
  inicio, el rumbo usa la posición tras 1 s de movimiento real; si ni así se aleja, queda en blanco. Oráculo: rumbo
  inicial 50,0° frente a 48,7° de verdad (antes 0,1°).
- **Severidad:** S3 (S2 para quien use el error de rumbo inicial del laberinto acuático)
- **Dónde:** *Initial heading error* (laberinto acuático) y *<zona>: initial heading error* / *signed initial heading
  error*, con la opción por defecto *Heading error: tras 1 s móvil* (2026-10-12, capa B del oráculo).
- **Observado:** las paradas de menos de 2 s cuentan como «móvil». Si el animal se queda quieto al soltarlo (≤ 2 s),
  el «segundo móvil» termina sin que se haya movido: el rumbo se calcula entre dos posiciones a 0,02 px de distancia,
  es decir, sale la dirección del temblor del rastreo. En el oráculo: 0,1° (verdad: 48,6°); con otro ruido saldría
  cualquier otro ángulo.
- **Esperado:** exigir un desplazamiento mínimo (la opción *por distancia* ya existe) o no contar como móviles los
  cuadros sin desplazamiento; al menos, dejar el resultado vacío si el desplazamiento es ínfimo.
- **Caso que lo verifica:** B17 (`water_maze`, `movil`): la capa A lo marca como diferencia conocida.

## H-P08 — La memoria de una prueba en vivo crece ~60 MB por hora: 40 pruebas de 24 h no caben

- **Severidad:** S2 para pruebas largas con muchas sesiones (la app anuncia hasta 40 pruebas simultáneas y pruebas
  de 24 h); sin consecuencias para pruebas cortas.
- **Estado:** CORREGIDO durante la prueba (2026-10-12, commit 405a9f1 sobre `validacion-qa`; `core/trackstore.py`).
  Cada sesión guarda en memoria solo el último minuto (1500 filas); el resto de la trayectoria y del contorno se
  añade por trozos a `<archivo de rescate>.track` (un archivo temporal sin archivo de rescate) y se lee al guardar
  la prueba. El archivo de rescate JSON lleva solo las filas en memoria y cuántas hay en el `.track`: una prueba
  recuperada tras un cierre tiene toda la trayectoria hasta el último guardado (un trozo a medio escribir se
  descarta). Los gráficos del monitor ya no copian la trayectoria entera. `prueba_larga.py --horas 1 --sesiones 4`:

  | | antes | después |
  |---|---|---|
  | memoria por sesión y hora de prueba | **62 MB** (0,70 KB por cuadro) | **1 MB** (0,01 KB por cuadro) |
  | ídem con archivos de rescate (`--autosave`, como *Run tests*) | **62 MB**; pico del proceso 811 MB | **3 MB**; pico 604 MB (al guardar) |
  | 40 pruebas de 24 h (estimado) | **57,8 GB** (no caben en 24 GB) | **0,8 GB** (2,4 GB con `--autosave`) |
  | archivo de rescate a 1 h, por sesión | 20,5 MB de JSON reescrito entero (1,15 s la última vez) | JSON pequeño (0,01 s) + 11,4 MB en el `.track` (solo se añade) |
  | coste por cuadro y sesión | 0,97 ms | 0,97 ms |

  Mismas distancias al centímetro y todas las posiciones guardadas (90 000 de 90 000 por sesión). Una sesión de
  8 h con `--autosave`: 121 MB a los 10 min, 126 MB al final (0,4 MB/h); 720 000 de 720 000 posiciones. En disco, el
  `.track` ocupa ~11 MB por hora y sesión (40 × 24 h ≈ 11 GB en `recordings/` mientras corren; se borra al guardar).
- **Queda (otra causa, fuera de la prueba en vivo):** leer una trayectoria larga guardada (`Track.from_csv`: resultados
  al terminar, *Review*) usa ~2,4 GB de pico para 8 h con contorno (≈ 6–7 GB para 24 h): lee todo el CSV como texto
  y listas de cadenas. Reconstruir la trayectoria al guardar usa ~0,4 GB para 8 h (≈ 1,3 GB para 24 h), una prueba
  cada vez.
- **Dónde:** `qa/herramientas/prueba_larga.py` (2026-10-12), con el código de *Run tests* sin interfaz.
- **Observado:** 0,6–0,7 KB por cuadro y sesión (54–64 MB por hora de prueba y sesión), casi todo por el contorno del
  cuerpo que se guarda en memoria cuadro a cuadro (*Record the animal's whole-body outline*, activado por defecto):
  sin él, 0,09 KB por cuadro (8 MB/h). En este Mac (24 GB): 8 pruebas de 24 h ≈ 10 GB; 40 pruebas de 24 h ≈ 50–61 GB
  (no caben). El coste de proceso sí aguanta: 0,9 ms por cuadro y sesión (40 sesiones ≈ 37 ms de los 40 ms de un
  cuadro a 25 cuadros/s, solo el rastreo).
- **Esperado:** volcar la trayectoria y el contorno a disco por trozos (ya hay un guardado cada 5 s para recuperarse de
  un cierre) en lugar de acumularlos en memoria, o avisar al armar una prueba larga con muchas sesiones.
- **Caso que lo verifica:** B08-40.

## H-P09 — El *rearing* automático no detecta los rears cuando la cola se ve

- **Estado:** CORREGIDO en sintético (2026-10-11, commit 3e7fdff; rama `validacion-qa`): el paso que quita la cola usa el tamaño
  habitual del animal, no el del cuerpo encogido; oráculo: 2 de 2 rears, 4,0 s. **En video real con cámara cenital
  sigue sin funcionar, por otra causa: H-P10.**
- **Severidad:** S2 (la medida falla en el caso normal: un roedor que se levanta deja la cola en el suelo). Pendiente de
  confirmar con un video real (B13-53).
- **Dónde:** *Detect rearing automatically* (Protocol ▸ Analysis), experimento `funciones` del oráculo (2026-10-12).
- **Observado:** dos rears programados de 2 s (el cuerpo visto desde arriba al 55 % de su largo): la app detecta **0**.
  El mismo video sin cola: detecta **2** (4,0 s, exacto). El área sí baja (35 % de la habitual, < 75 %), pero el largo
  cabeza–cola se queda en el 86–93 % (> 80 %): con el cuerpo pequeño, el punto de la «cola» salta del final del cuerpo
  a la **punta de la cola**, así que el largo no baja y no se cumplen las dos condiciones.
- **Esperado:** medir el largo del cuerpo sin la cola también cuando el cuerpo encoge (o no exigir el criterio del
  largo cuando el de la cola es dudoso).
- **Casos que lo verifican:** B17-12 (`funciones`), B13-53.

## H-P10 — El *rearing* automático no detecta los rears de una rata vista desde arriba

- **Severidad:** S2 (la medida no sirve con la cámara cenital, que es la habitual en OFT).
- **Estado:** ABIERTO (2026-10-11; lo encontró la sesión que corrigió H-P09).
- **Dónde:** OFT real `2025-04-22 20-10-36.mov` (arena izquierda, rata blanca, cámara cenital), primeros 180 s.
- **Observado:** a ojo hay 4 rears claros (≈ 116,5; 120,3; 127,0 y 152,5 s) y 3 probables. La app (antes y después
  de arreglar H-P09) no detecta ninguno de los 4 claros: visto desde arriba, el área del cuerpo **no baja** al
  levantarse (90–111 % de la habitual, por la perspectiva) y el criterio exige < 75 %. Los episodios que sí detecta
  (9 s, 17 s) son la rata olfateando o sentada junto a la separación, con el cuerpo saliendo de la zona del suelo.
- **Esperado:** otra señal para la vista cenital (acortamiento cabeza–centro, contorno, o el modelo de pose con
  nariz y orejas), validada contra un recuento manual (B13-53).
- **Casos que lo verifican:** B13-53.

## H-P11 — Con luz roja, el rastreo perdía la cola y los bordes del cuerpo (gris distinto al del fondo)

- **Severidad:** S2 (empeoraba la orientación de la cabeza, H-P01, en los videos del laboratorio).
- **Estado:** CORREGIDO (2026-10-11, commit deb1ac8; rama `validacion-qa`).
- **Observado:** el rastreo convertía los cuadros a gris de una forma y el fondo y la vista previa de detección de
  otra (la de OpenCV). Con la luz roja, en video HD, las dos difieren hasta 23 niveles de gris: la cola y los bordes
  se perdían al restar el fondo, y lo que se veía en la vista previa no era lo que se rastreaba.
- **Prueba automática:** `tests/test_tracking.py` (la conversión antigua se desvía 8,9 niveles).

## H-P12 — Hallazgos del piloto con computer use (2026-10-11)

- **Estado:** CORREGIDOS (2026-10-12, rama `validacion-qa`, `tests/gui/test_pilot_fixes_gui.py`), salvo donde se dice.
  - **H-001 (S3):** con un experimento abierto la ventana medía 1588 × 982 pt en una pantalla de 1440 × 900 (la
    cinta cortada, controles fuera). Ahora las páginas y la cinta se desplazan en vez de agrandar la ventana: cabe
    en cualquier pantalla (mínimo ≈ 290 × 270 px).
  - **H-002 (Obs):** `RuntimeWarning: Mean of empty slice` en la consola (importar posiciones de pose con cuadros
    sin ninguna parte del cuerpo): silenciado, el resultado (NaN) era el correcto.
  - **H-003 (S4):** *Check video* cortaba la columna *Findings* y no se podía agrandar: ahora el texto va en varias
    líneas y el diálogo se agranda.
  - **H-004 (S4):** el botón de la página de bienvenida decía *Open demo experiment* y el menú *Create demo
    experiment*: ahora los dos dicen lo segundo. (About y Quit mal ubicados en el inventario: corregido el
    generador.)
  - **H-005 (Obs):** una lista `.m3u` con un archivo que no existe, o un video vacío, se aceptaban sin aviso: el
    diálogo *Add tests from videos* lo avisa ahora (se pueden añadir igual). La etapa vacía sigue aceptándose:
    es válida (pruebas sin etapa).

## H-P13 — En el MWM con luz blanca, el rastreo tomaba el reflejo de la lámpara en vez de la rata

- **Severidad:** S1 para las medidas del MWM por el centro (latencia, recorrido, tiempo por cuadrante, cruces de la
  plataforma), las más usadas: con los ajustes por defecto, en ~1 de cada 3 cuadros el «animal» era un trozo del
  reflejo, a cientos de px de la rata.
- **Estado:** CORREGIDO (2026-10-12, rama de esta sesión sobre `validacion-qa`). Comprobado en video real; prueba
  automática `tests/test_water_reflections.py` (incluye cuadros reales, `tests/data/mwm_reflections.npz`);
  herramienta `qa/herramientas/rastreo_agua.py`.
- **Dónde:** MWM `MWM_T1-T8_LM-A_F0_HEMBRAS_29.04.2025`, los tres primeros videos (`2025-04-29 20-00-56`,
  `20-01-42`, `20-11-06`; luz blanca, piscina recortada a (350, 0, 620, 610)); plantilla `water_maze`, ajustes por
  defecto.
- **Causa:** con un solo animal el rastreador se quedaba con la mancha **más grande**. En gris la rata (≈ 175) y el
  reflejo de la lámpara del techo (≈ 169) brillan igual; al nadar, la rata ondula el agua y el reflejo (que está en
  el fondo, la mediana) se rompe en trozos de 1 100–2 700 px, tan grandes o más que la rata (1 000–1 500 px). No era
  el umbral ni el área ni la polaridad por sí solos: esos trozos superan apenas el umbral (diferencia media con el
  fondo +22 a +39 con umbral 25), mientras la rata lo supera con holgura (+44 a +63). Al final, el brazo del
  experimentador (hasta 7 600 px) también ganaba por tamaño; con luz roja, la mano brilla más que la rata.
- **Arreglo** (`ArenaTracker._choose_single`, solo con un animal): cada mancha puntúa su área por cuánto supera el
  umbral su contraste medio, contado como mucho hasta el contraste habitual del animal (mediana de las detecciones,
  como el área habitual); una mancha de más del doble del área habitual cuenta menos (cuadrado del exceso) y una
  fuera del alcance de la última posición (la puerta de identidad, 4 cuerpos + 1 por cuadro sin ver) cuenta la mitad.
  Con una sola mancha nada cambia. Varios animales: sin cambios. No hizo falta cambiar los ajustes por defecto de la
  plantilla ni añadir máscaras de reflejos.
- **Medición** (`qa/herramientas/rastreo_agua.py`). Referencia independiente: el color; con luz blanca la rata es
  cálida (R − B ≈ +75) y el reflejo neutro (≈ 0), y el rastreador trabaja en gris. Revisada a ojo: la referencia cae
  sobre la rata en 12 de 12 cuadros al azar. Clases: «rata» (la rata sola en el agua: 3 825 cuadros), «mano» (además el
  brazo del experimentador al soltarla o sacarla: 600), «ausente» (la rata fuera: 356). Acierto = centro a < 30 px
  (≈ 7 cm). «Crudo» = sin quitar saltos ni rellenar; «final» = la pista guardada (quitar saltos > 300 cm/s y rellenar).

  | Video | antes: crudo / final (rata) | después: crudo / final (rata) | antes → después, final (mano) |
  |---|---|---|---|
  | `2025-04-29 20-00-56` | 69,2 % / 97,0 % | 99,4 % / 99,6 % | 1,1 % → 90,9 % |
  | `2025-04-29 20-01-42` | 75,7 % / **92,3 %** | 100,0 % / 100,0 % | 18,8 % → 87,7 % |
  | `2025-04-29 20-11-06` | 61,3 % / 99,2 % | 97,9 % / 99,8 % | 41,1 % → 13,2 % |
  | **Los tres** | **68,6 % / 96,1 %** | **99,1 % / 99,8 %** | 18,3 % → 64,7 % |

  Es decir: los cuadros con reflejo o mano pasan de 31 % a 0,9 % en crudo. La eliminación de saltos inverosímiles (> 300 cm/s, ya en
  `validacion-qa`) tapaba buena parte en la pista final, pero rellenando a ciegas (y no en 20-01-42: 92 %).
- **Generalización** (sin ajustar nada a estos videos; 60 s de cada uno, la duración de la plantilla):
  - Luz blanca, referencia de color, cuadros «rata», crudo / final: adecuación hembras `2025-04-28 20-41-40`
    72,0 / 89,6 % → 100 / 100 %; `2025-04-28 21-39-46` 74,9 / 95,8 % → 100 / 100 %; adecuación machos
    `2025-05-06 20-31-49` 87,1 / 89,2 % → 99,7 / 99,9 %; cuarto video del 29.04 (`20-16-02`, no usado para ajustar)
    73,6 / 92,9 % → 99,5 / 100 %.
  - Luz roja (agua oscura, casi sin reflejos), contra las trayectorias independientes de
    `~/Downloads/Ratas/analisis_conductual/MWM_trayectorias` (10 Hz, < 40 px), crudo: `2025-04-29 20-59-45` 89,7 →
    90,9 %; `2025-05-07 19-58-13` 99,7 → 99,7 %; `2025-04-30 20-25-19` y `2025-05-08 20-03-55` 100 → 100 %. Lo que
    queda en 20-59-45 son las manos al soltar y sacar la rata.
- **EPM y OFT (H-P01), `qa/herramientas/cabeza_real.py`, 150 s:** la cabeza invertida sostenida sigue en **0,0 %** en
  las 8 arenas (2 EPM, 6 OFT), igual que antes del cambio; «contra la marcha» casi igual (EPM 7,3 → 7,3 % y 4,7 →
  4,4 %; OFT 20-32-43 izquierda 4,8 → 6,4 %, derecha 24,1 → 18,8 %; el resto idéntico).
- **Queda abierto:**
  - Cuadros «mano» (1–4 s al soltar y al sacar la rata): 65 % bien. En la mayoría del resto la mano ya tiene a la rata
    y forman una sola mancha: ninguna elección de mancha lo resuelve. Conviene que la ventana del ensayo los excluya (inicio
    *experimenter_leaves*, fin al llegar a la plataforma).
  - Con la rata fuera del agua siempre se «detecta» algo (un reflejo): ni antes ni ahora hay un criterio de
    «no hay animal».
  - Con *Downscale* 4 la rata se funde a veces con su estela oscura (en 1 de 12 cuadros reales de prueba); con 1 o 2,
    bien.
- **Casos que lo verifican:** `tests/test_water_reflections.py`; `qa/herramientas/rastreo_agua.py` sobre los tres
  videos (≥ 95 % en «rata», final, = OK).

