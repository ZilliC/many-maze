"""Users, roles and passwords, and experiments protected by a password: convenience-grade security (ANY-maze's
own words are "casual security"). It stops colleagues from changing a protocol or unblinding an experiment by
mistake and keeps a casual reader out of an experiment file; it does not stop someone determined who can edit the
files (user passwords and roles live in ``project.json``, which is plain JSON unless the experiment itself has a
password).

Users (``Project.users``): ``[{"name", "role": "admin" | "user", "pw_hash": "scrypt$…" | None}]`` for the users of
``Project.experimenters`` that have a password or a role; a user without an entry is an ordinary user without a
password. Passwords are hashed with scrypt (``hashlib``, a random 16-byte salt per password), never stored. The
first user who sets a password becomes the administrator. While no administrator with a password exists, nothing is
restricted and nobody is asked for a password: experiments without passwords behave as before users had any.

``Project.security``: ``{"reveal_codes": "admin" | "anyone", "lock_protocol": bool}`` — who may reveal the treatment
coding of a blind experiment, and whether only administrators may change the protocol (its elements, the apparatus
and the procedures). Both apply only while an administrator exists.

Experiment password: ``project.json`` (its automatic backups, the crash-recovery files of live tests and the copy in
an archive) is stored encrypted — AES-256-GCM, the key derived from the password with scrypt — in a JSON envelope
``{"format": "manymaze-project-encrypted", "kdf": {...}, "nonce", "data"}``. The ``cryptography`` package is
imported only when such a file is read or written, so unprotected experiments never need it. Tracks, recordings,
exports, the I/O devices' secrets (``io-secrets.json``, readable by its owner only) and the lock file are not
encrypted: the lock must stay readable so that another program can tell who has the experiment open.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import zlib

ROLES = {"admin": "Administrator", "user": "User"}
REVEAL = {"anyone": "Anyone", "admin": "Administrators only"}
SECURITY_DEFAULTS = {"reveal_codes": "anyone", "lock_protocol": False}
ENCRYPTED_FORMAT = "manymaze-project-encrypted"
# scrypt costs: user password hashes (checked when a user is chosen) and the experiment key (derived once per open)
HASH_PARAMS = {"n": 2 ** 14, "r": 8, "p": 1}
KEY_PARAMS = {"n": 2 ** 15, "r": 8, "p": 1}
_MAXMEM = 2 ** 26  # 64 MiB: scrypt with n = 2**15, r = 8 needs 32 MiB, more than OpenSSL's default limit
_AAD = b"manymaze-project/1"


class PasswordRequired(ValueError):
    """The experiment file is encrypted and no password (or the wrong one) was given."""

    def __init__(self, what: str = "This experiment", wrong: bool = False):
        self.what, self.wrong = what, wrong
        super().__init__(f"{what} is protected by a password" + (": the password is wrong" if wrong else
                                                                ": enter its password"))


class WrongPassword(PasswordRequired):
    def __init__(self, what: str = "This experiment"):
        super().__init__(what, wrong=True)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s) -> bytes:
    return base64.b64decode(str(s).encode("ascii"))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, length: int = 32) -> bytes:
    return hashlib.scrypt(str(password).encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=length,
                          maxmem=_MAXMEM)


# ================================================================================== user passwords
def hash_password(password: str) -> str:
    """A salted scrypt hash of a password: ``scrypt$n$r$p$salt$hash`` (base64)."""
    salt = os.urandom(16)
    n, r, p = HASH_PARAMS["n"], HASH_PARAMS["r"], HASH_PARAMS["p"]
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(_scrypt(password, salt, n, r, p))}"


def check_password(password: str, stored: str | None) -> bool:
    """Whether ``password`` matches a hash made by :func:`hash_password` (False for a malformed hash)."""
    try:
        kind, n, r, p, salt, digest = str(stored or "").split("$")
        if kind != "scrypt":
            return False
        want = _unb64(digest)
        got = _scrypt(password, _unb64(salt), int(n), int(r), int(p), len(want))
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(got, want)


# ================================================================================== users and roles
def users_from(v) -> list[dict]:
    """``Project.users`` from project.json (anything malformed is left out)."""
    out, seen = [], set()
    for u in v if isinstance(v, list) else []:
        if not isinstance(u, dict) or not str(u.get("name", "")).strip():
            continue
        name = " ".join(str(u["name"]).split())
        if name in seen:
            continue
        seen.add(name)
        role = u.get("role") if u.get("role") in ROLES else "user"
        pw = u.get("pw_hash") if isinstance(u.get("pw_hash"), str) and u.get("pw_hash") else None
        out.append({"name": name, "role": role, "pw_hash": pw})
    return out


def security_from(v) -> dict:
    """``Project.security`` from project.json, with the defaults for what is missing or invalid."""
    d = dict(SECURITY_DEFAULTS)
    if isinstance(v, dict):
        if v.get("reveal_codes") in REVEAL:
            d["reveal_codes"] = v["reveal_codes"]
        d["lock_protocol"] = bool(v.get("lock_protocol", False))
    return d


def user_entry(project, name: str) -> dict | None:
    name = " ".join(str(name or "").split())
    return next((u for u in project.users if u.get("name") == name), None) if name else None


def has_password(project, name: str) -> bool:
    u = user_entry(project, name)
    return bool(u and u.get("pw_hash"))


def role_of(project, name: str) -> str:
    """"admin" for an administrator (who must have a password), else "user"."""
    u = user_entry(project, name)
    return "admin" if u and u.get("role") == "admin" and u.get("pw_hash") else "user"


def admins(project) -> list[str]:
    """The administrators: users with the admin role and a password."""
    return [u["name"] for u in project.users if u.get("role") == "admin" and u.get("pw_hash")]


def security_on(project) -> bool:
    """An administrator exists: the security settings apply and users with a password are asked for it."""
    return bool(admins(project))


def is_admin(project, name: str | None = None) -> bool:
    """Whether the user (default: the current user) is an administrator."""
    return role_of(project, project.current_user if name is None else name) == "admin"


def can(project, what: str, name: str | None = None) -> bool:
    """Whether the user (default: the current user) may do ``what``: "reveal_codes" (reveal the treatment coding
    of a blind experiment), "edit_protocol" or "manage" (roles, other users' passwords, the security settings and
    the experiment password). Everything is allowed while no administrator exists."""
    if not security_on(project) or is_admin(project, name):
        return True
    sec = security_from(project.security)
    if what == "reveal_codes":
        return sec["reveal_codes"] == "anyone"
    if what == "edit_protocol":
        return not sec["lock_protocol"]
    return False


def verify(project, name: str, password: str) -> bool:
    """Whether ``password`` is the user's (True for a user without a password)."""
    u = user_entry(project, name)
    return not (u and u.get("pw_hash")) or check_password(password, u["pw_hash"])


