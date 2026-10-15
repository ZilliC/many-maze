# B09 — Procedimientos, dispositivos I/O (simulados), modo solo I/O y extras

**Inventario:** «Protocol ▸ Protocol ▸ Procedures», «… ▸ Hardware», pestaña *Procedures* de Run tests, diálogos
«I/O devices», «Operant chambers», «Touch screen», «mANY-MAZE touch screen», «Expression functions», «Procedure … as
JSON», «Analysis plug-in: …», «Calibrate …», «Install pose model», «Convert DeepLabCut model».
**Precondiciones:** copia del demo `$QA/experimentos/procedimientos.mmaze`; experimento nuevo `operante.mmaze` (modo
*Input/output only*). Servidor de correo falso en marcha (plan §3.4). **Sin hardware real**: todo con dispositivos
*simulated*; lo que exige hardware → `N/A (sin hardware)`.

## Editor de procedimientos

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B09-01 **[P]** | *New procedure* `Alerta centro`: *when* «Animal enters zone Centre» → *do* «Set variable n = n + 1» → *if* n > 3 → acción (sonido o mensaje). Usa *Add ▾*, *Add inside ▾*, ▲▼→←, *Copy*, *Remove*, *On/off*. | Árbol correcto; sin problemas en la lista roja. | Borrar el padre con hijos; mover fuera de rango; 200 sentencias (rendimiento). |
| B09-02 | Recorre el menú *Add ▾* entero: añade **una de cada** sentencia (when / wait / if / else if / repeat / set / do / call / go to) y para *when* y *do* abre la lista de **eventos** (≈90) y **acciones** (≈99): selecciona cada uno al menos una vez y mira sus campos. | Cada evento/acción muestra sus parámetros con nombres claros; nada lanza error. Anota los que no se entienden. | Es largo: una fila de bitácora por cada 10 eventos/acciones. |
| B09-03 | Expresiones: `1+`, `sqrt(-1)`, `n/0`, variable inexistente, función inexistente, `random(1,6)`, arrays. *Functions…* / *Insert function*. *ANY-maze maths*. | Errores claros al validar; las válidas se aceptan. | |
| B09-04 | *Sub-procedure* y *call*; *go to* a una etiqueta inexistente; recursión. | Detectado en la lista de problemas. | |
| B09-05 | *Edit as text* / *JSON*: edita y vuelve; JSON inválido. | Ida y vuelta sin pérdidas; JSON inválido rechazado con mensaje. | |
| B09-06 | *Duplicate procedure*, *Delete procedure*, *Functions* (cinta). | | |
| B09-07 | Corre una prueba en vivo (B08-02) con `Alerta centro`. Pestaña *Log*. | El log muestra las entradas al centro y la acción tras la 4.ª; variables guardadas como resultados si se marcó. | |

## Dispositivos I/O (simulados)

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B09-10 | *Set up I/O devices…* / cinta *I/O devices*: añade un dispositivo **simulado** de cada tipo que lo permita (Arduino/Firmata/NI/LabJack/serie: si no hay opción simulada, ábrelo, revisa campos y cancela → `N/A`). Canales de entrada y salida, *Test* (pulso de 0,5 s / tono), simular entrada. | Simulados funcionan; los reales sin conexión dan error claro (puerto inexistente). | Nombres duplicados; canal repetido; watchdog 0. |
| B09-11 | Bomba de jeringa simulada: elige jeringa de la lista (131), diámetro manual. | Campos coherentes. | Diámetro 0. |
| B09-12 | Dispositivo de **alertas e-mail**: servidor `127.0.0.1`, puerto `2525`, sin usuario, destino `qa@example.org`. Envía una alerta de prueba si hay botón. | Llega a `salidas/correos/`. | Puerto cerrado (2526): error claro, nada se cuelga. |
| B09-13 | *Operant chambers…*: cada preset (Med Associates, Coulbourn, Lafayette). | Crea dispositivos y canales coherentes. | |
| B09-14 | *Send synchronisation pulses*: salida, inicio/fin, por cuadro, por posición, ancho de pulso. Corre una prueba en vivo con un dispositivo simulado. | El log muestra los pulsos. | |
| B09-15 | *Touch screen settings…* y la ventana *mANY-MAZE touch screen*. | Abre la ventana de estímulos (en esta pantalla); se cierra bien. | |
| B09-16 | *Add plug-in* (plug-ins de análisis): importador CSV/TSV con un archivo de señal (crea uno con Bash: `t,valor` a 100 Hz, 30 s). *Run on the tests*. | Columnas de la señal en Results, por periodo y zona. | CSV sin tiempo; frecuencia distinta. |

