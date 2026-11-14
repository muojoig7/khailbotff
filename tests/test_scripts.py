"""test_scripts.py — سكربتات المالك + عرض التوكن

يغطي:
  • إنشاء سكربت من .py ومن .zip (مع main.py)
  • رفض ما ليس بايثون أو ما فيه مسارات مشبوهة
  • تشغيل حقيقي وإيقاف وقراءة الإخراج
  • حذف السكربت وملفاته
  • سقف السكربتات العاملة
  • التوكن: يُخزَّن مشفّراً ويُفكّ للمالك فقط
"""
import os
import zipfile

import pytest

from app import bots, db, provision, scripts, users


@pytest.fixture(scope="module", autouse=True)
def _setup(tmp_path_factory):
    db.init()
    tmp = tmp_path_factory.mktemp("scripts")
    runtime = tmp / "runtime"
    runtime.mkdir()
    (runtime / "main.py").write_text("import time\ntime.sleep(60)\n")
    provision.RUNTIME_DIR = str(runtime)
    provision.INSTANCES_DIR = str(tmp / "instances")
    scripts.SCRIPTS_DIR = str(tmp / "scripts")
    yield
    for row in db.all_rows("SELECT * FROM scripts WHERE pid IS NOT NULL"):
        try:
            scripts.stop(row["id"])
        except Exception:
            pass


SLEEPY = "import time\nprint('بدء السكربت')\ntime.sleep(60)\n"
QUICK = "print('مرحبا من السكربت')\n"


# ══════════════════════════════════════════════════
#  الإنشاء
# ══════════════════════════════════════════════════
def test_create_from_py_file(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text(QUICK)
    row = scripts.create_from_file(str(f), name="hello.py", owner_id=999)
    assert row["entry"] == "hello.py"
    assert os.path.isfile(os.path.join(scripts.script_dir(row["id"]), "hello.py"))


def test_reject_non_python_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hi")
    with pytest.raises(scripts.ScriptError):
        scripts.create_from_file(str(f), owner_id=999)


def test_create_from_zip_with_main(tmp_path):
    z = tmp_path / "bundle.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("main.py", QUICK)
        zf.writestr("helper.py", "x=1\n")
    row = scripts.create_from_zip(str(z), name="bundle", owner_id=999)
    d = scripts.script_dir(row["id"])
    assert os.path.isfile(os.path.join(d, "main.py"))
    assert os.path.isfile(os.path.join(d, "helper.py"))


def test_zip_without_main_rejected(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(scripts.ScriptError):
        scripts.create_from_zip(str(z), owner_id=999)


def test_zip_slip_ignored(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("main.py", QUICK)
        zf.writestr("../../escape.py", "print('hacked')")
    row = scripts.create_from_zip(str(z), owner_id=999)
    d = os.path.realpath(scripts.script_dir(row["id"]))
    parent = os.path.dirname(d)
    assert not os.path.exists(os.path.join(os.path.dirname(parent), "escape.py")), \
        "خرج السكربت عن مجلده (zip slip)"


def test_secrets_not_copied_from_zip(tmp_path):
    z = tmp_path / "withkey.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("main.py", QUICK)
        zf.writestr("data/deploy.key", "secret")
    row = scripts.create_from_zip(str(z), owner_id=999)
    assert not os.path.exists(os.path.join(scripts.script_dir(row["id"]), "data", "deploy.key"))


# ══════════════════════════════════════════════════
#  التشغيل والإخراج
# ══════════════════════════════════════════════════
def test_start_run_and_output(tmp_path):
    f = tmp_path / "run.py"
    f.write_text(QUICK)
    row = scripts.create_from_file(str(f), owner_id=999)

    ok, msg = scripts.start(row["id"])
    assert ok, msg
    row = scripts.get(row["id"])
    assert row["status"] == "running"
    assert scripts.is_alive(row["pid"])

    import time
    time.sleep(1.0)
    out = scripts.output(row["id"])
    assert "مرحبا من السكربت" in out, "الإخراج ما ظهر: %r" % out

    scripts.stop(row["id"])
    time.sleep(0.3)
    assert not scripts.is_alive(row["pid"])
    assert scripts.get(row["id"])["status"] == "stopped"


def test_stop_long_running(tmp_path):
    f = tmp_path / "sleepy.py"
    f.write_text(SLEEPY)
    row = scripts.create_from_file(str(f), owner_id=999)
    scripts.start(row["id"])
    pid = scripts.get(row["id"])["pid"]
    assert scripts.is_alive(pid)

    scripts.stop(row["id"])
    import time
    time.sleep(0.4)
    assert not scripts.is_alive(pid), "السكربت ما توقف"


def test_failed_script_marked(tmp_path):
    f = tmp_path / "boom.py"
    f.write_text("raise SystemExit(2)\n")
    row = scripts.create_from_file(str(f), owner_id=999)
    scripts.start(row["id"])
    import time
    time.sleep(1.0)
    scripts.refresh_statuses()
    assert scripts.get(row["id"])["status"] in ("failed", "stopped")


def test_delete_removes_files(tmp_path):
    f = tmp_path / "del.py"
    f.write_text(QUICK)
    row = scripts.create_from_file(str(f), owner_id=999)
    d = scripts.script_dir(row["id"])
    assert os.path.isdir(d)
    scripts.delete(row["id"])
    assert not os.path.isdir(d)
    assert scripts.get(row["id"]) is None


def test_running_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(scripts, "MAX_RUNNING", 1)
    a = scripts.create_from_file(str(_write(tmp_path, "a.py", SLEEPY)), owner_id=999)
    b = scripts.create_from_file(str(_write(tmp_path, "b.py", SLEEPY)), owner_id=999)
    scripts.start(a["id"])
    try:
        with pytest.raises(scripts.ScriptError) as e:
            scripts.start(b["id"])
        assert "الحد" in str(e.value)
    finally:
        scripts.stop(a["id"])


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


# ══════════════════════════════════════════════════
#  التوكن
# ══════════════════════════════════════════════════
def test_token_encrypted_at_rest_and_revealable():
    users.ensure(9501, "o", "مالك")
    users.add_points(9501, 500, "t")
    db.set_setting("bot_cost", 100)
    db.set_setting("max_bots_per_user", 10)
    db.set_setting("free_mode", 0)

    token = "830001:offline_token_value_1234567890"
    row = bots.create(9501, token, verify=False)
    assert token not in row["token_enc"], "التوكن مخزّن كنص صريح"
    assert row["token_enc"].startswith("f1:") or row["token_enc"].startswith("x1:")
    assert bots.decrypt(row["token_enc"]) == token, "لا يمكن للمالك استرجاع التوكن"


def test_paths_are_absolute():
    """خلل سابق: مسار نسبي + cwd = مجلد السكربت = مسار مكرر لا يعمل."""
    assert os.path.isabs(scripts.script_dir(1)), "مجلد السكربت ليس مساراً مطلقاً"
