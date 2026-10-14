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
- [x] QA pasada 1: terminar B00, B06 y B17 (el piloto dejó casos sin ejecutar, ver su `COBERTURA.md`)
      → sin computer use en subagentes: solo motor. B17 autoverificar 34 exp. (H-006, H-007); B06-02 14/15 por motor;
        lo de GUI queda BLOQUEADO
- [x] Arreglo: H-006 (S2) `novel_tank` regresó con c9b4880 (H-P13): distancia 4.74 cm vs 30.69 esperado, 232 DIFIERE
      en capa A; `qa/herramientas/oraculo_sintetico.py autoverificar` lo reproduce
      → fantasma del animal en el fondo mediano; `_absorb_ghosts` para todo fondo sustraído. novel_tank 88→289/328 (ca3c0d7)
- [x] Arreglo: H-P03 (sigue) `lista_con_archivo_que_falta.m3u` da `FileNotFoundError` crudo al añadir prueba
      → `_PlaylistCapture` ahora da IOError claro con lista y video (a0efbdd)
- [x] Arreglo: H-007 (herramienta) el oráculo etiqueta el último periodo `35-37 s` y la app `35-37.4 s` (26 SIN FILA
      en `open_field_intervalos`); regenerar `calibracion_capa_A.csv` tras reconstruir `dist`
      → la app tenía razón; oráculo sin redondeo, 26→0 SIN FILA; cuándo correr `calibrar` documentado (be9e3a1)
- [x] QA motor/CLI (sin GUI): B11 estadística contra `oraculo_estadistica.py`
      → 7 OK, 3 RARO; valores = oráculo; H-P02 ya no ocurre; H-008, H-009 (S3), H-010 (Obs), H-011 (herramienta)
- [x] QA motor/CLI (sin GUI): B16 línea de comandos
      → 4 OK, 3 RARO, 2 FALLA; H-012/H-013 (S2), H-014/H-015 (S3), H-016 (Obs), H-017 (herramienta)
- [x] QA motor/CLI (sin GUI): B13 videos reales (OFT, EPM, MWM, FST; `cabeza_real.py`, `comparar_anotaciones.py`)
      → P1: 3 OK, 1 RARO (MWM), 1 FALLA (OFT), 2 BLOQUEADO (sin anotaciones); H-P01 ya no ocurre;
        H-025 (S2), H-026/H-027 (S3), H-028 (Obs), H-029 (herramienta)
- [x] QA motor/CLI (sin GUI): B12 importar desde ANY-maze
      → offscreen: 2 OK, 3 RARO; H-018/H-019/H-020 (S3)
- [x] QA motor/CLI (sin GUI): B10 exportaciones contra la referencia
      → offscreen: 14 OK, 2 RARO; exportaciones = CLI (0 celdas distintas); H-021…H-024 (S4)
- [ ] QA pasada 1 (necesita computer use, ver PARA_TI): B01, B02, B03, B04, B05 (una tarea por bloque)
- [ ] QA pasada 1: B07, B08, B09, B10, B11 (una tarea por bloque)
- [ ] QA pasada 1: B12, B13, B14, B15, B16 (una tarea por bloque)
- [x] Arreglo: H-008 (S3) Statistics ▸ Two factors: la 1.ª variable solo admite Stage/Trial/Period, no Group × Sex
      (el motor sí lo calcula bien)
      → 1.er factor ofrece todos los factores (b353f1b, merge 2bc39bc)
- [x] Arreglo: H-009 (S3) Statistics: Paired t-test/Wilcoxon con 3 etapas se cambian a RM-ANOVA/Friedman y el filtro
      solo admite «=» (no «≠ Día 3»), así que no se pueden pedir
      → filtro con «=»/«≠»; pareadas con 2 etapas (b353f1b, merge 2bc39bc)
- [x] Arreglo: H-011 (herramienta) `qa/herramientas/oraculo_estadistica.py` no registra «Genotipo» en `animal_fields`
      → «Genotipo» registrado (cc42fd4, merge 2bc39bc)
- [ ] Decidir H-010 (Obs) Assumption checks solo da p, no el estadístico; D'Agostino por grupo, no global
- [ ] Arreglo: H-012 (S2) `manymaze demo RUTA_RELATIVA` → FileNotFoundError con la ruta duplicada
- [ ] Arreglo: H-013 (S2) `manymaze relink --folder RUTA_RELATIVA` dice «Relinked 6» pero guarda rutas relativas
      al directorio actual; `info` sigue con 6 faltantes
- [ ] Arreglo: H-014 (S3) CLI muestra Traceback con video inexistente/corrupto/vacío, plantilla inexistente
      (`KeyError`), `demo` en carpeta de solo lectura, `results -o` a directorio inexistente
- [ ] Arreglo: H-015 (S3) CLI `-o` con extensión no soportada escribe CSV en silencio; `report -o x.csv` escribe HTML
- [ ] Arreglo: H-017 (herramienta) `comparar_con_referencia.py` falla con `.slk` y `.xlsx --bins`; `inspeccionar.py`
      se cae si el archivo no existe; `referencia/` es anterior a H-P01 (regenerar)
- [ ] Decidir H-016 (Obs) `--bbox` fuera de imagen da ceros, `--size-cm` negativo se ignora, `demo` sobre carpeta
      existente la regenera (la app la abre)
- [ ] Arreglo: H-018 (S3) importar XML de ANY-maze roto da «Something went wrong unexpectedly…» + Traceback ParseError
- [ ] Arreglo: H-019 (S3) importar dos veces el mismo XML duplica pruebas y animales sin avisar
- [ ] Arreglo: H-020 (S3) mapa de zonas 80×60 en un aparato de 640×360 no se reescala ni avisa (y rendija de 1 px
      entre Left y Right)
- [ ] Arreglo: H-021…H-024 (S4) informe HTML `statistic=nan` en medidas constantes; correo con servidor apagado
      dice «file could not be read or written»; errores al guardar nombran un `.tmp`; «Colour by» ofrece
      «Defecation: count» y falla en silencio. Más: `inspeccionar.py` se cae con la línea `# …` de raw data
- [ ] Arreglo: H-025 (S2) OFT real: el rastreo se pega al borde de la caja (93 % de cuadros) sin aviso, en 3 de 6
      arenas; con la caja 15–20 px adentro rastrea bien (`~/mANY-MAZE-QA/2026-10-11_2224/salidas/B13/`)
- [ ] Arreglo: H-026 (S3) arena vacía: el rastreo da 98,8 % de detección en un cruce de la rejilla (300 s)
- [ ] Arreglo: H-027 (S3) MWM: distancia ×1,4–1,9 la referencia por los cuadros de la mano al soltar/recoger
- [ ] Arreglo: H-029 (herramienta) umbral de `cabeza_real.py` pensado para menos fps
- [ ] Decidir H-028 (Obs) OFT: entradas al centro 2–5× la referencia; calidad 54,8 % en mHFD11
