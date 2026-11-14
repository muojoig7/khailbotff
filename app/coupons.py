"""coupons.py — أكواد نقاط مجانية (30 نقطة افتراضياً)"""
import logging
import secrets
import string

from app import db, users

logger = logging.getLogger(__name__)

ALPHABET = string.ascii_uppercase + string.digits


def _code():
    body = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return "ALK-" + body[:4] + "-" + body[4:]


def generate(count=1, points=None, uses=1, by=None):
    points = points if points is not None else db.get_int("coupon_points", 30)
    out = []
    for _ in range(max(1, min(int(count), 100))):
        code = _code()
        while db.one("SELECT 1 FROM coupons WHERE code=?", (code,)):
            code = _code()
        db.run("INSERT INTO coupons (code, points, uses_left, created_by) VALUES (?,?,?,?)",
               (code, points, uses, by))
        out.append(code)
    return out


def get(code):
    code = (code or "").strip().upper()
    return db.one("SELECT * FROM coupons WHERE code=?", (code,))


def redeem(code, uid):
    row = get(code)
    if not row:
        return False, "الكود غير صحيح."
    if row["uses_left"] <= 0:
        return False, "هذا الكود مستخدم من قبل."

    ok, bal = users.add_points(uid, row["points"], "coupon", row["code"])
    if not ok:
        return False, "تعذّر إضافة النقاط."

    db.run("UPDATE coupons SET uses_left=?, used_by=?, used_at=datetime('now') WHERE code=?",
           (row["uses_left"] - 1, uid, row["code"]))
    return True, row["points"]


def stats():
    return {
        "total": int(db.scalar("SELECT COUNT(*) FROM coupons", (), 0)),
        "unused": int(db.scalar("SELECT COUNT(*) FROM coupons WHERE uses_left>0", (), 0)),
        "redeemed": int(db.scalar("SELECT COUNT(*) FROM coupons WHERE used_at IS NOT NULL", (), 0)),
    }


def recent(limit=8):
    return db.all_rows("SELECT * FROM coupons ORDER BY created_at DESC LIMIT ?", (limit,))
