# B05 — Experiment: tratamientos y animales

**Inventario:** «Experiment ▸ Experiment — CINTA», «… ▸ Treatments», «… ▸ Animals», diálogos «Add animals»,
«Randomise treatments», «Dose calculator», «Training criteria», «Weigh animals».
**Precondiciones:** experimento nuevo `$QA/experimentos/animales.mmaze` (open field) y copia del demo.
Oráculos: `referencia/ORACULOS.txt` (dosis, variabilidad).

## Tratamientos

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B05-01 **[P]** | *Add treatment* `Control`, `HFD`, `Fármaco`; *Rename*; *Colour*. | Colores usados en gráficos de Results y Statistics. | Nombre vacío, repetido, 300 caracteres, emoji. Mismo color para dos. |
| B05-02 | *Delete treatment* con animales asignados. | Avisa; los animales quedan sin tratamiento o se impide. | Borrar todos. |

## Animales

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B05-10 **[P]** | *Add animal* ×3, *Add animals* (en bloque: 10 con prefijo). Edita ID, tratamiento, sexo, notas en la tabla. | Persisten. | ID repetido, vacío, con espacios al final, 300 caracteres; sexo con valores raros. 0 y 10 000 animales en bloque (rendimiento). |
| B05-11 | *Duplicate*, *Delete animals* (uno, varios, todos), con pruebas asociadas. | Borrar uno con pruebas avisa qué pasa con ellas. | |
| B05-12 **[P]** | *Add field* `Peso (g)`, `Camada`; *Rename field*; *Remove field*. | Columnas nuevas visibles en Results (*Information columns*). | Campo con nombre de una columna existente (`Sex`, `Group`), vacío, `{x}`. |
| B05-13 | Ordenar por columnas, seleccionar con `Shift`/`Cmd`, copiar/pegar celdas desde/hacia la tabla. | Funciona como hoja de cálculo. | Pegar 1000 filas desde el portapapeles. |
| B05-14 | *Import animals*: `animales_anymaze.csv`, `animales.xlsx`, `animales_windows1252.csv`, `animales_con_problemas.csv`, `animales_solo_encabezado.csv`, `animales_vacio_0_bytes.csv`, `animales_1000.csv` (todos en `$QA/entradas/importar/`). Revisa el asistente de columnas. | Los buenos importan bien (acentos intactos en el de Windows-1252; notas en *Notes*). Los problemáticos: avisa de cada problema (ID repetido, sin ID, peso no numérico…) en lugar de importar basura en silencio. Anota fila por fila qué hizo. | Importar dos veces el mismo archivo (¿duplica o actualiza?). Cancelar el asistente. |
| B05-15 | *Import tests* desde `programa_pruebas.csv`. | Crea las pruebas de esos animales (ver Test schedule). | Animales que no existen. |
| B05-16 | *Export CSV* → `$QA/salidas/animales.csv`; `inspeccionar.py`; reimpórtalo en un experimento vacío. | Ida y vuelta sin pérdidas (acentos, notas, campos). | Destino `solo_lectura`. |

## Acciones

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B05-20 | *Retire* un animal con pruebas pendientes; pasa el ratón por *Status*; *Reinstate*. | Pruebas pendientes saltadas y luego reanudadas; motivo en tooltip. | Retirar todos; reinstaurar uno no retirado. |
| B05-21 | *Weigh*: pesa 3 animales a mano. | Columna *Weight (g)* e historial. | Peso 0, negativo, texto, 1e9. Sin balanza: *Read scale* explica que no hay balanza. |
| B05-22 | *Randomise treatments* con 12 animales, tratamientos marcados Control/HFD, *Balance within* sexo, semilla `42`. Repite con la misma semilla. | Números por grupo difieren en ≤1; por sexo equilibrado; misma semilla = misma asignación (anota ambas). | Sin tratamientos marcados; 1 animal; animales retirados (excluidos); semilla texto. |
| B05-23 | *Dose calculator*: columna de peso, dosis 10 mg/kg, concentración 2 mg/mL. | Volúmenes = oráculo (25 g → 0.125 mL; 250 g → 1.25 mL). Un animal con su propio *Dose (mg/kg)* usa esa. | Concentración 0, peso vacío, solo seleccionados. |
| B05-24 | *Training criteria…* (diálogo): con el criterio de B03-31 y pruebas con resultados, revisa la tabla; *Apply*. | Ensayos hechos, cuándo se cumplió, variabilidad, resultado; *Apply* completa etapas y retira. | Sin criterios definidos. |
| B05-25 | *Reveal treatment coding* con y sin ciego, con y sin permisos (B02). | Pide confirmación; revela. | |

## Persistencia y ciego

| Caso | Pasos | Esperado |
|---|---|---|
| B05-30 **[P]** | Guarda, cierra, reabre y repasa todo lo hecho en este bloque. | Todo persiste. |
| B05-31 | Con ciego activo, revisa esta página: códigos en lugar de nombres, color neutro, no se puede renombrar ni recolorear tratamientos. | Coherente con la guía (§6 *Blind testing*). |
| B05-40 | Criterio con variabilidad: crea 3 pruebas de un animal en `Día 1` con *Total distance* conocidas (usa las del demo duplicadas o puntuadas) o, si no puedes forzar valores, `N/A` explicado. | Variabilidad mostrada = fórmula de la guía. |
