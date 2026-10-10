"""A tiny SMTP server on localhost for the e-mail tests: it accepts every message (no TLS, no login) and keeps
them, parsed, in ``messages`` (Python 3.12 has no smtpd module any more)."""

from __future__ import annotations

import email
import email.policy
import socketserver
import threading


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        srv = self.server
        self.reply("220 localhost fake SMTP")
        mail_from, rcpt = "", []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            cmd = line[:4].upper()
            if cmd in ("EHLO", "HELO"):
                self.reply("250-localhost")
                self.reply("250 SIZE 50000000")
            elif cmd == "MAIL":
                mail_from, rcpt = line.split(":", 1)[1].strip(), []
                self.reply("250 OK")
            elif cmd == "RCPT":
                rcpt.append(line.split(":", 1)[1].strip().strip("<>"))
                self.reply("250 OK")
            elif cmd == "DATA":
                self.reply("354 End data with <CR><LF>.<CR><LF>")
                data = []
                while True:
                    part = self.rfile.readline()
                    if part in (b".\r\n", b".\n", b""):
                        break
                    data.append(part[1:] if part.startswith(b"..") else part)
                msg = email.message_from_bytes(b"".join(data), policy=email.policy.default)
                srv.messages.append({"from": mail_from, "to": list(rcpt), "message": msg})
                self.reply("250 OK queued")
            elif cmd == "QUIT":
                self.reply("221 Bye")
                return
            elif cmd in ("RSET", "NOOP"):
                self.reply("250 OK")
            else:
                self.reply("502 Command not implemented")


class FakeSMTP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.messages: list[dict] = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.shutdown()
        self.server_close()


def notify_device(port: int, **kw) -> dict:
    """An alert device (I/O device type notify) sending through the fake server."""
    d = {"name": "Alerts", "type": "notify", "smtp_host": "127.0.0.1", "smtp_port": port, "smtp_user": "",
         "smtp_password": "", "from_addr": "lab@example.org", "email_to": "pi@example.org", "sms_to": ""}
    d.update(kw)
    return d
