"""Users with roles and passwords, the security settings and experiments protected by a password (core.security):
password hashing, roles, what each user may do, the encrypted experiment file, its backups, archives,
crash-recovery files, the lock file, the command line and older experiments without these fields."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from manymaze.cli import main as cli_main
from manymaze.core import autosave, explock
from manymaze.core import security as sec
from manymaze.core.apparatus import Apparatus, load_apparatus_file
from manymaze.core.archive import archive_project, extract_archive
from manymaze.core.project import PROJECT_FILE, Project, is_protected_file, read_project_file
from manymaze.core.workflow import copy_protocol, remove_experimenter

ROOT = Path(__file__).resolve().parent.parent


def _project(tmp_path, name="Secret study") -> Project:
    p = Project(name=name, apparatus=[Apparatus(name="Box")])
    p.ensure_animal("M1", "Drug")
    p.ensure_animal("M2", "Saline")
    p.save(tmp_path / f"{name}.mmaze")
    return p


# ---------------------------------------------------------------------------------- passwords and roles
def test_password_hashes_are_salted_scrypt():
    a, b = sec.hash_password("hunter2"), sec.hash_password("hunter2")
    assert a != b and a.startswith("scrypt$") and "hunter2" not in a
    assert sec.check_password("hunter2", a) and sec.check_password("hunter2", b)
    assert not sec.check_password("hunter3", a)
    assert not sec.check_password("hunter2", "") and not sec.check_password("x", "md5$abc")


def test_first_password_makes_the_administrator_and_roles():
    p = Project()
    p.experimenters = ["Ann", "Bob", "Cy"]
    assert not sec.security_on(p) and sec.can(p, "reveal_codes") and sec.can(p, "manage")
    assert sec.verify(p, "Bob", "anything")  # no password: nothing to check
    assert sec.set_password(p, "Ann", "a-pass") == "admin"  # the first user with a password
    assert sec.set_password(p, "Bob", "b-pass") == "user"
    assert sec.admins(p) == ["Ann"] and sec.security_on(p)
    assert sec.verify(p, "Ann", "a-pass") and not sec.verify(p, "Ann", "b-pass")
    with pytest.raises(ValueError):
        sec.set_role(p, "Cy", "admin")  # an administrator needs a password
    with pytest.raises(ValueError):
        sec.set_role(p, "Ann", "user")  # the only administrator
    sec.set_role(p, "Bob", "admin")
    assert sorted(sec.admins(p)) == ["Ann", "Bob"]
    sec.set_role(p, "Ann", "user")
    assert sec.admins(p) == ["Bob"] and sec.role_of(p, "Ann") == "user"
    assert sec.set_password(p, "Bob", None) == "user"  # an administrator without a password is not one
    assert not sec.security_on(p)  # nobody is: nothing is restricted any more
    assert [u["name"] for u in p.users] == ["Ann"]  # entries that say nothing are not kept


def test_what_each_user_may_do():
    p = Project()
    sec.set_password(p, "Ann", "a")
    sec.set_password(p, "Bob", "b")
    p.current_user = "Bob"
    # defaults: anyone reveals, the protocol is not locked; only administrators manage
    assert sec.can(p, "reveal_codes") and sec.can(p, "edit_protocol") and not sec.can(p, "manage")
    sec.set_security(p, reveal_codes="admin", lock_protocol=True)
    assert not sec.can(p, "reveal_codes") and not sec.can(p, "edit_protocol")
    assert sec.can(p, "reveal_codes", "Ann") and sec.can(p, "edit_protocol", "Ann") and sec.can(p, "manage", "Ann")
    p.current_user = ""
    assert not sec.can(p, "edit_protocol")
    with pytest.raises(ValueError):
        sec.set_security(p, reveal_codes="nobody")


def test_users_and_security_saved_and_old_files_open(tmp_path):
    p = _project(tmp_path)
    sec.set_password(p, "Ann", "a")
    sec.set_security(p, "admin", True)
    p.save()
    raw = json.loads((p.path / PROJECT_FILE).read_text())
    assert raw["users"][0]["name"] == "Ann" and raw["users"][0]["pw_hash"].startswith("scrypt$")
    assert "a" not in [raw["users"][0]["pw_hash"]] and raw["security"] == {"reveal_codes": "admin",
                                                                          "lock_protocol": True}
    q = Project.load(p.path)
    assert sec.admins(q) == ["Ann"] and sec.verify(q, "Ann", "a") and q.security["lock_protocol"]
    # an experiment written before users, security, sync, plug-ins, weighing and the start delay existed
    for k in ("users", "security", "sync", "analysis_plugins", "require_weight_before_test",
              "start_switch_delay_s"):
        raw.pop(k)
    (p.path / PROJECT_FILE).write_text(json.dumps(raw))
    old = Project.load(p.path)
    assert old.users == [] and old.security == sec.SECURITY_DEFAULTS and old.sync == {}
    assert old.analysis_plugins == [] and not old.require_weight_before_test and old.start_switch_delay_s == 0
    assert not sec.security_on(old) and sec.can(old, "edit_protocol")
    # malformed entries are ignored
    raw["users"] = [{"name": "  Zed  ", "role": "boss", "pw_hash": 3}, "junk", {"role": "admin"}]
    raw["security"] = {"reveal_codes": "?", "lock_protocol": 1}
    (p.path / PROJECT_FILE).write_text(json.dumps(raw))
    odd = Project.load(p.path)
    assert odd.users == [{"name": "Zed", "role": "user", "pw_hash": None}]
    assert odd.security == {"reveal_codes": "anyone", "lock_protocol": True}


def test_users_follow_removal_and_protocol_copies(tmp_path):
    p = _project(tmp_path)
    p.experimenters = ["Ann", "Bob"]
    sec.set_password(p, "Ann", "a")
    sec.set_password(p, "Bob", "b")
    sec.set_security(p, "admin", True)
    new = Project(name="Follow-up")
    copy_protocol(p, new)
    assert sec.admins(new) == ["Ann"] and sec.verify(new, "Bob", "b") and new.security["lock_protocol"]
    assert not new.protected
    assert remove_experimenter(p, "Bob") and sec.user_entry(p, "Bob") is None


# ---------------------------------------------------------------------------------- experiment password
def test_protected_experiment_round_trip(tmp_path):
    p = _project(tmp_path)
    assert not p.protected and not is_protected_file(p.path)
    p.set_experiment_password("open sesame")
    p.save()
    text = (p.path / PROJECT_FILE).read_text()
    env = json.loads(text)
    assert env["format"] == sec.ENCRYPTED_FORMAT and env["cipher"] == "AES-256-GCM"
    assert env["kdf"]["name"] == "scrypt" and "Secret study" not in text and "Drug" not in text
    assert is_protected_file(p.path) and is_protected_file(p.path / PROJECT_FILE)
    with pytest.raises(sec.PasswordRequired) as e:
        Project.load(p.path)
    assert not e.value.wrong
    with pytest.raises(sec.WrongPassword):
        Project.load(p.path, password="open sesame!")
    q = Project.load(p.path, password="open sesame")
    assert q.protected and q.name == "Secret study" and [a.group for a in q.animals] == ["Drug", "Saline"]
    q.description = "changed"
    q.save()  # stays encrypted, with a new nonce
    env2 = json.loads((p.path / PROJECT_FILE).read_text())
    assert env2["nonce"] != env["nonce"] and env2["kdf"]["salt"] == env["kdf"]["salt"]
    assert Project.load(p.path, "open sesame").description == "changed"
    d, key = read_project_file(p.path / PROJECT_FILE, "open sesame")
    assert d["name"] == "Secret study" and key is not None
    # the protection removed: plain JSON again
    q.set_experiment_password(None)
    q.save()
    assert json.loads((p.path / PROJECT_FILE).read_text())["name"] == "Secret study"
    assert not Project.load(p.path).protected


def test_backups_follow_the_protection(tmp_path):
    p = _project(tmp_path)
    p.save()
    p.backup()  # a backup of the unprotected file
    assert p.list_backups()
    p.set_experiment_password("pw1")
    p.save()
    for b in p.list_backups():  # written again, encrypted: no plain copy of the experiment is left
        assert sec.is_encrypted(json.loads(b.read_text())), b.name
    q = Project.load(p.path, "pw1")
    restored = q.restore_backup(q.list_backups()[-1])
    assert restored.name == "Secret study" and restored.protected and restored.password == "pw1"
    # a new password: the backups follow it
    q.set_experiment_password("pw2")
    q.save()
    b = q.list_backups()[-1]
    with pytest.raises(sec.WrongPassword):
        read_project_file(b, "pw1")
    assert read_project_file(b, "pw2")[0]["name"] == "Secret study"
    # a backup encrypted with an older password needs that one
    (q.path / "backups" / "project-19990101-000000.json").write_text(
        sec.ExperimentKey("old").encrypt(json.dumps(q.to_dict())))
    old_b = q.path / "backups" / "project-19990101-000000.json"
    with pytest.raises(sec.WrongPassword):
        q.restore_backup(old_b)
    assert q.restore_backup(old_b, password="old").name == "Secret study"


def test_archive_and_crash_recovery_files_stay_encrypted(tmp_path):
    p = _project(tmp_path)
    p.set_experiment_password("pw")
    p.save()
    zp = archive_project(p, tmp_path / "a.zip")
    with zipfile.ZipFile(zp) as z:
        inner = z.read(f"Secret study.mmaze/{PROJECT_FILE}").decode()
    assert sec.is_encrypted(json.loads(inner)) and "Drug" not in inner
    out = extract_archive(zp, tmp_path / "unpacked")
    with pytest.raises(sec.PasswordRequired):
        Project.load(out)
    assert Project.load(out, "pw").animals[0].group == "Drug"
    # crash-recovery side file
    side = tmp_path / "side.autosave.json"
    autosave.write(str(side), {"meta": {"animal": "M1"}, "events": []}, p.file_key)
    assert sec.is_encrypted(json.loads(side.read_text())) and "M1" not in side.read_text()
    with pytest.raises(sec.PasswordRequired):
        autosave.read(str(side))
    assert autosave.read(str(side), "pw")["meta"] == {"animal": "M1"}
    saver = autosave.Autosaver(str(side), lambda: {"meta": {"animal": "M2"}}, lambda m: None, key=p.file_key)
    saver.flush()
    assert autosave.read(str(side), "pw")["meta"]["animal"] == "M2"


def test_recovery_of_an_encrypted_side_file(tmp_path):
    from manymaze.core.live import IOSession

    p = _project(tmp_path)
    p.set_experiment_password("pw")
    p.save()
    t = p.add_test("", "M1", "Box")
    p.save()
    path = autosave.path_for(p, t)
    s = IOSession(None, duration_s=0, start_mode="immediate", autosave_path=path, autosave_key=p.file_key,
                  autosave_meta={"test_id": t.id, "animal": "M1", "apparatus": "Box"})
    s.tick(0.0)
    s.tick(1.5)
    s.score("rear", "point")
    s.flush_autosave()
    assert sec.is_encrypted(json.loads(Path(path).read_text()))
    q = Project.load(p.path, "pw")
    rec = autosave.recover(q)
    assert [x.id for x in rec] == [t.id] and not Path(path).exists()
    assert sec.is_encrypted(json.loads((p.path / PROJECT_FILE).read_text()))  # saved encrypted


def test_lock_file_of_a_protected_experiment_stays_readable(tmp_path):
    p = _project(tmp_path)
    p.set_experiment_password("pw")
    p.save()
    assert explock.acquire(p.path) is None
    info = json.loads((p.path / explock.LOCK_FILE).read_text())  # readable without the password
    assert info["pid"] == os.getpid() and explock.is_mine(explock.read(p.path))
    explock.release(p.path)
    assert not (p.path / explock.LOCK_FILE).exists()


def test_apparatus_from_a_protected_experiment(tmp_path):
    p = _project(tmp_path)
    p.set_experiment_password("pw")
    p.save()
    with pytest.raises(sec.PasswordRequired):
        load_apparatus_file(p.path)
    assert [a.name for a in load_apparatus_file(p.path, "pw")] == ["Box"]


def test_cli_reads_the_password_from_the_environment(tmp_path, monkeypatch, capsys):
    p = _project(tmp_path)
    p.set_experiment_password("pw")
    p.save()
    monkeypatch.delenv("MANYMAZE_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as e:
        cli_main(["project", str(p.path), "info"])
    assert "MANYMAZE_PASSWORD" in str(e.value.code)
    monkeypatch.setenv("MANYMAZE_PASSWORD", "nope")
    with pytest.raises(SystemExit) as e:
        cli_main(["project", str(p.path), "info"])
    assert "wrong" in str(e.value.code)
    monkeypatch.setenv("MANYMAZE_PASSWORD", "pw")
    cli_main(["project", str(p.path), "info"])
    assert "Secret study" in capsys.readouterr().out


def test_unprotected_experiments_never_import_cryptography(tmp_path):
    code = ("import sys, tempfile; from pathlib import Path; from manymaze.core.project import Project; "
            "d = Path(tempfile.mkdtemp()) / 'x.mmaze'; p = Project(name='x'); p.save(d); p.save(); "
            "Project.load(d); assert 'cryptography' not in sys.modules, 'imported'")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
