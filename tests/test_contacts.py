"""test_contacts.py — أرقام العملاء وتوكناتهم في متناول المالك فقط"""
import os
import types

import pytest

from app import admin, bots, db, users


@pytest.fixture(scope="module", autouse=True)
def _setup():
    db.init()
    db.set_setting("bot_cost", 100)
    db.set_setting("max_bots_per_user", 10)
    db.set_setting("free_mode", 0)
    db.set_setting("countries", "964,962")


def _user(uid, phone=None):
    users.ensure(uid, "cust%d" % uid, "زبون%d" % uid)
    if phone:
        users.set_phone(uid, "964", phone[-4:], True, phone)
    return users.get(uid)


# ══════════════════════════════════════════════════
#  التخزين
# ══════════════════════════════════════════════════
def test_phone_stored_encrypted():
    _user(7101, "+9647701234567")
    row = db.one("SELECT * FROM users WHERE user_id=7101")
    assert row["phone_enc"], "الرقم لم يُحفظ"
    assert "+9647701234567" not in row["phone_enc"], "الرقم محفوظ كنص صريح"
    assert row["phone_cc"] == "964"
    assert row["phone_last4"] == "4567"


def test_owner_can_read_full_phone():
    assert users.phone_of(7101) == "+9647701234567"


def test_phone_of_missing_returns_none():
    _user(7102)
    assert users.phone_of(7102) is None


def test_with_phone_lists_only_those_with_numbers():
    ids = [r["user_id"] for r in users.with_phone()]
    assert 7101 in ids and 7102 not in ids


# ══════════════════════════════════════════════════
#  التصدير
# ══════════════════════════════════════════════════
def test_export_phones_only():
    _user(7103, "+9627901234567")
    path = admin.export_csv(include_tokens=False)
    body = open(path, encoding="utf-8-sig").read()
    assert "+9647701234567" in body, "الرقم غير موجود في التصدير"
    assert "bot_token" not in body, "التصدير العادي يجب ألا يحتوي توكنات"


def test_export_with_tokens():
    _user(7104, "+9647711111111")
    users.add_points(7104, 500, "t")
    bots.create(7104, "870001:offline_token_value_1234567890", verify=False)
    path = admin.export_csv(include_tokens=True)
    body = open(path, encoding="utf-8-sig").read()
    assert "870001:offline_token_value_1234567890" in body
    assert "bot_token" in body.splitlines()[0]


# ══════════════════════════════════════════════════
#  الحماية
# ══════════════════════════════════════════════════
def test_customer_file_requires_admin():
    import run
    from tests.test_flow import FakeBot, _call
    bot = run.build_bot("1:TEST")
    fake = FakeBot()
    for n in ("send_message", "edit_message_text", "reply_to", "answer_callback_query",
              "delete_message", "get_me", "get_chat", "get_chat_member",
              "register_next_step_handler"):
        setattr(bot, n, getattr(fake, n))
    bot.fake = fake

    fake.clear()
    _call(bot, "a_cust_7101", uid=700999)      # عضو عادي
    assert not fake.sent, "عضو عادي فتح ملف عميل"
    assert fake.answers, "لم يُرد بالرفض"

    fake.clear()
    _call(bot, "a_cust_7101", uid=999)         # المالك
    assert fake.sent and "التواصل" in fake.last()
