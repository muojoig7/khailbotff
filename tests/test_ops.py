"""test_ops.py — الحزمة التشغيلية: نسخ احتياطي، سقوف، تعارض، تدقيق، كوبونات، توقيع، تقرير"""
import os
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app import audit, backup, bots, coupons, db, deploy, provision, report, users
from app.runner import Runner


@pytest.fixture(scope="module", autouse=True)
def _setup(tmp_path_factory):
    db.init()
    tmp = tmp_path_factory.mktemp("ops")
    runtime = tmp / "runtime"
    runtime.mkdir()
    (runtime / "main.py").write_text("import time\ntime.sleep(60)\n")
    provision.RUNTIME_DIR = str(runtime)
    provision.INSTANCES_DIR = str(tmp / "instances")
    provision.RELEASES_DIR = str(tmp / "releases")
    db.set_setting("bot_cost", 100)
    db.set_setting("free_mode", 0)
    db.set_setting("max_bots", 500)
    db.set_setting("max_bots_per_user", 10)
    yield
    for row in db.all_rows("SELECT * FROM bots WHERE pid IS NOT NULL"):
        try:
            provision.stop(row)
        except Exception:
            pass


def _owner(uid, points=1000):
    users.ensure(uid, "o%d" % uid, "مالك%d" % uid)
    users.add_points(uid, points, "test")


# ══════════════════════════════════════════════════
#  السقوف
# ══════════════════════════════════════════════════
def test_one_bot_per_user():
    db.set_setting("max_bots_per_user", 1)
    try:
        _owner(4101)
        bots.create(4101, "820001:offline_token_value_1234567890", verify=False)
        with pytest.raises(bots.BotError) as e:
            bots.create(4101, "820002:offline_token_value_1234567890", verify=False)
        assert "بوت لكل حساب" in str(e.value)
        assert len(bots.by_owner(4101)) == 1
    finally:
        db.set_setting("max_bots_per_user", 10)


def test_server_cap_blocks_new_bots():
    _owner(4102)
    before = int(db.scalar("SELECT COUNT(*) FROM bots", (), 0))
    db.set_setting("max_bots", before)
    try:
        with pytest.raises(bots.BotError) as e:
            bots.create(4102, "820003:offline_token_value_1234567890", verify=False)
        assert "الطاقة ممتلئة" in str(e.value)
    finally:
        db.set_setting("max_bots", 500)


# ══════════════════════════════════════════════════
#  التعارض
# ══════════════════════════════════════════════════
def test_conflict_stops_and_schedules_retry():
    _owner(4103)
    row = bots.create(4103, "820004:offline_token_value_1234567890", verify=False)
    provision.start(row, wait=0.5)
    row = bots.get(row["id"])
    assert provision.is_alive(row["pid"])

    log = os.path.join(provision.instance_dir(row["id"]), "bot.log")
    with open(log, "a", encoding="utf-8") as f:
        f.write("ERROR:core.engine: Conflict: terminated by other getUpdates request\n")

    Runner().tick()

    row = bots.get(row["id"])
    assert row["status"] == "conflict", "لم يُكتشف التعارض"
    assert not provision.is_alive(row["pid"]), "العملية لم تُوقف"
    assert row["retry_at"], "لا موعد إعادة محاولة"
    assert "مكان آخر" in bots.status_text(row)


def test_conflict_not_retried_before_retry_at():
    row = db.one("SELECT * FROM bots WHERE status='conflict' LIMIT 1")
    before = row["pid"]
    Runner().tick()
    row2 = bots.get(row["id"])
    assert row2["status"] == "conflict"
    assert row2["pid"] == before, "أُعيد التشغيل قبل الموعد"


# ══════════════════════════════════════════════════
#  تدوير السجل
# ══════════════════════════════════════════════════
def test_log_trimmed_when_large(tmp_path):
    path = tmp_path / "bot.log"
    path.write_bytes(b"x" * (6 * 1024 * 1024))
    assert provision._trim_log(str(path)) is True
    assert os.path.getsize(path) < 2 * 1024 * 1024


def test_log_untouched_when_small(tmp_path):
    path = tmp_path / "small.log"
    path.write_bytes(b"x" * 1000)
    assert provision._trim_log(str(path)) is False
    assert os.path.getsize(path) == 1000


# ══════════════════════════════════════════════════
#  النسخ الاحتياطي
# ══════════════════════════════════════════════════
def test_backup_creates_valid_zip():
    path = backup.create("test")
    assert os.path.isfile(path)
    with zipfile.ZipFile(path) as z:
        assert "factory.db" in z.namelist()
        assert z.read("factory.db")[:15] == b"SQLite format 3"
    row = db.one("SELECT * FROM backups WHERE path=?", (path,))
    assert row and row["size"] > 0


