# Cola de tareas

`[ ]` pendiente · `[x]` hecha · `[!]` atascada (ver `PARA_TI.md`). El agente toma siempre la primera `[ ]`.

- [x] Arreglo: `tests/gui/test_shell_audit.py` tiene 2 tests en rojo en `validacion-qa`
      (`test_ribbon_commands_keep_the_text_being_typed`, `test_delete_time_period_uses_the_table_worked_in_and_invalid_periods_are_reported`)
      → no se reproduce con `QT_QPA_PLATFORM=offscreen` (21/21 en el archivo, 299/299 en `tests/gui`)
- [x] Arreglo: `ruff check .` limpio (instalar ruff 0.11.13 en `.venv` si falta, la misma versión que el CI)
      → ya estaba limpio; ruff 0.11.13 instalado en `.venv` con `uv pip`
- [x] Revisar `~/mANY-MAZE-QA/2026-10-11_2224/bitacora/HALLAZGOS.md`: cada hallazgo abierto que no esté en
      `qa/HALLAZGOS_PREVIOS.md` como corregido → una tarea Arreglo en esta cola
      → H-001…H-004 arreglados en a07a9ad; H-005 (Obs) en parte: Stage vacío y videos rotos «se quedan así»
        (pruebas sin etapa son válidas; el fallo al rastrear ya se explica). Ningún S1/S2/S3 abierto.
- [ ] QA pasada 1: terminar B00, B06 y B17 (el piloto dejó casos sin ejecutar, ver su `COBERTURA.md`)
- [ ] QA pasada 1: B01, B02, B03, B04, B05 (una tarea por bloque)
- [ ] QA pasada 1: B07, B08, B09, B10, B11 (una tarea por bloque)
- [ ] QA pasada 1: B12, B13, B14, B15, B16 (una tarea por bloque)
