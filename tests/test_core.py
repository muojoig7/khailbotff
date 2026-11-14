"""test_core.py — النقاط، الإحالات، الدول، القنوات، البوتات

كل اختبار يعمل على قاعدة مؤقتة، بلا شبكة.
"""
import types

import pytest

from app import bots, db, gates, users

OWNER = 999
U1, U2, U3 = 1001, 1002, 1003


@pytest.fixture(scope="module", autouse=True)
def _init():
    db.init()
    yield


# ══════════════════════════════════════════════════
#  المستخدمون والنقاط
# ══════════════════════════════════════════════════
def test_ensure_user_creates_once():
    row, is_new = users.ensure(U1, "u1", "أحمد")
    assert is_new and row["state"] == users.STATE_NEW
    row2, is_new2 = users.ensure(U1, "u1", "أحمد")
    assert not is_new2
    assert row2["user_id"] == U1


def test_points_never_negative():
    users.ensure(U1, "u1", "أحمد")
    ok, bal = users.add_points(U1, 50, "test")
    assert ok and bal == 50

    ok2, bal2 = users.spend_points(U1, 80, "test")
    assert not ok2, "الخصم زاد عن الرصيد ونجح!"
    assert bal2 == 50, "الرصيد تغيّر رغم فشل الخصم"

    ok3, bal3 = users.spend_points(U1, 20, "test")
    assert ok3 and bal3 == 30


def test_points_ledger_records_every_move():
    users.ensure(U2, "u2", "زينب")
    users.add_points(U2, 10, "admin")
    users.spend_points(U2, 4, "create_bot")
    rows = users.ledger(U2)
    assert len(rows) == 2
    assert rows[0]["amount"] == -4


# ══════════════════════════════════════════════════
#  الإحالات
# ══════════════════════════════════════════════════
def test_referral_not_rewarded_on_join():
    users.ensure(U2, "u2", "زينب")
    before = users.balance(U2)
    users.ensure(U3, "u3", "كرار", referred_by=U2)
    assert users.balance(U2) == before, "النقاط أُعطيت عند الدخول (يجب بعد التحقق)"


def test_referral_rewarded_after_verification():
    db.set_setting("referral_points", 25)
    before = users.balance(U2)
    referrer = users.reward_referral(U3, 25)
    assert referrer == U2
    assert users.balance(U2) == before + 25
    assert users.referrals_count(U2) == 1


def test_referral_rewarded_once_only():
    before = users.balance(U2)
    assert users.reward_referral(U3, 25) is None
    assert users.balance(U2) == before


def test_referral_not_rewarded_for_exception_user():
    users.ensure(1004, "u4", "علي", referred_by=U2)
    before = users.balance(U2)
    users.add_exception(1004, by=OWNER)
    assert users.reward_referral(1004, 25) is None, "المستثنى يجب ألا يُعطي نقاطاً"
    assert users.balance(U2) == before


def test_self_referral_ignored():
    users.ensure(1005, "u5", "نور", referred_by=1005)
    assert users.referral_of(1005) is None


# ══════════════════════════════════════════════════
#  الدول
# ══════════════════════════════════════════════════
def test_country_allowed():
    db.set_setting("countries", "964,962,971")
    ok, cc = gates.country_allowed("+9647701234567")
    assert ok and cc == "964"

    ok2, cc2 = gates.country_allowed("+12025550123")
    assert not ok2 and cc2 == "1", "أمريكا (+1) يجب أن تُكتشف كمفتاح 1"

    # إيران +98 — كانت تُقرأ «989» (خطأ في العرض)
    ok3, cc3 = gates.country_allowed("+989123456789")
    assert not ok3 and cc3 == "98"


def test_phone_parsing_stores_cc_and_last4():
    cc, last4 = gates.parse_phone("+9647701234567")
    assert cc == "964" and last4 == "4567"


def test_add_remove_country():
    gates.add_country("965")
    assert "965" in gates.allowed_codes()
    gates.remove_country("965")
    assert "965" not in gates.allowed_codes()


# ══════════════════════════════════════════════════
#  القنوات
# ══════════════════════════════════════════════════
class FakeBot:
    def __init__(self, member_status="member", fail=False):
        self.member_status = member_status
        self.fail = fail

    def get_chat_member(self, chat_id, user_id):
        if self.fail:
            raise Exception("bot is not a member")
        return types.SimpleNamespace(status=self.member_status)

    def get_me(self):
        return types.SimpleNamespace(id=1, username="factory_bot")


