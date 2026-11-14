"""gates.py — بوابتا الدخول: قنوات الاشتراك + فحص الدولة من رقم الهاتف

ملاحظة تقنية مهمة: تيليجرام لا يعطي «دولة الحساب». ما نحصل عليه هو رقم
الهاتف إذا شاركه المستخدم بنفسه. فالدولة = مفتاح الاتصال في الرقم (+964…).
ولذلك نرفض أي رقم لا يخصّ المستخدم نفسه (contact.user_id).
"""
import logging
import re
import time

from app import db

logger = logging.getLogger(__name__)

_cache = {}          # (chat_id, user_id) -> (ts, bool)
CACHE_TTL = 30   # كاش قصير: العضو قد يخرج من القناة


# ══════════════════════════════════════════════════
#  قنوات الاشتراك
# ══════════════════════════════════════════════════
def add_channel(chat_id, username=None, title=None):
    db.run("INSERT OR REPLACE INTO channels (chat_id, username, title) VALUES (?,?,?)",
           (int(chat_id), username, title))


def remove_channel(chat_id):
    db.run("DELETE FROM channels WHERE chat_id=?", (int(chat_id),))


def channels():
    return db.all_rows("SELECT * FROM channels ORDER BY added_at")


def channels_enabled():
    return db.get_int("verify_channels", 1) == 1


def _member_ok(bot, chat_id, user_id):
    """فحص عضوية واحدة (مع كاش دقيقة)."""
    key = (int(chat_id), int(user_id))
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    ok = False
    try:
        member = bot.get_chat_member(chat_id, user_id)
        ok = getattr(member, "status", "") in ("member", "administrator", "creator")
    except Exception as e:
        # البوت ليس مشرفاً في القناة، أو القناة محذوفة
        logger.warning("channel check %s: %s", chat_id, e)
        ok = False
    _cache[key] = (now, ok)
    return ok


def missing_channels(bot, user_id):
    """يرجع القنوات التي لم يشترك بها المستخدم."""
    if not channels_enabled():
        return []
    return [ch for ch in channels() if not _member_ok(bot, ch["chat_id"], user_id)]


def channel_health(bot):
    """فحص أن البوت يستطيع التحقق (مشرف في كل قناة)."""
    report = []
    for ch in channels():
        try:
            me = bot.get_chat_member(ch["chat_id"], bot.get_me().id)
            status = getattr(me, "status", "")
            report.append((ch, status in ("administrator", "creator"), status))
        except Exception as e:
            report.append((ch, False, str(e)[:40]))
    return report


# ══════════════════════════════════════════════════
#  فحص الدولة من الرقم
# ══════════════════════════════════════════════════
def country_enabled():
    return db.get_int("verify_country", 1) == 1


def allowed_codes():
    return db.get_list("countries")


# مفاتيح اتصال معروفة — تُستخدم لمعرفة الدولة حتى لو لم تكن مسموحة
# (وإلا فُهم +9899… على أنه «989» بدل «98»)
KNOWN_CODES = sorted({
    "964", "962", "963", "965", "966", "967", "968", "970", "971", "972", "973",
    "974", "975", "976", "977", "992", "993", "994", "995", "996", "998",
    "20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44",
    "45", "46", "47", "48", "49", "51", "52", "53", "54", "55", "56", "57", "58",
    "60", "61", "62", "63", "64", "65", "66", "81", "82", "84", "86", "90", "91",
    "92", "93", "94", "95", "98",
    "1", "7",
}, key=len, reverse=True)


def detect_code(phone):
    """مفتاح الدولة من الرقم (أطول مطابقة معروفة)."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return None
    for code in KNOWN_CODES:
        if digits.startswith(code):
            return code
    return digits[:3] if len(digits) >= 3 else digits


def parse_phone(phone):
    """يرجع (مفتاح الدولة، آخر 4 أرقام)."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return None, None
    return detect_code(phone), digits[-4:]


def country_allowed(phone):
    """هل مفتاح الدولة ضمن المسموح؟ يرجع (مسموح؟، المفتاح)."""
    code = detect_code(phone)
    if not code:
        return False, None
    return code in set(allowed_codes()), code


def add_country(code):
    code = re.sub(r"\D", "", str(code))
    if not code:
        return False
    codes = set(allowed_codes())
    codes.add(code)
    db.set_setting("countries", ",".join(sorted(codes)))
    return True


def remove_country(code):
    code = re.sub(r"\D", "", str(code))
    codes = set(allowed_codes())
    codes.discard(code)
    db.set_setting("countries", ",".join(sorted(codes)))
    return True
