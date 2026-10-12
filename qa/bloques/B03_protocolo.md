# B03 — Protocol ▸ Protocol: protocolo, seguimiento, etapas, claves, análisis y cálculos

**Inventario:** todas las secciones «Protocol ▸ Protocol …» salvo *Procedures* y *Hardware* (van en B09).
**Precondiciones:** copia nueva del demo en `$QA/experimentos/protocolo.mmaze`. Para cada control: aplica la regla 12
del plan (normal, mínimo, máximo, fuera de rango, vacío, texto, especiales). Los cambios de análisis se verifican en
*Results ▸ Data* (columna afectada) — exporta CSV antes y después y compáralos con `comparar_con_referencia.py`
(deben diferir **solo** en las columnas esperadas).

## Protocol (elemento «Protocol»)

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B03-01 **[P]** | Cambia *Protocol name* y *Description*. | Título de ventana / informes se actualizan. | Vacío, 300 caracteres, emoji, saltos de línea en la descripción. |
| B03-02 | *Type of test*: recorre los 23 valores. Tras cada cambio mira *Analysis* (opciones específicas: Barnes, CPP, NOR, three-chamber, FST…) y las columnas de resultados. | Las opciones y medidas específicas aparecen/desaparecen coherentemente; nada se rompe con las pruebas ya rastreadas. | Vuelve a *Open field*: resultados idénticos a la referencia. |
| B03-03 | *Mode*: *Video tracking* → *TakeNote* → *Input/output only* → vuelta. | Run tests y Review cambian (sin video en TakeNote / IO only); avisos si hay procedimientos incompatibles. | Cambiar el modo con pruebas rastreadas: ¿qué pasa con sus resultados? |
| B03-04 | *Each test lasts*: 0, 1 s, 30 s, 10 min, 24 h, negativo. | Resultados del demo recortados a esa duración (la columna *Test duration* cambia). | 1 s y 0. |
| B03-05 | *Tests start*: los 3 valores; mira el efecto en el demo (Review: inicio de cada prueba). | Con *When first detected* el inicio se mueve al primer cuadro detectado. | |
| B03-06 | *Blind testing* (casilla y botón de cinta *Testing ▸ Blind testing*). | Tratamientos → códigos (*Group XX99*) en Experiment, Test schedule, Run tests, Review, Results, Statistics, exportaciones. Desmarcar pide confirmación. | Exporta CSV con ciego: columna *Group* = código. |
| B03-07 | *Confirm the animal's ID*: actívala y puntúa una tecla en Review. | Pide ID; uno incorrecto bloquea; el correcto deja puntuar. | ID con mayúsculas/minúsculas distintas, con espacios. |
| B03-08 | *Weigh the animal before each live test*: actívala sin balanza. | Explica que hace falta una balanza o no bloquea; ver B08. | |
| B03-09 | Tabla de terminología (Term / Called / Plural): renombra *test* → `ensayo`, *apparatus* → `laberinto`. | El explorador, la cinta, los encabezados de resultados y estadística usan los nuevos términos. | Vacío, igual a otro término, con acentos. Restaura. |
| B03-10 | Cinta: *Add item* (todas las opciones del menú), *Apply template* (todas), *Apparatus from template*. | Cada opción hace algo visible y coherente; *Apply template* avisa si va a sobrescribir. | Cancelar en cada diálogo. |

## Animal tracking

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B03-20 | Cada control de la sección (≈36): cambia el valor, ve a *Review and score* ▸ *Detection preview* de una prueba del demo y observa el efecto. | El efecto coincide con el nombre del control (umbral, tamaño mínimo, desenfoque, etc.). | Regla 12 para cada número. *Colour of the animal* con `#zzzzzz`, `red`, vacío. |
| B03-21 | *Set the detection up for*: Mouse, Rat, Hooded rat, White animal on sawdust, Zebrafish. | Cada preset cambia un conjunto de valores (anótalos). | |
| B03-22 | *Detect the animal using*: los 3 métodos con el demo, rastrea una prueba con cada uno (*Track this test* en Review). | Los 3 producen un track razonable o un mensaje claro. | |
| B03-23 | *Locate the body parts using: A pose model (AI)* sin modelo instalado. | Avisa que falta el modelo; no se cuelga al rastrear. | *Install…* solo con permiso (regla 8). *Custom ONNX…* con un archivo que no es ONNX (usa `texto_disfrazado.mp4`). *Convert DeepLabCut model…* cancelar y con carpeta vacía. |
| B03-24 | Cinta *Restore defaults*. | Todos los valores vuelven a los de fábrica (compara con capturas de B03-20). | |
| B03-25 **[P]** | Cambia 5 valores, guarda, cierra, reabre. | Los 5 persisten. | |
| B03-26 | Animal casi inmóvil: copia `$QA/oraculos/trayectorias/open_field_casi_inmovil.mmaze`, rastréalo con los ajustes por defecto y verifica con `oraculo_sintetico.py verificar` (B17). Después prueba *Build the background from*: *An adaptive model* y *Detection threshold* manual, y vuelve a rastrear. | Con el fondo «mediana» el animal quieto el 80 % del tiempo se funde con el fondo: se espera *Detection (%)* baja. Lo que se prueba es si la app **lo avisa** (aviso de calidad del rastreo, columna o mensaje) y si la guía explica qué hacer. Sin ningún aviso → hallazgo S2. Anota qué ajuste lo resuelve. | |

