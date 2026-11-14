"""runner.py — المشرف: يشغّل نسخ العملاء ويراقبها ويوقف المنتهية

يعمل في خيط مستقل داخل بوت المصنع. كل 15 ثانية:
  • نسخة نشطة توقفت → إعادة تشغيل (بحد أقصى 5 مرات في 10 دقائق).
  • نسخة تجاوزت الانتهاء + فترة السماح → إيقاف وتحويلها إلى expired.
  • نسخة معلّقة (لم يكن كود التشغيل جاهزاً) → محاولة تشغيل.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from app import bots as bots_mod
from app import db, provision

logger = logging.getLogger(__name__)

MAX_RESTARTS = 5
RESTART_WINDOW = 600
INTERVAL = 15


class Runner:
    def __init__(self, interval=INTERVAL, notify=None):
        self.interval = interval
        self.notify = notify
        self._stop = False
        self._restarts = {}      # bot_row_id -> [timestamps]
        self._last_check = 0
        self._notified = set()   # بوتات أُشعِر بتفعيلها

    def stop(self):
        self._stop = True

    # ── دورة واحدة (قابلة للاختبار) ──
    def tick(self, now=None):
        now = now or time.time()
        grace_h = db.get_int("grace_hours", 4)
        rows = db.all_rows(
            "SELECT * FROM bots WHERE status IN ('active','pending','error','stopped','conflict')")

        for row in rows:
            try:
                self._handle(row, now, grace_h)
            except Exception as e:
                logger.error("runner bot %s: %s", row["id"], e)
                db.run("UPDATE bots SET last_error=? WHERE id=?", (str(e)[:300], row["id"]))

        try:
            self.sync_groups()
        except Exception as e:
            logger.error("sync groups: %s", e)
        try:
            self.purge_expired()
        except Exception as e:
            logger.error("purge: %s", e)
        return len(rows)

    def _has_conflict(self, row):
        """يقرأ آخر أسطر سجل النسخة بحثاً عن تعارض getUpdates."""
        try:
            tail = provision.log_tail(row, 1500).lower()
        except Exception:
            return False
        return ("conflict" in tail and "getupdates" in tail) or "terminated by other" in tail

    # ── إشعار التفعيل في مجموعة العميل ──
    def sync_groups(self):
        """يقرأ مجموعة كل بوت من قاعدته، ويُشعِر أول مرة فقط."""
        rows = db.all_rows("SELECT * FROM bots WHERE status IN ('active','stopped')")
        for row in rows:
            try:
                gid, title = provision.sync_group(row)
            except Exception:
                continue
            if not gid or row["id"] in self._notified or row["group_id"]:
                continue
            self._notified.add(row["id"])
            self._notify_activation(row, title)

    def _notify_activation(self, row, title):
        if not self.notify:
            return
        from app.ui import card, mention, code, esc
        # العميل
        try:
            self.notify(row["owner_id"], card("✅ بوتك صار يعمل", [
                "🤖 " + code("@" + (row["username"] or "bot")),
                "👥 <b>المجموعة:</b> " + esc(title or "—"),
                "",
                "كل شيء جاهز — اكتب <code>الاوامر</code> في المجموعة للتحكم.",
            ]))
        except Exception:
            pass
        # المالك
        try:
            from config import OWNER_ID
            self.notify(OWNER_ID, card("🆕 بوت تفعّل في مجموعة", [
                "🤖 " + code("@" + (row["username"] or "bot")),
                "👥 <b>المجموعة:</b> " + esc(title or "—"),
                "👤 <b>المالك:</b> " + mention(row["owner_id"]),
            ]))
        except Exception:
            pass

    # ── حذف بيانات المنتهية بعد 30 يوماً ──
    def purge_expired(self):
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        rows = db.all_rows("SELECT * FROM bots WHERE status='expired' AND expires_at IS NOT NULL")
        for row in rows:
            try:
                exp = datetime.fromisoformat((row["expires_at"] or "").replace("Z", ""))
            except Exception:
                continue
            if exp >= cutoff:
                continue
            provision.purge(row)
            db.run("INSERT INTO purged (bot_row, username, owner_id) VALUES (?,?,?)",
                   (row["id"], row["username"], row["owner_id"]))
            db.run("DELETE FROM bots WHERE id=?", (row["id"],))
            logger.info("purged bot row %s after 30 days", row["id"])

    def _expired(self, row, grace_h):
        try:
            exp = datetime.fromisoformat((row["expires_at"] or "").replace("Z", ""))
        except Exception:
            return False
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=grace_h)
        return exp < cutoff

    def _handle(self, row, now, grace_h):
        if self._expired(row, grace_h):
            if db.get_int("notify_expiry", 1) == 1 and self.notify:
                try:
                    from app.ui import card, code
                    self.notify(row["owner_id"], card("⌛ انتهى اشتراك بوتك", [
                        "🤖 " + code("@" + (row["username"] or "bot")),
                        "",
                        "البوت توقف. جدّده من <b>بوتاتي</b> ليعود للعمل فوراً.",
                        "بياناتك محفوظة 30 يوماً.",
                    ]))
                except Exception:
                    pass
            if provision.is_alive(row["pid"]):
                provision.stop(row)
            db.run("UPDATE bots SET status='expired', pid=NULL WHERE id=?", (row["id"],))
            return

        if row["status"] == "conflict":
            try:
                retry = datetime.fromisoformat((row["retry_at"] or "").replace("Z", ""))
            except Exception:
                retry = None
            if retry and datetime.now(timezone.utc).replace(tzinfo=None) < retry:
                return
            db.run("UPDATE bots SET status='pending', retry_at=NULL WHERE id=?", (row["id"],))
            row = db.one("SELECT * FROM bots WHERE id=?", (row["id"],))

        if provision.is_alive(row["pid"]):
            if self._has_conflict(row):
                provision.stop(row)
                from app import bots as _b
                _b.mark_conflict(row["id"])
                logger.warning("conflict detected for bot row %s — stopped", row["id"])
                if self.notify:
                    try:
                        from app.ui import card, code
                        self.notify(row["owner_id"], card("⚠️ بوتك يعمل في مكان آخر", [
                            "🤖 " + code("@" + (row["username"] or "bot")),
                            "",
                            "يوجد نسخة أخرى تعمل بنفس التوكن (على خادم آخر مثلاً).",
                            "أوقفنا نسختنا مؤقتاً، وسنعيدها تلقائياً كل 10 دقائق "
                            "حتى تتفرّغ الأخرى.",
                        ]))
                    except Exception:
                        pass
                return
            if row["status"] != "active":
                db.run("UPDATE bots SET status='active' WHERE id=?", (row["id"],))
            return

        if row["status"] == "stopped":
            return          # إيقاف يدوي من العميل

        # توقفت بلا سبب واضح → إعادة تشغيل بحد
        stamps = [t for t in self._restarts.get(row["id"], []) if now - t < RESTART_WINDOW]
        if len(stamps) >= MAX_RESTARTS:
            db.run("UPDATE bots SET status='error', last_error=? WHERE id=?",
                   ("توقف متكرر — أوقفناه تلقائياً", row["id"]))
            return

        ok, msg = provision.start(row)
        if not ok:
            stamps.append(now)
            self._restarts[row["id"]] = stamps
            logger.warning("start failed for %s: %s", row["id"], msg)
            return

        stamps.append(now)
        self._restarts[row["id"]] = stamps
        db.run("UPDATE bots SET restarts = restarts + 1 WHERE id=?", (row["id"],))
        logger.info("started bot row %s (pid %s)", row["id"], row["pid"])

    # ── الحلقة ──
    def loop(self):
        logger.info("runner started (interval=%ss)", self.interval)
        while not self._stop:
            try:
                self.tick()
            except Exception as e:
                logger.error("runner tick: %s", e)
            for _ in range(int(self.interval * 10)):
                if self._stop:
                    return
                time.sleep(0.1)

    def stop_all(self):
        for row in db.all_rows("SELECT * FROM bots WHERE pid IS NOT NULL"):
            try:
                provision.stop(row)
            except Exception:
                pass
