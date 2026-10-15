# B14 — Robustez y casos límite transversales

Casos que cruzan toda la app. Usa copias del demo (`$QA/experimentos/robustez_N.mmaze`).

## Interrupciones y archivos que cambian por debajo

| Caso | Pasos | Esperado |
|---|---|---|
| B14-01 | Durante *Track all untracked*: `Cmd+Q`. | Pregunta; si se confirma, sale; al reabrir, las pruebas a medias no quedan corruptas (pending o tracked, nunca un track roto). |
| B14-02 | Durante una exportación grande (XML con tracks, video export): cierra el experimento. | Se cancela limpiamente o espera; ningún archivo a medias sin aviso. |
| B14-03 | Con el experimento abierto, renombra con Bash un video de una prueba (`mv`). Abre esa prueba en Review y rastréala. | Mensaje claro de video que falta; no se cierra. Restaura el nombre: vuelve a funcionar. |
| B14-04 | Con el experimento abierto, mueve **la carpeta del experimento** (`mv`) y luego guarda. | Error claro o guarda en algún sitio razonable (anota dónde); nunca pierde datos en silencio. |
| B14-05 | Con el experimento abierto, borra `tracks/` con Bash; ve a Results. | Las pruebas sin track se muestran como tales; sin cierre. |
| B14-06 | Con el experimento abierto, edita `project.json` con Bash (cambia el nombre) y guarda desde la app. | La app no detecta el cambio externo (normal) o avisa; anota qué versión gana. |
| B14-07 | `kill -9` en medio de un *Save*. Reabre. | El experimento abre (escritura atómica) o se ofrece la copia de seguridad. |
| B14-08 | Disco: exporta a un volumen casi lleno si existe; si no `N/A`. | |

## Interfaz

| Caso | Pasos | Esperado |
|---|---|---|
| B14-10 | Doble y triple clic rápido en botones que abren diálogos (Add tests from videos, Select data, Schedule…). | Un solo diálogo. |
| B14-11 | Cada diálogo de la lista del inventario: se cierra con Esc, con Cancel y con el botón rojo; ninguno queda detrás de la ventana principal. | |
| B14-12 | Navegación solo con teclado en 3 páginas (Tab, Shift+Tab, Enter, espacio, flechas en tablas). | Foco visible; se puede operar. |
| B14-13 **[O]** | Modo oscuro: recorre **todas** las páginas y sub-páginas del explorador (≈30) con una captura de cada una. | Todo legible; anota cada pantalla con contraste pobre. |
| B14-14 | Ventana en el tamaño mínimo y en pantalla completa, en todas las páginas. | Nada inaccesible. |
| B14-15 | Cambia de página con un campo a medio escribir (sin Enter) en Protocol, Apparatus y Animals; vuelve. | El valor se guardó (o se descartó de forma obvia). |
| B14-16 | Pulsa cada botón de la cinta de cada página **sin selección** y **con selección múltiple**. | Deshabilitado o mensaje claro; nunca error. |
| B14-17 | Textos: busca erratas, textos cortados con «…», tooltips ausentes en botones de solo icono, mezcla de idiomas. | Anota cada uno (S4). |

## Rendimiento y escala

| Caso | Pasos | Esperado |
|---|---|---|
| B14-20 | Experimento con 1000 animales (`animales_1000.csv`) y `Schedule…` 1000 × 1 × 1. Recorre Experiment, Test schedule, Results. | Usable (anota tiempos de carga y de scroll). |
| B14-21 | 50 etapas × 10 ensayos para 10 animales. | Usable. |
| B14-22 | 46 claves, 100 zonas (rejilla 10×10), 20 procedimientos. Results con todas las medidas. | Usable; anota el tiempo de *Recalculate*. |
| B14-23 | Abre y cierra el demo 10 veces seguidas; mira la memoria con `ps -o rss= -p $(pgrep -f mANY-MAZE)` al principio y al final. | Sin crecimiento grande (anota los valores). |

## Matriz de persistencia

| Caso | Pasos | Esperado |
|---|---|---|
| B14-30 | En una copia del demo cambia **un valor de cada página y sub-página** (≈30 cambios, anótalos en una tabla). Guarda, cierra la app, relanza, abre. Comprueba los 30. | Los 30 persisten. Cada uno que no, hallazgo S1. |
| B14-31 | Igual pero con *Save as* a otra carpeta y abriendo la copia. | Los 30 están en la copia. |
| B14-32 | Igual pero con *Archive experiment* + *Open archive*. | Los 30 están. |

## Mensajes y errores de la exploración automática

| Caso | Pasos | Esperado |
|---|---|---|
| B14-40 | Sección del inventario «Mensajes de la app que hay que provocar al menos una vez» (≈94): para cada uno, deduce de su texto y de la función indicada qué lo provoca (entrada inválida, archivo que falta, confirmación antes de borrar…) y **provócalo**. Una fila por mensaje. | El mensaje aparece cuando debe, se entiende (dice qué pasó y qué hacer), está bien escrito, y tras cerrarlo la app sigue usable. Uno que no consigas provocar en 10 min → `BLOQUEADO` con lo que intentaste. |
| B14-41 | Sección «⚠ Errores (excepciones) al pulsar» del inventario, si existe (y `bitacora/errores_al_pulsar.txt`): cada uno salió al pulsar algo con la app sin pantalla. Reprodúcelo en la app real con la ruta indicada. | Si se reproduce: hallazgo (S2 si la ventana muestra un error, S3 si solo sale en `consola.log`). Si no: `RARO` con la nota «solo sin pantalla». |
| B14-42 | Sección «Mensajes que aparecen al pulsar sin preparar datos»: revisa que cada uno sea claro para un usuario nuevo. | Ningún mensaje técnico (nombres de Python, rutas internas, «None»). |
| B14-43 | Reposo del Mac durante una prueba en vivo de 3 min: **solo si el usuario lo pide y lo hace él** (cerrar la tapa / menú Apple ▸ Reposo; el Mac mini puede tener una sesión remota que se cortaría). Si no, `N/A`. | Al despertar la prueba sigue o termina con un motivo claro; los datos hasta el reposo se conservan. |
