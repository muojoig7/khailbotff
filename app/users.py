"""users.py — المستخدمون، النقاط، الإحالات، الاستثناءات

قواعد مثبتة:
  • لا نقاط سالبة أبداً — أي خصم يفشل إن كان سيُنقص الرصيد تحت الصفر.
  • كل حركة نقاط تُسجَّل في دفتر (points_ledger) مع السبب.
  • نقاط الإحالة لا تُعطى عند الدخول، بل بعد التحقق بالرقم فقط،
    ولا تُعطى لمن دخل عبر الاستثناء الإداري.
"""
import logging
from datetime import datetime, timedelta

from app import db

logger = logging.getLogger(__name__)

STATE_NEW = "new"
STATE_CHANNELS = "channels"
STATE_PHONE = "phone"
STATE_PENDING = "pending"
STATE_VERIFIED = "verified"


# ══════════════════════════════════════════════════
#  المستخدم
# ══════════════════════════════════════════════════
def get(uid):
    return db.one("SELECT * FROM users WHERE user_id=?", (uid,))


def ensure(user_id, username=None, first_name=None, referred_by=None):
    """ينشئ المستخدم أول مرة ويرجع (row, is_new)."""
    row = get(user_id)
    if row:
        db.run("UPDATE users SET username=?, first_name=?, last_seen=datetime('now') "
               "WHERE user_id=?", (username, first_name, user_id))
        return get(user_id), False

    db.run(
        "INSERT INTO users (user_id, username, first_name, state, referred_by, last_seen) "
        "VALUES (?,?,?,?,?, datetime('now'))",
        (user_id, username, first_name, STATE_NEW, referred_by))

    if referred_by and referred_by != user_id and get(referred_by):
        db.run("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?,?)",
               (referred_by, user_id))
    return get(user_id), True


def set_state(uid, state):
    db.run("UPDATE users SET state=? WHERE user_id=?", (state, uid))


def state_of(uid):
    r = get(uid)
    return r["state"] if r else STATE_NEW


def set_verified(uid):
    db.run("UPDATE users SET state=?, country_ok=1, verified_at=datetime('now') "
           "WHERE user_id=?", (STATE_VERIFIED, uid))


def block(uid):
    db.run("UPDATE users SET is_blocked=1 WHERE user_id=?", (uid,))


def is_blocked(uid):
    r = get(uid)
    return bool(r and r["is_blocked"])


def set_phone(uid, cc, last4, country_ok, phone=None):
    """يحفظ مفتاح الدولة وآخر 4 أرقام + الرقم الكامل مشفّراً (للتواصل)."""
    from app.secret import encrypt
    enc = encrypt(phone) if phone else None
    db.run("UPDATE users SET phone_cc=?, phone_last4=?, country_ok=?, "
           "phone_enc=COALESCE(?, phone_enc) WHERE user_id=?",
           (cc, last4, 1 if country_ok else 0, enc, uid))


def phone_of(uid):
    """الرقم الكامل (للمالك فقط — الاستدعاء يُسجَّل في التدقيق)."""
    from app.secret import decrypt
    r = db.one("SELECT phone_enc FROM users WHERE user_id=?", (uid,))
    if not r or not r["phone_enc"]:
        return None
    try:
        return decrypt(r["phone_enc"])
    except Exception:
        return None


def with_phone(limit=500):
    """قائمة المستخدمين الذين عندهم رقم محفوظ."""
    return db.all_rows(
        "SELECT user_id, first_name, username, phone_cc, joined_at FROM users "
        "WHERE phone_enc IS NOT NULL ORDER BY user_id DESC LIMIT ?", (limit,))


def touch(uid):
    db.run("UPDATE users SET last_seen=datetime('now') WHERE user_id=?", (uid,))


# ══════════════════════════════════════════════════
#  الاستثناءات
# ══════════════════════════════════════════════════
def add_exception(uid, by=None, note=None):
    db.run("INSERT OR REPLACE INTO exceptions (user_id, added_by, note) VALUES (?,?,?)",
           (uid, by, note))
    db.run("UPDATE users SET exception=1, state=?, country_ok=1, "
           "verified_at=COALESCE(verified_at, datetime('now')) WHERE user_id=?",
           (STATE_VERIFIED, uid))
    # من دخل بالاستثناء لا يُعطى عنه نقاط إحالة
    db.run("UPDATE referrals SET rewarded=-1 WHERE referred_id=? AND rewarded=0", (uid,))


def remove_exception(uid):
    db.run("DELETE FROM exceptions WHERE user_id=?", (uid,))
    db.run("UPDATE users SET exception=0 WHERE user_id=?", (uid,))


def is_exception(uid):
    r = get(uid)
    return bool(r and r["exception"])


