# B17 — Oráculo de verdad conocida (34 experimentos, ~16 000 valores esperados)

**Para qué:** es el bloque que detecta **medidas mal calculadas** y **errores del rastreo**. Comparar con la línea de
comandos (B10) solo demuestra que las dos dan lo mismo, porque usan el mismo motor. Aquí el valor correcto lo fija
un recorrido programado y la definición de la guía, no la app.

## Cómo funciona (léelo antes de empezar)

`$QA/oraculos/trayectorias/` tiene 34 experimentos **sin rastrear**, cada uno con un video sintético cuyo recorrido
está programado (entra a tal zona en tal segundo, se para, gira, mete la cabeza, se levanta…) y un `esperado.csv`.
Los valores esperados se calcularon con `qa/herramientas/oraculo_medidas.py` y `oraculo_funciones.py` a partir de
ese recorrido, con las definiciones de la guía (sección 9), sin el código de medidas de la app. Cubren el 96 % de los
tipos de medida que da la app (424 de 440).

La verificación tiene **dos capas**:

| Capa | Compara la app con… | Una diferencia significa… |
|---|---|---|
| **A** (verdad programada) | lo que pasó de verdad en el video | rastreo + cálculo. Muchas diferencias son efectos del rastreo ya conocidos (H-P05, H-P07), guardados al calibrar en `calibracion_capa_A.csv` |
| **B** (cálculo) | las mismas fórmulas aplicadas a la trayectoria **que rastreó la app** | **un error de cálculo**: con la misma trayectoria, la app y la definición no dan lo mismo |

Estados que da el verificador:

| Estado | Qué hacer |
|---|---|
| `OK` | nada |
| `ALTERNATIVA` | la app usa otra definición coherente (variante «impl»; listadas en H-P06). Si es una que H-P06 no lista: hallazgo *Observación* (documentación) |
| `CONOCIDO` | (capa A) diferencia que ya salía al calibrar, con la etiqueta del hallazgo previo que la explica (`[H-P05]` temblor, `[H-P07]` rumbo inicial, `[H-P09]` rearing, `[H-P04]` rotaciones, `[H-P01]`, `[B03-26]`, `[identidades]`). No se listan salvo con `--todo`. Anota en la bitácora cuántas hay por experimento |
| `NO APLICA` | la medida no existe en esa plantilla (p. ej. la rejilla 4×4) |
| `DIFIERE` / `SIN COLUMNA` | **hallazgo**. En la capa B, S1 siempre. En la capa A, S1 salvo que sea claramente un efecto de rastreo nuevo (entonces S2, con captura de Review) |
| `SIN FILA` | falta la fila de un animal o de un periodo en la exportación: casi siempre *Show time periods* sin marcar (paso 3). Si con eso sigue, hallazgo S2 |

Calibración (2026-10-12, motor de la rama `validacion-qa` sin interfaz, con las definiciones que fijan la guía y
ANY-maze ya sin alternativa): **capa B, 0 diferencias** en 16 468 valores (14 978 exactos y 1464 con la variante
«impl» de una decisión que la guía deja abierta); capa A, 0 diferencias nuevas (el resto, efectos de rastreo
conocidos). Si la app no ha cambiado, la interfaz debe dar exactamente lo mismo: cualquier diferencia nueva es de la
interfaz o de la exportación.

## Procedimiento para cada experimento (una fila de bitácora por experimento y capa)

```bash
cp -R "$QA/oraculos/trayectorias/<nombre>.mmaze" "$QA/experimentos/B17_<nombre>.mmaze"
```

1. *File ▸ Open experiment…* → `$QA/experimentos/B17_<nombre>.mmaze`.
2. *Test ▸ Test schedule ▸ Track all untracked* (en `io` no hay nada que rastrear). Espera a que estén *tracked*.
3. *Results ▸ Data ▸ Select data*: marca **todas** las medidas (todas las categorías). Ojo con el grupo
   *Information*: viene **parcialmente** marcado y hay que marcarlo entero (si no, faltan 3–4 columnas y el
   verificador da `SIN COLUMNA`). El panel *Select data* recuerda si estaba abierto: pulsar su botón cuando ya está
   abierto lo cierra. En `funciones` y `open_field_intervalos` hay periodos: marca **Show time periods** (cinta de
   Results, grupo *Time periods*, al final de la cinta) o no salen sus filas (el verificador da `SIN FILA`).
4. *Spreadsheet ▸ Save* → CSV → `$QA/salidas/B17/<nombre>.csv`.
5. Verifica las dos capas:

   ```bash
   cd ~/Downloads/many-maze
   .venv/bin/python qa/herramientas/oraculo_sintetico.py verificar "$QA/salidas/B17/<nombre>.csv" \
       "$QA/oraculos/trayectorias/<nombre>.mmaze/esperado.csv" --experimento "$QA/experimentos/B17_<nombre>.mmaze"
   ```

