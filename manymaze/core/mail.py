"""Sending e-mail through the SMTP server of an alert device (I/O device type ``notify``): the sensor and procedure
alerts (:class:`.iodrivers.NotifyDevice`) and reports e-mailed from the Results page.

The server settings are the device's (``smtp_host``, ``smtp_port``, ``smtp_user``, ``from_addr``, ``email_to``);
its password is kept in ``io-secrets.json``, not in project.json, and is put back into the device when the
experiment opens. Port 465 uses SSL; any other port must offer STARTTLS before a password or a message is sent,
except a server on this computer (localhost), which may also be plain (a local relay or a test server).
"""

from __future__ import annotations

import ipaddress
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

TIMEOUT_S = 20.0
MAX_ATTACHMENTS_MB = 20.0  # most mail servers refuse larger messages


def mail_devices(project) -> list[dict]:
    """The experiment's alert devices that have an SMTP server (their configurations, secrets included)."""
    return [d for d in getattr(project, "io_devices", None) or []
            if isinstance(d, dict) and d.get("type") == "notify" and str(d.get("smtp_host", "")).strip()]


def addresses(text) -> list[str]:
    """Addresses from "a@x.org, b@y.org; c@z.org"."""
    return [a.strip() for a in str(text or "").replace(";", ",").split(",") if a.strip()]


def is_local(host: str) -> bool:
    """A server on this computer (no network between us and it)."""
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def build_message(cfg: dict, to, subject: str, text: str, attachments=()) -> EmailMessage:
    """The message: plain text with the files attached."""
    rcpt = addresses(to) if not isinstance(to, (list, tuple)) else list(to)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_addr") or cfg.get("smtp_user") or "manymaze@localhost"
    msg["To"] = ", ".join(rcpt)
    msg.set_content(text)
    for f in attachments:
        p = Path(f)
        ctype, enc = mimetypes.guess_type(p.name)
        if ctype is None or enc is not None:
            ctype = "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)
    return msg


def send_email(cfg: dict, to, subject: str, text: str, attachments=(), timeout: float = TIMEOUT_S) -> list[str]:
    """Send a message through the device's SMTP server; returns the recipients. Raises RuntimeError (no server,
    no recipient, attachments too large, no STARTTLS) or the smtplib / socket errors."""
    host, port = str(cfg.get("smtp_host", "")).strip(), int(cfg.get("smtp_port", 587) or 587)
    if not host:
        raise RuntimeError("no SMTP server configured")
    rcpt = addresses(to) if not isinstance(to, (list, tuple)) else [a for a in to if a]
    if not rcpt:
        raise RuntimeError("no e-mail address to send to")
    size = sum(Path(f).stat().st_size for f in attachments) / 1e6
    if size > MAX_ATTACHMENTS_MB:
        raise RuntimeError(f"the attachments are too large to e-mail ({size:.0f} MB, at most "
                           f"{MAX_ATTACHMENTS_MB:.0f} MB)")
    msg = build_message(cfg, rcpt, subject, text, attachments)
    ctx = ssl.create_default_context()
    if port == 465:
        srv = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
    else:
        srv = smtplib.SMTP(host, port, timeout=timeout)
    with srv:
        if port != 465:
            srv.ehlo()
            if srv.has_extn("starttls"):
                srv.starttls(context=ctx)
                srv.ehlo()
            elif not is_local(host):
                raise RuntimeError(f"the SMTP server {host} does not offer STARTTLS: nothing was sent (use port 465 "
                                   f"for SSL)")
        if cfg.get("smtp_user"):
            srv.login(str(cfg["smtp_user"]), str(cfg.get("smtp_password", "")))
        srv.send_message(msg, to_addrs=rcpt)
    return rcpt
