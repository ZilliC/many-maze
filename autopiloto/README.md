# Autopiloto de mANY-MAZE

Instrucciones para el agente que trabaja solo, en bucle, durante días. **Léelas completas en cada iteración**
(son cortas a propósito: cada línea se paga en tokens en cada vuelta).

## Meta: qué significa «terminado al 100 %»

Todo esto a la vez:

1. `.venv/bin/python -m pytest -q` pasa entero y `.venv/bin/ruff check .` sale limpio.
2. Los bloques B00–B17 de `qa/PLAN_PRUEBA_INTEGRAL.md` se ejecutaron (pasada 2 = todo), con la cobertura anotada.
3. No queda ningún hallazgo **S1** ni **S2** abierto; los S3/S4 están arreglados o anotados como «se queda así» con
   el motivo.
4. La revisión final (§6 del plan de QA) está escrita y la rama `validacion-qa` está empujada con el CI en verde.

Lo que necesita hardware real, videos de ANY-maze, dinero o una decisión de producto (los `- [ ]` que quedan en
`TODO.md`) **no** lo intentes: anótalo una sola vez en `PARA_TI.md` y sigue.

## Una iteración = una tarea

1. Lee `COLA.md` y las últimas ~30 líneas de `BITACORA.md`. Nada más de contexto salvo lo que pida la tarea.
2. Toma la **primera** tarea sin hacer de `COLA.md`. Si la cola está vacía, genera las siguientes tareas a partir
   de la meta (hallazgos abiertos → bloques de QA pendientes → revisión final) y escríbelas en la cola.
3. Delega la tarea a **un subagente** (tipo `general-purpose`, modelo `sonnet`), con un prompt autocontenido: qué
   hacer, qué archivos mirar, cómo verificar y qué devolver (un resumen de ≤ 10 líneas). Tú solo coordinas: así tu
   contexto no crece y cada tarea empieza limpia. Usa `model: "opus"` solo para un fallo que Sonnet ya intentó dos
   veces sin resolver, o para la revisión final.
4. Tipos de tarea:
   - **Arreglo** (test rojo, hallazgo, mejora): el subagente trabaja en una rama nueva `auto/<tema>` en un *worktree*,
     escribe o ajusta el test que demuestra el fallo, lo arregla, corre primero los tests de lo que tocó y al final
     la suite completa + ruff. Solo si todo está verde, haz merge a `validacion-qa`, push y borra el worktree.
   - **QA** (un bloque B##): sigue el plan de QA tal cual (el ejecutor **solo observa y anota, no arregla**). Usa la
     carpeta de prueba más reciente de `~/mANY-MAZE-QA/` o crea una con `qa/herramientas/preparar_prueba.py`.
     Cada hallazgo S1/S2/S3 nuevo se convierte en una tarea **Arreglo** al final de la cola.
5. Marca la tarea como hecha en `COLA.md`, añade una línea a `BITACORA.md`
   (`fecha · tarea · resultado · commit/rama`) y haz commit de `autopiloto/` en `validacion-qa`.

## Reglas

- Nunca hagas `push --force`, no reescribas historia, no borres ramas sin merge ni datos de `~/Downloads/Ratas`.
- Si una tarea falla tres veces, márcala `[!]` en la cola con el motivo, anótala en `PARA_TI.md` y sigue con otra.
- No preguntes al usuario; si algo de verdad necesita una decisión suya, va a `PARA_TI.md` y avisas con una
  notificación (solo en ese caso, o cuando la meta se cumpla).
- Si la meta se cumple: escribe el resumen final en `BITACORA.md`, avisa y **detén el bucle**.

## Ritmo (para dejar tokens para otras cosas)

- Máximo **6 tareas por día**. Cuenta las de hoy en `BITACORA.md`; si ya van 6, programa el siguiente despertar para
  mañana a las 09:00 y no hagas nada más.
- Entre tareas, espera ~20 min; si una tarea dejó algo corriendo (prueba larga, CI), espera lo que tarde eso.
- La suite completa tarda ~4 min: córrela una vez antes de cada merge, no después de cada cambio.
- QA con computer use es lo más caro (capturas de pantalla): pasada 1 (solo P1) de todos los bloques primero,
  pasada 2 después.
