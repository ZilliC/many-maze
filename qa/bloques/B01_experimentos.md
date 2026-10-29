# B01 — Ciclo de vida de los experimentos

**Inventario:** «Barra de menús» (File), «Pestaña File (con experimento abierto)», diálogos «New experiment».
**Precondiciones:** app abierta sin experimento. Todo se crea en `$QA/experimentos/`.

## Crear

| Caso | Pasos | Esperado | Límites / variantes |
|---|---|---|---|
| B01-01 | *File ▸ New experiment…*: nombre `OF prueba`, carpeta `$QA/experimentos`, protocolo *Open field*, duración por defecto. | Se crea `OF prueba.mmaze/` con `project.json`, `tracks/`, `recordings/`, `exports/`; el título de la ventana muestra el nombre; se habilitan las pestañas. | Comprueba la carpeta con `ls`. |
| B01-02 | Repite B01-01 con **cada protocolo** de la lista (23: open field, EPM, EZM, Y, T, radial, RAPC, water maze, Barnes, NOR, light/dark, three-chamber, fear conditioning, forced swim, tail suspension, novel tank, multi-well, CPP, hole board, thermal gradient, home cage, activity wheel, custom). Nombre `P_<protocolo>`. Tras crear cada uno, mira *Protocol ▸ Protocol* (tipo de test y duración) y *Protocol ▸ Apparatus*. | Cada protocolo propone una duración coherente y queda registrado como tipo de test. Anota en una tabla: protocolo, duración propuesta, si crea aparato, qué ve el usuario. | Una fila de bitácora por protocolo. |
| B01-03 | *File ▸ Create demo experiment* en `$QA/experimentos`. | Barra de progreso; se abre «Demo open field» con 6 pruebas *tracked*, 2 tratamientos (Control/Anxious). | Repite eligiendo la misma carpeta: debe abrir el existente, no duplicarlo ni fallar. |
| B01-04 | Nuevo experimento **basado en otro** (*Based on* → el demo de B01-03). | Copia protocolo, aparato, claves, ajustes; **no** copia animales ni pruebas. Verifícalo en cada pestaña. | Basado en un `project.json` inexistente / en una carpeta que no es experimento / en un experimento cifrado (después de B02). |
| B01-05 | Nombres límite: `Ñandú ü 🐭`, 300 caracteres, `a/b`, `a:b`, `.oculto`, solo espacios, vacío, `CON`, el nombre de un experimento que ya existe. | Los válidos se crean y reabren bien; los inválidos se rechazan con mensaje claro **antes** de crear nada; el existente pide confirmación o se rechaza (nunca sobrescribe en silencio). | Comprueba en Finder/`ls` qué carpeta quedó. |
| B01-06 | Carpeta destino `$QA/entradas/solo_lectura`. | Error claro, sin experimento a medio crear. | |
| B01-07 | Duración: 0, 0.5, 86400, 1e6, negativa, texto. | Rango validado; 0 = sin límite si la guía lo dice. | |

## Guardar, cerrar, abrir

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B01-10 **[P]** | En `OF prueba`, cambia la descripción, guarda con `Cmd+S`, cierra (*Close experiment*), reabre con *Open experiment…*. | La descripción persiste. El título muestra `•` mientras hay cambios sin guardar. | Guarda con el botón de la esquina y con *File ▸ Save*. |
| B01-11 | Cambia algo y cierra sin guardar: prueba *Save*, *Don't save* y *Cancel* en la pregunta. | Cada opción hace lo suyo; *Cancel* deja el experimento abierto con el cambio. | Igual al cerrar la ventana y con `Cmd+Q`. |
| B01-12 | *Open recent*: abre desde la lista (menú y pestaña File). | Abre el elegido. | Borra (con `mv` a otra carpeta) un experimento reciente y elígelo: mensaje claro, se ofrece quitarlo de la lista o similar. Restáuralo después. |
| B01-13 | Abre una carpeta que no es experimento, un `project.json` corrupto (copia el demo y trunca su `project.json` a la mitad con Bash), y un archivo de texto cualquiera. | Mensaje claro; ningún cierre; el experimento actual sigue abierto. | |
| B01-14 **[P]** | *Save as…* del demo a `$QA/experimentos/demo_copia.mmaze`. | Sigue trabajando en la copia (título); la copia tiene `tracks/`; el original no cambia. | *Save as* a su propia carpeta; a `solo_lectura`; sobre otro experimento existente (debe preguntar). |
| B01-15 | *Reveal experiment folder* / *Show in folder*. | Abre Finder en la carpeta. (Si no tienes acceso a Finder, comprueba con una captura de pantalla.) | |

