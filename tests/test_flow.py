"""test_flow.py — رحلة المستخدم كاملة (بلا شبكة)

/start ← القنوات ← الرقم ← الواجهة ← إنشاء بوت ← بوتاتي ← الدعم ← الإحالة
"""
import types

import pytest

from app import db, flows, gates, users


class FakeBot:
    """بديل البوت: يسجّل كل ما يُرسل ويحوّل الشبكة إلى دوال بسيطة."""

    def __init__(self, member_status="member"):
        self.sent = []          # (chat_id, text, kb)
        self.answers = []       # نصوص answer_callback_query
        self.deleted = []
        self.steps = []         # (callback, args)

    # إرسال
    @staticmethod
    def _kb(markup):
        """يحوّل كائن الكيبورد إلى قاموس متداخل حتى تفحصه الاختبارات."""
        import json
        if markup is None:
            return None
        if isinstance(markup, dict):
            return markup
        to_json = getattr(markup, "to_json", None)
        if callable(to_json):
            try:
                return json.loads(to_json())
            except Exception:
                pass
        rows = getattr(markup, "keyboard", None)
        if rows is not None:
            return {"keyboard": rows}
        return markup

    def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, str(text), self._kb(kw.get("reply_markup"))))
        return types.SimpleNamespace(message_id=len(self.sent))

    def edit_message_text(self, text, chat_id=None, message_id=None, **kw):
        self.sent.append((chat_id, str(text), self._kb(kw.get("reply_markup"))))
        return types.SimpleNamespace(message_id=message_id or 1)

    def reply_to(self, m, text, **kw):
        return self.send_message(m.chat.id, text, **kw)

    def answer_callback_query(self, cid, text=None, **kw):
        self.answers.append(text)

    def delete_message(self, chat_id, mid):
        self.deleted.append(mid)

    def get_me(self):
        return types.SimpleNamespace(id=1, username="factory_bot")

    def get_chat(self, cid):
        return types.SimpleNamespace(id=cid, username="ch", title="قناة")

    def get_chat_member(self, chat_id, user_id):
        return types.SimpleNamespace(status=self.member_status)

    def register_next_step_handler(self, msg, cb, *a, **k):
        self.steps.append((cb, a, k))

    # أدوات اختبار
    def last(self):
        return self.sent[-1][1] if self.sent else ""

    def clear(self):
        self.sent.clear()
        self.answers.clear()
        self.steps.clear()


def _msg(text=None, uid=5001, name="مستخدم", uname="user1", contact=None):
    m = types.SimpleNamespace()
    m.text = text
    m.chat = types.SimpleNamespace(id=uid, type="private")
    m.from_user = types.SimpleNamespace(id=uid, first_name=name, username=uname,
                                        is_bot=False)
    m.message_id = 10
    m.reply_to_message = None
    m.contact = contact
    m.content_type = "contact" if contact else "text"
    m.entities = None
    return m


def _call(bot, data, uid=5001, name="مستخدم"):
    c = types.SimpleNamespace(
        id="cb", data=data,
        from_user=types.SimpleNamespace(id=uid, first_name=name, username="user1"),
        message=types.SimpleNamespace(message_id=20,
                                      chat=types.SimpleNamespace(id=uid, type="private")))
    for h in bot.callback_query_handlers:
        f = h.get("filters", {}).get("func")
        if f is None:
            continue
        try:
            if not f(c):
                continue
        except Exception:
            continue
        return h["function"](c)
    return None


def _dispatch(bot, m):
    for h in bot.message_handlers:
        filters = h.get("filters", {})
        types_wanted = filters.get("content_types")
        if types_wanted and m.content_type not in types_wanted:
            continue
        f = filters.get("func")
        if f is not None:
            try:
                if not f(m):
                    continue
            except Exception:
                continue
        return h["function"](m)
    return None