6. Anota el resumen de cada capa (`== … [A …]: n/m OK, …` y `[B …]`) y el **código de salida** (0 = sin
   `DIFIERE`, `SIN COLUMNA` ni `SIN FILA`). Abre un hallazgo por cada `DIFIERE`, y revisa tus pasos 3–4 ante un
   `SIN COLUMNA` o `SIN FILA` antes de abrirlo (casi siempre es una medida o los periodos sin marcar). Pega la línea
   del verificador; para la capa A, una captura de Review en ese momento del video.

## Los experimentos

| Caso | Experimento | Qué pone a prueba (además de todas las medidas generales, por zona, punto y línea) |
|---|---|---|
| B17-01 | `open_field` | grupos *Corners* / *Periphery*, inmovilidad, freezing, rejilla 4×4 |
| B17-02 | `epm` / `ezm` | % de tiempo y entradas en brazos / cuadrantes abiertos |
| B17-03 | `novel_object` | exploración del objeto nuevo y del familiar, índices |
| B17-04 | `novel_object_aproximacion_lenta` y `novel_object_giro_instantaneo` | H-P01 en sintético. La lenta debe salir bien. En la de giro instantáneo (media vuelta en un cuadro) las diferencias salen como `CONOCIDO [H-P01]`, que solo se ven con `--todo`: verifícala con `--todo` y busca `[H-P01]` (con los arreglos de H-P01, la exploración del objeto nuevo ya sale bien: 8,3 s; quedan *Head turn angle* y las rotaciones, inevitables con media vuelta en un solo cuadro). Si la exploración del objeto nuevo sale 0 s, H-P01 ha vuelto: hallazgo S1. **Que salgan bien no cierra H-P01**: en video real se comprueba en B13-54 |
| B17-05 | `y_maze`, `t_maze`, `radial_arm_maze`, `rapc` | alternancia, elección, errores de memoria, secuencia de puertas |
| B17-06 | `water_maze` | latencia de escape, plataforma, cuadrantes, Whishaw, distancia a la plataforma (H-P07 en la capa A) |
| B17-07 | `barnes_maze`, `hole_board` | visitas y cabeceos **por la cabeza** |
| B17-08 | `light_dark`, `three_chamber`, `cpp`, `novel_tank`, `thermal_gradient`, `home_cage`, `custom` | medidas propias de cada prueba |
| B17-09 | `activity_wheel` | revoluciones y rotaciones en cada sentido |
| B17-10 | `forced_swim`, `tail_suspension`, `fear_conditioning` | inmovilidad por forcejeo y freezing |
| B17-11 | `multi_well` | 6 pocillos en un video: un aparato por pocillo |
| B17-12 | `funciones` | zona de investigación, zonas por cabeza y por cola, secuencia, rejilla 3×3, *rearing* automático (H-P09: hoy no detecta los 2 rears programados), teclas (también por zona), periodo con nombre, periodo anclado a la entrada al centro, cálculo |
| B17-13 | `open_field_intervalos` | filas por intervalos de 5 s |
| B17-14 | `home_cage_escondido` | zona oculta: tiempo escondido, salida parcial, velocidad a la vista |
| B17-15 | `social` | dos animales: distancias y contacto. **Identidades:** la app no sabe quién es quién al empezar; si la fila de S1 trae los valores de S2, usa *Review and score ▸ Track editing ▸ Swap identities* sobre toda la prueba (B07-43), guarda, vuelve a exportar y verifica de nuevo. Que haga falta no es un fallo; que *Swap* no lo arregle, sí |
| B17-16 | `movil` | 4 pruebas del mismo video: normal, **plataforma movida** a SW (el cuadrante objetivo y las posiciones de comparación deben seguirla), **mapa desplazado** (30, −20 px) y **calibración propia** (×1,5). Comprueba en Review que la plataforma se ve donde corresponde en cada prueba |
| B17-17 | `io` | prueba solo de entradas/salidas: palanca, comedero y una señal analógica |
| B17-19 | `rata_grande_lenta` | escala de una rata real (cuerpo de 110 px, 2,5 px/cuadro): todas las medidas por la cabeza deben salir bien |
| B17-18 | `open_field_casi_inmovil` | quieto el 80 % del tiempo: se funde con el fondo «mediana» (diferencias esperadas en la capa A). Lo que se juzga es si la app lo avisa: ver B03-26 |

## Variantes que deben dar lo mismo

| Caso | Qué | Esperado |
|---|---|---|
| B17-30 | En `open_field`, exporta también a **Excel** y **TSV**; el verificador lee CSV: compara a mano 5 valores. | Iguales al CSV |
| B17-31 | En `y_maze`, cambia la unidad a mm (Protocol ▸ Apparatus) y exporta. | Columnas en `(mm)` y valores ×10; el verificador marca `SIN COLUMNA` en ellas (esperado: compruébalo a mano). Vuelve a cm |
| B17-32 | En `water_maze`, *Set segment length* 10 s. | La suma de los tiempos por zona de los segmentos = la de la prueba (±0,1 s) |
| B17-33 | Repite `epm` rastreando desde *Review and score ▸ Track this test*. | Mismos resultados |