## Stages (etapas) y criterios

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B03-30 **[P]** | Escribe 3 etapas, una por línea (`Habituación`, `Día 1`, `Día 2`); *New stage* en la cinta. | Aparecen en Schedule…, Test schedule y Statistics. | 50 etapas (pega 50 líneas); 51 (debe limitar o avisar); líneas vacías, duplicadas, de 300 caracteres. Renombrar una etapa que ya usan pruebas. |
| B03-31 | *New criterion*: etapa `Día 1`, medida *Total distance (cm)*, `<`, 300, 2 consecutivos, *Stage completed*, retirar tras 5. | Fila válida. | Medida inexistente, valor texto, consecutivos 0/100. |
| B03-32 | Variabilidad: *Variability (%)* ≤ 10 sobre 3 ensayos; *SD*; *CV (%)*. | Se guarda. Comprueba con datos en B05-40 (oráculo: 25, 24, 27 → 5.9 %). | |
| B03-33 | *Delete criterion*, *Remove*, *Add criterion*. | Coherente con la selección. | Borrar sin selección. |

## Keys (claves de puntuación)

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B03-40 **[P]** | *New key*: `Olfateo`, tecla `o`, tipo *Toggle*; otra `Salto`, `s`, *Point*; otra `Lavado`, `l`, *Simple/Hold*; color distinto a cada una. | Aparecen como botones en Review ▸ Scoring y en Run tests. | |
| B03-41 | Teclas inválidas: repetida, espacio, flecha, `Cmd`, dos caracteres, mayúscula de una ya usada. | Problemas listados en rojo bajo la tabla; no se puede usar. | |
| B03-42 | Crea 46 claves (usa letras, dígitos y `- = [ ] ; ' , . / \ \``) y luego una 47.ª. | 46 permitidas; la 47.ª se rechaza o se marca. | Rendimiento de Review con 46 botones (¿se ven todos?). |
| B03-43 | Conjunto exclusivo: pon *Rearing* y *Grooming* en el mismo set. Puntúa en Review: inicia uno y luego el otro. | Iniciar uno cierra el otro. | |
| B03-44 | *This behaviour counts as activity*. | Afecta *Time active* en resultados (con el modo de actividad ANY-maze). | |
| B03-45 | *Duplicate key*, *Delete key* (con y sin eventos puntuados con esa clave). | Borrar una clave usada avisa qué pasa con sus eventos. | |

## Analysis

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B03-50 | Cada control (≈60). Para los que afectan a medidas del demo (suavizado, inmovilidad, zona por centro/cabeza, % del cuerpo, visitas mínimas, latencia nunca ocurrida, promedio de nada, tigmotaxis, intervalos de tiempo), cambia, ve a Results y confirma que cambian **solo** las columnas esperadas (exporta y compara con la referencia). | Coherente; *Restore defaults* devuelve la referencia exacta. | Regla 12 en cada número. |
| B03-51 | *Split each test into time bins of*: 10 s. | Results muestra filas por periodo; coincide con `referencia/demo_resultados_por_intervalos.csv`. | 0, 0.01, mayor que la prueba. |
| B03-52 | *New time period*: `0–10 s`, `10–30 s`; *New event period*: «30 s después de salir de Centre por primera vez». | Aparecen como periodos en Results y Statistics. | Fin < inicio; solapados; evento que nunca ocurre; *Delete time period*. |
| B03-53 | Opciones específicas (Barnes, CPP, NOR, three-chamber, OPAD, end zone): cambia el tipo de test (B03-02) y prueba cada una. | Sin errores; efectos visibles donde haya datos. | Marcar con N/A las que no tienen datos de prueba. |

## Calculations

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B03-60 **[P]** | *New calculation*: `Índice centro` = `100 * {Centre: time (s)} / {Test duration (s)}`, 2 decimales, unidad `%`. Usa *Insert measure…* e *Insert function*. | Nueva columna en Results igual a *Centre: time (%)* del demo (compara). | |
| B03-61 | Fórmulas inválidas: llaves sin cerrar, medida inexistente, `1/0`, `sqrt(-1)`, texto, vacía, recursiva (que se use a sí misma). | Error claro al escribir o celda vacía con explicación; nunca un cierre. | |
| B03-62 | Valores con nombre (*Name / Value*); funciones entre ensayos (count, mean, last trial) con el botón *Functions…*. | Resultados coherentes (con varias pruebas por animal, crea duplicados en B06). | |
| B03-63 | Decimales 0 y 10; ejes Y mín > máx. | Validado. | |
| B03-64 | *Duplicate calculation*, *Delete calculation* (usada en un criterio de entrenamiento). | Avisa de la dependencia. | |
