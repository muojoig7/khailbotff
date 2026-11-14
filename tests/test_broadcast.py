"""test_broadcast.py — المرحلة 3: البث، الوضع المجاني برسالة، إشعار التفعيل، حذف المنتهي"""
import os
import types
from datetime import datetime, timedelta, timezone

import pytest

from app import bots, broadcast, db, provision, users
from app.runner import Runner

OWNER = 999


@pytest.fixture(scope="module", autouse=True)
def _setup(tmp_path_factory):
    db.init()
    tmp = tmp_path_factory.mktemp("bc")
    runtime = tmp / "runtime"
    runtime.mkdir()
    (runtime / "main.py").write_text("import time\ntime.sleep(60)\n")
    provision.RUNTIME_DIR = str(runtime)
    provision.INSTANCES_DIR = str(tmp / "instances")
    db.set_setting("bot_cost", 100)
    db.set_setting("free_mode", 0)
    yield
    for row in db.all_rows("SELECT * FROM bots WHERE pid IS NOT NULL"):
        try:
            provision.stop(row)
        except Exception:
            pass


class FakeBot:
    def __init__(self, fail_for=None):
        self.sent = []
        self.fail_for = fail_for or set()

    def send_message(self, chat_id, text, **kw):
        if chat_id in self.fail_for:
            raise Exception("Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(message_id=1)


def _clear():
    db.run("DELETE FROM broadcast_queue")
    db.run("DELETE FROM broadcasts")
    broadcast._text_cache.clear()


def _mk_user(uid, blocked=0):
    users.ensure(uid, "u%d" % uid, "مستخدم%d" % uid)
    db.run("UPDATE users SET is_blocked=? WHERE user_id=?", (blocked, uid))


# ══════════════════════════════════════════════════
#  الأهداف
# ══════════════════════════════════════════════════
def test_targets_exclude_blocked():
    _clear()
    _mk_user(9101)
    _mk_user(9102, blocked=1)
    u, g, total = broadcast.count_targets()
    assert u >= 1
    ids = [t[0] for t in broadcast._user_targets()]
    assert 9101 in ids and 9102 not in ids, "المحظور مشمول بالبث"


def test_targets_include_groups():
    users.ensure(9200, "o", "مالك")
    users.add_points(9200, 500, "t")
    row = bots.create(9200, "800200:offline_token_value_1234567890", verify=False)
    db.run("UPDATE bots SET group_id=-100555, group_title='مجموعة', status='active' WHERE id=?",
           (row["id"],))
    u, g, total = broadcast.count_targets()
    assert g >= 1
    assert any(via.startswith("bot_") for _, via in broadcast._group_targets())


# ══════════════════════════════════════════════════
#  الإرسال
# ══════════════════════════════════════════════════
def test_broadcast_to_users_counts():
    _clear()
    _mk_user(9301)
    _mk_user(9302)
    bid, total = broadcast.create("manual", "مرحباً بالجميع")
    assert total >= 2

    bot = FakeBot()
    done, finished = broadcast.tick(bot, limit=50)
    assert done >= 2
    assert any(t == "مرحباً بالجميع" for _, t in bot.sent)

    p = broadcast.progress(bid)
    assert p["sent"] >= 2 and p["pending"] == 0
    assert p["status"] == "done"
    assert bid in finished


def test_broadcast_marks_blocked():
    _clear()
    _mk_user(9303)
    bid, _ = broadcast.create("manual", "اختبار حظر")
    bot = FakeBot(fail_for={9303})
    broadcast.tick(bot, limit=50)
    p = broadcast.progress(bid)
    assert p["blocked"] >= 1, "لم يُصنَّف الحظر"
    assert p["pending"] == 0


def test_broadcast_to_group_uses_bot_token(monkeypatch):
    _clear()
    users.ensure(9400, "o2", "مالك")
    users.add_points(9400, 500, "t")
    row = bots.create(9400, "800300:offline_token_value_1234567890", verify=False)
    db.run("UPDATE bots SET group_id=-100777, group_title='مجموعة', status='active' WHERE id=?",
           (row["id"],))

    calls = []

    class Resp:
        def json(self):
            return {"ok": True, "result": {}}

    monkeypatch.setattr(broadcast.requests, "post",
                        lambda url, json=None, timeout=None, **kw: (calls.append((url, json)), Resp())[1])

    bid, total = broadcast.create("manual", "رسالة للمجموعة", users=False, groups=True)
    broadcast.tick(FakeBot(), limit=50)

    assert calls, "لم يُرسل عبر بوت العميل"
    ours = [(u, p) for u, p in calls if "800300:" in u]
    assert ours, "لم يُستخدم توكن بوت العميل صاحب المجموعة"
    assert ours[0][1]["chat_id"] == -100777


def test_cancel_stops_queue():
    _clear()
    _mk_user(9304)
    bid, _ = broadcast.create("manual", "سيُلغى")
    broadcast.cancel(bid)
    bot = FakeBot()
    broadcast.tick(bot, limit=50)
    p = broadcast.progress(bid)
    assert p["sent"] == 0
    assert p["status"] == "cancelled"


def test_report_sent_to_owner():
    _clear()
    _mk_user(9305)
    bid, _ = broadcast.create("manual", "تقرير")
    bot = FakeBot()
    w = broadcast.Worker(bot)
    w.tick()
    owner_msgs = [t for cid, t in bot.sent if cid == OWNER]
    assert owner_msgs and "انتهى البث" in owner_msgs[-1]


# ══════════════════════════════════════════════════
#  الوضع المجاني برسالة
# ══════════════════════════════════════════════════
def test_free_mode_requires_message_then_broadcasts(monkeypatch):
    from app import admin, flows

    db.set_setting("free_mode", 0)
    _clear()
    _mk_user(9501)

    sent = []
    bot = FakeBot()
    bot.send_message = lambda chat_id, text, **kw: (sent.append((chat_id, text)),
                                                    types.SimpleNamespace(message_id=1))[1]
    admin.bind(bot)
    flows.bind(bot)

    # المسودة ثم التأكيد
    admin._BC_DRAFT[OWNER] = "بوت مجاني لأول 100 مستخدم!"
    call = types.SimpleNamespace(
        id="c", from_user=types.SimpleNamespace(id=OWNER),
        message=types.SimpleNamespace(message_id=1, chat=types.SimpleNamespace(id=OWNER)))
    admin._do_free(bot, call)

    assert db.get_int("free_mode", 0) == 1, "الوضع المجاني لم يُفعّل"
    row = db.one("SELECT * FROM broadcasts WHERE kind='free_mode'")
    assert row and row["total"] >= 1
    assert broadcast.pending_count() >= 1


# ══════════════════════════════════════════════════
#  إشعار التفعيل + الحذف
# ══════════════════════════════════════════════════
def test_runner_notifies_activation_once():
    users.ensure(9600, "o3", "مالك")
    users.add_points(9600, 500, "t")
    row = bots.create(9600, "800400:offline_token_value_1234567890", verify=False)
    provision.prepare(row)

    import sqlite3
    path = os.path.join(provision.instance_dir(row["id"]), "data", "alkhal.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE bots (id INTEGER PRIMARY KEY, group_id INTEGER, group_title TEXT)")
    conn.execute("INSERT INTO bots VALUES (1, -100999, 'مجموعة الاختبار')")
    conn.commit()
    conn.close()

    msgs = []
    r = Runner(notify=lambda cid, text: msgs.append((cid, text)))
    r.tick()
    r.tick()

    assert any("بوتك صار يعمل" in t for cid, t in msgs if cid == 9600)
    assert sum(1 for cid, t in msgs if cid == OWNER and "تفعّل" in t) == 1, "الإشعار تكرر"
    assert bots.get(row["id"])["group_title"] == "مجموعة الاختبار"


def test_runner_purges_after_30_days():
    users.ensure(9700, "o4", "مالك")
    users.add_points(9700, 500, "t")
    row = bots.create(9700, "800500:offline_token_value_1234567890", verify=False)
    provision.prepare(row)
    d = provision.instance_dir(row["id"])
    assert os.path.isdir(d)

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=40)
    db.run("UPDATE bots SET status='expired', expires_at=? WHERE id=?",
           (old.replace(microsecond=0).isoformat() + "Z", row["id"]))

    Runner().tick()

    assert bots.get(row["id"]) is None, "لم يُحذف بعد 30 يوماً"
    assert not os.path.isdir(d), "ملفات النسخة باقية"
    assert db.one("SELECT * FROM purged WHERE bot_row=?", (row["id"],))


def test_runner_keeps_expired_within_30_days():
    users.ensure(9701, "o5", "مالك")
    users.add_points(9701, 500, "t")
    row = bots.create(9701, "800600:offline_token_value_1234567890", verify=False)
    provision.prepare(row)

    recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)
    db.run("UPDATE bots SET status='expired', expires_at=? WHERE id=?",
           (recent.replace(microsecond=0).isoformat() + "Z", row["id"]))

    Runner().tick()
    assert bots.get(row["id"]) is not None, "حُذف قبل موعده"