## Modo solo I/O (operante)

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B09-20 | En `operante.mmaze` (modo *Input/output only*), preset de cámara operante simulada, procedimiento FR5 (cada 5 presiones de palanca → pellet). *Run tests ▸ One test* → *Arm / Start test*; simula presiones de palanca desde el diálogo de dispositivos o la pestaña Monitor. | Sin cámara; Monitor muestra entradas y salidas; al terminar, prueba *scored* con medidas I/O; Review muestra la línea de tiempo y la lista del registro I/O. | Procedimiento que usa una zona → armado rechazado con la lista de sentencias. |
| B09-21 | *Several tests*: 3 paneles sin cámara (*Add test panel*), uno por cámara operante. *Start all* / *Stop all*. | Cada panel con su dispositivo; armar dos con el mismo se rechaza. | |
| B09-22 | Review del test I/O: filtro de la lista, línea de tiempo, pestaña Results. | | |

## Modelo de pose

| Caso | Pasos | Esperado |
|---|---|---|
| B09-30 | Cinta *Install pose model* / *Install…*: **solo con permiso del usuario** (regla 8). Si se instala: rastrea una prueba del demo con *A pose model (AI)*. | Instalación con progreso; nariz / centro / base de la cola en Review. Si no hay permiso: `BLOQUEADO`. |
| B09-31 | *Custom ONNX…* con un archivo no ONNX; *Convert DeepLabCut model…* con carpeta vacía. | Errores claros. |

## Cada evento, cada acción y cada función (sistemático)

Las listas completas están en el inventario: «Procedimientos: eventos» (90), «… acciones» (99) y «… funciones de
las expresiones» (102), con el ID de cada uno. Usa un experimento aparte (`$QA/experimentos/B09_catalogo.mmaze`,
copia del demo) con un dispositivo I/O **simulado** que tenga entradas y salidas, y una prueba en vivo con un video
del demo como cámara (B08-01).

| Caso | Pasos | Esperado |
|---|---|---|
| B09-40 | **Eventos**, de 10 en 10: un procedimiento con 10 `when <evento>` → `do Write to log «E:<id>»` (y `Increment variable` del contador del evento). Corre una prueba y **provoca** cada evento: zonas (el animal del video entra y sale), teclas (pulsa la tecla), entradas (simúlalas desde el diálogo I/O), temporizadores, inicio / pausa / fin de prueba, etc. | Cada evento provocado aparece en *Log* con su `E:<id>`. Uno que no se puede provocar sin hardware real → `N/A (sin hardware)` con el motivo. Uno que se provocó y no apareció → `FALLA` (S2). Una fila por evento. |
| B09-41 | **Acciones**, de 10 en 10: `when Test starts` → las 10 acciones, cada una seguida de `Write to log «A:<id>»`. | Cada acción se ejecuta sin error: el log muestra su efecto (salida activada, variable cambiada, sonido, ventana emergente…) y el `A:<id>`. Las que necesitan hardware real → `N/A` con el motivo. Las de *Shock* y *Optogenetics* con dispositivo simulado: comprueba que respetan el tiempo máximo. |
| B09-42 | **Funciones**: en una sentencia `Set variable`, escribe una expresión por función con argumentos válidos (p. ej. `abs(-3)`, `min(4, 2, 9)`, `random(1, 6)`), luego con el número de argumentos mínimo − 1 y máximo + 1 (columna *Notas* del inventario), y con texto donde va un número. | Las válidas dan el valor correcto en el log (compruébalo a mano). Las inválidas se marcan en la lista de problemas **antes** de correr la prueba. |
| B09-43 | Un procedimiento con los 11 eventos de *Logic* y las sentencias de control anidadas (`if` dentro de `repeat` dentro de `when`, `go to`, `call` de un sub-procedimiento con argumentos). | Orden de ejecución correcto en el log. |
