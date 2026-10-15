# Bitácora de ejecución — prueba integral de mANY-MAZE con computer use

> Una fila por paso o por control probado, **en el momento** (no al final del bloque). Si la sesión se corta, esta
> bitácora es lo único que queda: debe permitir saber exactamente hasta dónde se llegó.

- Carpeta de prueba: `…` (ver `ENTORNO.txt`)
- Commit / versión (Help ▸ About): `…`
- Ejecutor (modelo): `…`
- Bloque(s) de esta sesión: `…`
- Inicio: `AAAA-MM-DD HH:MM` — Fin: `…`

## Registro

Estados: `OK` · `FALLA` (enlaza el hallazgo `H-###`) · `RARO` (funciona pero algo no convence: anótalo como hallazgo de
severidad *Observación*) · `BLOQUEADO` (no se pudo probar: motivo) · `N/A` (no aplica: motivo).

| Hora | Caso / ID inventario | Acción realizada | Resultado esperado | Resultado observado | Estado | Hallazgo | Evidencia |
|---|---|---|---|---|---|---|---|
| 10:02 | B01-03 | File ▸ Create demo experiment, carpeta `experimentos/` | Se abre el demo con 6 pruebas rastreadas | Abierto, 6 pruebas «tracked» | OK | | |

## Resumen del bloque (al terminar cada bloque)

| Bloque | Casos OK | FALLA | RARO | BLOQUEADO | N/A | Controles del inventario cubiertos | Notas |
|---|---|---|---|---|---|---|---|
