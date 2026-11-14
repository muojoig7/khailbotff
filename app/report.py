"""report.py — تقرير أسبوعي للمالك"""
import logging
import time
from datetime import datetime, timedelta, timezone

from app import db

logger = logging.getLogger(__name__)

INTERVAL_DAYS = 7


def _since(days=7):
    d = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    return d.replace(microsecond=0).isoformat(sep=" ")


def collect(days=7):
    since = _since(days)
    return {
        "new_users": int(db.scalar("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (since,), 0)),
        "verified": int(db.scalar("SELECT COUNT(*) FROM users WHERE verified_at >= ?", (since,), 0)),
        "new_bots": int(db.scalar("SELECT COUNT(*) FROM bots WHERE created_at >= ?", (since,), 0)),
        "renewals": int(db.scalar(
            "SELECT COUNT(*) FROM points_ledger WHERE reason='renew_bot' AND created_at >= ?",
            (since,), 0)),
        "points_granted": int(db.scalar(
            "SELECT COALESCE(SUM(amount),0) FROM points_ledger "
            "WHERE reason='admin' AND amount>0 AND created_at >= ?", (since,), 0)),
        "referrals": int(db.scalar(
            "SELECT COUNT(*) FROM referrals WHERE rewarded=1 AND created_at >= ?", (since,), 0)),
        "coupons": int(db.scalar(
            "SELECT COUNT(*) FROM coupons WHERE used_at >= ?", (since,), 0)),
        "expired": int(db.scalar("SELECT COUNT(*) FROM bots WHERE status='expired'", (), 0)),
        "total_users": int(db.scalar("SELECT COUNT(*) FROM users", (), 0)),
        "total_bots": int(db.scalar("SELECT COUNT(*) FROM bots", (), 0)),
        "running": int(db.scalar("SELECT COUNT(*) FROM bots WHERE status='active'", (), 0)),
        "points_circulating": int(db.scalar("SELECT COALESCE(SUM(points),0) FROM users", (), 0)),
    }


def build_text(days=7):
    from app.ui import panel, section, row, num, footer
    d = collect(days)
    blocks = [
        [section("هذا الأسبوع", "📈"),
         row("مستخدمون جدد", num(d["new_users"]), "👥"),
         row("تحقّقوا", num(d["verified"]), "✅"),
         row("بوتات جديدة", num(d["new_bots"]), "🤖"),
         row("تجديدات", num(d["renewals"]), "🔄")],
        [section("المال", "💰"),
         row("نقاط أضفتها", num(d["points_granted"]), "💎"),
         row("كوبونات مستخدمة", num(d["coupons"]), "🎟️"),
         row("إحالات مكافأة", num(d["referrals"]), "🎁")],
        [section("الحالة", "⚙️"),
         row("إجمالي المستخدمين", num(d["total_users"]), "👥"),
         row("إجمالي البوتات", num(d["total_bots"]), "🤖"),
         row("تعمل الآن", num(d["running"]), "🟢"),
         row("منتهية", num(d["expired"]), "🔴"),
         row("نقاط متداولة", num(d["points_circulating"]), "💎")],
    ]
    return panel("📊", "التقرير الأسبوعي", "آخر %d أيام" % days, blocks,
                 footer("يُرسل تلقائياً كل أسبوع"))


def send(bot, chat_id=None):
    from config import OWNER_ID
    try:
        bot.send_message(chat_id or OWNER_ID, build_text(), parse_mode="HTML",
                         disable_web_page_preview=True)
        db.set_setting("last_weekly_report",
                       datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" "))
        return True
    except Exception as e:
        logger.warning("weekly report: %s", e)
        return False


class Scheduler:
    def __init__(self, bot, check_every=3600):
        self.bot = bot
        self.check_every = check_every
        self._stop = False

    def stop(self):
        self._stop = True

    def due(self):
        if db.get_int("weekly_report", 1) != 1:
            return False
        last = db.get_setting("last_weekly_report", "")
        if not last:
            return True
        try:
            last_t = datetime.fromisoformat(str(last).replace("Z", ""))
        except Exception:
            return True
        return (datetime.now(timezone.utc).replace(tzinfo=None) - last_t) >= timedelta(days=INTERVAL_DAYS)

    def loop(self):
        logger.info("weekly report scheduler started")
        while not self._stop:
            try:
                if self.due():
                    send(self.bot)
            except Exception as e:
                logger.error("weekly: %s", e)
            for _ in range(int(self.check_every * 10)):
                if self._stop:
                    return
                time.sleep(0.1)
