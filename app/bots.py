"""bots.py — إنشاء بوتات العملاء (سجل فقط في هذه المرحلة)

الترتيب المتفق عليه: تحقق من التوكن ← أنشئ البوت ← اخصم النقاط.
ولأن الخصم يأتي أخيراً، فلو فشل لأي سبب (رصيد تغيّر بين الفحص والخصم)
نحذف السجل فوراً حتى لا يبقى «بوت يتيم» بلا مقابل.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

from app import db, users

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{20,}$")
from app.secret import encrypt, decrypt, mask as _mask


def mask(token):
    return (token.split(":", 1)[0][:8] + ":***") if token and ":" in token else "***"


# ── تيليجرام ──
def get_me(token, timeout=8):
    try:
        r = requests.get("https://api.telegram.org/bot%s/getMe" % token, timeout=timeout)
        data = r.json()
        return data.get("result") if data.get("ok") else None
    except Exception as e:
        logger.warning("getMe: %s", e)
        return None


class BotError(Exception):
    """خطأ مفهوم يُعرض للمستخدم كما هو."""


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt):
    return dt.replace(microsecond=0).isoformat() + "Z"


def days_left(bot_row):
    try:
        exp = datetime.fromisoformat((bot_row["expires_at"] or "").replace("Z", ""))
    except Exception:
        return None
    return max(0, (exp - _now()).days)


def create(owner_id, token, verify=True):
    """ينشئ بوتاً للعميل. يرفع BotError برسالة عربية عند أي فشل."""
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise BotError("صيغة التوكن غير صحيحة. انسخه كاملاً من @BotFather.")

    me = None
    if verify:
        me = get_me(token)
        if not me:
            raise BotError("تيليجرام رفض هذا التوكن. تأكد أنك نسخته كاملاً.")
    else:
        me = {"id": int(token.split(":", 1)[0]), "username": "test_bot",
              "first_name": "Test"}

    if db.one("SELECT 1 FROM bots WHERE bot_id=?", (me["id"],)):
        raise BotError("هذا البوت مسجّل عندنا من قبل.")

    cap = db.get_int("max_bots", 25)
    total = int(db.scalar("SELECT COUNT(*) FROM bots", (), 0))
    if cap and total >= cap:
        raise BotError("الطاقة ممتلئة حالياً (%d بوت). تواصل مع الدعم." % cap)

    per_user = db.get_int("max_bots_per_user", 1)
    if per_user and len(by_owner(owner_id)) >= per_user:
        raise BotError("مسموح <b>%d</b> بوت لكل حساب. احذف بوتك الحالي أولاً "
                       "أو راسل الدعم." % per_user)

    cost = db.get_int("bot_cost", 100)
    free_mode = db.get_int("free_mode", 0) == 1
    user = users.get(owner_id)
    is_free = 0

    if free_mode and user and not user["free_used"]:
        cost = 0
        is_free = 1

    if users.balance(owner_id) < cost:
        raise BotError("نقاطك لا تكفي. تحتاج <b>%d</b> نقطة، وعندك <b>%d</b>."
                       % (cost, users.balance(owner_id)))

    days = db.get_int("free_days", 7) if is_free else db.get_int("bot_days", 30)
    expires = _iso(_now() + timedelta(days=days))

    try:
        cur = db.run(
            "INSERT INTO bots (owner_id, token_enc, bot_id, username, name, status, is_free, expires_at) "
            "VALUES (?,?,?,?,?, 'pending', ?, ?)",
            (owner_id, encrypt(token), me["id"], me.get("username"),
             me.get("first_name"), is_free, expires))
        bot_row_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise BotError("هذا البوت مسجّل عندنا من قبل.")

    # الخصم أخيراً (كما اتفقنا) — ولو فشل نحذف السجل فوراً
    if cost > 0:
        ok, bal = users.spend_points(owner_id, cost, "create_bot", str(bot_row_id))
        if not ok:
            db.run("DELETE FROM bots WHERE id=?", (bot_row_id,))
            raise BotError("نقاطك لا تكفي (تغيّر رصيدك أثناء الإنشاء). لم يُنشأ شيء.")
    if is_free:
        db.run("UPDATE users SET free_used=1 WHERE user_id=?", (owner_id,))

    return db.one("SELECT * FROM bots WHERE id=?", (bot_row_id,))


def by_owner(owner_id):
    return db.all_rows("SELECT * FROM bots WHERE owner_id=? ORDER BY id DESC", (owner_id,))


def get(bot_row_id):
    return db.one("SELECT * FROM bots WHERE id=?", (bot_row_id,))


def renew(bot_row_id, owner_id=None):
    """تجديد بمدّة الخطة مقابل النقاط (الوضع المجاني لا يشمل التجديد)."""
    row = get(bot_row_id)
    if not row:
        raise BotError("البوت غير موجود.")
    if owner_id and row["owner_id"] != owner_id:
        raise BotError("هذا البوت ليس لك.")

    cost = db.get_int("bot_cost", 100)
    if users.balance(row["owner_id"]) < cost:
        raise BotError("نقاطك لا تكفي للتجديد. تحتاج <b>%d</b> نقطة." % cost)

    days = db.get_int("bot_days", 30)
    base = _now()
    try:
        cur = datetime.fromisoformat((row["expires_at"] or "").replace("Z", ""))
        if cur > base:
            base = cur
    except Exception:
        pass

    ok, _ = users.spend_points(row["owner_id"], cost, "renew_bot", str(bot_row_id))
    if not ok:
        raise BotError("تعذّر الخصم — نقاطك لا تكفي.")
    db.run("UPDATE bots SET expires_at=?, status='active' WHERE id=?",
           (_iso(base + timedelta(days=days)), bot_row_id))
    return get(bot_row_id)


def set_status(bot_row_id, status):
    db.run("UPDATE bots SET status=? WHERE id=?", (status, bot_row_id))


def add_days(bot_row_id, days):
    row = get(bot_row_id)
    if not row:
        raise BotError("البوت غير موجود.")
    base = _now()
    try:
        cur = datetime.fromisoformat((row["expires_at"] or "").replace("Z", ""))
        if cur > base:
            base = cur
    except Exception:
        pass
    db.run("UPDATE bots SET expires_at=?, status='active' WHERE id=?",
           (_iso(base + timedelta(days=days)), bot_row_id))
    return get(bot_row_id)


def start_runtime(bot_row_id, wait=1.2):
    """يشغّل نسخة العميل فعلياً. يرجع (نجاح؟، رسالة)."""
    from app import provision
    row = get(bot_row_id)
    if not row:
        return False, "البوت غير موجود"
    if not provision.runtime_ready():
        db.run("UPDATE bots SET status='pending', last_error=? WHERE id=?",
               ("كود التشغيل غير جاهز على الخادم", bot_row_id))
        return False, "التشغيل غير جاهز على الخادم"
    return provision.start(row, wait=wait)


def stop_runtime(bot_row_id):
    from app import provision
    row = get(bot_row_id)
    if not row:
        return False, "البوت غير موجود"
    provision.stop(row)
    db.run("UPDATE bots SET status='stopped' WHERE id=?", (bot_row_id,))
    return True, "تم الإيقاف"


def delete_runtime(bot_row_id, owner_id=None):
    from app import provision
    row = get(bot_row_id)
    if not row:
        raise BotError("البوت غير موجود.")
    if owner_id and row["owner_id"] != owner_id:
        raise BotError("هذا البوت ليس لك.")
    provision.purge(row)
    db.run("DELETE FROM bots WHERE id=?", (bot_row_id,))
    return True


def mark_conflict(bot_row_id, reason="البوت يعمل في مكان آخر"):
    """تعارض getUpdates: بوت آخر يستخدم نفس التوكن."""
    db.run("UPDATE bots SET status='conflict', last_error=?, "
           "retry_at=datetime('now', '+10 minutes') WHERE id=?",
           (reason[:200], bot_row_id))


def status_text(row):
    from app import provision
    if row["status"] == "conflict":
        return "⚠️ يعمل في مكان آخر"
    if row["status"] == "expired":
        return "🔴 منتهٍ"
    if row["status"] == "stopped":
        return "⏸️ موقوف"
    if row["status"] == "error":
        return "⚠️ خطأ"
    if row["status"] == "pending":
        return "⏳ بانتظار التشغيل"
    return "🟢 يعمل" if provision.is_alive(row["pid"]) else "🟡 غير متصل"


def expired_bots():
    return db.all_rows(
        "SELECT * FROM bots WHERE status!='expired' AND expires_at IS NOT NULL "
        "AND expires_at < ?", (_iso(_now()),))