## Bloqueo de uso simultáneo

| Caso | Pasos | Esperado |
|---|---|---|
| B01-20 | Con el demo abierto, lanza una **segunda** instancia de la app (§3.3 de nuevo) y abre el mismo experimento. | Pregunta *read-only* / *open anyway*. En solo lectura: se puede mirar y exportar, *Save* se rechaza con mensaje; *Save as* funciona. Cierra la segunda instancia. |
| B01-21 | Con el demo abierto en la app, en Bash: `.venv/bin/manymaze project "<demo>" track`. | La línea de comandos se niega (experimento abierto en otro sitio). |
| B01-22 | Mata la app con `kill -9` teniendo el demo abierto; relanza y ábrelo. | El bloqueo huérfano se ignora (no pregunta, o pregunta y explica). |

## Copias de seguridad, informe y archivo

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B01-30 | Haz 3 cambios guardando entre ellos (la app guarda como mucho 1 copia cada 10 min: si hace falta, espera o anota el comportamiento). *File ▸ Restore a backup…* | Lista por fecha y hora; restaurar vuelve al estado elegido y hace antes copia del actual. | Sin copias: mensaje claro. Cancelar. |
| B01-31 | *File ▸ Protocol report…* → `$QA/salidas/protocolo.html`; `inspeccionar.py` y ábrelo. | HTML con opciones, etapas, claves, mapa de cada aparato, ajustes cambiados marcados, procedimientos, dispositivos sin contraseñas. | En el demo, en un experimento vacío recién creado y en uno con procedimientos (tras B09). |
| B01-32 | *Archive experiment…* del demo → `$QA/salidas/demo.zip`; `inspeccionar.py` sobre el zip. | Zip íntegro con `project.json`, `tracks/`, los videos; sin `backups/`, sin `.manymaze.lock`. | Experimento con videos fuera de su carpeta (los de `videos_reales`): se copian a `videos/external/`. Destino `solo_lectura`. Cancelar a mitad (si hay barra). |
| B01-33 | *Open archive…* de ese zip en `$QA/experimentos/desde_zip`. | Se descomprime y abre; las pruebas encuentran sus videos; resultados idénticos (exporta y compara con `comparar_con_referencia.py`). | Zip corrupto (trunca uno con Bash), zip que no es experimento, carpeta destino no vacía. |
| B01-34 | Mueve la carpeta del demo (`mv`) con la app cerrada y reábrela desde la nueva ruta. | Los videos se encuentran (rutas relativas). | Mueve también los videos externos: la app debe indicar videos que faltan (la guía dice que no hay botón de reenlazar: confírmalo y anota la experiencia del usuario). |

## Compatibilidad y otros

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B01-40 | Copia `$QA/entradas/version_anterior/Demo v0.2.0.mmaze` a `$QA/experimentos/B01_v020.mmaze` y ábrela (la creó la versión v0.2.0). Recorre todas las páginas; exporta CSV y compáralo con `resultados_v0.2.0.csv` (`comparar_con_referencia.py`). | Abre sin errores ni avisos raros; resultados iguales (las rutas de los videos se ignoran). Si avisa de una conversión, anota el texto. | Guárdala con la versión actual y vuelve a abrirla. |
| B01-41 | Arrastra desde Finder (si tienes acceso) un `.mmaze`, un video y un CSV de animales sobre la ventana, sobre el Test schedule y sobre la tabla de animales. | Si la app no admite arrastrar archivos, no debe pasar nada raro (anótalo como *Observación* de usabilidad: un usuario lo intentará). Si lo admite, hace lo esperado. | |
| B01-42 | Región con coma decimal: **no cambies los ajustes del sistema** (prohibido). Pregunta al usuario si su macOS usa coma decimal; si sí, comprueba que los campos numéricos aceptan y muestran la coma, y que el CSV exportado usa punto (o lo que diga la guía). Si no, `N/A`. | Coherente y documentado. | |
