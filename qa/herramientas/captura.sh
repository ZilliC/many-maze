#!/bin/zsh
# Guarda una captura de las ventanas de mANY-MAZE como evidencia (solo la app: no el resto de la pantalla, y la
# ventana entera aunque se salga de la pantalla):
#   qa/herramientas/captura.sh <carpeta_prueba> <ID> [descripción]
# → <carpeta_prueba>/evidencias/<hora>_<ID>_<descripción>.png (una por ventana abierta: _2, _3… para los diálogos);
#   imprime las rutas. Sin ventanas de la app, captura la pantalla entera (y lo dice).
set -e
dest="$1"; id="$2"; desc="${3:-}"
[[ -z "$dest" || -z "$id" ]] && { echo "uso: captura.sh <carpeta_prueba> <ID> [descripción]"; exit 2; }
here="${0:A:h}"
slug=$(echo "$desc" | tr ' /:' '___' | tr -cd '[:alnum:]_-' | cut -c1-40)
base="$dest/evidencias/$(date +%H%M%S)_${id}${slug:+_$slug}"
mkdir -p "$dest/evidencias"
bin="${TMPDIR:-/tmp}/mmz_ventanas"
[[ -x "$bin" && "$bin" -nt "$here/ventanas.swift" ]] || swiftc -O "$here/ventanas.swift" -o "$bin" 2>/dev/null
ids=($("$bin" | cut -f1))
if (( ${#ids} == 0 )); then
  screencapture -x "$base.png"; echo "$base.png (pantalla entera: no hay ventanas de la app)"; exit 0
fi
n=1
for w in $ids; do
  out="$base$([[ $n -gt 1 ]] && echo _$n).png"
  screencapture -x -o -l "$w" "$out" && echo "$out"
  n=$((n + 1))
done