def list_exceptions(limit=50):
    return db.all_rows(
        "SELECT e.*, u.first_name, u.username FROM exceptions e "
        "LEFT JOIN users u ON u.user_id=e.user_id ORDER BY e.created_at DESC LIMIT ?",
        (limit,))


# ══════════════════════════════════════════════════
#  النقاط (لا سالب، وكل حركة مسجّلة)
# ══════════════════════════════════════════════════
def add_points(uid, amount, reason, ref=None):
    """يضيف (أو يخصم إن كان سالباً) مع منع الرصيد السالب. يرجع (نجاح، رصيد جديد)."""
    amount = int(amount)
    if amount == 0:
        return True, balance(uid)

    with db.tx() as c:
        row = c.execute("SELECT points FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return False, 0
        current = int(row["points"] or 0)
        new = current + amount
        if new < 0:
            return False, current
        c.execute("UPDATE users SET points=? WHERE user_id=?", (new, uid))
        c.execute("INSERT INTO points_ledger (user_id, amount, reason, ref) VALUES (?,?,?,?)",
                  (uid, amount, reason, ref))
    return True, new


def spend_points(uid, amount, reason, ref=None):
    """خصم ذرّي: يرفض إن لم يكفِ الرصيد."""
    return add_points(uid, -abs(int(amount)), reason, ref)


def balance(uid):
    return int(db.scalar("SELECT points FROM users WHERE user_id=?", (uid,), 0))


def ledger(uid, limit=10):
    return db.all_rows("SELECT * FROM points_ledger WHERE user_id=? ORDER BY id DESC LIMIT ?",
                       (uid, limit))


# ══════════════════════════════════════════════════
#  الإحالات
# ══════════════════════════════════════════════════
def referral_of(uid):
    return db.one("SELECT * FROM referrals WHERE referred_id=?", (uid,))


def reward_referral(uid, points):
    """يُمنح لصاحب الرابط عند تحقق المدعو بالرقم (لا للاستثناء)."""
    ref = referral_of(uid)
    if not ref or ref["rewarded"] != 0:
        return None
    referrer = ref["referrer_id"]
    if not referrer or referrer == uid:
        db.run("UPDATE referrals SET rewarded=-1 WHERE referred_id=?", (uid,))
        return None
    if not get(referrer):
        db.run("UPDATE referrals SET rewarded=-1 WHERE referred_id=?", (uid,))
        return None

    ok, _ = add_points(referrer, points, "referral", str(uid))
    db.run("UPDATE referrals SET rewarded=? WHERE referred_id=?", (1 if ok else -1, uid))
    if ok:
        db.run("UPDATE users SET referrals = referrals + 1 WHERE user_id=?", (referrer,))
        return referrer
    return None


def referrals_count(uid):
    return int(db.scalar("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND rewarded=1",
                         (uid,), 0))


# ══════════════════════════════════════════════════
#  الأدمنية
# ══════════════════════════════════════════════════
def is_admin(uid):
    from config import OWNER_ID
    if uid and uid == OWNER_ID:
        return True
    return db.one("SELECT 1 FROM admins WHERE user_id=?", (uid,)) is not None


def is_owner(uid):
    from config import OWNER_ID
    return bool(uid) and uid == OWNER_ID


def add_admin(uid, by=None):
    db.run("INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?,?)", (uid, by))


def remove_admin(uid):
    db.run("DELETE FROM admins WHERE user_id=?", (uid,))


def list_admins():
    return db.all_rows("SELECT * FROM admins ORDER BY added_at")


# ══════════════════════════════════════════════════
#  إحصاءات سريعة
# ══════════════════════════════════════════════════
def stats():
    return {
        "users": int(db.scalar("SELECT COUNT(*) FROM users", (), 0)),
        "today": int(db.scalar(
            "SELECT COUNT(*) FROM users WHERE date(joined_at)=date('now')", (), 0)),
        "verified": int(db.scalar("SELECT COUNT(*) FROM users WHERE state='verified'", (), 0)),
        "unverified": int(db.scalar("SELECT COUNT(*) FROM users WHERE state<>'verified'", (), 0)),
        "blocked": int(db.scalar("SELECT COUNT(*) FROM users WHERE is_blocked=1", (), 0)),
        "exceptions": int(db.scalar("SELECT COUNT(*) FROM exceptions", (), 0)),
        "points_total": int(db.scalar("SELECT SUM(points) FROM users", (), 0)),
        "referrals": int(db.scalar("SELECT COUNT(*) FROM referrals WHERE rewarded=1", (), 0)),
        "bots": int(db.scalar("SELECT COUNT(*) FROM bots", (), 0)),
    }
