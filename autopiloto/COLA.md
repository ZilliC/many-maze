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
- [x] Arreglo: H-012 (S2) `manymaze demo RUTA_RELATIVA` → FileNotFoundError con la ruta duplicada
      → ruta resuelta antes de escribir (merge dea53d0)
- [x] Arreglo: H-013 (S2) `manymaze relink --folder RUTA_RELATIVA` dice «Relinked 6» pero guarda rutas relativas
      al directorio actual; `info` sigue con 6 faltantes
      → carpeta resuelta a absoluta (merge dea53d0)
- [x] Arreglo: H-014 (S3) CLI muestra Traceback con video inexistente/corrupto/vacío, plantilla inexistente
      (`KeyError`), `demo` en carpeta de solo lectura, `results -o` a directorio inexistente
      → mensajes de una línea y código 1 (merge dea53d0)
- [x] Arreglo: H-015 (S3) CLI `-o` con extensión no soportada escribe CSV en silencio; `report -o x.csv` escribe HTML
      → `-o` validado: tablas y `.html/.htm` para report/protocol (merge dea53d0)
- [x] Arreglo: H-017 (herramienta) `comparar_con_referencia.py` falla con `.slk` y `.xlsx --bins`; `inspeccionar.py`
      se cae si el archivo no existe; `referencia/` es anterior a H-P01 (regenerar)
      → herramientas arregladas, `--hoja` nuevo (merge 220df68)
- [ ] Decidir H-016 (Obs) `--bbox` fuera de imagen da ceros, `--size-cm` negativo se ignora, `demo` sobre carpeta
      existente la regenera (la app la abre)
- [x] Arreglo: H-018 (S3) importar XML de ANY-maze roto da «Something went wrong unexpectedly…» + Traceback ParseError
      → mensaje «damaged or incomplete…», sin traceback (merge 220df68)
- [x] Arreglo: H-019 (S3) importar dos veces el mismo XML duplica pruebas y animales sin avisar
      → avisa y pregunta antes de reimportar, «No» por defecto (merge 220df68)
- [ ] Arreglo: H-020 (S3) mapa de zonas 80×60 en un aparato de 640×360 no se reescala ni avisa (y rendija de 1 px
      entre Left y Right)
- [x] Arreglo: H-021…H-024 (S4) informe HTML `statistic=nan` en medidas constantes; correo con servidor apagado
      dice «file could not be read or written»; errores al guardar nombran un `.tmp`; «Colour by» ofrece
      «Defecation: count» y falla en silencio. Más: `inspeccionar.py` se cae con la línea `# …` de raw data
      → nota «not computed…», mensaje de servidor de correo, nombre real del archivo, «Colour by» sin
        medidas de puntuación manual (merge ef948c5)
- [x] Arreglo: H-025 (S2) OFT real: el rastreo se pega al borde de la caja (93 % de cuadros) sin aviso, en 3 de 6
      arenas; con la caja 15–20 px adentro rastrea bien (`~/mANY-MAZE-QA/2026-10-11_2224/salidas/B13/`)
      → en parte: fondo tolerante junto a líneas finas; fC2 801→2322 cm (ref 2457); fC1 sigue en una
        mancha clara del suelo (merge eb1245c)
- [x] Arreglo: H-026 (S3) arena vacía: el rastreo da 98,8 % de detección en un cruce de la rejilla (300 s)
      → región >50 % de la arena no es el animal; 98,8 %→17 % (merge eb1245c)
- [!] Arreglo: H-027 (S3) MWM: distancia ×1,4–1,9 la referencia por los cuadros de la mano al soltar/recoger
      → necesita decisión (inicio por defecto del MWM / recortar la mano final), ver PARA_TI
- [x] Arreglo: H-029 (herramienta) umbral de `cabeza_real.py` pensado para menos fps
      → umbral en px/s (60 px/s) (merge ef948c5)
