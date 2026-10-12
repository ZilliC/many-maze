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
  eso no baja a 0. MWM y FST (agua) no se han medido con recorte: pendiente (B13-54).
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