@pytest.fixture(scope="module")
def bot():
    db.init()
    import run
    b = run.build_bot("1:TEST")
    fake = FakeBot()
    # نستبدل دوال الإرسال فقط، ونبقي الهاندلرات الحقيقية
    for name in ("send_message", "edit_message_text", "reply_to", "answer_callback_query",
                 "delete_message", "get_me", "get_chat", "get_chat_member",
                 "register_next_step_handler"):
        setattr(b, name, getattr(fake, name))
    b.fake = fake

    # بلا شبكة: getMe مزيّف (يعيد معرّف التوكن نفسه)
    from app import bots as bots_mod
    bots_mod.get_me = lambda token, timeout=8: {
        "id": int(str(token).split(":", 1)[0]),
        "username": "test_bot",
        "first_name": "Test",
    }
    return b


def _buttons(text_or_kb):
    """يرجع قائمة callback_data من آخر رسالة."""
    kb = text_or_kb
    if isinstance(kb, str):
        return []
    if not kb:
        return []
    return [b.get("callback_data") for row in kb.get("inline_keyboard", []) for b in row]


def _reset_flow(uid):
    """يرجّع المستخدم لحالة البداية."""
    db.run("DELETE FROM users WHERE user_id=?", (uid,))
    db.run("DELETE FROM referrals WHERE referred_id=?", (uid,))
    gates._cache.clear()
    db.set_setting("verify_enabled", 1)
    db.set_setting("verify_country", 1)
    db.set_setting("verify_channels", 1)


# ══════════════════════════════════════════════════
#  الدخول
# ══════════════════════════════════════════════════
def test_start_new_user_reaches_phone(bot):
    _reset_flow(5001)
    bot.fake.clear()
    _dispatch(bot, _msg("/start"))

    assert any(cid == 999 for cid, _, _ in bot.fake.sent), "المالك لم يُشعَر بالدخول"
    assert "مشاركة رقمي" in bot.fake.last() or "تحقّق من رقمك" in bot.fake.last()


def test_start_notifies_owner_with_id(bot):
    _reset_flow(5002)
    bot.fake.clear()
    _dispatch(bot, _msg("/start", uid=5002, name="سارة", uname="sara"))
    owner_msgs = [t for cid, t, _ in bot.fake.sent if cid == 999]
    assert owner_msgs, "لا إشعار للمالك"
    assert "5002" in owner_msgs[0] and "sara" in owner_msgs[0]


def test_verify_disabled_opens_menu_directly(bot):
    _reset_flow(5003)
    db.set_setting("verify_enabled", 0)
    bot.fake.clear()
    _dispatch(bot, _msg("/start", uid=5003))
    assert "m_create" in _buttons(bot.fake.sent[-1][2]), "الواجهة لم تظهر"
    assert users.state_of(5003) == users.STATE_VERIFIED
    db.set_setting("verify_enabled", 1)


def test_channels_gate_shown_when_channels_exist(bot):
    _reset_flow(5004)
    gates.add_channel(-100777, "ch_test", "قناة الاختبار")
    gates._cache.clear()
    bot.fake.clear()
    bot.get_chat_member = lambda cid, uid: types.SimpleNamespace(status="left")
    _dispatch(bot, _msg("/start", uid=5004))
    assert "اشترك في هذه القنوات" in bot.fake.last()
    bot.get_chat_member = lambda cid, uid: types.SimpleNamespace(status="member")
    gates.remove_channel(-100777)


def test_channel_check_button_passes_when_member(bot):
    _reset_flow(5005)
    gates.add_channel(-100778, "ch2", "قناة")
    gates._cache.clear()
    _dispatch(bot, _msg("/start", uid=5005))
    bot.fake.clear()
    _call(bot, "g_check", uid=5005)
    assert "مشاركة رقمي" in bot.fake.last() or "تحقّق من رقمك" in bot.fake.last()
    gates.remove_channel(-100778)


# ══════════════════════════════════════════════════
#  الرقم والدولة
# ══════════════════════════════════════════════════
def test_contact_allowed_country_verifies(bot):
    _reset_flow(5006)
    db.set_setting("countries", "964")
    bot.fake.clear()
    _dispatch(bot, _msg("/start", uid=5006))

    contact = types.SimpleNamespace(phone_number="+9647701234567", user_id=5006)
    _dispatch(bot, _msg(uid=5006, contact=contact))

    assert users.state_of(5006) == users.STATE_VERIFIED
    assert users.get(5006)["phone_cc"] == "964"
    assert users.get(5006)["phone_last4"] == "4567"
    assert "m_create" in _buttons(bot.fake.sent[-1][2])