- [ ] Decidir H-028 (Obs) OFT: entradas al centro 2–5× la referencia; calidad 54,8 % en mHFD11
- [x] Arreglo: H-025 (resto) OFT arena fC1: el track se queda en una mancha clara del suelo ~50 % de cuadros
      (53 % estático, 84 % detección); quizá aviso «track pegado/estático» (`salidas/B13/`)
      → fantasma debe durar 1 s antes de entrar al fondo (la cabeza del experimentador al inicio); fC1 detección
        84→99,5 %; distancia baja en 4 arenas (antes medía la cola) (merge 77c4f3f)
- [x] QA offscreen B07 → 7 OK, 3 RARO; H-030/H-032 (S3), H-031/H-033/H-034 (Obs)
- [x] QA offscreen B08 → 7 OK, 3 RARO; H-P08 ya no ocurre (8 sesiones 20 min); H-035/H-036 (S3), H-037…H-040 (Obs)
- [x] QA offscreen B03 → 7 OK, 4 RARO, 1 FALLA; H-042 (S2), H-041/H-043/H-044/H-045 (S3), H-046 (Obs)
- [x] QA offscreen B04 → 1 OK, 7 RARO; persistencia OK en los 8; H-047/H-048/H-049 (S3), H-050/H-051 (Obs)
- [x] QA offscreen B05 → 3 OK, 3 RARO, 1 FALLA; persistencia OK; dosis y aleatorización OK; H-052/H-053/H-054 (S3), H-055 (Obs)
- [x] QA offscreen B02 → 2 OK, 2 RARO, 1 FALLA; persistencia y cifrado OK; H-056/H-057 (S2), H-058/H-059 (S3), H-060 (Obs)
- [x] QA offscreen B09 → 3 OK, 5 RARO; 73/90 eventos en vivo, 85 acciones; H-062/H-063/H-064/H-069 (S3), H-061/H-067/H-068 (S4), H-065/H-066/H-070 (Obs)
- [ ] QA offscreen B14 robustez (clases reales de la app, sin computer use; lo visual BLOQUEADO)
- [x] Arreglo: H-030 (S3) cambiar Test start o Duration no recalcula Results ni avisa (hasta «Track this test»)
      → aviso en rojo en Results si la ventana ya no coincide con el track (sin recálculo; ver PARA_TI)
- [x] Arreglo: H-032 (S3) «Interpolate» sobre todo el video deja el track como «not detected» y dice «Interpolated 750»
      → se niega si no hay posiciones detectadas de las que interpolar (merge 6170031)
- [ ] Arreglo: H-031/H-033/H-034 (Obs) reproducción 4×/8× va a ~5×/10×; «Mark position» acepta clic fuera del
      cuadro; «Copy table» copia filas ocultas por el filtro
- [ ] Arreglo: H-035 (S3) en vivo: una tecla de inicio que también es de puntuación (p. ej. `r`) nunca inicia la prueba
- [ ] Arreglo: H-036 (S3) en vivo: «On a start switch» con entrada vacía o inexistente espera para siempre sin aviso
- [ ] Arreglo: H-039 (Obs, persistencia) en vivo: duración, modo de inicio y «record» no persisten al reabrir
- [ ] Decidir H-037/H-038/H-040 (Obs) pruebas en espera descartadas al acabar el video demo; «nuevo archivo cada»
      solo en minutos; «Several tests» acepta el mismo animal en dos paneles
- [x] Arreglo: H-042 (S2) Keys: el color elegido en la muestra de la tabla no se guarda (sí desde la página «Key»)
      → color guardado al elegirlo en la tabla (merge 6170031)
- [ ] Arreglo: H-041 (S3) «Colour of the animal» / marcas de color aceptan cualquier texto; la vista previa lanza
      ValueError en cada cuadro
- [ ] Arreglo: H-043 (S3) cancelar el desbloqueo de ciego deja el botón «Blind testing» desmarcado con ciego activo
- [ ] Arreglo: H-044/H-045 (S3) Keys: tecla de varias letras se corta sin aviso (`Left`→`l`), espacio deja sin tecla;
      se pueden guardar teclas con errores (duplicadas nunca puntúan, nombre vacío se pierde)