def test_backup_rotation_keeps_limit():
    db.set_setting("backup_keep", 3)
    try:
        for _ in range(5):
            backup.create("rot")
        rows = db.all_rows("SELECT * FROM backups ORDER BY id DESC")
        assert len(rows) <= 3, "لم تُحذف النسخ القديمة"
    finally:
        db.set_setting("backup_keep", 14)


def test_backup_scheduler_respects_interval():
    db.set_setting("backup_hours", 6)
    db.run("DELETE FROM backups")
    sent = []

    class FakeBot:
        def send_document(self, cid, f, caption=None):
            sent.append(cid)

        def send_message(self, *a, **k):
            pass

    s = backup.Scheduler(FakeBot())
    assert s.tick() is True
    assert s.tick() is False, "أخذ نسخة ثانية قبل الموعد"


# ══════════════════════════════════════════════════
#  الكوبونات
# ══════════════════════════════════════════════════
def test_coupon_generate_and_redeem_once():
    db.set_setting("coupon_points", 30)
    codes = coupons.generate(2, by=999)
    assert len(codes) == 2 and codes[0].startswith("ALK-")

    _owner(4201, points=0)
    ok, pts = coupons.redeem(codes[0], 4201)
    assert ok and pts == 30
    assert users.balance(4201) == 30

    ok2, msg = coupons.redeem(codes[0], 4201)
    assert not ok2, "الكود استُخدم مرتين"
    assert users.balance(4201) == 30


def test_coupon_invalid_code():
    ok, msg = coupons.redeem("ALK-NOPE-NOPE", 4202)
    assert not ok and "غير صحيح" in msg


def test_coupon_default_is_30_points():
    assert db.get_int("coupon_points", 0) == 30


# ══════════════════════════════════════════════════
#  سجل التدقيق
# ══════════════════════════════════════════════════
def test_audit_records_and_lists():
    before = audit.count()
    audit.log(999, "grant_points", "4201", "+30")
    audit.log(999, "stop_bot", "7", "يدوي")
    assert audit.count() == before + 2
    rows = audit.recent(2)
    assert rows[0]["action"] == "stop_bot"
    assert rows[0]["actor"] == 999


# ══════════════════════════════════════════════════
#  إشعار الانتهاء
# ══════════════════════════════════════════════════
def test_expiry_notifies_customer_once():
    _owner(4301)
    row = bots.create(4301, "820010:offline_token_value_1234567890", verify=False)
    old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=10))
    db.run("UPDATE bots SET expires_at=?, status='active' WHERE id=?",
           (old.replace(microsecond=0).isoformat() + "Z", row["id"]))
    db.set_setting("grace_hours", 4)
    db.set_setting("notify_expiry", 1)

    msgs = []
    r = Runner(notify=lambda cid, text: msgs.append((cid, text)))
    r.tick()
    r.tick()

    mine = [t for cid, t in msgs if cid == 4301 and "انته" in t]
    assert len(mine) == 1, "إشعار الانتهاء لم يُرسل مرة واحدة (%d)" % len(mine)
    assert bots.get(row["id"])["status"] == "expired"


# ══════════════════════════════════════════════════
#  توقيع الإصدارات
# ══════════════════════════════════════════════════
def _signed_zip(path, version="1.2.3", tamper=False):
    import hashlib
    import hmac
    import json

    files = {"main.py": "x=1\n", "tests/test_ok.py": "def test_ok():\n    assert True\n"}
    items = sorted("%s:%s" % (k, hashlib.sha256(v.encode()).hexdigest())
                   for k, v in files.items())
    digest = hashlib.sha256("\n".join(items).encode()).hexdigest()
    key = deploy.load_key()
    manifest = {"version": version, "digest": digest}
    if key:
        manifest["sig"] = hmac.new(key, ("%s:%s" % (version, digest)).encode(),
                                   hashlib.sha256).hexdigest()
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content + ("# tampered\n" if tamper and name == "main.py" else ""))
        z.writestr("manifest.json", json.dumps(manifest))
    return str(path)


def test_signature_accepts_signed_release(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "KEY_FILE", str(tmp_path / "k.key"))
    deploy.load_key(create=True)
    z = _signed_zip(tmp_path / "ok.zip")
    dest = tmp_path / "out"
    deploy.extract(z, str(dest))
    assert deploy.verify_signature(str(dest))["signed"] is True