def test_contact_foreign_country_goes_pending(bot):
    _reset_flow(5007)
    db.set_setting("countries", "964")
    bot.fake.clear()
    _dispatch(bot, _msg("/start", uid=5007))

    contact = types.SimpleNamespace(phone_number="+12025550123", user_id=5007)
    _dispatch(bot, _msg(uid=5007, contact=contact))

    assert users.state_of(5007) == users.STATE_PENDING
    assert not users.is_exception(5007)
    owner_msgs = [t for cid, t, _ in bot.fake.sent if cid == 999]
    assert any("استثناء" in t for t in owner_msgs), "المالك لم يُشعَر بطلب الاستثناء"


def test_contact_of_other_person_rejected(bot):
    _reset_flow(5008)
    db.set_setting("countries", "964")
    _dispatch(bot, _msg("/start", uid=5008))
    bot.fake.clear()

    contact = types.SimpleNamespace(phone_number="+9647701234567", user_id=999999)
    _dispatch(bot, _msg(uid=5008, contact=contact))

    assert users.state_of(5008) != users.STATE_VERIFIED, "رقم شخص آخر مرّ!"
    assert "رقم غير صالح" in bot.fake.last()


def test_owner_allow_gives_access(bot):
    _reset_flow(5009)
    db.set_setting("countries", "964")
    _dispatch(bot, _msg("/start", uid=5009))
    contact = types.SimpleNamespace(phone_number="+12025550123", user_id=5009)
    _dispatch(bot, _msg(uid=5009, contact=contact))
    assert users.state_of(5009) == users.STATE_PENDING

    bot.fake.clear()
    _call(bot, "a_allow_5009", uid=999, name="المالك")
    assert users.state_of(5009) == users.STATE_VERIFIED
    assert users.is_exception(5009)


def test_pending_user_sees_waiting_screen(bot):
    _reset_flow(5010)
    db.set_setting("countries", "964")
    _dispatch(bot, _msg("/start", uid=5010))
    contact = types.SimpleNamespace(phone_number="+12025550123", user_id=5010)
    _dispatch(bot, _msg(uid=5010, contact=contact))
    bot.fake.clear()
    _dispatch(bot, _msg("/start", uid=5010))
    assert "بانتظار الموافقة" in bot.fake.last()


# ══════════════════════════════════════════════════
#  الواجهة
# ══════════════════════════════════════════════════
def _verified(uid, points=500):
    _reset_flow(uid)
    users.ensure(uid, "u%d" % uid, "مستخدم%d" % uid)
    users.set_verified(uid)
    users.add_points(uid, points, "test")


def test_menu_has_four_buttons(bot):
    _verified(5011)
    bot.fake.clear()
    _call(bot, "m_home", uid=5011)
    kb = bot.fake.sent[-1][2]
    flat = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    for want in ("m_create", "m_mybots", "m_support", "m_ref"):
        assert want in flat, "زر مفقود: " + want


def test_create_bot_flow(bot):
    _verified(5012, points=300)
    db.set_setting("bot_cost", 100)
    db.set_setting("free_mode", 0)
    bot.fake.clear()

    _call(bot, "m_create", uid=5012)
    assert bot.fake.steps, "لم يطلب التوكن"

    cb, args, kwargs = bot.fake.steps[-1]
    token_msg = _msg("799012:offline_token_value_1234567890", uid=5012)
    cb(token_msg, *args, **kwargs)

    from app import bots
    assert len(bots.by_owner(5012)) == 1
    assert users.balance(5012) == 200
    assert "تم إنشاء بوتك" in bot.fake.last()


def test_my_bots_shows_days(bot):
    bot.fake.clear()
    _call(bot, "m_mybots", uid=5012)
    assert "بوتاتي" in bot.fake.last() and "يوم" in bot.fake.last()


def test_referral_screen_shows_counts(bot):
    bot.fake.clear()
    _call(bot, "m_ref", uid=5012)
    text = bot.fake.last()
    assert "إحالاتك" in text and "نقاطك" in text


