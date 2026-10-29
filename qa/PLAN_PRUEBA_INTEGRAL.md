# Prueba integral de mANY-MAZE con computer use

Plan para que un agente (pensado para **Claude Sonnet** con *computer use*) use mANY-MAZE como lo haría una persona
del laboratorio, toque **cada botón, menú y control**, recorra **cada caso de uso** y **cada caso límite**, y deje
**todo anotado**: lo que funcionó, lo que falló, lo raro y lo que no se pudo probar.

```
qa/
├── PLAN_PRUEBA_INTEGRAL.md        ← este archivo: reglas, preparación, convenciones, prioridades, orden de los bloques
├── INVENTARIO_UI.md               ← GENERADO: todos los controles (páginas, cintas y sus menús, diálogos, modos),
│                                    los mensajes de la app, los elementos de los procedimientos y las 47 pruebas
├── HALLAZGOS_PREVIOS.md           ← fallos ya encontrados al construir los oráculos (no duplicarlos)
├── bloques/B00…B17_*.md           ← los casos de prueba, un archivo por bloque (una sesión por bloque)
├── plantillas/                    ← BITACORA.md, HALLAZGOS.md, COBERTURA.md, INFORME_FINAL.md
└── herramientas/
    ├── preparar_prueba.py         ← crea la carpeta de prueba con todas las entradas y los oráculos
    ├── generar_inventario.py      ← regenera INVENTARIO_UI.md: recorre la app sin pantalla y pulsa todo
    ├── oraculo_sintetico.py       ← 34 experimentos con recorrido programado, sus valores esperados y la verificación
    │                                en dos capas (A: verdad; B: cálculo sobre lo rastreado) (B17)
    ├── oraculo_medidas.py         ← las definiciones de la guía, medida a medida (lo usa el anterior)
    ├── oraculo_funciones.py       ← ídem para investigación, reglas de entrada, secuencias, rejillas, rearing, teclas,
    │                                periodos, zona oculta, varios animales y I/O
    ├── oraculo_estadistica.py     ← experimento de diseño conocido y los resultados esperados de cada prueba (B11)
    ├── prueba_larga.py            ← horas de pruebas en vivo simultáneas: memoria, tiempo real, guardado (B08-40)
    ├── anotar.py                  ← anotación manual de fotogramas de un video real (la verdad de referencia)
    ├── comparar_anotaciones.py    ← precisión del rastreo frente a esas anotaciones (B13-50/51)
    ├── cabeza_real.py             ← % de cuadros con la cabeza contra la marcha en un video real (H-P01, B13-54)
    ├── inspeccionar.py            ← describe y valida cualquier archivo exportado
    ├── comparar_con_referencia.py ← compara una tabla exportada con otra (línea de comandos, versión anterior)
    ├── smtp_local.py              ← servidor de correo falso para «E-mail report» y alertas
    ├── captura.sh                 ← guarda una captura de las ventanas de la app como evidencia
    └── ventanas.swift             ← ventanas de la app (id, tamaño) y tiempo hasta que abre (B00-01, B00-10)
```

### Tres tipos de verificación (de más a menos fuerte)

1. **Verdad conocida** (B17, B11, B13-50, *Check video*, dosis): el valor correcto lo fija algo externo a la app
   (un recorrido programado, scipy, una fórmula de libro, una persona que anota fotogramas). Detecta medidas mal
   calculadas y errores del rastreo. En B17, la capa B separa los errores de cálculo de los de rastreo.
2. **Coherencia** (B10, B16, B01-40): dos caminos de la app deben dar lo mismo (interfaz y línea de comandos,
   CSV y Excel, versión actual y v0.2.0). Detecta fallos de interfaz, exportación y compatibilidad, **no** cálculos
   equivocados (los dos caminos usan el mismo motor).
3. **Juicio de usuario** (el resto): ¿hace lo que dice la guía? ¿se entiende? ¿se rompe con entradas raras?

### Cómo lanzar cada sesión

Una sesión nueva de Claude Code (modelo Sonnet, con computer use activado) por bloque, en esta carpeta del
repositorio. Mensaje para pegar, cambiando el bloque y la carpeta:

