"""broadcast.py — بث بطابور (مستخدمون + مجموعات) مع تقرير

نقطتان مهمتان في التصميم:

1) **المجموعات تُبثّ ببوت العميل لا ببوت المصنع.** بوت المصنع ليس عضواً في
   مجموعات العملاء، فلا يستطيع الكتابة فيها. لذلك كل صف في الطابور يحمل
   «عبر من»: المصنع للمستخدمين، وبوت العميل لمجموعته.

2) **الطابور محفوظ في القاعدة**، فلو أُعيد تشغيل المصنع يكمل من حيث توقف،
   ولا تُرسل رسالة مرتين.

الحد: ~20 رسالة/ثانية (تيليجرام يسمح ~30). وعند 429 نحترم retry_after.
"""
import logging
import time

import requests

from app import db

logger = logging.getLogger(__name__)

RATE_PER_SEC = 20
BATCH = 20
TIMEOUT = 8


# ══════════════════════════════════════════════════
#  إنشاء بث
# ══════════════════════════════════════════════════
def _user_targets():
    rows = db.all_rows("SELECT user_id FROM users WHERE is_blocked=0")
    return [(r["user_id"], "factory") for r in rows]


def _group_targets():
    rows = db.all_rows(
        "SELECT DISTINCT group_id, id FROM bots "
        "WHERE group_id IS NOT NULL AND status IN ('active','stopped')")
    return [(r["group_id"], "bot_%d" % r["id"]) for r in rows]


def count_targets(users=True, groups=True):
    u = len(_user_targets()) if users else 0
    g = len(_group_targets()) if groups else 0
    return u, g, u + g


def create(kind, text, users=True, groups=True, by=None):
    """ينشئ بثاً ويضع كل الأهداف في الطابور. يرجع (المعرّف، العدد الكلي)."""
    targets = []
    if users:
        targets += _user_targets()
    if groups:
        targets += _group_targets()

    cur = db.run("INSERT INTO broadcasts (kind, text, total, created_by) VALUES (?,?,?,?)",
                 (kind, text, len(targets), by))
    bid = cur.lastrowid
    for chat_id, via in targets:
        db.run("INSERT INTO broadcast_queue (broadcast_id, chat_id, via) VALUES (?,?,?)",
               (bid, chat_id, via))
    return bid, len(targets)


def get(bid):
    return db.one("SELECT * FROM broadcasts WHERE id=?", (bid,))


def progress(bid):
    row = get(bid)
    if not row:
        return None
    pending = int(db.scalar(
        "SELECT COUNT(*) FROM broadcast_queue WHERE broadcast_id=? AND status='pending'",
        (bid,), 0))
    return {
        "id": bid, "kind": row["kind"], "total": row["total"],
        "sent": row["sent"], "failed": row["failed"], "blocked": row["blocked"],
        "pending": pending, "status": row["status"],
    }


def last_broadcasts(limit=5):
    return db.all_rows("SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", (limit,))


# ══════════════════════════════════════════════════
#  الإرسال
# ══════════════════════════════════════════════════
def _send_factory(bot, chat_id, text):
    bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)


def _send_via_bot(bot_row_id, chat_id, text):
    """يرسل عبر توكن بوت العميل مباشرة (بلا كائن TeleBot جديد)."""
    from app import bots
    row = bots.get(bot_row_id)
    if not row:
        raise RuntimeError("البوت غير موجود")
    token = bots.decrypt(row["token_enc"])
    r = requests.post(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=TIMEOUT)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description"))[:120])


def _classify(error_text):
    low = (error_text or "").lower()
    if "blocked" in low or "deactivated" in low or "chat not found" in low:
        return "blocked"
    return "failed"


