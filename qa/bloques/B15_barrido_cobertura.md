# B15 — Barrido de cobertura del inventario

Objetivo: que **ningún** elemento de `INVENTARIO_UI.md` quede sin probar: controles de páginas y cintas, ítems de
los menús de la cinta, controles de cada diálogo, controles que solo aparecen en los modos TakeNote / solo I/O, los
mensajes de la app (B14-40), los eventos, acciones y funciones de los procedimientos (B09-40 a 42) y las 47 pruebas
estadísticas (B11).

1. Lista los IDs del inventario que no aparecen en `bitacora/COBERTURA.md`:

   ```bash
   cd "$QA/bitacora"
   grep -o '`[A-Z0-9]*-[0-9A-F]*[0-9]*`' INVENTARIO_UI.md | tr -d '`' | sort -u > todos.txt
   grep -o '^[A-Z0-9]*-[0-9A-F]*[0-9]*' COBERTURA.md | sort -u > cubiertos.txt
   comm -23 todos.txt cubiertos.txt > pendientes.txt; wc -l pendientes.txt
   ```

2. Para cada ID pendiente, en el orden del inventario: busca su fila (contexto, «Cómo llegar», tipo, nombre), llega al
   control y pruébalo con la regla 12 del plan (valor normal, límites, persistencia si es un ajuste). Anota en la
   bitácora con el caso `B15-<ID>` y en `COBERTURA.md`.
3. Controles que **no se encuentran** en la app (el inventario se generó con el demo; algunos dependen del tipo de
   test o del modo): anota `N/A (no visible en <contexto>)` y en qué condiciones crees que aparece. Si no aparece en
   ninguna, hallazgo *Observación* (posible control huérfano).
4. Controles que el inventario **no lista** y que encontraste durante la prueba (menús contextuales, diálogos
   internos, controles que solo aparecen con otro tipo de test): añádelos al final de `COBERTURA.md` en una sección
   «Fuera del inventario», con su ruta y estado.
5. Al terminar, `pendientes.txt` debe quedar vacío o con cada ID explicado.
