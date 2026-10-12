# B13 — Validación con videos reales (flujo completo de laboratorio)

Este bloque usa la app **como en el laboratorio**, de principio a fin, con los videos reales de ratas, y compara las
medidas con el análisis independiente previo. **Ese análisis es una referencia, no la verdad** (`LEEME.txt`): una
diferencia grande no prueba que mANY-MAZE esté mal, pero hay que anotarla, mirar el track en Review y decir cuál
parece correcto viendo el video.

Este bloque complementa a B17: B17 dice si las medidas se calculan bien con verdad conocida; este dice si el
rastreo funciona con videos reales, difíciles (reflejos, agua, ratas de distinto color).

**Precondiciones:** `referencia/analisis_independiente/` (lo crea `preparar_prueba.py`). Para comparar, rastrea los
**videos originales completos** (rutas en la columna `video_original` y en `videos_reales/ORIGENES.txt`); solo se
leen, nunca se modifican. Los recortes de 60 s sirven para ensayos rápidos.

Antecedente conocido (prueba del 2026-10-11): en el MWM con luz blanca la detección por defecto saltaba al reflejo
del agua (distancias 3–7× más altas) y en el FST la inmovilidad salía ~0 s. Hay arreglos posteriores (eliminación de
saltos por defecto, inmovilidad del FST desde el forcejeo). **Este bloque verifica si esos problemas siguen.**

## Campo abierto (OFT)

| Caso | Pasos | Esperado |
|---|---|---|
| B13-01 | *New experiment* `OFT real`, protocolo *Open field*, duración 300 s. Fondo: video original de `OFT_macho_1`. **Dos arenas por video** (columna `lado` izq/der): crea dos aparatos *From template* (uno por lado) con su tamaño real (pregunta al usuario si no lo sabes; si no, usa 100 cm y anótalo como supuesto). | Dos aparatos sobre el mismo video. |
| B13-02 | Animales y tratamientos según la referencia (columna `animal`, `grupo`). *Add tests from videos* con los 3 originales OFT, una prueba por lado y animal. | Las pruebas que comparten video se rastrean en una sola pasada. |
| B13-03 | *Track all untracked*. Cronometra. | Sin errores. |
| B13-04 | Review: mira cada prueba a 4×; anota saltos, pérdidas, cabeza/cola invertidas. | |
| B13-05 | Exporta CSV y compara a mano con `OFT_01_por_animal.csv`: distancia (cm), % centro, entradas al centro, % periferia, inmovilidad. Tabla en la bitácora: medida, mANY-MAZE, referencia, diferencia %. | Diferencias < 15 % en distancia y % centro (si hay más, investiga en Review y anota). |

## Laberinto en cruz elevado (EPM)

| Caso | Pasos | Esperado |
|---|---|---|
| B13-10 | Experimento `EPM real`, plantilla *Elevated plus maze* sobre `EPM_macho_1` original, 300 s. Pruebas con los 2 originales EPM. Rastrear. | |
| B13-11 | Compara con `EPM_01_por_animal.csv`: % abiertos, % cerrados, % centro, entradas a abiertos/cerrados, latencia al primer abierto, distancia. | Diferencias razonables; revisa en Review las grandes. |

## Laberinto acuático de Morris (MWM)

| Caso | Pasos | Esperado |
|---|---|---|
| B13-20 | Experimento `MWM real`, plantilla *Morris water maze* sobre `MWM_hembra_1` (piscina 150 cm: ≈4,27 px/cm a 1280×720; la piscina es la mayor región azul). Pruebas con los 3 originales MWM, ajustes de detección **por defecto**. Rastrear. | |
| B13-21 | Compara con `MWM_01_por_ensayo.csv`: distancia, velocidad media, latencia a la plataforma, % tigmotaxis, % cuadrantes. **Repite la prueba del antecedente**: ¿la distancia por defecto sale 3–7× más alta? | Si sigue saltando al reflejo: hallazgo S1 con capturas de Review. |
| B13-22 | Con *Remove jumps faster than* y/o detección por color (`#f4d29f`) como en el antecedente; vuelve a rastrear y comparar. | Anota cuánto mejora. |
| B13-23 | Zona móvil: mueve la plataforma en una prueba (B07-44) y verifica la latencia. | |

## Nado forzado (FST)

