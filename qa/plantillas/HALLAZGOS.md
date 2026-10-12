# Hallazgos — prueba integral de mANY-MAZE

> Un hallazgo por problema, numerados `H-001`, `H-002`… en orden de aparición. Antes de abrir uno nuevo, busca si ya
> existe (mismo síntoma en otra pantalla → añade la nueva ruta al existente en «También ocurre en»).
> **No intentes arreglar nada ni leer el código para explicar la causa**: describe lo que ve un usuario.

## Severidades

| Severidad | Significado | Ejemplos |
|---|---|---|
| **S1 Crítico** | Se cierra la app, se pierden o corrompen datos, resultados numéricos incorrectos | cierre inesperado; experimento que no vuelve a abrir; exportación que no coincide con la referencia |
| **S2 Grave** | Una función no hace lo que promete y no hay forma razonable de lograrlo | botón que no hace nada; importación que ignora filas sin avisar; diálogo que no se puede cerrar |
| **S3 Moderado** | Funciona pero con error visible o hay un rodeo | mensaje de error confuso; valor que no se guarda al cambiar de página; tabla que no se refresca |
| **S4 Menor** | Cosmético o de texto | texto cortado, falta de ortografía, icono desalineado, modo oscuro ilegible |
| **Obs Observación** | No es un fallo claro: duda de usabilidad, lentitud, sugerencia | tarda 40 s sin barra de progreso; no es obvio cómo deshacer |

---

## H-001 — <título corto: qué falla, dónde>

- **Severidad:** S2
- **Caso / ID inventario:** B05-12 / `TESTESSCH-1A2B`
- **Ruta:** Test ▸ Test schedule ▸ botón «Check video»
- **Precondiciones:** demo abierto, 2 pruebas seleccionadas
- **Pasos para reproducir:**
  1. …
  2. …
- **Esperado:** …
- **Observado:** … (texto exacto de los mensajes de error, entre comillas)
- **Reproducible:** siempre / 2 de 3 intentos / una vez
- **Evidencia:** `evidencias/101500_TESTESSCH-1A2B_error.png`
- **Consola / registro:** (si se lanzó desde la terminal: últimas líneas relevantes de `salidas/consola.log`)
- **También ocurre en:** …
- **Impacto en lo siguiente:** ¿bloquea otros casos? ¿cuáles se marcaron BLOQUEADO por esto?
