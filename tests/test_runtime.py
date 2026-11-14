"""test_runtime.py — تشغيل بوت العميل فعلياً (المرحلة 2)

نستخدم «كود تشغيل مزيّف» (سكربت ينام) حتى نختبر إدارة العمليات الحقيقية
بلا شبكة وبلا بوت حقيقي: تشغيل، إيقاف، كشف الموت، إعادة التشغيل، الانتهاء.
"""
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import bots, db, provision, users
from app.runner import Runner

FAKE_RUNTIME = '''
import os, time, pathlib
pathlib.Path("alive.flag").write_text("1")
time.sleep(120)
'''


@pytest.fixture(scope="module", autouse=True)
def _setup(tmp_path_factory):
    db.init()
    tmp = tmp_path_factory.mktemp("runtime")
    runtime = tmp / "runtime"
    runtime.mkdir()
    (runtime / "main.py").write_text(FAKE_RUNTIME)
    provision.RUNTIME_DIR = str(runtime)
    provision.INSTANCES_DIR = str(tmp / "instances")
    yield
    # تنظيف العمليات
    for row in db.all_rows("SELECT * FROM bots WHERE pid IS NOT NULL"):
        try:
            provision.stop(row)
        except Exception:
            pass


def _make_bot(owner=3001, tg_id=800001, points=500):
    users.ensure(owner, "o%d" % owner, "مالك")
    users.add_points(owner, points, "test")
    db.set_setting("bot_cost", 100)
    db.set_setting("free_mode", 0)
    return bots.create(owner, "%06d:offline_token_value_1234567890" % tg_id, verify=False)


# ══════════════════════════════════════════════════
#  التجهيز والبيئة
# ══════════════════════════════════════════════════
def test_runtime_ready_detected():
    assert provision.runtime_ready() is True


def test_prepare_creates_dirs_and_meta():
    row = _make_bot(3002, 800002)
    d = provision.prepare(row)
    assert os.path.isdir(os.path.join(d, "data"))
    assert os.path.isdir(os.path.join(d, "logs"))
    meta = os.path.join(d, "instance.json")
    assert os.path.exists(meta)


def test_env_has_token_and_owner():
    row = _make_bot(3003, 800003)
    token = bots.decrypt(row["token_enc"])
    env = provision.env_for(row, token)
    assert env["BOT_TOKEN"] == token
    assert env["SUPER_ADMIN_ID"] == str(3003)
    assert env["DB_PATH"].endswith("alkhal.db")
    assert env["PYTHONPATH"] == provision.RUNTIME_DIR


def test_token_never_written_to_disk():
    row = _make_bot(3004, 800004)
    provision.prepare(row)
    token = bots.decrypt(row["token_enc"])
    d = provision.instance_dir(row["id"])
    for root, _, files in os.walk(d):
        for f in files:
            path = os.path.join(root, f)
            try:
                with open(path, "rb") as fh:
                    assert token.encode() not in fh.read(), "التوكن مكتوب في %s!" % path
            except OSError:
                pass


# ══════════════════════════════════════════════════
#  التشغيل والإيقاف
# ══════════════════════════════════════════════════
def test_start_creates_real_process():
    row = _make_bot(3005, 800005)
    ok, msg = provision.start(row, wait=0.8)
    assert ok, msg

    row = bots.get(row["id"])
    assert row["pid"], "لم يُسجَّل pid"
    assert provision.is_alive(row["pid"])
    assert row["status"] == "active"

    flag = os.path.join(provision.instance_dir(row["id"]), "alive.flag")
    assert os.path.exists(flag), "العملية لم تبدأ فعلياً"


def test_stop_kills_process():
    row = db.one("SELECT * FROM bots WHERE owner_id=?", (3005,))
    pid = row["pid"]
    provision.stop(row)
    time.sleep(0.2)
    assert not provision.is_alive(pid)
    assert bots.get(row["id"])["pid"] is None