def test_support_creates_ticket(bot):
    _verified(5013)
    bot.fake.clear()
    _call(bot, "m_support", uid=5013)
    assert bot.fake.steps

    cb, args, kwargs = bot.fake.steps[-1]
    cb(_msg("مشكلتي في البوت", uid=5013), *args, **kwargs)

    row = db.one("SELECT * FROM tickets WHERE user_id=?", (5013,))
    assert row, "لم تُنشأ تذكرة"
    owner_msgs = [t for cid, t, _ in bot.fake.sent if cid == 999]
    assert any("رسالة دعم" in t for t in owner_msgs)


# ══════════════════════════════════════════════════
#  الصلاحيات
# ══════════════════════════════════════════════════
def test_non_admin_cannot_open_panel(bot):
    _verified(5014)
    bot.fake.clear()
    _call(bot, "a_home", uid=5014)
    assert any("للمالك" in str(a) for a in bot.fake.answers), "عضو عادي فتح اللوحة!"
    assert "لوحة المالك" not in bot.fake.last()


def test_admin_can_open_panel(bot):
    users.add_admin(5015, by=999)
    _verified(5015)
    bot.fake.clear()
    _call(bot, "a_home", uid=5015)
    assert "لوحة المالك" in bot.fake.last()


def test_owner_stats_screen(bot):
    bot.fake.clear()
    _call(bot, "a_stats", uid=999, name="المالك")
    text = bot.fake.last()
    assert "الإحصائيات" in text and "القمع" in text


def test_settings_toggle_verify(bot):
    db.set_setting("verify_enabled", 1)
    bot.fake.clear()
    _call(bot, "a_t_verify", uid=999, name="المالك")
    assert db.get_int("verify_enabled", 1) == 0
    _call(bot, "a_t_verify", uid=999, name="المالك")
    assert db.get_int("verify_enabled", 1) == 1


def test_admin_points_grant(bot):
    _verified(5016, points=0)
    bot.fake.clear()
    _call(bot, "a_points", uid=999, name="المالك")
    assert bot.fake.steps
    cb, args, kwargs = bot.fake.steps[-1]
    cb(_msg("5016 250", uid=999), *args, **kwargs)
    assert users.balance(5016) == 250


def test_points_cannot_go_negative_from_admin(bot):
    before = users.balance(5016)
    bot.fake.clear()
    _call(bot, "a_points", uid=999, name="المالك")
    cb, args, kwargs = bot.fake.steps[-1]
    cb(_msg("5016 -9999", uid=999), *args, **kwargs)
    assert users.balance(5016) == before, "الأدمن أنزل الرصيد تحت الصفر!"


# ══════════════════════════════════════════════════
#  المالك والأدمن: لا بوابات ولا عقوبات
# ══════════════════════════════════════════════════
def test_owner_bypasses_all_gates(bot):
    """المالك لا يُطلب منه اشتراك قنوات ولا فحص دولة."""
    _reset_flow(999)
    db.set_setting("verify_enabled", 1)
    db.set_setting("countries", "964")
    gates.add_channel(-100999, "ch_owner", "قناة")
    gates._cache.clear()
    bot.fake.clear()

    _dispatch(bot, _msg("/start", uid=999, name="المالك", uname="owner"))

    assert users.state_of(999) == users.STATE_VERIFIED, "المالك لم يُتحقق تلقائياً"
    assert "لوحة المالك" in bot.fake.last(), "لوحة المالك لم تُفتح للمالك"
    assert "اشترك في هذه القنوات" not in " ".join(t for _, t, _ in bot.fake.sent)
    gates.remove_channel(-100999)


def test_owner_not_blocked_by_country(bot):
    """لو أرسل المالك رقماً أجنبياً لا يُحجز في الانتظار."""
    _reset_flow(999)
    db.set_setting("countries", "964")
    _dispatch(bot, _msg("/start", uid=999, name="المالك"))
    bot.fake.clear()

    contact = types.SimpleNamespace(phone_number="+989123456789", user_id=999)
    _dispatch(bot, _msg(uid=999, contact=contact))

    assert users.state_of(999) == users.STATE_VERIFIED
    assert "لوحة المالك" in bot.fake.last()


