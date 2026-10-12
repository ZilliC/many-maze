# B02 — Usuarios, contraseñas y seguridad

**Inventario:** botón de usuario (esquina), *File ▸ Current user… / Users and security… / Protect experiment… /
Allowed programs…*, diálogos «Users and security», «Allowed programs».
**Precondiciones:** copia nueva del demo: `cp -R "$QA/entradas/demo_original/Demo open field.mmaze" "$QA/experimentos/seguridad.mmaze"`.
Usa contraseñas de prueba inventadas y **anótalas en la bitácora** (son de prueba, no de nadie).

| Caso | Pasos | Esperado | Límites |
|---|---|---|---|
| B02-01 | Botón «No user» ▸ *New user…* → `Ana`. Luego `Beto`. Cambia entre ellos. | El botón muestra el usuario; se recuerda al relanzar. | Nombre vacío, repetido, con acentos, 300 caracteres. |
| B02-02 | Con `Ana`, rastrea o puntúa una prueba (cualquiera). Ve a Test schedule. | Columna *User* = Ana en esa prueba. | Cámbialo a mano en la celda. *Set user* con varias pruebas seleccionadas. |
| B02-03 | Menú de usuario ▸ *Set password…* para Ana. | Ana pasa a ser **administradora**. | Contraseña vacía, de 1 carácter, con emoji; confirmación distinta. |
| B02-04 | Cambia a Ana desde otro usuario. | Pide contraseña; 3 intentos; cancelar deja el usuario anterior. | Contraseña mal 3 veces. |
| B02-05 | *File ▸ Users and security…* (como Ana): haz admin a Beto (tras darle contraseña), activa *Lock the protocol* y *Reveal treatment coding: Administrators only*. | Se guarda. | Intenta quitar el rol al último administrador: se impide. |
| B02-06 **[P]** | Cambia a un usuario sin contraseña (`Carla`). Intenta editar Protocol, Apparatus, procedimientos (también en Run tests). | Todo en solo lectura, con explicación; sí puede rastrear, puntuar y ver resultados. | Prueba atajos: pegar en el mapa, borrar zona con Supr, importar aparato, *Apply template*. Ninguno debe colarse. |
| B02-07 | Como Carla con *Blind testing* activo (actívalo antes como admin): *Reveal treatment coding*. | Se niega. Como Ana: pide confirmación y revela. | |
| B02-08 | *Remove a user from this experiment…* | Quita de la lista; sus pruebas mantienen el nombre. | Quitar al usuario actual; quitar a un admin sin serlo. |
| B02-09 **[P]** | *File ▸ Protect experiment…* (como Ana) con contraseña `qa-prueba-1`. Cierra y reabre. | Pide contraseña al abrir; `project.json` ya no es JSON legible (`head -c 200`). | Contraseña errónea; cancelar; *Based on* este experimento (B01-04); importar su aparato (B04); `manymaze project … info` sin y con `MANYMAZE_PASSWORD`. |
| B02-10 | Cambia la contraseña del experimento; luego déjala vacía. | Pide la actual; vacía = sin protección (`project.json` legible otra vez). | |
| B02-11 | *Restore a backup* de una copia hecha con la contraseña anterior. | Pide esa contraseña. | |
| B02-12 | *File ▸ Allowed programs…* | Abre, se entiende qué controla, se puede añadir/quitar y cancelar. Si no se entiende para qué sirve, Observación. | |
| B02-13 | Quita la contraseña a Ana (*clear password* tras confirmación). | Nadie queda a cargo: todo vuelve a estar permitido. | |
