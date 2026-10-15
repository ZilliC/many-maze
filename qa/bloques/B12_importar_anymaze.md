# B12 — Importar desde ANY-maze

**Inventario:** botón *Import from ANY-maze* (pestaña File) y su menú (*Animals and treatments…*, *Test schedule…*,
*Experiment exported as XML…*, *Zone maps…*), el asistente de columnas, *Import animals* / *Import tests* (Experiment).
**Precondiciones:** un experimento **nuevo y guardado** por caso (`$QA/experimentos/imp_<caso>.mmaze`). Archivos en
`$QA/entradas/importar/`.

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B12-01 | *Animals and treatments…* con `animales_anymaze.csv`. Revisa el emparejamiento automático de columnas. | 12 animales, tratamientos Control/HFD, pesos, sexo, notas. | Cambia a mano una columna del asistente; cancela. |
| B12-02 | Igual con `animales.xlsx`, `animales_windows1252.csv`, `animales_con_problemas.csv`, `animales_solo_encabezado.csv`, `animales_vacio_0_bytes.csv`, `animales_1000.csv`. | Ver B05-14 (no repitas hallazgos: enlázalos). | |
| B12-03 | *Test schedule…* con `programa_pruebas.csv` (en un experimento con esos animales y con aparato `Open field`, y en otro sin ellos). | Pruebas creadas; animales/aparatos desconocidos avisados o creados (anota cuál). | |
| B12-04 | *Experiment exported as XML…* con `anymaze_experimento.xml`. | Animales M01 y «Animal 2», aparato *Box A* con zonas *Centre* y *Arena*, 2 pruebas *tracked*, etapa *Day 1*, notas, calibración 5 px/cm y otra de 4 px/cm por prueba. Results muestra 2 filas. | Importar dos veces (¿duplica?). En un experimento sin guardar (debe pedir guardar). |
| B12-05 | Igual con `anymaze_formato_real.xml` (Windows-1252, formato real). | Aparato *Pool* con *Platform* como disco recortado del *Pool*; resultados de entradas/tiempo coherentes. | |
| B12-06 | `anymaze_roto.xml` y `xml_que_no_es_anymaze.xml`. | Mensajes claros, nada importado a medias. | |
| B12-07 | *Zone maps…* con los dos mapas de `mapas_de_zonas/` (individual de *Box A*, combinado de *Box B*), y `mapa_roto.csv`. | Zonas *Centre* (Box A), *Left*/*Right* (Box B); el roto se rechaza con mensaje. | Importar el mapa en un aparato existente con otro tamaño. |
| B12-08 | *Import track data* (Test schedule) con `track_tabulado_metros.txt` usando el asistente: escala 1000 px/m, desplazamiento. | Track de 500 puntos, 25 fps. (Puede estar ya cubierto en B06-14: enlázalo.) | |
| B12-09 **[P]** | Guarda, cierra y reabre cada experimento importado. | Todo persiste. | |
