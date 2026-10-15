# B07 — Review and score (revisar y puntuar)

**Inventario:** «Test ▸ Review and score — CINTA», pestañas *Results*, *Plots*, *Scoring*, *Detection*, *Track
editing*, «Test ▸ Review and score».
**Precondiciones:** copia del demo `$QA/experimentos/revision.mmaze`; para B07-40 el experimento `videos.mmaze` de B06.

## Reproducción y vista

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B07-01 | *Play*, *Pause*, *Stop*, *Step back*, *Step forward*, *Speed* (cada velocidad). Teclado: espacio, ←/→, Shift+←/→. | El tiempo avanza a la velocidad elegida; los pasos son de 1 cuadro (Shift = 1 s). | Pulsar Play 10 veces rápido; Step back en el cuadro 0; Step forward en el último. |
| B07-02 | Arrastra la barra de tiempo al principio, al final y a mitad. | Video y superposición sincronizados. | |
| B07-03 | *Previous test* / *Next test* y la lista *Test shown*. | Cambia de prueba; en la primera/última se deshabilita lo que corresponda. | |
| B07-04 | *View ▸ Zones*, *Animal*, *Trail*; longitud del trail. | Superposiciones on/off; cabeza roja, cola azul. | Trail 0 y máximo. |
| B07-05 | *Detection preview*. | Máscara del primer plano en vivo. | Activa durante la reproducción. |
| B07-06 **[O]** | Pestañas *Results* (*Filter measures…*, *Copy table*) y *Plots* (track, mapa de calor, velocidad, freezing). | Coinciden con la fila de esa prueba en `referencia/demo_resultados.csv` (comprueba 5 medidas). *Copy table* deja la tabla en el portapapeles. | Filtro sin coincidencias. |
| B07-07 **[P]** | Inicio de la prueba (*= now*) y duración (*End = now*, 0 = por defecto). | Resultados de esa prueba recalculados (exporta y compara: solo cambia esa fila). | Inicio > fin; inicio al final del video. |

## Detección por prueba

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B07-10 | Pestaña *Detection*: cambia umbral y tamaño mínimo para esta prueba con *Detection preview* activo; *Track this test*. | Afecta solo a esta prueba. | |
| B07-11 | *Reset to experiment defaults*, *Save as experiment default*, *Apply to all tests*. | Cada uno hace lo que dice (verifica en Protocol ▸ Animal tracking y en otra prueba). *Apply to all* pide confirmación. | |

## Puntuación

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B07-20 **[P]** | Pestaña *Scoring*: haz clic en el video, reproduce y pulsa `R` (toggle) dos veces, `D` (point) tres veces, mantén `G` si es hold. | Eventos listados con inicio, fin y duración; resultados de la prueba con *Rearing: duration*, *Defecation: count*, etc. | Teclas pulsadas muy rápido; tecla mantenida (no debe repetir); tecla no asignada. |
| B07-21 | Botones en pantalla (*On-screen keys*): mismos eventos con el ratón. | Igual que con teclado; los activos se ven rellenos. | |
| B07-22 | Clic en un evento (salta a él), *Delete selected*, *Clear all*, *Undo*. | Coherente; *Clear all* pide confirmación. | |
| B07-23 | Conjunto exclusivo (B03-43): iniciar uno cierra el otro. | | |
| B07-24 | Cambia de prueba con un comportamiento en curso. | Se cierra en el tiempo actual. | |
| B07-25 **[P]** | *Notes* de la prueba. | Persisten y se ven en Test schedule. | 5000 caracteres, emoji. |
| B07-26 | Puntúa una prueba **sin rastrear** (de `videos.mmaze`). | Estado *scored*; resultados solo de comportamientos. | |

## TakeNote (observación directa)

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B07-30 | Abre una prueba sin video (*Add test* de B06-04). Reloj de observación: *Start*, puntúa, *Pause*, *Resume*, *Stop*. | Prueba *scored* con la duración observada. | Dejar llegar a la duración de la prueba. *Start* otra vez en una puntuada: pregunta si borrar. Con *Confirm the animal's ID* (B03-07). |
| B07-31 | Cinta *Scoring ▸ Start / Pause / Stop*. | Iguales a los botones de la página. | |

## Edición del track

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B07-40 | *Mark position* / *Place (click on video)*: marca la posición en 3 cuadros. | El track cambia en esos cuadros. | Clic fuera de la arena. |
| B07-41 | *Range start* / *Range end* (*= now*), *Interpolate*, *Delete range*. | Tramo interpolado / borrado (no detectado). | Rango vacío; inicio > fin; todo el video. |
| B07-42 | *Remove jumps* en una prueba de un video real (`MWM_hembra_1` de B13 si ya existe, o un OFT). *Undo*. | Saltos eliminados sin volver a rastrear; deshacer restaura. | Con *Remove jumps faster than* = 0. |
| B07-43 | *Swap identities*: necesita una prueba con 2 animales (*Identify several animals by colour marks* o 2 animales por arena). Si no hay datos para ello, `N/A` explicado. | Intercambio en el rango; *Undo*. | |
| B07-44 | *Moveable zones in this test*: mueve la zona móvil (plataforma en un water maze; en el demo hazla *Moveable* en B04-20). | Solo esta prueba usa la nueva posición; resultados de las demás intactos. | |
| B07-45 | *Apparatus position in this test*: *Move right*, *Move down*, *Rotate*, *Scale*, *Reset*. | El mapa se mueve solo en esta prueba. | Valores extremos (rotar 720°, escala 0). |
| B07-46 **[P]** | Guarda, cierra, reabre: correcciones, zonas movidas y posición del aparato persisten. | | |