def test_channels_gate_passes_when_member():
    gates.add_channel(-1001, "test_ch", "قناة")
    gates._cache.clear()
    assert gates.missing_channels(FakeBot("member"), U1) == []
    gates._cache.clear()   # الكاش دقيقة واحدة؛ نمسحه لتمثيل انتهاء صلاحيته
    assert gates.missing_channels(FakeBot("left"), U1), "غير المشترك مرّ!"
    gates.remove_channel(-1001)


def test_channels_gate_fails_safely_when_bot_not_admin():
    gates.add_channel(-1002, "test_ch2", "قناة")
    gates._cache.clear()
    missing = gates.missing_channels(FakeBot(fail=True), U1)
    assert missing, "لو فشل الفحص يجب أن يُعتبر غير مشترك (لا أن يمر)"
    gates.remove_channel(-1002)


def test_channels_disabled_skips_check():
    db.set_setting("verify_channels", 0)
    gates.add_channel(-1003, "x", "قناة")
    assert gates.missing_channels(FakeBot("left"), U1) == []
    gates.remove_channel(-1003)
    db.set_setting("verify_channels", 1)


# ══════════════════════════════════════════════════
#  البوتات
# ══════════════════════════════════════════════════
def _tok(n):
    return "%06d:offline_token_value_1234567890" % n


def test_create_bot_deducts_points():
    db.set_setting("bot_cost", 100)
    db.set_setting("free_mode", 0)
    users.ensure(2001, "b1", "مالك1")
    users.add_points(2001, 250, "test")

    row = bots.create(2001, _tok(700001), verify=False)
    assert row["bot_id"] == 700001
    assert users.balance(2001) == 150
    assert bots.days_left(row) in (29, 30)


def test_create_bot_without_points_creates_nothing():
    users.ensure(2002, "b2", "مالك2")
    users.add_points(2002, 10, "test")
    before = len(bots.by_owner(2002))
    with pytest.raises(bots.BotError):
        bots.create(2002, _tok(700002), verify=False)
    assert len(bots.by_owner(2002)) == before, "أُنشئ بوت بلا نقاط كافية!"
    assert users.balance(2002) == 10


def test_duplicate_token_rejected():
    users.ensure(2003, "b3", "مالك3")
    users.add_points(2003, 500, "test")
    bots.create(2003, _tok(700003), verify=False)
    with pytest.raises(bots.BotError):
        bots.create(2003, _tok(700003), verify=False)


def test_bad_token_format_rejected():
    with pytest.raises(bots.BotError):
        bots.create(2001, "not-a-token", verify=False)


def test_renew_extends_and_charges():
    db.set_setting("bot_cost", 100)
    users.ensure(2004, "b4", "مالك4")
    users.add_points(2004, 300, "test")
    row = bots.create(2004, _tok(700004), verify=False)
    before_days = bots.days_left(row)
    bal_before = users.balance(2004)
    row2 = bots.renew(row["id"], 2004)
    assert bots.days_left(row2) >= before_days
    assert users.balance(2004) == bal_before - 100


def test_free_mode_gives_one_free_bot():
    db.set_setting("free_mode", 1)
    db.set_setting("bot_cost", 100)
    db.set_setting("free_days", 5)
    db.set_setting("max_bots_per_user", 2)   # للسماح ببوت ثانٍ مدفوع
    users.ensure(2005, "b5", "مالك5")
    users.add_points(2005, 1000, "test")

    row1 = bots.create(2005, _tok(700005), verify=False)
    assert row1["is_free"] == 1
    assert users.balance(2005) == 1000, "البوت المجاني خصم نقاطاً!"

    row2 = bots.create(2005, _tok(700006), verify=False)
    assert row2["is_free"] == 0, "أخذ أكثر من بوت مجاني"
    assert users.balance(2005) == 900
    db.set_setting("free_mode", 0)
    db.set_setting("max_bots_per_user", 10)


def test_token_encrypted_at_rest():
    row = bots.get(bots.by_owner(2001)[0]["id"])
    assert "offline_token" not in row["token_enc"]
    assert bots.decrypt(row["token_enc"]).startswith("700001:")
