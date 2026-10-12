"""Describe cualquier archivo que haya exportado la interfaz, para verificarlo sin abrir otra aplicación.

    .venv/bin/python qa/herramientas/inspeccionar.py ARCHIVO [ARCHIVO…]

CSV / TSV / XLSX / SLK / DBF: filas, columnas, primeras columnas, celdas vacías, valores no numéricos donde se esperan
números, NaN / inf. XML: elemento raíz y conteos. HTML: título, imágenes, tablas, errores ("Traceback", "nan").
PNG / SVG / PDF / MP4: tamaño y dimensiones. Sale con código 1 si encuentra algo sospechoso.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SUSPECT = re.compile(r"Traceback|Exception|\bnan\b|\binf\b|None\b|#VALUE|#DIV", re.I)


def table_rows(p: Path):
    suf = p.suffix.lower()
    if suf in (".csv", ".tsv", ".txt"):
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=",\t;") if text.strip() else csv.excel
        return list(csv.reader(text.splitlines(), dialect))
    if suf == ".xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        print(f"  hojas: {wb.sheetnames}")
        return [["" if v is None else v for v in r] for r in wb[wb.sheetnames[0]].iter_rows(values_only=True)]
    if suf == ".slk":
        from manymaze.core.export import read_sylk

        return read_sylk(p)
    if suf == ".dbf":
        from manymaze.core.export import read_dbf

        head, body = read_dbf(p)
        return [head] + [list(r) for r in body]
    return None


def check(p: Path) -> bool:
    print(f"\n== {p}  ({p.stat().st_size:,} bytes)")
    if p.stat().st_size == 0:
        print("  ✗ archivo vacío")
        return False
    ok = True
    suf = p.suffix.lower()
    rows = table_rows(p)
    if rows is not None:
        rows = [r for r in rows if any(str(c).strip() for c in r)]
        if not rows:
            print("  ✗ sin filas")
            return False
        head, body = rows[0], rows[1:]
        print(f"  filas de datos: {len(body)}   columnas: {len(head)}")
        print(f"  encabezado: {' | '.join(map(str, head[:12]))}{' …' if len(head) > 12 else ''}")
        widths = {len(r) for r in body}
        if len(widths) > 1:
            print(f"  ⚠ filas con distinto número de columnas: {sorted(widths)}")
            ok = False
        bad = [(i + 2, head[j] if j < len(head) else j, c) for i, r in enumerate(body) for j, c in enumerate(r)
               if isinstance(c, str) and SUSPECT.search(c)]
        if bad:
            print(f"  ⚠ {len(bad)} celdas sospechosas, p. ej. {bad[:5]}")
            ok = False
        empty_cols = [h for j, h in enumerate(head) if all((j >= len(r) or str(r[j]).strip() == "") for r in body)]
        if empty_cols:
            print(f"  ⓘ columnas completamente vacías ({len(empty_cols)}): {empty_cols[:10]}")
        return ok
    text = p.read_text(encoding="utf-8", errors="replace") if suf in (".xml", ".html", ".htm", ".json", ".svg",
                                                                        ".m3u", ".txt") else None
    if suf == ".xml" and text is not None:
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(p.read_bytes())
        except ET.ParseError as e:
            print(f"  ✗ XML no válido: {e}")
            return False
        tags = {}
        for el in root.iter():
            t = el.tag.split("}")[-1]
            tags[t] = tags.get(t, 0) + 1
        print(f"  raíz: {root.tag}   elementos más frecuentes: {sorted(tags.items(), key=lambda x: -x[1])[:10]}")
    elif suf in (".html", ".htm") and text is not None:
        title = re.search(r"<title>(.*?)</title>", text, re.S)
        print(f"  título: {title.group(1).strip() if title else '—'}   imágenes: {text.count('<img')}   "
              f"tablas: {text.count('<table')}   svg: {text.count('<svg')}")
        hits = SUSPECT.findall(re.sub(r"<[^>]+>", " ", text))
        if hits:
            print(f"  ⚠ texto sospechoso: {sorted(set(hits))[:10]}")
            ok = False
    elif suf == ".png":
        import cv2

        im = cv2.imread(str(p))
        print(f"  imagen {im.shape[1]}×{im.shape[0]}" if im is not None else "  ✗ PNG ilegible")
        ok = im is not None and im.std() > 1
        if im is not None and im.std() <= 1:
            print("  ⚠ imagen de un solo color")
    elif suf in (".mp4", ".mov", ".avi", ".m4v"):
        import cv2

        c = cv2.VideoCapture(str(p))
        n, fps = c.get(cv2.CAP_PROP_FRAME_COUNT), c.get(cv2.CAP_PROP_FPS)
        okr, _ = c.read()
        print(f"  video {int(c.get(3))}×{int(c.get(4))}, {n:.0f} cuadros, {fps:.2f} fps, ~{n / max(fps, 1):.1f} s, "
              f"primer cuadro {'legible' if okr else 'ILEGIBLE'}")
        ok = okr and n > 0
    elif suf == ".pdf":
        head = p.read_bytes()[:5]
        print(f"  PDF {'válido' if head == b'%PDF-' else 'NO válido'}")
        ok = head == b"%PDF-"
    elif suf == ".svg" and text is not None:
        print(f"  SVG con {text.count('<path')} paths, {text.count('<text')} textos")
    elif suf == ".zip":
        import zipfile

        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            print(f"  zip con {len(names)} archivos; prueba de integridad: {z.testzip() or 'OK'}")
            print("  " + "\n  ".join(names[:25]))
    return ok


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    results = [check(Path(a)) for a in sys.argv[1:]]
    sys.exit(0 if all(results) else 1)