def tick(bot, limit=BATCH):
    """يرسل دفعة من الطابور. يرجع عدد ما أُرسل فعلياً."""
    rows = db.all_rows(
        "SELECT * FROM broadcast_queue WHERE status='pending' ORDER BY id LIMIT ?", (limit,))
    if not rows:
        return 0, _finish_open()

    started = time.time()
    done = 0
    for row in rows:
        try:
            if row["via"] == "factory":
                _send_factory(bot, row["chat_id"], _text_of(row["broadcast_id"]))
            else:
                bot_row_id = int(row["via"].split("_")[1])
                _send_via_bot(bot_row_id, row["chat_id"], _text_of(row["broadcast_id"]))
            db.run("UPDATE broadcast_queue SET status='sent', sent_at=datetime('now') WHERE id=?",
                   (row["id"],))
            db.run("UPDATE broadcasts SET sent = sent + 1 WHERE id=?", (row["broadcast_id"],))
            done += 1
        except Exception as e:
            kind = _classify(str(e))
            db.run("UPDATE broadcast_queue SET status=?, error=? WHERE id=?",
                   (kind, str(e)[:200], row["id"]))
            col = "blocked" if kind == "blocked" else "failed"
            db.run("UPDATE broadcasts SET %s = %s + 1 WHERE id=?" % (col, col),
                   (row["broadcast_id"],))

    # احترام حد تيليجرام
    elapsed = time.time() - started
    need = len(rows) / float(RATE_PER_SEC)
    if elapsed < need:
        time.sleep(need - elapsed)

    return done, _finish_open()


_text_cache = {}


def _text_of(bid):
    if bid not in _text_cache:
        row = get(bid)
        _text_cache[bid] = row["text"] if row else ""
    return _text_cache[bid]


def _finish_open():
    """يُغلق البثوث المكتملة ويرجع معرّفاتها (لتقريرها للمالك)."""
    open_ids = [r["id"] for r in db.all_rows(
        "SELECT DISTINCT broadcast_id AS id FROM broadcast_queue WHERE status='pending'")]
    finished = []
    for row in db.all_rows("SELECT id FROM broadcasts WHERE status='pending'"):
        b = row["id"]
        if b in open_ids:
            continue
        db.run("UPDATE broadcasts SET status='done', finished_at=datetime('now') WHERE id=?", (b,))
        _text_cache.pop(b, None)
        finished.append(b)
    return finished


def pending_count():
    return int(db.scalar("SELECT COUNT(*) FROM broadcast_queue WHERE status='pending'", (), 0))


def cancel(bid):
    db.run("UPDATE broadcast_queue SET status='cancelled' WHERE broadcast_id=? AND status='pending'",
           (bid,))
    db.run("UPDATE broadcasts SET status='cancelled', finished_at=datetime('now') WHERE id=?", (bid,))


# ══════════════════════════════════════════════════
#  الخيط
# ══════════════════════════════════════════════════
class Worker:
    def __init__(self, bot, interval=1.0):
        self.bot = bot
        self.interval = interval
        self._stop = False

    def stop(self):
        self._stop = True

    def loop(self):
        logger.info("broadcast worker started")
        while not self._stop:
            try:
                if pending_count():
                    self.tick()
            except Exception as e:
                logger.error("broadcast: %s", e)
            time.sleep(self.interval)

    def tick(self):
        done, finished = tick(self.bot)
        for bid in finished:
            self._report(bid)
        return done

    def _report(self, bid):
        """تقرير نهائي للمالك بعد انتهاء أي بث."""
        from config import OWNER_ID
        from app.ui import panel, section, row, num, footer
        p = progress(bid)
        if not p:
            return
        blocks = [[
            section("النتيجة", "📊"),
            row("الهدف", num(p["total"]), "🎯"),
            row("وصل", num(p["sent"]), "✅"),
            row("فشل", num(p["failed"]), "⚠️"),
            row("حظر/محذوف", num(p["blocked"]), "🚫"),
        ]]
        text = panel("📣", "انتهى البث #%d" % bid, "تقرير الإرسال", blocks, footer("شكراً"))
        try:
            self.bot.send_message(OWNER_ID, text, parse_mode="HTML")
        except Exception as e:
            logger.warning("report: %s", e)