def test_blocked_owner_still_enters(bot):
    """حتى لو حُظر حساب المالك بالخطأ، لا يُقفل عليه."""
    _reset_flow(999)
    users.ensure(999, "owner", "المالك")
    users.block(999)
    bot.fake.clear()
    _dispatch(bot, _msg("/start", uid=999, name="المالك"))
    assert "⛔ محظور" not in bot.fake.last(), "المالك محجوز بالحظر"
    assert "لوحة المالك" in bot.fake.last()
    users.ensure(999, "owner", "المالك")
    db.run("UPDATE users SET is_blocked=0 WHERE user_id=999")


def test_admin_bypasses_gates(bot):
    users.add_admin(777001, by=999)
    _reset_flow(777001)
    db.set_setting("countries", "964")
    gates.add_channel(-100998, "ch_admin", "قناة")
    gates._cache.clear()
    bot.fake.clear()

    _dispatch(bot, _msg("/start", uid=777001, name="أدمن"))

    assert users.state_of(777001) == users.STATE_VERIFIED
    assert "لوحة المالك" in bot.fake.last()
    gates.remove_channel(-100998)


def test_exception_user_with_foreign_number_passes(bot):
    """المستثنى يعبر عند مشاركة رقمه حتى لو دولته غير مسموحة."""
    _reset_flow(6001)
    db.set_setting("countries", "964")
    users.ensure(6001, "u6001", "مستثنى")
    users.add_exception(6001, by=999)
    db.run("UPDATE users SET state='phone' WHERE user_id=6001")
    bot.fake.clear()

    contact = types.SimpleNamespace(phone_number="+989123456789", user_id=6001)
    _dispatch(bot, _msg(uid=6001, contact=contact))

    assert users.state_of(6001) == users.STATE_VERIFIED
    assert "m_create" in _buttons(bot.fake.sent[-1][2])


# ══════════════════════════════════════════════════════════
#  لا كيبورد قاموسياً — تيليجرام يرفضه برسالة:
#  "Bad Request: can't parse reply keyboard markup JSON object"
# ══════════════════════════════════════════════════════════
def test_markup_converts_dict_to_telegram_object():
    import json
    from app.ui import markup, btn

    kb = {"inline_keyboard": [[btn("اختبار", "x_test")]]}
    obj = markup(kb)
    assert hasattr(obj, "to_json"), "markup لم تحوّل القاموس إلى كائن تيليجرام"
    data = json.loads(obj.to_json())
    assert data["inline_keyboard"][0][0]["callback_data"] == "x_test"


def test_markup_passes_telegram_objects_through():
    from telebot import types
    from app.ui import markup

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("x", callback_data="y"))
    assert markup(kb) is kb

    rm = types.ReplyKeyboardRemove()
    assert markup(rm) is rm, "الكائنات التي لا تملك to_dict يجب أن تمر كما هي"


def test_send_never_passes_raw_dict(bot):
    """كل رسالة تحمل كيبورد يجب أن يصل كائنَ تيليجرام لا قاموساً."""
    _verified(7001)
    seen = []
    real_send = bot.send_message
    real_edit = bot.edit_message_text

    def spy(chat_id, text, **kw):
        if kw.get("reply_markup") is not None:
            seen.append(type(kw["reply_markup"]).__name__)
        return real_send(chat_id, text, **kw)

    def spy_edit(text, chat_id=None, message_id=None, **kw):
        if kw.get("reply_markup") is not None:
            seen.append(type(kw["reply_markup"]).__name__)
        return real_edit(text, chat_id, message_id, **kw)

    bot.send_message = spy
    bot.edit_message_text = spy_edit
    try:
        _call(bot, "m_home", uid=7001)
        _call(bot, "m_mybots", uid=7001)
        _call(bot, "m_ref", uid=7001)
    finally:
        bot.send_message = real_send
        bot.edit_message_text = real_edit

    assert seen, "لم تُرسل أي رسالة بكيبورد"
    assert all("Markup" in s for s in seen), "أُرسل قاموس خام: %s" % seen