def test_signature_rejects_tampered_release(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "KEY_FILE", str(tmp_path / "k.key"))
    deploy.load_key(create=True)
    z = _signed_zip(tmp_path / "bad.zip", tamper=True)
    dest = tmp_path / "out2"
    deploy.extract(z, str(dest))
    with pytest.raises(deploy.DeployError) as e:
        deploy.verify_signature(str(dest))
    assert "توقيع" in str(e.value)


def test_signature_requires_sig_when_key_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "KEY_FILE", str(tmp_path / "k.key"))
    deploy.load_key(create=True)
    z = tmp_path / "unsigned.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("main.py", "x=1\n")
        zf.writestr("manifest.json", '{"version": "1.0"}')
    dest = tmp_path / "out3"
    deploy.extract(str(z), str(dest))
    with pytest.raises(deploy.DeployError) as e:
        deploy.verify_signature(str(dest))
    assert "غير موقّع" in str(e.value)


def test_signature_allows_when_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "KEY_FILE", str(tmp_path / "missing.key"))
    z = tmp_path / "plain.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("main.py", "x=1\n")
    dest = tmp_path / "out4"
    deploy.extract(str(z), str(dest))
    assert deploy.verify_signature(str(dest))["signed"] is False


# ══════════════════════════════════════════════════
#  التقرير الأسبوعي
# ══════════════════════════════════════════════════
def test_weekly_report_numbers_and_text():
    d = report.collect(7)
    for key in ("new_users", "new_bots", "renewals", "total_users", "running"):
        assert key in d
    text = report.build_text()
    assert "التقرير الأسبوعي" in text and "بوتات جديدة" in text


def test_weekly_report_scheduler_due():
    db.set_setting("weekly_report", 1)
    db.set_setting("last_weekly_report", "")
    s = report.Scheduler(bot=None)
    assert s.due() is True

    class FakeBot:
        def send_message(self, *a, **k):
            return True

    assert report.send(FakeBot()) is True
    assert s.due() is False, "أرسل تقريراً ثانياً قبل أسبوع"


def test_weekly_report_can_be_disabled():
    db.set_setting("weekly_report", 0)
    assert report.Scheduler(bot=None).due() is False
    db.set_setting("weekly_report", 1)


# ══════════════════════════════════════════════════
#  تجهيز كود التشغيل (سبب «البوت ما اشتغل»)
# ══════════════════════════════════════════════════
def test_install_runtime_from_zip(tmp_path, monkeypatch):
    import zipfile
    target = tmp_path / "runtime_new"
    z = tmp_path / "alkhal_bot_v6_2.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("alkhal_bot/main.py", "x=1\n")
        zf.writestr("alkhal_bot/core/a.py", "y=1\n")
        zf.writestr("alkhal_bot/data/secret.key", "k")

    ok, msg = provision.install_runtime_from_zip(str(z), str(target))
    assert ok, msg
    assert os.path.isfile(os.path.join(str(target), "main.py")), "main.py لم يُفكّ"
    assert not os.path.exists(os.path.join(str(target), "data", "secret.key")), "ملف حساس نُقل"


def test_install_rejects_zip_without_main(tmp_path):
    import zipfile
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "hi")
    ok, msg = provision.install_runtime_from_zip(str(z), str(tmp_path / "rt"))
    assert not ok and "main.py" in msg


def test_auto_setup_finds_zip(tmp_path, monkeypatch):
    import zipfile
    work = tmp_path / "work"
    work.mkdir()
    with zipfile.ZipFile(work / "alkhal_bot_v6_2.zip", "w") as zf:
        zf.writestr("main.py", "x=1\n")

    monkeypatch.setattr(provision, "RUNTIME_DIR", str(tmp_path / "runtime_auto"))
    ok, msg = provision.auto_setup(str(work))
    assert ok, msg
    assert provision.runtime_ready()


def test_auto_setup_reports_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "RUNTIME_DIR", str(tmp_path / "empty_rt"))
    work = tmp_path / "nowork"
    work.mkdir()
    ok, msg = provision.auto_setup(str(work))
    assert not ok and "zip" in msg


def test_start_error_mentions_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "RUNTIME_DIR", str(tmp_path / "missing_rt"))
    _owner(4401)
    row = bots.create(4401, "820020:offline_token_value_1234567890", verify=False)
    ok, msg = provision.start(row)
    assert not ok
    assert "setup_runtime" in msg, "رسالة الخطأ لا تدل على الحل"
