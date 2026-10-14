"""Servidor de correo falso en 127.0.0.1 para probar «E-mail report» y las alertas sin enviar nada real.

    .venv/bin/python qa/herramientas/smtp_local.py CARPETA_PRUEBA [--puerto 2525]

Acepta todo (sin TLS ni contraseña) y guarda cada mensaje en CARPETA_PRUEBA/salidas/correos/NNN.eml, con un resumen
en la consola (remitente, destinatarios, asunto, adjuntos). Ctrl+C para terminar.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.fake_smtp import FakeSMTP, _Handler  # noqa: E402


class FixedPort(FakeSMTP):
    def __init__(self, port):  # noqa: D107 — como FakeSMTP pero en un puerto fijo
        import socketserver
        import threading

        socketserver.ThreadingTCPServer.__init__(self, ("127.0.0.1", port), _Handler)
        self.messages = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)


def next_number(out: Path) -> int:
    """Último número NNN.eml que ya existe en la carpeta (0 si no hay): se sigue desde ahí, sin pisar nada."""
    nums = [int(f.stem) for f in out.glob("*.eml") if f.stem.isdigit()]
    return max(nums, default=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta", type=Path)
    ap.add_argument("--puerto", type=int, default=2525)
    a = ap.parse_args()
    out = a.carpeta / "salidas" / "correos"
    out.mkdir(parents=True, exist_ok=True)
    with FixedPort(a.puerto) as srv:
        print(f"SMTP falso escuchando en 127.0.0.1:{a.puerto}; correos en {out}", flush=True)
        seen = 0
        base = next_number(out)  # correos de arranques anteriores: la numeración continúa
        while True:
            time.sleep(0.5)
            while seen < len(srv.messages):
                m = srv.messages[seen]
                seen += 1
                msg = m["message"]
                n = base + seen
                (out / f"{n:03d}.eml").write_bytes(msg.as_bytes())
                att = [(p.get_filename(), len(p.get_payload(decode=True) or b"")) for p in msg.iter_attachments()]
                print(f"[{n:03d}] de {m['from']} a {m['to']} — «{msg['subject']}» — adjuntos: {att}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