> Eres el ejecutor de la prueba integral de mANY-MAZE. Lee `qa/PLAN_PRUEBA_INTEGRAL.md` completo y después
> `qa/bloques/B06_programa_pruebas.md`. La carpeta de prueba es `~/mANY-MAZE-QA/2026-10-12_0930`. Lee el final de su
> `bitacora/BITACORA.md` y de `bitacora/HALLAZGOS.md` para saber dónde quedó la sesión anterior. Ejecuta el bloque
> entero siguiendo las reglas del §1 sin pedirme confirmaciones (salvo la regla 8), anotando cada paso al momento.
> Al terminar, escribe el resumen del bloque y actualiza `COBERTURA.md`. Pasada: [1 = solo P1 / 2 = todo].
> [Autorizo / No autorizo] descargar el modelo de pose.

Al terminar todos los bloques, una última sesión (puede ser Opus) revisa los hallazgos — duplicados, severidades,
reproducibilidad — y escribe el informe final (§6).

---

## 1. Reglas para el ejecutor (leer siempre antes de empezar una sesión)

1. **Solo observar y anotar. Nunca arreglar.** No edites código, no hagas commits, no leas el código fuente para
   explicar un fallo. Tu trabajo es describir lo que vería un usuario, con precisión suficiente para reproducirlo.
2. **Anota en el momento.** Después de *cada* caso (o de cada grupo de 3–5 controles sencillos) escribe la fila en
   `bitacora/BITACORA.md`. Si la sesión se corta, la bitácora debe decir exactamente dónde retomar.
3. **Cada `FALLA` o `RARO` lleva hallazgo y evidencia**: un `H-###` en `bitacora/HALLAZGOS.md` (plantilla incluida) y
   una captura con `qa/herramientas/captura.sh`. Copia los mensajes de error **literalmente**.
4. **Los números no se juzgan a ojo.** Toda exportación se valida con `inspeccionar.py`, y las tablas de resultados
   del demo con `comparar_con_referencia.py`. Los valores esperados independientes están en
   `referencia/ORACULOS.txt`.
5. **¿Sigue viva la app?** Después de cada caso que haga trabajo pesado (rastrear, exportar, importar, live), y siempre
   que la ventana no responda, ejecuta `pgrep -fl mANY-MAZE`. Si se cerró: hallazgo **S1**, guarda las últimas 80
   líneas de `salidas/consola.log` en el hallazgo, vuelve a lanzarla (§3.3) y continúa con el siguiente caso.
6. **Tiempos.** Si una acción tarda más de 30 s sin barra de progreso, o más de 5 min en total, anótalo
   (*Observación*, o S2 si parece colgada). Para decidir que está colgada espera 3 min sin cambios en pantalla.
7. **Solo dentro de la carpeta de prueba.** Crea, guarda y exporta únicamente en `$QA` (la carpeta que creó
   `preparar_prueba.py`). Nunca escribas en `~/Downloads/Ratas` (fuente de los videos reales, solo lectura), ni en
   el repositorio, ni en Documentos del usuario. Si un diálogo propone otra carpeta, cámbiala.
8. **Nada sale de la máquina.** El correo va solo al servidor falso (`127.0.0.1:2525`). *Install…* del modelo de
   pose descarga 23 MB de internet: hazlo **solo si el usuario lo autorizó al iniciar la sesión**; si no, `BLOQUEADO
   (requiere permiso de descarga)`. *Check for updates* solo consulta GitHub: está permitido.
9. **Sin hardware real.** No hay cámara ni placas I/O: usa archivos de video como cámaras simuladas y dispositivos
   *simulated*. Lo que de verdad necesita hardware se anota `N/A (sin hardware)`, nunca `OK`.
10. **Un bloque por sesión**, en el orden de §5. Al empezar una sesión: lee este archivo, el bloque que toca, y el
    final de la bitácora y de los hallazgos (para no duplicar). Al terminar: resumen del bloque en la bitácora y
    los IDs del inventario cubiertos en `COBERTURA.md`.
11. **Ante la duda, la especificación es la guía del usuario** (`manymaze/resources/USER_GUIDE.md`, también en
    *Help ▸ User guide*). Si la app hace algo distinto de lo que dice la guía, es un hallazgo (de la app o de la
    guía: anota cuál parece correcto). Si la guía no dice nada, juzga como un usuario razonable.
12. **Prueba el camino feliz y luego intenta romperlo.** Cada control: valor normal → mínimo → máximo → fuera de
    rango → vacío → texto donde va número → caracteres especiales (`ñ á ü 🐭 " ' / \ : ; ,`) → texto muy largo
    (300 caracteres) → cambiar y salir sin guardar → guardar, cerrar, reabrir y comprobar que el valor persiste.