def _entry(project, name: str) -> dict:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("A user needs a name")
    if name not in project.experimenters:
        project.experimenters.append(name)
    u = user_entry(project, name)
    if u is None:
        u = {"name": name, "role": "user", "pw_hash": None}
        project.users.append(u)
    return u


def _prune(project):
    """Entries that say nothing (an ordinary user without a password) are not kept."""
    project.users[:] = [u for u in project.users if u.get("pw_hash") or u.get("role") == "admin"]


def set_password(project, name: str, password: str | None) -> str:
    """Set (or with None / "" remove) a user's password; the first user who sets one becomes the administrator,
    and an administrator who removes theirs becomes an ordinary user. Returns the user's role. (Who may do it is the
    caller's business: the user themself, or an administrator.)"""
    u = _entry(project, name)
    if password:
        first = not security_on(project)
        u["pw_hash"] = hash_password(password)
        if first:
            u["role"] = "admin"
    else:
        u["pw_hash"] = None
        u["role"] = "user"
    role = role_of(project, u["name"])
    _prune(project)
    return role


def set_role(project, name: str, role: str):
    """Make a user an administrator ("admin", only with a password) or an ordinary user ("user"). The last
    administrator stays one (otherwise nobody could change the security settings any more)."""
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}")
    if role == "admin" and not has_password(project, name):
        raise ValueError(f"{name} has no password: an administrator needs one")
    if role == "user" and admins(project) == [" ".join(str(name).split())]:
        raise ValueError(f"{name} is the only administrator")
    u = _entry(project, name)
    u["role"] = role
    _prune(project)


