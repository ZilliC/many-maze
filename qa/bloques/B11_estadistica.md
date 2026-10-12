# B11 — Results ▸ Statistics

**Inventario:** «Results ▸ Statistics — CINTA», «Results ▸ Statistics» y sus vistas *Compare groups*, *Two factors*,
*Correlation*, *Grouped*, *Categorical*; la sección del inventario «Estadística: las 47 pruebas».
**Precondiciones:** copia del oráculo de estadística:

```bash
cp -R "$QA/oraculos/estadistica/estadistica.mmaze" "$QA/experimentos/B11_estadistica.mmaze"
```

Diseño: 3 tratamientos (Control, HFD, Fármaco) × 2 sexos × 4 animales = 24 animales, 3 etapas (Día 1–3), un campo
de animal «Genotipo» (KO/WT, desequilibrado entre tratamientos). Ya está rastreado.

**Oráculo:** `$QA/oraculos/estadistica/ESPERADO_ESTADISTICA.md`, secciones E01–E10. Los valores se calcularon con
scipy y con fórmulas de libro, no con el código de la app. Al crearlo se compararon con el módulo de estadística de
la app: coinciden en todo salvo Dunnett (H-P02). También está `estadistica_resultados.csv`, la tabla de la que salen
los números.

**Cómo leer los valores de la app:** *Copy summary* y *Copy table*, y después `read_clipboard`; o *Save report* /
*Save table* y abrir el archivo. Los números se comparan con lo que copies, no con lo que veas en el gráfico.

## Una prueba por fila: las 47

Una fila de bitácora por prueba de la sección «Estadística: las 47 pruebas» del inventario: qué elegiste en la página,
el valor de la app, el esperado (sección E del oráculo) y el estado. Si una prueba no se puede elegir en ninguna
vista, `FALLA` (S2) con la ruta que intentaste.

| Caso | Vista y ajustes | Esperado |
|---|---|---|
| B11-01 | *Compare groups*: medida *Total distance (cm)*, factor *Group*, **Only** Stage = Día 1, α 0,05, *Run*. *Descriptive statistics*. | E01 (n, media, DE, EEM, mediana de cada grupo) |
| B11-02 | Igual, factor **Sex**: *Automatic* paramétrico, luego no paramétrico; después elige a mano *Student's t-test*, *Kolmogorov-Smirnov*, *Brunner-Munzel*. Tamaño del efecto. | E02. Mann-Whitney puede ser exacto o con aproximación normal: anota cuál |
| B11-03 | Factor **Stage**, *Repeated measures*, sin filtro: *Paired t-test* y *Wilcoxon* (solo Día 1 y Día 2: filtro Stage ≠ Día 3), *Repeated-measures ANOVA* y *Friedman* (3 etapas). | E03 |
| B11-04 | *Test value* = 200: *One-sample t-test* y *Wilcoxon signed-rank vs value*, todos juntos y por grupo. | E04 |
| B11-05 | Factor **Group**: *One-way ANOVA*, *Welch's ANOVA*, *Alexander-Govern*, *Kruskal-Wallis*, *Mood's median test*; después **cada** post-hoc (Tukey, Bonferroni, Holm, Šidák, FDR, Dunnett con control = Control, Games-Howell, Dunn, LSD, Scheffé, SNK, Duncan) con su p por par. **H-P02:** con Dunnett elegido, pulsa *Recalculate* 3 veces y anota los 3 p de Control–HFD. | E05 y E06. Bonferroni, Holm, Šidák y FDR deben coincidir con la fila «t de Welch por pares». Dunnett: el exacto es 6,997e-05 · 1,624e-05; si los 3 p cambian entre sí, `FALLA H-P02 (sigue)` |
| B11-06 | *Two factors*: 1.º *Group*, 2.º *Sex*, diseño entre sujetos, Only Día 1. Luego *Mixed* (Group entre, Stage dentro), *Scheirer-Ray-Hare*, *Aligned rank transform*, *ANCOVA* (covariable *Centre: time (%)*). Todos los tipos de gráfico. | E07: F, gl y p de Group, Sex y la interacción. Los demás deben correr, con gl coherentes |
| B11-07 | *Correlation*: X *Total distance (cm)*, Y *Centre: time (%)*, Only Día 1; Pearson, Spearman, Kendall; *Linear regression*; colorear por Group (ANCOVA). | E08 |
| B11-08 | *Compare groups* → pestaña *Assumption checks*: Shapiro-Wilk por grupo, D'Agostino, Levene, Brown-Forsythe, Bartlett, Fligner-Killeen (las que aparezcan; las que no, búscalas en otras vistas). | E09 |
| B11-09 | *Categorical*: resultado **Genotipo**, agrupar por Group, Only Día 1; chi-cuadrado de independencia, G, bondad de ajuste y Fisher (si la tabla lo permite). | E10. Tabla Control 3/5, HFD 5/3, Fármaco 7/1 (KO/WT) |
| B11-10 | *Grouped*: 1, 2 y 3 variables (Group, Sex, Stage). | Descriptivos por celda = medias de `estadistica_resultados.csv` (comprueba 3 celdas con Bash) |

## Gráficos, salidas y casos límite

| Caso | Pasos | Esperado |
|---|---|---|
| B11-20 **[O]** | Cada tipo de gráfico (columna, puntos, caja, violín; línea en *Two factors*) × barras de error (EEM, DE, IC 95 %) × *Show individual values*. | Se dibuja; colores de tratamiento; las estrellas coinciden con los p |
| B11-21 | *Copy summary*, *Copy figure*, *Copy table*; *Save figure* (PNG/SVG/PDF), *Save report*, *Save table* → `$QA/salidas/B11/`; `inspeccionar.py`. | Archivos válidos; los números de la tabla guardada = los de la pantalla |
| B11-22 **[P]** | Ajustes (medida, factor, gráfico, α, prueba elegida) → guardar, cerrar, reabrir. | Persisten |
| B11-23 | Excluye una prueba en Test schedule y *Recalculate*. | n baja en 1 en ese grupo; los p cambian |
| B11-24 | Casos límite: filtro que deja 1 animal por grupo; un solo grupo; medida constante (*Test duration (s)*); un factor con 24 niveles (*Animal*); α = 0 y α = 1; medida con celdas vacías. | Mensajes claros («Not enough data…»), nunca un error técnico ni `nan` sin explicar |
| B11-25 | Demo original (`$QA/entradas/demo_original`, copia): *Centre: time (%)* por Group (el grupo *Anxious* tiene varianza 0). | Welch t y Mann-Whitney = `referencia/ORACULOS.txt`; manejo razonable de la varianza 0 |
| B11-26 | Blind testing activo. | Grupos con códigos en tablas, gráficos y salidas |