13. **No pidas confirmación al usuario durante la ejecución** salvo para la regla 8; si algo te bloquea, anótalo
    como `BLOQUEADO` con el motivo y sigue con el siguiente caso.

### Qué cuenta como fallo

- La app se cierra, se congela, muestra un *Traceback* o un mensaje técnico (Python, Qt) → **S1/S2**.
- Datos que se pierden: un cambio que no persiste tras guardar y reabrir; un archivo que no vuelve a abrir → **S1**.
- Números incorrectos: exportación ≠ referencia, estadística ≠ oráculo, Check video ≠ oráculo → **S1**.
- Un control que no hace nada, hace otra cosa, o queda habilitado cuando no debería (o al revés) → **S2/S3**.
- Entrada inválida aceptada en silencio, o rechazada sin explicar por qué → **S3**.
- Diálogo que no se cierra con Cancelar / Esc / el botón rojo; foco perdido; ventana fuera de pantalla → **S3**.
- Texto cortado, ilegible en modo oscuro, erratas, iconos rotos, tooltips ausentes en botones solo-icono → **S4**.
- Algo correcto pero confuso, lento, o que un usuario de ANY-maze no encontraría → **Observación**.

---

## 2. Consejos de computer use para esta app

Lo que sigue lo confirmó el piloto (B00, B17, B06 el 2026-10-11): con estas recetas el plan se ejecuta sin atascos.

- La app es Qt (Python). Pide acceso con `request_access` a la aplicación **mANY-MAZE** (si se lanzó desde el código
  fuente aparece como **Python**).
- **Los diálogos de archivos (Abrir / Guardar / Elegir carpeta) son de otro proceso de macOS**, no de la app: las
  herramientas `app_*` los rechazan («belongs to a different process (an Open/Save panel)»). Antes del primer caso
  que abra uno, pide `request_full_control` y usa las herramientas de pantalla completa mientras el diálogo esté
  abierto. Afecta a B00-04, B01, B05, B06, B10, B12 y a todo B17.
- **Recetas para los diálogos de archivos:**
  - *Abrir* un `.mmaze` o un archivo: `Cmd+Shift+G` → escribe o pega la **carpeta** → `Enter` → selecciona el
    archivo (o, para un `.mmaze`, pulsa *Open* directamente: es una carpeta) → *Open*.
  - *Guardar*: `Cmd+Shift+G` → la **carpeta** → `Enter` → `Cmd+A` en el campo *Save As* → el **nombre** (solo el
    nombre, sin ruta) → `Enter`. Escribir una ruta completa en *Save As* abre otro «Ir a la carpeta» y no guarda; con
    triple clic puede quedar texto suelto en el nombre (`open_field.csvUsers.csv`): comprueba el nombre antes de
    `Enter`.
  - Varios archivos: `Cmd+2` (vista de lista) y `Shift+clic`.
  - Rutas largas o con acentos: `write_clipboard` + `Cmd+V`.
- **Atajos del sistema:** `Cmd+Q` y `Cmd+W` pueden quedar bloqueados por la herramienta aunque `request_access` con
  `systemKeyCombos` diga «granted». Sal por el menú de la app **mANY-MAZE ▸ Quit mANY-MAZE**; si un caso pide
  probar el atajo y no se puede, `BLOQUEADO` (no es fallo de la app).
- **Menús de macOS:** *About mANY-MAZE*, *Settings…* y *Quit* están en el menú de la aplicación (**mANY-MAZE**), no
  en *Help* ni en *File*, como en toda app de Mac. Los atajos que la guía escribe `Ctrl+…` son `Cmd+…` en macOS
  (`Cmd+1`…`Cmd+8` del menú *Go* llevan a cada página).
- La **cinta** (ribbon) cambia según la página; si no cabe en la ventana se desplaza de lado (barra bajo la cinta,
  o la rueda del ratón sobre ella). Los nombres exactos de cada botón están en `INVENTARIO_UI.md`; usa
  `app_ax_find` / la accesibilidad para encontrarlos por nombre antes que por coordenadas.
- **Ventana:** debe caber en la pantalla con cualquier página (antes del arreglo de H-001 medía 1588 × 982 pt en una
  de 1440 × 900 y había que arrastrarla). Mídela con `swift qa/herramientas/ventanas.swift` (B00-10).