def remove_user(project, name: str):
    """Forget a user's role and password (the user is removed from the experiment's users)."""
    project.users[:] = [u for u in project.users if u.get("name") != name]


def set_security(project, reveal_codes: str | None = None, lock_protocol: bool | None = None) -> dict:
    sec = security_from(project.security)
    if reveal_codes is not None:
        if reveal_codes not in REVEAL:
            raise ValueError(f"Unknown setting {reveal_codes!r}")
        sec["reveal_codes"] = reveal_codes
    if lock_protocol is not None:
        sec["lock_protocol"] = bool(lock_protocol)
    project.security = sec
    return sec


# ================================================================================== experiment password
def is_encrypted(d) -> bool:
    """Whether a decoded experiment file (or crash-recovery file) is an encrypted envelope."""
    return isinstance(d, dict) and d.get("format") == ENCRYPTED_FORMAT


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover - a dependency, but an old environment may lack it
        raise RuntimeError("Experiments protected by a password need the cryptography package "
                           "(pip install cryptography)") from e
    return AESGCM(key)


@functools.lru_cache(maxsize=8)
def _derived(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return _scrypt(password, salt, n, r, p, 32)


class ExperimentKey:
    """The key of an experiment password (scrypt with its salt): encrypts the experiment's files. The key is derived
    once; every file gets a new random nonce."""

    def __init__(self, password: str, salt: bytes | None = None, n: int = KEY_PARAMS["n"], r: int = KEY_PARAMS["r"],
                 p: int = KEY_PARAMS["p"]):
        if not password:
            raise ValueError("The experiment password is empty")
        self.salt = salt or os.urandom(16)
        self.n, self.r, self.p = int(n), int(r), int(p)
        self._key = _derived(password, self.salt, self.n, self.r, self.p)

    def encrypt(self, text: str) -> str:
        """The envelope (JSON text) of a text: compressed, then encrypted with AES-256-GCM."""
        nonce = os.urandom(12)
        data = _aesgcm(self._key).encrypt(nonce, zlib.compress(text.encode("utf-8"), 6), _AAD)
        return json.dumps({"format": ENCRYPTED_FORMAT, "version": 1, "cipher": "AES-256-GCM", "compression": "zlib",
                           "kdf": {"name": "scrypt", "n": self.n, "r": self.r, "p": self.p, "salt": _b64(self.salt)},
                           "nonce": _b64(nonce), "data": _b64(data)}, indent=1)

    @classmethod
    def for_envelope(cls, envelope: dict, password: str) -> ExperimentKey:
        """The key of an envelope (its salt and costs) for a password; decrypt() tells whether it is right."""
        kdf = envelope.get("kdf") or {}
        return cls(password, _unb64(kdf.get("salt", "")), kdf.get("n", KEY_PARAMS["n"]), kdf.get("r", KEY_PARAMS["r"]),
                   kdf.get("p", KEY_PARAMS["p"]))

    def decrypt(self, envelope: dict, what: str = "This experiment") -> str:
        try:
            plain = _aesgcm(self._key).decrypt(_unb64(envelope["nonce"]), _unb64(envelope["data"]), _AAD)
        except (KeyError, ValueError, TypeError) as e:
            raise ValueError(f"{what}: the encrypted file is damaged") from e
        except Exception as e:  # cryptography's InvalidTag: another password (or a damaged file)
            raise WrongPassword(what) from e
        return (zlib.decompress(plain) if envelope.get("compression") == "zlib" else plain).decode("utf-8")


def decrypt(envelope: dict, password: str | None, what: str = "This experiment") -> tuple[str, ExperimentKey]:
    """The text in an envelope and the key that opened it. PasswordRequired without a password, WrongPassword when
    it does not open it."""
    if not password:
        raise PasswordRequired(what)
    key = ExperimentKey.for_envelope(envelope, password)
    return key.decrypt(envelope, what), key


def loads(text: str, password: str | None = None, what: str = "This experiment") -> tuple[dict, ExperimentKey | None]:
    """A decoded experiment (or crash-recovery) file, decrypted with the password when it is an envelope; returns
    (data, key or None)."""
    d = json.loads(text)
    if not is_encrypted(d):
        return d, None
    plain, key = decrypt(d, password, what)
    return json.loads(plain), key