- [ ] Decidir H-046 (Obs) etapas duplicadas aceptadas; pruebas conservan una etapa que ya no existe
- [ ] Arreglo: H-047 (S3) «Zone grid» de celdas cuadradas en arena no convexa (EPM, T) crea 0 celdas sin aviso
- [ ] Arreglo: H-048 (S3) se aceptan formas de área cero (plantilla de ancho 0 → 0 px/cm, polígonos colineales o
      cruzados, `centre_fraction` 1.0)
- [ ] Arreglo: H-049 (S3) `{` o `}` en el nombre de una zona rompe las fórmulas de cálculo
- [ ] Arreglo: H-050/H-051 (Obs) «Duplicate» de zona no copia reglas/opciones; varias entradas inválidas sin aviso
      (Y fuera del rectángulo, agujero Barnes fuera de rango, renombrar vacío, archivo de aparato vacío, borrar el
      último aparato deja pruebas sin aparato, extensión de mapa desconocida da mensaje crudo)
- [ ] Revisar OFT mHFD12 (28,9 % detectado, 1061 cm vs 3477 de referencia; la rata sale de la arena) y mHFD11
      (distancia 1511 vs 2135) mirando cuadros con el track superpuesto; ¿arreglo o es la referencia?
- [ ] Arreglo: H-052 (S3) un campo de animal llamado `Stage`/`Apparatus`/`Test` sobrescribe esa columna en Results
- [ ] Arreglo: H-053 (S3) «Import animals» sin avisos: ID duplicado sobrescribe, filas sin ID se pierden, pesos malos
      se guardan, dice «Imported 7» con 6; columna *Status* de ANY-maze ignorada
- [ ] Arreglo: H-054 (S3) exportar animales pierde estado/motivo de retirado, notas con `=` vuelven con apóstrofo; en
      modo ciego escribe `=SUM(...)`/`+cmd|...` sin protección contra fórmulas
- [ ] Decidir H-055 (Obs) dosis con concentración 0 → 0,0001 sin aviso; pesos 1e9 → 100000; tope de 1000 animales;
      tratamientos que solo difieren en mayúsculas y colores duplicados permitidos
- [x] Arreglo: H-056 (S2) protocolo bloqueado: la tecla Supr en el mapa borra zonas que el admin dejó seleccionadas
      (6→0, sin deshacer)
      → borrar/pegar comprueban el bloqueo; cambiar de usuario limpia la selección (merge 8ee5e8e)
- [x] Arreglo: H-057 (S2) cualquier no-admin puede «Restore a backup» y borrar usuarios, roles y bloqueo sin que se
      pida administrador
      → restaurar copia exige administrador si hay seguridad (merge 8ee5e8e)
- [x] Arreglo: H-058 (S3) el único administrador puede quitarse a sí mismo del experimento y apaga toda la seguridad
      → no se puede quitar al único administrador (merge 8ee5e8e)
- [x] Arreglo: H-059 (S3) con protocolo bloqueado se puede cambiar la unidad de distancia y las teclas/interruptor de
      Run tests
      → unidad y teclas/interruptor de Run tests bajo el bloqueo (merge 8ee5e8e)
- [ ] Decidir H-060 (Obs) contraseñas de 1 carácter o espacios; sin límite de intentos; archivo cifrado manipulado
      dice «password is wrong»; «Wrong password» antes de escribir en «Restore a backup»
- [ ] Arreglo: H-062/H-063 (S3) procedimientos: no se puede accionar una entrada simulada en vivo desde la GUI;
      canales con el mismo nombre en un dispositivo
- [ ] Arreglo: H-064 (S3) «End the test» en el mismo cuadro que la última recompensa deja el pellet en 0 ms y los
      resultados lo cuentan de menos
- [ ] Arreglo: H-069 (S3) pausa por procedimiento no se reanuda con una espera; con video simulado la prueba termina pronto
- [ ] Arreglo: H-061/H-067/H-068 (S4) mensajes de «Test» < 50 ms; alertas e-mail sin validar destino/puerto; editor
      sin confirmar ni deshacer al borrar bloque, claves JSON desconocidas ignoradas
- [ ] Arreglo: H-065/H-066/H-070 (Obs) Log repite cada acción 3–4×; `immobile_time_reaches` no se dispara;
      `smtp_local.py` sobrescribe correos anteriores