- Tras cada acción que abre o cierra un diálogo, espera ~2 s antes de capturar: `app_list_windows` y las capturas van
  con una acción de retraso. Los diálogos modales pueden aparecer detrás o en otro escritorio (usa
  `app_list_windows` / `app_bring_to_current_space`).
- Las tablas se editan con doble clic en la celda, escribir, `Enter`. Para seleccionar varias filas: `Cmd+clic` o
  `Shift+clic`. En listas desplegables largas cuya posición cambia (p. ej. *Randomised* en B06-05), escribe las
  primeras letras de la opción en vez de hacer clic en una posición.
- Capturas: `qa/herramientas/captura.sh` captura **solo las ventanas de la app** (no tu conversación ni el resto de
  la pantalla), enteras aunque se salgan de la pantalla.
- La accesibilidad de Qt escribe en la consola avisos como `qt.accessibility.table: Cell requested for row … is out
  of bounds`: los provoca la herramienta al leer las tablas, **no son hallazgos**.

---

## 3. Preparación (una vez, antes de B00)

### 3.1 Carpeta de prueba

```bash
cd ~/Downloads/many-maze
.venv/bin/python qa/herramientas/preparar_prueba.py --segundos 60
```

Tarda unos 5 minutos (crea y rastrea los oráculos). Antes, si la interfaz cambió desde la última vez, regenera el
inventario (≈6 min; la app no se abre en pantalla y no sale a internet):

```bash
.venv/bin/python qa/herramientas/generar_inventario.py
```

Imprime la carpeta creada (p. ej. `~/mANY-MAZE-QA/2026-10-12_0930`). En esta guía se llama **`$QA`**. Contenido en
la cabecera de `preparar_prueba.py`; lo importante:

| Ruta | Para qué |
|---|---|
| `$QA/entradas/demo_original/` | demo limpio; **no se abre nunca**: se copia a `$QA/experimentos/` cuando un caso lo pide |
| `$QA/entradas/videos_reales/` | 10 recortes de 60 s (OFT, EPM, MWM, FST) de los videos reales; `ORIGENES.txt` dice de cuál viene cada uno |
| `$QA/entradas/videos_limite/` | videos vacíos, corruptos, truncados, de 1 cuadro, con nombres raros, partes y listas M3U |
| `$QA/entradas/importar/` | archivos para importar, buenos y malos |
| `$QA/entradas/solo_lectura/` | carpeta sin permiso de escritura |
| `$QA/referencia/` | resultados del demo por línea de comandos, `ORACULOS.txt`, `analisis_independiente/` (videos reales) |
| `$QA/oraculos/trayectorias/` | 34 experimentos de verdad conocida, cada uno con su `esperado.csv` (B17) |
| `$QA/oraculos/estadistica/` | experimento de diseño conocido y `ESPERADO_ESTADISTICA.md` (B11) |
| `$QA/entradas/version_anterior/` | un demo creado por la versión v0.2.0 y sus resultados (B01-40) |
| `$QA/bitacora/` | **aquí se escribe todo**: bitácora, hallazgos, cobertura, informe final |
| `$QA/evidencias/` | capturas |
| `$QA/experimentos/` | los experimentos que se crean durante la prueba |
| `$QA/salidas/` | exportaciones, registro de consola, correos recibidos, ajustes aislados |

Para copiar el demo limpio cuando un caso lo pida:

```bash
cp -R "$QA/entradas/demo_original/Demo open field.mmaze" "$QA/experimentos/demo_B10.mmaze"
```

### 3.2 Versión de la app

Se prueba la app empaquetada (`dist/mANY-MAZE.app`), que es lo que usa el laboratorio. Si es más vieja que el último
commit (`stat -f %Sm dist/mANY-MAZE.app` vs `git log -1 --format=%cd`), reconstrúyela antes con
`scripts/build_macos.sh` y anótalo en `ENTORNO.txt`.

### 3.3 Lanzar la app (siempre así)

Con ajustes aislados (no toca las preferencias reales del usuario) y la consola guardada:

```bash
QA=~/mANY-MAZE-QA/<carpeta>
MANYMAZE_SETTINGS="$QA/salidas/ajustes.ini" \
  ~/Downloads/many-maze/dist/mANY-MAZE.app/Contents/MacOS/mANY-MAZE >> "$QA/salidas/consola.log" 2>&1 &
```

