# Para ti (lo que el agente no puede hacer solo)

Cosas que necesitan hardware real, videos, dinero o una decisión tuya. El agente las anota aquí y sigue trabajando.

- Ya en `TODO.md`: comparar números con ANY-maze en los mismos videos, umbrales FST/TST en videos reales, Arduino
  con analizador lógico, cámara USB en vivo, laboratorio piloto, Apple Developer ID, decidir Windows y traducciones.
- 2026-10-13: los subagentes de esta sesión **no tienen computer use**, así que los casos de QA que necesitan la
  interfaz quedan BLOQUEADO. Para avanzar B01–B16 hace falta lanzar sesiones con computer use activado (§«Cómo lanzar
  cada sesión» de `qa/PLAN_PRUEBA_INTEGRAL.md`); mientras tanto el autopiloto hace lo que se puede por motor/CLI.
- 2026-10-13: B13-50/51 (precisión del rastreo frente a fotogramas anotados a mano) necesitan que una persona anote
  fotogramas con `qa/herramientas/anotar.py`; no hay anotaciones previas.
- 2026-10-13 · **H-027 (decisión)**: en el laberinto de agua la distancia sale ×1,4–1,9 la referencia por los
  cuadros de la mano al soltar y recoger. Con *Start: experimenter leaves* baja a ×1,45 / ×1,10 / ×1,16; el resto es
  la mano al final. ¿Cambiar el inicio por defecto de `water_maze` a «experimenter leaves»? ¿Recortar la mano al
  final (cortaría datos sin avisar, o con aviso)?
- Pendientes de decidir (observaciones): H-010 Assumption checks solo da p; H-016 `--bbox` fuera de imagen /
  `--size-cm` negativo / `demo` sobre carpeta existente; H-028 entradas al centro OFT 2–5× la referencia.
- 2026-10-13 · **H-030 (decisión)**: al cambiar Test start/Duration, Results ahora avisa en rojo que hay que volver a
  rastrear. Un recálculo real sin re-rastrear exige recortar/desplazar el track en el análisis (toca el núcleo).
  ¿Lo hacemos o basta el aviso?
- 2026-10-15 · **H-071 (decisión)**: tras un cierre forzado durante el rastreo, las pruebas pueden quedar «pending»
  con el track completo. ¿Recuperarlas al abrir (hay que definir cuándo un track cuenta como completo)?
- 2026-10-15 · **Revisión final**: `qa/REVISION_FINAL_2026-10-15.md` (sin commit) — léela y dime si la subo.
- 2026-10-15 · **H-P10 (permiso)**: el rearing con cámara cenital no se puede detectar con forma ni brillo del blob
  (12 señales medidas, ninguna separa los rears). Lo más prometedor es el modelo de pose (nariz y orejas). ¿Autorizas
  descargarlo para probarlo?
- 2026-10-15 · **H-P05 (decisión)**: las medidas de giro ignoran ya movimientos < 0,5 cm (ajuste nuevo en Analysis ▸
  Movement). En videos reales los giros bajan ~45 % y siguen bajando con mínimos mayores (el temblor real supera
  0,5 cm). ¿Subir el mínimo por defecto, o validar contra un recuento externo de giros?
- 2026-10-15 · **H-080/H-081 (decisión)**: el rastreo bloquea la ventana a propósito (para que el experimento no
  cambie debajo); ¿quieres poder seguir trabajando mientras rastrea? «Add tests from videos» usa el nombre del
  archivo como Animal ID (ahora avisa de los animales nuevos); ¿preferirías que proponga animales existentes?