def test_double_start_is_idempotent():
    row = _make_bot(3006, 800006)
    ok1, _ = provision.start(row, wait=0.6)
    assert ok1
    row = bots.get(row["id"])
    ok2, msg2 = provision.start(row)
    assert ok2 and "بالفعل" in msg2


def test_start_fails_cleanly_without_runtime():
    saved = provision.RUNTIME_DIR
    provision.RUNTIME_DIR = "/nonexistent/runtime"
    try:
        row = _make_bot(3007, 800007)
        ok, msg = provision.start(row)
        assert not ok
        assert "غير موجود" in msg
        assert bots.get(row["id"])["status"] == "pending"
    finally:
        provision.RUNTIME_DIR = saved


# ══════════════════════════════════════════════════
#  المشرف
# ══════════════════════════════════════════════════
def test_runner_starts_active_bot_without_process():
    row = _make_bot(3008, 800008)
    db.run("UPDATE bots SET status='active', pid=NULL WHERE id=?", (row["id"],))
    r = Runner()
    r.tick()
    row = bots.get(row["id"])
    assert provision.is_alive(row["pid"]), "المشرف لم يشغّل النسخة"
    assert row["restarts"] >= 1


def test_runner_skips_manually_stopped():
    row = _make_bot(3009, 800009)
    db.run("UPDATE bots SET status='stopped', pid=NULL WHERE id=?", (row["id"],))
    r = Runner()
    r.tick()
    row = bots.get(row["id"])
    assert row["status"] == "stopped"
    assert not row["pid"]


def test_runner_expires_after_grace():
    row = _make_bot(3010, 800010)
    provision.start(row, wait=0.6)
    row = bots.get(row["id"])
    assert row["pid"]

    past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=10))
    db.run("UPDATE bots SET expires_at=? WHERE id=?",
           (past.replace(microsecond=0).isoformat() + "Z", row["id"]))

    db.set_setting("grace_hours", 4)
    r = Runner()
    r.tick()

    row = bots.get(row["id"])
    assert row["status"] == "expired", "لم يُوقَف بعد انتهاء المدة + السماح"
    assert not provision.is_alive(row["pid"])


def test_runner_gives_up_after_max_restarts():
    row = _make_bot(3011, 800011)
    db.run("UPDATE bots SET status='active', pid=NULL WHERE id=?", (row["id"],))
    saved = provision.RUNTIME_DIR
    provision.RUNTIME_DIR = "/nonexistent/runtime"   # كل تشغيل يفشل
    try:
        r = Runner()
        for _ in range(7):
            r.tick()
        row = bots.get(row["id"])
        assert row["status"] in ("error", "pending")
        assert row["last_error"], "لا يوجد سبب مسجّل للتوقف"
    finally:
        provision.RUNTIME_DIR = saved


# ══════════════════════════════════════════════════
#  قراءة المجموعة من قاعدة العميل
# ══════════════════════════════════════════════════
def test_read_group_from_instance_db():
    row = _make_bot(3012, 800012)
    d = provision.prepare(row)
    path = os.path.join(d, "data", "alkhal.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE bots (id INTEGER PRIMARY KEY, group_id INTEGER, group_title TEXT)")
    conn.execute("INSERT INTO bots (id, group_id, group_title) VALUES (1, -100123, 'مجموعة الخال')")
    conn.commit()
    conn.close()

    gid, title = provision.read_group(row)
    assert gid == -100123 and title == "مجموعة الخال"

    provision.sync_group(row)
    assert bots.get(row["id"])["group_title"] == "مجموعة الخال"


def test_delete_removes_files_and_row():
    row = _make_bot(3013, 800013)
    provision.prepare(row)
    d = provision.instance_dir(row["id"])
    assert os.path.isdir(d)
    bots.delete_runtime(row["id"], owner_id=3013)
    assert not os.path.isdir(d)
    assert bots.get(row["id"]) is None


def test_delete_other_users_bot_refused():
    row = _make_bot(3014, 800014)
    with pytest.raises(bots.BotError):
        bots.delete_runtime(row["id"], owner_id=999999)