(Ejecútalo con la herramienta Bash en segundo plano.) Tras cada cierre inesperado, vuelve a lanzarla igual y anota
en la bitácora `RELANZADA`. Después de cada bloque busca errores en la consola:

```bash
grep -n -i -E "traceback|error|exception|warning" "$QA/salidas/consola.log" | tail -40
```

Todo *Traceback* nuevo es un hallazgo aunque la ventana no mostrara nada (S2 si coincide con una acción, S3 si no).

### 3.4 Servidor de correo falso (solo en B10 y B09)

```bash
~/Downloads/many-maze/.venv/bin/python ~/Downloads/many-maze/qa/herramientas/smtp_local.py "$QA" --puerto 2525
```

En segundo plano. Los correos recibidos quedan en `$QA/salidas/correos/`.

### 3.5 Evidencias

```bash
~/Downloads/many-maze/qa/herramientas/captura.sh "$QA" B05-12 "error al importar"
```

---

## 4. Convenciones

- **Casos**: `Bxx-yy` (bloque-caso). Cada caso dice *Pasos*, *Esperado* y, cuando aplica, *Límites* (variantes que
  hay que intentar romper). Un caso con varios límites lleva una fila de bitácora por límite (`B05-12a`, `B05-12b`…).
- **Controles**: cada caso indica las secciones de `INVENTARIO_UI.md` que cubre. Al probar un control, anota su ID.
- **Hallazgos previos**: `HALLAZGOS_PREVIOS.md` (H-P01, H-P02…). Al verificarlos anota `FALLA H-P0x (sigue)` u
  `OK H-P0x (ya no ocurre)`; no abras uno nuevo para lo mismo.
- **Estados**: `OK`, `FALLA`, `RARO`, `BLOQUEADO`, `N/A` (ver plantilla de bitácora).
- **Severidades**: S1–S4 y Observación (ver plantilla de hallazgos).
- **Persistencia**: los casos marcados **[P]** exigen guardar (`Cmd+S`), cerrar el experimento, reabrirlo y comprobar
  que el cambio sigue ahí. Es la verificación más importante del plan.
- **Modo oscuro**: los casos marcados **[O]** se repiten con *View ▸ Appearance ▸ Dark* y se comprueba legibilidad.

---

## 5. Orden de los bloques (una sesión por bloque)

| Bloque | Archivo | Qué cubre | Duración aprox. |
|---|---|---|---|
| B00 | `bloques/B00_arranque.md` | arranque, ventana, menús, apariencia, ayuda, actualizaciones | 30 min |
| B01 | `bloques/B01_experimentos.md` | nuevo / abrir / guardar / guardar como / cerrar / recientes / bloqueo / copias / archivo zip / informe de protocolo, las 23 plantillas | 2 h |
| B02 | `bloques/B02_usuarios_seguridad.md` | usuarios, contraseñas, administrador, protocolo bloqueado, experimento cifrado, programas permitidos | 1 h |
| B03 | `bloques/B03_protocolo.md` | Protocol ▸ Protocol, Animal tracking, Stages, Keys, Analysis, Calculations | 3 h |
| B04 | `bloques/B04_aparatos.md` | diseñador de aparatos: plantillas, herramientas, zonas, puntos, líneas, grupos, rejillas, secuencias, calibración, lente, importar/exportar, imagen del mapa | 3 h |
| B05 | `bloques/B05_animales.md` | tratamientos, animales, campos, importar/exportar, retirar, pesar, aleatorizar, dosis, criterios, ciego | 2 h |
| B06 | `bloques/B06_programa_pruebas.md` | Test schedule: añadir pruebas (videos buenos y rotos), Schedule…, rastrear, Check video, importar tracks, estados, unir videos | 3 h |
| B07 | `bloques/B07_revisar_puntuar.md` | Review and score: reproducción, superposiciones, detección, puntuación con teclas y botones, TakeNote, edición del track, zonas móviles, posición del aparato | 3 h |
| B08 | `bloques/B08_pruebas_en_vivo.md` | Run tests: una prueba, varias, solo observación, modos de inicio, pausa, grabación, ajustes en vivo, recuperación tras un cierre | 3 h |
| B09 | `bloques/B09_procedimientos_hardware.md` | editor de procedimientos, dispositivos I/O simulados, cámaras operantes, modo solo I/O, pantalla táctil, sincronización, plug-ins de análisis, modelo de pose | 3 h |
| B10 | `bloques/B10_resultados.md` | Results ▸ Data: selección de datos, hoja, todas las exportaciones contra la referencia, informes, correo, gráficos de track, mapas de calor, charts, exportación de video, periodos y cálculos en resultados | 3 h |
| B11 | `bloques/B11_estadistica.md` | Statistics: las 5 vistas, procedimientos, gráficos, guardado, contra los oráculos | 2 h |
| B12 | `bloques/B12_importar_anymaze.md` | importar desde ANY-maze: tablas, XML, mapas de zonas, asistente de columnas | 1 h |
| B13 | `bloques/B13_videos_reales.md` | validación científica con videos reales: OFT, EPM, MWM, FST de principio a fin | 3 h |
| B14 | `bloques/B14_robustez.md` | casos límite transversales: cierres, interrupciones, archivos que desaparecen, dos instancias, tamaño de ventana, teclado, rendimiento, persistencia completa | 2 h |
| B15 | `bloques/B15_barrido_cobertura.md` | recorrer todo ID del inventario aún sin probar | variable |
| B16 | `bloques/B16_linea_de_comandos.md` | `manymaze` por línea de comandos y coherencia con la interfaz | 45 min |
| B17 | `bloques/B17_oraculo_trayectorias.md` | **verdad conocida**: 34 experimentos (23 plantillas + funciones, varios animales, zonas móviles, I/O), ≈16 000 valores esperados, 96 % de los tipos de medida | 4 h |
| — | `plantillas/INFORME_FINAL.md` | cierre: resumen, cobertura, los 10 hallazgos más graves | 30 min |

