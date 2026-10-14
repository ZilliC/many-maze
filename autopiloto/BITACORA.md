# Bitácora del autopiloto

`fecha · tarea · resultado · commit/rama`

2026-10-13 · Arreglo test_shell_audit (2 rojos) · no se reproduce en offscreen; 299/299 tests/gui verdes, sin cambios · validacion-qa@eb143a4
2026-10-13 · Arreglo ruff check limpio · ya limpio (0 hallazgos con 0.11.13), ruff instalado en .venv, sin cambios · validacion-qa@fb2de9e
2026-10-13 · Revisar hallazgos del piloto 2026-10-11 · H-001…H-004 arreglados (a07a9ad), H-005 Obs se queda así; 0 tareas nuevas · —
2026-10-13 · QA pasada 1 B00/B17/B06 (resto) · sin computer use: solo motor; H-006 S2 regresión novel_tank (c9b4880), H-007 Obs oráculo; GUI BLOQUEADO · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · Arreglo H-006 novel_tank · arreglado: absorber fantasmas en todo fondo sustraído; capa A 88→289/328; 1316 tests verdes · ca3c0d7
2026-10-13 · Arreglo H-P03 .m3u con video que falta · arreglado: mensaje claro con lista y video; 1317 tests verdes · a0efbdd
2026-10-13 · QA B11 por motor (offscreen) · 7 OK 3 RARO, valores = oráculo; H-008/H-009 S3, H-010 Obs, H-011 herramienta · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · Arreglo H-007 etiqueta último periodo del oráculo · arreglado en la herramienta (la app tenía razón); 26→0 SIN FILA; 1317 tests · be9e3a1
2026-10-13 · QA B16 línea de comandos · 4 OK 3 RARO 2 FALLA; H-012/H-013 S2, H-014/H-015 S3, H-016 Obs, H-017 herramienta · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · QA B12 importar ANY-maze (offscreen) · 2 OK 3 RARO; H-018/H-019/H-020 S3 · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · QA B10 resultados/exportaciones (offscreen) · 14 OK 2 RARO, exportaciones GUI = CLI; H-021…H-024 S4 · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · QA B13 videos reales (motor) · P1 3 OK 1 RARO 1 FALLA 2 BLOQ; H-025 S2 OFT pegado al borde, H-026/H-027 S3, H-028 Obs, H-029 herramienta · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · Arreglo H-008/H-009/H-011 estadística · arreglados; oráculo B11 coincide; 1319 tests · 2bc39bc
2026-10-13 · Arreglo H-012…H-015 línea de comandos · arreglados; 1321 tests · dea53d0
2026-10-13 · Arreglo H-017/H-018/H-019 herramientas e importación ANY-maze · arreglados; 1325 tests · 220df68, 0b9c8f0
2026-10-13 · Arreglo H-021…H-024 (S4) y H-029 herramienta · arreglados; 1334 tests · ef948c5
2026-10-13 · Arreglo H-025/H-026/H-027 rastreo real · H-025 en parte (fC1 queda), H-026 arreglado, H-027 a decisión; oráculo 14403→14404; 1331 tests · eb1245c
2026-10-13 · QA B07 revisar y puntuar (offscreen) · 7 OK 3 RARO; H-030/H-032 S3, H-031/H-033/H-034 Obs · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · QA B08 pruebas en vivo (offscreen, cámaras simuladas) · 7 OK 3 RARO; H-035/H-036 S3, H-037…H-040 Obs · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · QA B03 protocolo (offscreen) · 7 OK 4 RARO 1 FALLA; H-042 S2, H-041/H-043/H-044/H-045 S3, H-046 Obs · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · QA B04 aparatos (offscreen) · 1 OK 7 RARO, persistencia OK; H-047/H-048/H-049 S3, H-050/H-051 Obs · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · Arreglo H-025 resto (fC1) · arreglado: fantasma persistente ≥1 s; detección fC1 99,5 %; oráculo sin regresión; 1337 tests · 77c4f3f
2026-10-13 · QA B05 animales (offscreen) · 3 OK 3 RARO 1 FALLA, persistencia OK; H-052/H-053/H-054 S3, H-055 Obs · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · QA B02 usuarios y seguridad (offscreen) · 2 OK 2 RARO 1 FALLA; H-056/H-057 S2, H-058/H-059 S3, H-060 Obs · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · Arreglo H-042/H-030/H-032 · H-042 y H-032 arreglados, H-030 solo aviso (decisión en PARA_TI); 1340 tests · 6170031
2026-10-13 · Arreglo H-056…H-059 seguridad · arreglados; 1344 tests · 8ee5e8e
2026-10-13 · QA B09 procedimientos e I/O simulado (offscreen) · 3 OK 5 RARO; H-061…H-070 (4 S3) · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-13 · Arreglo H-052/H-053/H-054 animales · arreglados; CSV de animales gana columna Status; 1349 tests · a893721
2026-10-14 · QA B14 robustez (offscreen) · 2 OK 4 RARO 1 N/A; H-071…H-076 (1 S3) · ~/mANY-MAZE-QA/2026-10-11_2224
2026-10-14 · Arreglo H-041/H-043/H-044/H-045 protocolo · arreglados; 1354 tests · 4b93b0f
2026-10-14 · Arreglo H-047…H-050 aparatos · arreglados; 1355 tests · f40f1aa
2026-10-14 · Arreglo H-039/H-075/H-072/H-073/H-071, H-074 en parte · arreglados; 1366 tests · 4d8c000
2026-10-14 · Arreglo H-062…H-066, H-069 procedimientos · arreglados; 1360 tests antes del merge · 5e24b1a
2026-10-14 · Arreglo H-051 aparatos y H-076 modo oscuro · arreglados; 1377 tests · b71e773
2026-10-14 · Arreglo H-020, H-031/H-033/H-034 · arreglados; 1387 tests · 8f6467e
2026-10-14 · Revisar OFT mHFD12/mHFD11 a ojo · rastreo correcto; referencias con artefactos (mHFD12 objeto del borde tras 95 s; escala p1–p99) · /tmp/oft_review
