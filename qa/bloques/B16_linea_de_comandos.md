# B16 — Línea de comandos y coherencia con la interfaz

Con la herramienta Bash (no computer use). `MM=~/Downloads/many-maze/.venv/bin/manymaze`. Salidas en
`$QA/salidas/B16/`.

| Caso | Comando | Esperado | Límites |
|---|---|---|---|
| B16-01 | `$MM --help`, `$MM --version`, `$MM templates`, `$MM <sub> --help` para cada subcomando. | Ayuda clara; versión = *About* de la app. | Subcomando inexistente. |
| B16-02 | `$MM demo "$QA/salidas/B16/demo_cli.mmaze"` | Demo creado; ábrelo en la app: igual al de *Create demo experiment* (6 pruebas). | Carpeta existente; destino `solo_lectura`. |
| B16-03 | `$MM track "$QA/entradas/videos_reales/OFT_macho_1.mp4" --template open_field --bbox <x,y,w,h> --size-cm 100 -o "$QA/salidas/B16/oft.csv"` (saca el bbox mirando el fondo en la app). | CSV con medidas. | Video corrupto, bbox fuera de la imagen, plantilla inexistente. |
| B16-04 | `$MM project "<copia demo>" info / track / results -o x.csv / -o x.xlsx --bins / -o x.tsv / .slk / .dbf / .xml / report -o r.html` | Cada formato válido (`inspeccionar.py`); `results` = exportación de la app en B10 (`comparar_con_referencia.py`). | Proyecto inexistente; extensión no soportada. |
| B16-05 | `relink --folder`: mueve los videos de una copia del demo a otra carpeta y reenlaza. | Encuentra cada video por nombre; `info` ya no lista faltantes; la app los abre. | Dos videos con el mismo nombre en carpetas distintas (empate listado). |
| B16-06 | `MANYMAZE_WORKERS=1` y `=8` con `project … track` en una copia con tracks borrados (`Clear tracks`). | Mismos resultados; anota tiempos. | |
| B16-07 | Experimento cifrado (B02-09): `project … info` sin y con `MANYMAZE_PASSWORD`. | Pide/rechaza sin contraseña; funciona con ella. | Contraseña errónea. |
| B16-08 | Reporte guardado (B10-03) desde la línea de comandos (si la ayuda lo ofrece). | Igual al de la app. | |