## 5.1 Prioridades y pasadas

No es realista ejecutar todo con todos los valores límite de una vez: son miles de acciones. Por eso hay **dos
pasadas**, y una ejecución que se quede a medias sigue siendo útil.

- **Pasada 1 (imprescindible):** los casos P1 de todos los bloques, en este orden: B00 → B17 → B11 → B01 → B06 →
  B07 → B10 → B13 → B08 → B03 → B04 → B05 → B02 → B09 → B12 → B14 → B16. En los controles que tocan esos casos, la
  regla 12 completa.
- **Pasada 2 (resto):** todos los demás casos, y después B15 (barrido del inventario). En los controles que solo
  aparecen aquí, basta con un valor normal, uno fuera de rango y la persistencia (si es un ajuste).

Casos P1 por bloque (todos los demás son P2):

| Bloque | P1 |
|---|---|
| B00 | 01, 02, 04, 06, 10, 12 |
| B01 | 01, 02, 03, 10, 11, 13, 14, 20, 32, 33, 40 |
| B02 | 03, 05, 06, 09, 10 |
| B03 | 02, 03, 06, 20, 25, 30, 40, 41, 50, 51, 60, 61 |
| B04 | 02, 03, 10, 20, 30, 31, 32, 33 |
| B05 | 01, 10, 14, 16, 22, 23, 30 |
| B06 | 01, 02, 05, 10, 11, 13, 20, 21, 22, 28 |
| B07 | 01, 06, 07, 10, 20, 26, 30, 41, 42, 46 |
| B08 | 01, 02, 03, 04, 06, 20, 21, 30, 31, 40 |
| B09 | 01, 02, 07, 10, 12, 20, 40, 41 |
| B10 | 01, 02, 10–20, 30, 31, 45 |
| B11 | 01–10 |
| B12 | 01, 04, 05, 06, 07 |
| B13 | 05, 11, 21, 31, 50, 51, 54 |
| B14 | 01, 03, 07, 13, 30, 40, 41 |
| B16 | 04 |
| B17 | todos |

---

## 6. Cierre

Al terminar todos los bloques (o la pasada 1, si no hay tiempo para más), rellena `$QA/bitacora/INFORME_FINAL.md` (plantilla en `qa/plantillas/`) y
comprueba que:

- cada `FALLA`/`RARO` de la bitácora tiene su `H-###`, y cada hallazgo tiene pasos, esperado, observado y evidencia;
- `COBERTURA.md` lista todos los IDs del inventario con estado (los que faltan, explicados);
- el registro de consola se revisó (§3.3).

Para limpiar después (la carpeta de solo lectura impide borrar): `chmod -R u+w "$QA"`.