| Caso | Pasos | Esperado |
|---|---|---|
| B13-30 | Experimento `FST real`, plantilla *Forced swim*, *Detect immobility: from the struggle*, con los 2 originales FST (vista lateral). Rastrear. | |
| B13-31 | Compara inmovilidad (s y %), latencia a la primera inmovilidad y episodios con `FST_01_por_animal.csv` (referencia dudosa en FST). Repite la prueba del antecedente: ¿inmovilidad ≈ 0 s? | Si ≈ 0 s: S1. Si no, mira el video en Review y juzga si los episodios marcados son inmovilidad real. |
| B13-32 | *Forced swim: split the struggle into climbing and swimming*. | Medidas de trepar / nadar presentes y coherentes. |

## Cierre del bloque

| Caso | Pasos | Esperado |
|---|---|---|
| B13-40 | Statistics en cada experimento: compara Control vs HFD en la medida principal (% centro, % abiertos, latencia, inmovilidad). | Corre sin errores. Anota p y tamaño de efecto (con n tan pequeño no se espera significancia). |
| B13-41 | HTML report de cada experimento. | Válido. |
| B13-42 | Tiempos de rastreo: tabla con video, duración, tiempo de rastreo, fps efectivos. | Para la sección de rendimiento del informe. |

## Verdad anotada a mano (la referencia más fuerte disponible sin ANY-maze)

El análisis independiente de `analisis_conductual/` es otro programa, no la verdad. La verdad de un video real la da
una persona marcando dónde está el animal. **Esto lo hace el usuario** (unos 10 min por video, 100 fotogramas); el
ejecutor prepara los comandos, compara y anota.

| Caso | Pasos | Esperado |
|---|---|---|
| B13-50 | El usuario anota 100 fotogramas de cada video original que se rastreó en B13-03/10/20/30 (centro del cuerpo y nariz; ver la ayuda de la herramienta): `.venv/bin/python qa/herramientas/anotar.py "<video original>" --n 100 --salida "$QA/salidas/B13/anot_<prueba>.csv"` | 4 archivos de anotaciones |
| B13-51 | Para cada uno: `.venv/bin/python qa/herramientas/comparar_anotaciones.py "$QA/experimentos/<experimento>.mmaze" "$QA/salidas/B13/anot_<prueba>.csv" --prueba <ID>`. Pega el resumen en la bitácora. | Mediana del centro ≤ 1 cm, ≥ 90 % a ≤ 2 cm, inversiones ≤ 5 %, sin detecciones perdidas. Lo que no se cumpla: hallazgo con los números y una captura de Review en los peores fotogramas (`*_comparado.csv`, columna `error_centro_px`). En el MWM y el FST, compara además con los ajustes que mejoraron el rastreo en B13-22. |
| B13-52 | (Opcional, recomendado) Otra persona anota los mismos fotogramas (misma `--semilla`) en otro archivo, y se comparan las dos anotaciones entre sí con el mismo script tratando una como «rastreo» (o a mano en una hoja). | Da el margen humano: si la app se aleja de una persona tanto como dos personas entre sí, el rastreo es tan bueno como la anotación manual. |
| B13-53 | *Rearing* en video real (H-P09): en el OFT original de B13-01, activa *Detect rearing automatically*, recalcula, y en *Review and score* cuenta a mano los rears de los primeros 3 min (puntúa con una tecla *point* cada vez que la rata se levanta). Compara *Rears* (automático) con tu recuento. | Coinciden ±20 %. Si el automático da muchos menos (o 0), confirma H-P09 en video real: anota ambos números. |
| B13-54 | **Orientación de la cabeza en video real (H-P01; decisivo).** Para cada video original de B13: `.venv/bin/python qa/herramientas/cabeza_real.py "<video original>"` (≈ 1–3 min cada uno; sin interfaz). Los OFT con dos arenas se miden por separado solos (recortes conocidos); en MWM y FST añade `--recorte X Y ANCHO ALTO` con el rectángulo de la piscina o del cilindro (léelo en *Protocol ▸ Apparatus*). Pega la línea de cada arena. Luego, en *Review and score* del EPM rastreado en B13, avanza cuadro a cuadro en 3 momentos en que la rata camina y mira dónde dibuja la app la cabeza. | «invertida sostenida» `OK` (≤ 5 %) en todas las arenas. Con los arreglos, el 2026-10-12 daba 0,0 % en las 8 arenas de EPM y OFT (`mac-ci`: 23–60 % en 5 de 8). Si alguna da `FALLA: H-P01`, adjunta 2 capturas de Review con la cabeza dibujada en la cola. En agua el criterio es orientativo |
