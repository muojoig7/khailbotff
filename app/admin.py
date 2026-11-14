"""admin.py — لوحة المالك والأدمن

المالك (من config) والأدمنية (جدول admins) يرون نفس اللوحة.
الفرق الوحيد: رفع تحديث ZIP للمالك فقط (المرحلة 4).
كل إجراء حساس يمر بفحص صلاحية في أول سطر.
"""
import logging
import os

import csv
from datetime import datetime

from app import (audit, backup, bots, broadcast, coupons, db, deploy, gates,
                 report, scripts, users)
from app.flows import send, main_menu
from app.ui import (card, screen, btn, grid, hint, esc, num, mention, code, error, ok)

logger = logging.getLogger(__name__)

_BOT = None


def bind(bot):
    global _BOT
    _BOT = bot


def _guard(call):
    if not users.is_admin(call.from_user.id):
        try:
            call.bot.answer_callback_query(call.id, "🔒 هذا الزر للمالك فقط", show_alert=True)
        except Exception:
            pass
        return False
    return True


def _ack(bot, call, text=None, alert=False):
    try:
        if text:
            bot.answer_callback_query(call.id, text, show_alert=alert)
        else:
            bot.answer_callback_query(call.id)
    except Exception:
        pass


# ══════════════════════════════════════════════════
#  اللوحة الرئيسية
# ══════════════════════════════════════════════════
def home(bot, chat_id, uid, mid=None):
    from app.ui import panel, section, row, plain, num, btn, grid, footer
    from app import provision

    s_ = users.stats()
    free_mode = db.get_int("free_mode", 0) == 1
    running = int(db.scalar("SELECT COUNT(*) FROM bots WHERE status='active'", (), 0))
    stopped = int(db.scalar("SELECT COUNT(*) FROM bots WHERE status IN ('error','stopped')", (), 0))
    pending = int(db.scalar("SELECT COUNT(*) FROM users WHERE state='pending'", (), 0))
    verify = db.get_int("verify_enabled", 1) == 1
    runtime_ok = provision.runtime_ready()

    blocks = [
        [
            section("نبض المصنع", "📊"),
            row("المستخدمون", "<b>" + num(s_["users"]) + "</b> · اليوم +" + num(s_["today"]), "👥"),
            row("متحققون", num(s_["verified"]), "✅"),
            row("بانتظار", num(s_["unverified"]), "⏳"),
            row("محظورون", num(s_["blocked"]), "🚫"),
        ],
        [
            section("البوتات", "🤖"),
            row("الكل", num(s_["bots"]), "🤖"),
            row("تعمل الآن", num(running), "🟢"),
            row("متوقفة", num(stopped), "🔴"),
        ],
        [
            section("الاقتصاد", "💰"),
            row("النقاط عند الجميع", num(s_["points_total"]), "💎"),
            row("سعر البوت", num(db.get_int("bot_cost", 100)) + " نقطة", "💵"),
            row("نقاط الإحالة", num(db.get_int("referral_points", 25)), "🎁"),
        ],
        [
            section("يحتاج انتباهك", "⚠️"),
            plain("طلبات استثناء بانتظارك: <b>%s</b>" % num(pending)),
            plain("بوتات متوقفة: <b>%s</b>" % num(stopped)),
            plain("كود التشغيل: " + ("<b>جاهز ✅</b>" if runtime_ok else "<b>غير جاهز ❌</b>")),
        ],
        [
            section("الحالة", "⚙️"),
            row("التحقق", "✅ مفعّل" if verify else "⬜ معطّل", "🔐"),
            row("الوضع المجاني", "✅ مفعّل" if free_mode else "⬜ معطّل", "🆓"),
        ],
    ]

    text = panel("👑", "لوحة المالك", "كل شيء تحت يدك — من تيليجرام فقط", blocks,
                 footer("اختر قسماً من الأزرار"))
    rows = grid([
        ("🤖  البوتات", "a_bots"),
        ("💵  الإعدادات", "a_settings"),
        ("📢  القنوات", "a_channels"),
        ("🌍  الدول", "a_countries"),
        ("✅  الاستثناءات", "a_exc"),
        ("👤  الأدمنية", "a_admins"),
        ("💎  النقاط", "a_points"),
        ("📊  الإحصائيات", "a_stats"),
        ("📇  العملاء", "a_cust"),
        ("📣  البث", "a_bc"),
        ("🎟️  الكوبونات", "a_coupons"),
        ("💾  النسخ الاحتياطي", "a_backup"),
        ("📜  سجل التدقيق", "a_audit"),
        ("📊  التقرير", "a_report"),
        ("🆓  الوضع المجاني", "a_free"),
    ], per_row=2)
    if users.is_owner(uid):
        rows["inline_keyboard"].append([btn("🧩  كود التشغيل", "a_runtime"),
                                        btn("🚀  تحديث الكود", "a_deploy")])
        rows["inline_keyboard"].append([btn("🐍  السكربتات", "a_scripts")])
    rows["inline_keyboard"].append([btn("↩  رجوع للواجهة", "m_home")])
    return send(bot, chat_id, text, rows, mid)


# ══════════════════════════════════════════════════
#  الإعدادات
# ══════════════════════════════════════════════════
def settings_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, btn, grid, footer, num
    verify = db.get_int("verify_enabled", 1) == 1
    ch = db.get_int("verify_channels", 1) == 1
    co = db.get_int("verify_country", 1) == 1
    nt = db.get_int("notify_new_user", 1) == 1

    blocks = [
        [
            section("الأسعار والمدة", "💵"),
            row("سعر البوت", num(db.get_int("bot_cost", 100)) + " نقطة", "💵"),
            row("نقاط الإحالة", num(db.get_int("referral_points", 25)), "🎁"),
            row("مدة البوت", num(db.get_int("bot_days", 30)) + " يوم", "📅"),
            row("مدة المجاني", num(db.get_int("free_days", 7)) + " يوم", "🆓"),
            row("فترة السماح", num(db.get_int("grace_hours", 4)) + " ساعة", "⏳"),
        ],
        [
            section("البوابات والإشعار", "🔐"),
            row("التحقق العام", "✅ مفعّل" if verify else "⬜ معطّل", "🔐"),
            row("فحص القنوات", "✅" if ch else "⬜", "📢"),
            row("فحص الدولة", "✅" if co else "⬜", "🌍"),
            row("إشعار الدخول", "✅ فوري" if nt else "⬜ معطّل", "🔔"),
        ],
        [plain("<i>اضغط أي زر لتغييره فوراً</i>")],
    ]
    text = panel("💵", "الإعدادات", "كل أرقام المصنع في مكان واحد", blocks,
                 footer("التغيير يُطبّق على العمليات الجديدة"))
    rows = grid([
        ("💵 سعر البوت", "a_edit_cost"),
        ("🎁 نقاط الإحالة", "a_edit_ref"),
        ("📅 مدة البوت", "a_edit_days"),
        ("🆓 مدة المجاني", "a_edit_freedays"),
        ("⏳ فترة السماح", "a_edit_grace"),
        ("🔐 التحقق " + ("✅" if verify else "⬜"), "a_t_verify"),
        ("📢 القنوات " + ("✅" if ch else "⬜"), "a_t_channels"),
        ("🌍 الدولة " + ("✅" if co else "⬜"), "a_t_country"),
        ("🔔 الإشعار " + ("✅" if nt else "⬜"), "a_t_notify"),
    ], per_row=2)
    rows["inline_keyboard"].append([btn("↩  رجوع", "a_home")])
    return send(bot, chat_id, text, rows, mid)


def _ask_value(bot, chat_id, key, prompt, back="a_settings"):
    msg = bot.send_message(chat_id, prompt + "\n<i>للالغاء: الغاء</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, _save_value, key, back)


def _save_value(m, key, back):
    bot = _BOT
    text = (m.text or "").strip()
    if text.lower() in ("الغاء", "إلغاء"):
        return send(bot, m.chat.id, "❌ تم الإلغاء.")
    if not text.isdigit():
        return send(bot, m.chat.id, error("أرسل رقماً صحيحاً."))
    db.set_setting(key, int(text))
    send(bot, m.chat.id, ok("تم الحفظ: %s = %s" % (key, text)))
    if back == "a_home":
        return home(bot, m.chat.id, m.from_user.id)
    return settings_screen(bot, m.chat.id)


def _flip(key, label):
    """يقلب إعداداً ثنائياً ويرجع نص الحالة."""
    new = 0 if db.get_int(key, 1) == 1 else 1
    db.set_setting(key, new)
    return "%s %s" % ("✅" if new else "⬜", label)


# ══════════════════════════════════════════════════
#  بوتات العملاء
# ══════════════════════════════════════════════════
def bots_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, btn, bar, footer, esc
    rows = db.all_rows("SELECT * FROM bots ORDER BY id DESC LIMIT 8")
    total = int(db.scalar("SELECT COUNT(*) FROM bots", (), 0))
    running = int(db.scalar("SELECT COUNT(*) FROM bots WHERE status='active'", (), 0))
    total_days = db.get_int("bot_days", 30)

    blocks = []
    kb_rows = []
    for r in rows:
        left = bots.days_left(r) or 0
        icon = "🟢" if r["status"] == "active" else "🔴"
        blocks.append([
            section("@%s" % esc(r["username"] or "bot"), icon),
            plain(bots.status_text(r) + " · " + bar(left, total_days) + " <b>%s</b> يوم" % num(left)),
        ])
        kb_rows.append([
            btn("▶️", "a_bstart_%d" % r["id"]),
            btn("⏸", "a_bstop_%d" % r["id"]),
            btn("➕30ي", "a_bext_%d" % r["id"]),
        ])
    if not rows:
        blocks = [[plain("ماكو بوتات بعد.")]]
    kb_rows.append([btn("↩  رجوع", "a_home")])

    text = panel("🤖", "بوتات العملاء", "%s بوت · %s يعمل الآن" % (num(total), num(running)),
                 blocks, footer("▶️ تشغيل · ⏸ إيقاف · ➕30ي تمديد"))
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


# ══════════════════════════════════════════════════
#  القنوات
# ══════════════════════════════════════════════════
def channels_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, plain, num, btn, footer, esc, code
    rows = gates.channels()
    blocks = []
    kb_rows = []
    if rows:
        lines = []
        for ch in rows:
            label = ch["title"] or ch["username"] or str(ch["chat_id"])
            lines.append(plain("<b>%s</b> · %s" % (esc(label), code(ch["chat_id"]))))
            kb_rows.append([btn("🗑️ حذف " + esc(label)[:16], "a_chdel_%d" % ch["chat_id"])])
        blocks = [[section("القنوات المضافة", "📢")] + lines]
    else:
        blocks = [[section("لا قنوات", "📢"),
                   plain("البوت لا يطلب اشتراكاً من أحد.")]]
    blocks.append([plain("<i>يجب أن يكون البوت <b>مشرفاً</b> في القناة حتى يتحقق</i>")])

    kb_rows.append([btn("➕  إضافة قناة", "a_chadd"), btn("🩺  فحص", "a_chcheck")])
    kb_rows.append([btn("↩  رجوع", "a_home")])
    text = panel("📢", "قنوات الاشتراك", "%s قناة إجبارية" % num(len(rows)), blocks,
                 footer("احذف القناة لإلغاء الإجبار"))
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


def _ask_channel(bot, chat_id):
    msg = bot.send_message(chat_id, "أرسل معرف القناة:\n"
                                    "• <code>@username</code> للقناة العامة\n"
                                    "• أو <code>-100123456789</code> للخاصة\n"
                                    "<i>للالغاء: الغاء</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, _save_channel)


def _save_channel(m):
    bot = _BOT
    raw = (m.text or "").strip()
    if raw.lower() in ("الغاء", "إلغاء"):
        return send(bot, m.chat.id, "❌ تم الإلغاء.")
    chat_id, username, title = None, None, None
    try:
        if raw.startswith("@"):
            chat = bot.get_chat(raw)
            chat_id, username, title = chat.id, chat.username, chat.title
        elif raw.lstrip("-").isdigit():
            chat_id = int(raw)
            try:
                chat = bot.get_chat(chat_id)
                username, title = chat.username, chat.title
            except Exception:
                pass
        else:
            return send(bot, m.chat.id, error("صيغة غير صحيحة."))
    except Exception as e:
        return send(bot, m.chat.id, error("ما لكيت القناة: " + esc(str(e)[:60])))

    gates.add_channel(chat_id, username, title)
    audit.log(m.from_user.id, "channel_add", chat_id, title)
    send(bot, m.chat.id, ok("تمت إضافة القناة."))
    channels_screen(bot, m.chat.id)


# ══════════════════════════════════════════════════
#  الدول
# ══════════════════════════════════════════════════
def countries_screen(bot, chat_id, mid=None):
    codes = gates.allowed_codes()
    lines = ["🌍 <b>المسموح:</b> " + " · ".join(code("+" + c) for c in codes[:40])]
    if len(codes) > 40:
        lines.append("… و %d غيرها" % (len(codes) - 40))
    kb_rows = [[btn("➕  إضافة مفتاح", "a_coadd"), btn("➖  حذف مفتاح", "a_codel")],
               [btn("↩  رجوع", "a_home")]]
    text = screen("🌍  الدول المسموحة", num(len(codes)) + " مفتاح اتصال", lines,
                  hint("مثال: 964 العراق · 962 الأردن · 971 الإمارات"))
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


def _ask_country(bot, chat_id, remove=False):
    msg = bot.send_message(chat_id, "أرسل مفتاح الدولة (أرقام فقط، مثال: 964)")
    bot.register_next_step_handler(msg, _save_country, remove)


def _save_country(m, remove):
    bot = _BOT
    raw = (m.text or "").strip().lstrip("+")
    if not raw.isdigit():
        return send(bot, m.chat.id, error("أرسل أرقاماً فقط."))
    if remove:
        gates.remove_country(raw)
        send(bot, m.chat.id, ok("تم الحذف: +" + raw))
    else:
        gates.add_country(raw)
        send(bot, m.chat.id, ok("تمت الإضافة: +" + raw))
    return countries_screen(bot, m.chat.id)


# ══════════════════════════════════════════════════
#  الاستثناءات
# ══════════════════════════════════════════════════
def exceptions_screen(bot, chat_id, mid=None):
    rows = users.list_exceptions()
    lines, kb_rows = [], []
    for r in rows:
        name = r["first_name"] or "عضو"
        lines.append("✅ %s · %s" % (mention(r["user_id"], name), code(r["user_id"])))
        kb_rows.append([btn("➖ إزالة " + name[:14], "a_exdel_%d" % r["user_id"])])
    if not rows:
        lines.append("ماكو استثناءات.")
    kb_rows.append([btn("➕  إضافة استثناء", "a_exadd")])
    kb_rows.append([btn("↩  رجوع", "a_home")])
    text = screen("✅  استثناءات العبور", num(len(rows)) + " مستخدم", lines,
                  hint("المستثنى يعبر بلا فحص دولة، ولا تُحتسب إحالته"))
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


def _ask_exception(bot, chat_id):
    msg = bot.send_message(chat_id, "أرسل آيدي المستخدم (أرقام).")
    bot.register_next_step_handler(msg, _save_exception)


def _save_exception(m):
    bot = _BOT
    raw = (m.text or "").strip()
    if not raw.isdigit():
        return send(bot, m.chat.id, error("أرسل آيدي رقمياً."))
    uid = int(raw)
    users.add_exception(uid, by=m.from_user.id, note="manual")
    audit.log(m.from_user.id, "exception_add", uid)
    send(bot, m.chat.id, ok("تم السماح للمستخدم %s" % code(uid)))
    try:
        send(bot, uid, card("✅ تم السماح لك", [
            "وافق المالك على عبورك.",
            "أرسل /start للبدء.",
        ]))
    except Exception:
        pass
    return exceptions_screen(bot, m.chat.id)


# ══════════════════════════════════════════════════
#  الأدمنية
# ══════════════════════════════════════════════════
def admins_screen(bot, chat_id, mid=None):
    rows = users.list_admins()
    lines, kb_rows = [], []
    for r in rows:
        lines.append("👤 " + code(r["user_id"]))
        kb_rows.append([btn("➖ إزالة " + str(r["user_id"]), "a_addel_%d" % r["user_id"])])
    if not rows:
        lines.append("ماكو أدمنية غيرك.")
    kb_rows.append([btn("➕  إضافة أدمن", "a_adadd")])
    kb_rows.append([btn("↩  رجوع", "a_home")])
    text = screen("👤  الأدمنية", num(len(rows)) + " أدمن", lines,
                  hint("الأدمن يملك نفس صلاحياتك ما عدا رفع التحديث"))
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


def _ask_admin(bot, chat_id):
    msg = bot.send_message(chat_id, "أرسل آيدي الشخص الذي تريد رفعه أدمن.")
    bot.register_next_step_handler(msg, _save_admin)


def _save_admin(m):
    bot = _BOT
    raw = (m.text or "").strip()
    if not raw.isdigit():
        return send(bot, m.chat.id, error("أرسل آيدي رقمياً."))
    users.add_admin(int(raw), by=m.from_user.id)
    audit.log(m.from_user.id, "admin_add", int(raw))
    send(bot, m.chat.id, ok("تم رفعه أدمن."))
    try:
        send(bot, int(raw), card("🎉 صرت أدمن", ["أرسل /start لتفتح لوحة المالك."]))
    except Exception:
        pass
    return admins_screen(bot, m.chat.id)


# ══════════════════════════════════════════════════
#  النقاط
# ══════════════════════════════════════════════════
def points_screen(bot, chat_id, mid=None):
    text = screen("💎  النقاط", "أضف أو اخصم نقاطاً لأي مستخدم", [
        "الصيغة: <code>الآيدي المبلغ</code>",
        "مثال: <code>123456789 50</code> (إضافة) · <code>123456789 -20</code> (خصم)",
        "",
        "لا يمكن أن يصير الرصيد سالباً.",
    ])
    msg = send(bot, chat_id, text, {"inline_keyboard": [[btn("↩  رجوع", "a_home")]]}, mid)
    ask = bot.send_message(chat_id, "📩 أرسل: الآيدي والمبلغ\n<i>للالغاء: الغاء</i>",
                           parse_mode="HTML")
    bot.register_next_step_handler(ask, _save_points)
    return msg


def _save_points(m):
    bot = _BOT
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].lstrip("-").isdigit():
        return send(bot, m.chat.id, error("الصيغة: الآيدي المبلغ"))
    uid, amount = int(parts[0]), int(parts[1])
    if not users.get(uid):
        return send(bot, m.chat.id, error("المستخدم غير موجود."))
    ok_, bal = users.add_points(uid, amount, "admin", str(m.from_user.id))
    if not ok_:
        return send(bot, m.chat.id, error("الرصيد لا يكفي للخصم (لن يصير سالباً)."))
    audit.log(m.from_user.id, "points", uid, "%+d → %d" % (amount, bal))
    send(bot, m.chat.id, ok("تم · رصيده الآن %s" % num(bal)))
    try:
        send(bot, uid, card("💎 تغيّر رصيدك", [
            ("+" if amount > 0 else "") + num(amount) + " نقطة",
            "💎 <b>رصيدك:</b> " + num(bal),
        ]))
    except Exception:
        pass
    return home(bot, m.chat.id, m.from_user.id)


# ══════════════════════════════════════════════════
#  الإحصائيات
# ══════════════════════════════════════════════════
def stats_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, bar, btn, footer
    st = users.stats()
    ch_pass = int(db.scalar("SELECT COUNT(*) FROM users WHERE state IN ('phone','verified')", (), 0))
    phone = int(db.scalar("SELECT COUNT(*) FROM users WHERE phone_cc IS NOT NULL", (), 0))
    total = max(1, st["users"])

    blocks = [
        [
            section("الأرقام", "📊"),
            row("المستخدمون", "<b>" + num(st["users"]) + "</b> · اليوم +" + num(st["today"]), "👥"),
            row("متحققون", num(st["verified"]), "✅"),
            row("بانتظار", num(st["unverified"]), "⏳"),
            row("محظورون", num(st["blocked"]), "🚫"),
            row("استثناءات", num(st["exceptions"]), "⭐"),
        ],
        [
            section("قمع التحويل", "🧭"),
            plain(bar(ch_pass, total) + "  تجاوز القنوات · <b>%s</b>" % num(ch_pass)),
            plain(bar(phone, total) + "  شارك رقمه · <b>%s</b>" % num(phone)),
            plain(bar(st["verified"], total) + "  تحقّق · <b>%s</b>" % num(st["verified"])),
        ],
        [
            section("البوتات والنقاط", "🤖"),
            row("البوتات", num(st["bots"]), "🤖"),
            row("النقاط عند الجميع", num(st["points_total"]), "💎"),
            row("إحالات مكافأة", num(st["referrals"]), "🎁"),
            row("بثوث", num(int(db.scalar("SELECT COUNT(*) FROM broadcasts", (), 0))), "📣"),
        ],
    ]
    text = panel("📊", "الإحصائيات", "أرقام المصنع كاملة", blocks,
                 footer("القمع يخبرك أين يتوقف الناس"))
    return send(bot, chat_id, text, {"inline_keyboard": [[btn("↩  رجوع", "a_home")]]}, mid)


# ══════════════════════════════════════════════════
#  الوضع المجاني
# ══════════════════════════════════════════════════
def coupons_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, btn, footer, code
    st = coupons.stats()
    rows = coupons.recent(6)
    blocks = [
        [section("الحالة", "🎟️"),
         row("الكل", num(st["total"]), "🎟️"),
         row("غير مستخدمة", num(st["unused"]), "🟢"),
         row("استُخدمت", num(st["redeemed"]), "✅"),
         row("قيمة الكود", num(db.get_int("coupon_points", 30)) + " نقطة", "💎")],
    ]
    if rows:
        lines = [section("آخر الأكواد", "🕓")]
        for r in rows:
            lines.append(plain("%s · %s نقطة · %s" % (
                code(r["code"]), num(r["points"]),
                "✅ مستخدم" if r["used_at"] else "متاح")))
        blocks.append(lines)
    blocks.append([plain("<i>الأكواد تُستخدم مرة واحدة — وزّعها في قنواتك</i>")])
    text = panel("🎟️", "الكوبونات", "أكواد نقاط مجانية", blocks,
                 footer("30 نقطة لكل كود افتراضياً"))
    kb = {"inline_keyboard": [
        [btn("➕  توليد 1", "a_cp_1"), btn("➕  توليد 5", "a_cp_5"), btn("➕  توليد 10", "a_cp_10")],
        [btn("↩  رجوع", "a_home")],
    ]}
    return send(bot, chat_id, text, kb, mid)


def backup_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, btn, footer
    rows = backup.last(6)
    blocks = [
        [section("الإعدادات", "💾"),
         row("كل", num(db.get_int("backup_hours", 6)) + " ساعة", "⏰"),
         row("نحتفظ", num(db.get_int("backup_keep", 14)) + " نسخة", "📦")],
    ]
    if rows:
        lines = [section("آخر النسخ", "🕓")]
        for r in rows:
            lines.append(plain("%s · %.0f KB %s" % (
                (r["created_at"] or "")[:16], (r["size"] or 0) / 1024,
                "· أُرسلت ✅" if r["sent"] else "")))
        blocks.append(lines)
    else:
        blocks.append([plain("ماكو نسخ بعد.")])
    text = panel("💾", "النسخ الاحتياطي", "قاعدة المصنع محفوظة", blocks,
                 footer("نسخة لم تُجرَّب ليست نسخة"))
    kb = {"inline_keyboard": [
        [btn("💾  نسخة الآن", "a_backup_now")],
        [btn("↩  رجوع", "a_home")],
    ]}
    return send(bot, chat_id, text, kb, mid)


def audit_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, plain, num, btn, footer, code, esc
    rows = audit.recent(15)
    lines = [section("آخر 15 عملية", "📜")]
    for r in rows:
        lines.append(plain("%s · %s · %s · %s" % (
            (r["created_at"] or "")[5:16], code(r["actor"]), esc(r["action"]),
            esc(r["details"] or r["target"] or ""))))
    if not rows:
        lines.append(plain("ماكو عمليات مسجّلة."))
    text = panel("📜", "سجل التدقيق", "%s عملية" % num(audit.count()), [lines],
                 footer("من فعل ماذا ومتى"))
    return send(bot, chat_id, text, {"inline_keyboard": [[btn("↩  رجوع", "a_home")]]}, mid)


PER_PAGE_CUST = 10


def customers_screen(bot, chat_id, mid=None, page=0):
    from app.ui import panel, section, row, plain, num, btn, footer, code, esc
    total = int(db.scalar("SELECT COUNT(*) FROM users", (), 0))
    with_phone = int(db.scalar("SELECT COUNT(*) FROM users WHERE phone_enc IS NOT NULL", (), 0))
    pages = max(1, (total + PER_PAGE_CUST - 1) // PER_PAGE_CUST)
    page = max(0, min(page, pages - 1))
    rows = db.all_rows(
        "SELECT * FROM users ORDER BY user_id DESC LIMIT ? OFFSET ?",
        (PER_PAGE_CUST, page * PER_PAGE_CUST))

    blocks = [
        [section("الأرقام", "📇"),
         row("المستخدمون", num(total), "👥"),
         row("عندهم رقم محفوظ", num(with_phone), "📱")],
    ]
    kb_rows = []
    if rows:
        lines = [section("العملاء (صفحة %d/%d)" % (page + 1, pages), "📋")]
        for r in rows:
            mark = "📱" if r["phone_enc"] else "—"
            lines.append(plain("%s <b>%s</b> · %s" % (
                mark, esc(r["first_name"] or "عضو"),
                ("@" + esc(r["username"])) if r["username"] else code(r["user_id"]))))
            kb_rows.append([btn("%s %s" % (mark, (r["first_name"] or str(r["user_id"]))[:16]),
                                "a_cust_%d" % r["user_id"])])
    else:
        blocks.append([plain("ماكو عملاء بعد.")])

    blocks.append([
        section("التصدير", "📤"),
        plain("ملف CSV فيه الأرقام — يوصلك هنا في تيليجرام"),
    ])

    text = panel("📇", "العملاء", "أرقامهم وبوتاتهم في مكان واحد", blocks,
                 footer("الأرقام مشفّرة في القاعدة — تُفكّ لك وحدك"))
    nav = []
    if page > 0:
        nav.append(btn("⬅️ السابق", "a_cust_p_%d" % (page - 1)))
    if page < pages - 1:
        nav.append(btn("التالي ➡️", "a_cust_p_%d" % (page + 1)))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([btn("📤 تصدير الأرقام", "a_cust_export")])
    kb_rows.append([btn("↩  رجوع", "a_home")])
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


def customer_screen(bot, chat_id, uid, mid=None):
    from app.ui import panel, section, row, plain, num, btn, footer, code, esc, mention
    from app.secret import mask
    u = users.get(uid)
    if not u:
        return send(bot, chat_id, "⚠️ المستخدم غير موجود.",
                    {"inline_keyboard": [[btn("↩ رجوع", "a_cust")]]}, mid)

    phone = users.phone_of(uid)
    my_bots = bots.by_owner(uid)
    blocks = [
        [section("البيانات", "👤"),
         plain("👤 " + mention(uid, u["first_name"] or "عضو")),
         row("اليوزر", ("@" + esc(u["username"])) if u["username"] else "—", "🔗"),
         row("الآيدي", code(uid), "🆔"),
         row("النقاط", num(u["points"]), "💎"),
         row("انضم", (u["joined_at"] or "")[:16], "🗓️")],
        [section("التواصل", "📱"),
         row("الهاتف", code(mask(phone)) if phone else "غير متوفر", "📱"),
         row("الدولة", code("+" + str(u["phone_cc"])) if u["phone_cc"] else "—", "🌍"),
         row("متحقق", "✅" if u["state"] == "verified" else "⏳", "🔐")],
    ]
    if my_bots:
        lines = [section("بوتاته (%d)" % len(my_bots), "🤖")]
        kb_rows = []
        for b in my_bots[:6]:
            left = bots.days_left(b) or 0
            lines.append(plain("%s <b>@%s</b> · %s · %s يوم" % (
                "🟢" if b["status"] == "active" else "🔴",
                esc(b["username"] or "bot"), bots.status_text(b), num(left))))
            kb_rows.append([btn("🔑 توكن @%s" % (b["username"] or "bot")[:14],
                                "a_tok_%d" % b["id"])])
        blocks.append(lines)
    else:
        kb_rows = []
        blocks.append([section("بوتاته", "🤖"), plain("ما عند ذاك المستخدم بوت.")])

    if phone:
        kb_rows.insert(0, [btn("📱 إظهار الرقم كاملاً", "a_cust_phone_%d" % uid)])
    kb_rows.append([btn("↩  رجوع للعملاء", "a_cust")])

    text = panel("📇", "ملف العميل", esc(u["first_name"] or str(uid)), blocks,
                 footer("كل عرض للرقم أو التوكن يُسجَّل"))
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


def export_csv(include_tokens=False):
    """يبني ملف CSV بالأرقام (والتوكنات اختيارياً) ويرجع مساره."""
    import csv
    from app.secret import decrypt
    os.makedirs("data/exports", exist_ok=True)
    path = os.path.join("data/exports", "customers-%s.csv" %
                        datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        head = ["user_id", "name", "username", "phone", "country", "points", "state", "joined"]
        if include_tokens:
            head += ["bot_username", "bot_token"]
        w.writerow(head)

        for u in db.all_rows("SELECT * FROM users ORDER BY user_id DESC"):
            phone = users.phone_of(u["user_id"]) or ""
            base = [u["user_id"], u["first_name"] or "", u["username"] or "", phone,
                    u["phone_cc"] or "", u["points"] or 0, u["state"] or "",
                    u["joined_at"] or ""]
            if not include_tokens:
                w.writerow(base)
                continue
            my = bots.by_owner(u["user_id"])
            if not my:
                w.writerow(base + ["", ""])
            for b in my:
                try:
                    tok = decrypt(b["token_enc"])
                except Exception:
                    tok = ""
                w.writerow(base + [b["username"] or "", tok])
    return path


def scripts_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, btn, footer, code, esc
    scripts.refresh_statuses()
    rows = scripts.all_scripts(8)
    blocks = [
        [section("الحالة", "🐍"),
         row("الكل", num(int(db.scalar("SELECT COUNT(*) FROM scripts", (), 0))), "🐍"),
         row("تعمل الآن", num(scripts.running_count()), "🟢"),
         row("السقف", num(scripts.MAX_RUNNING), "🚧")],
    ]
    kb_rows = []
    if rows:
        lines = [section("السكربتات", "📂")]
        for r in rows:
            lines.append(plain("%s <b>%s</b> · %s" % (
                "🟢" if r["status"] == "running" else ("🔴" if r["status"] == "failed" else "⏸"),
                esc(r["name"] or "script"), scripts.status_text(r))))
            kb_rows.append([
                btn("▶️", "a_sc_start_%d" % r["id"]),
                btn("⏸", "a_sc_stop_%d" % r["id"]),
                btn("📜", "a_sc_out_%d" % r["id"]),
                btn("🗑", "a_sc_del_%d" % r["id"]),
            ])
        blocks.append(lines)
    else:
        blocks.append([plain("ماكو سكربتات بعد.")])

    blocks.append([
        section("كيف ترفع سكربتاً", "📤"),
        plain("• ابعت ملف <code>.py</code> هنا في الخاص"),
        plain("• أو أرشيف <code>.zip</code> فيه <code>main.py</code>"),
        plain("• ثم تتحكم به: تشغيل · إيقاف · إخراج · حذف"),
    ])

    text = panel("🐍", "السكربتات", "شغّل ملفات بايثون بتحكم كامل", blocks,
                 footer("المالك فقط · كل سكربت معزول في مجلده"))
    kb_rows.append([btn("↩  رجوع", "a_home")])
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


def runtime_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, btn, footer, code, esc
    from app import provision
    ok = provision.runtime_ready()
    zips = []
    try:
        zips = [f for f in os.listdir(".") if f.lower().endswith(".zip") and "alkhal_bot" in f.lower()]
    except OSError:
        pass

    blocks = [
        [section("الحالة", "🧩"),
         row("كود التشغيل", "✅ جاهز" if ok else "❌ غير موجود", "⚙️"),
         row("المسار", code(provision.RUNTIME_DIR), "📁"),
         row("بوتات تعمل", num(int(db.scalar(
             "SELECT COUNT(*) FROM bots WHERE status='active'", (), 0))), "🟢")],
    ]
    if not ok:
        lines = [section("كيف تجهّزه", "🛠️")]
        if zips:
            lines.append(plain("ملف موجود: " + code(zips[0])))
            lines.append(plain("اضغط الزر بالأسفل — سأفكّه تلقائياً."))
        else:
            lines.append(plain("<b>الأسهل:</b> ابعت ملف <code>alkhal_bot_v6_2.zip</code> "
                               "هنا في الخاص — سأجهّزه فوراً."))
            lines.append(plain("أو ضعه بجانب المصنع واضغط «إعادة الفحص»"))
            lines.append(plain("أو من الطرفية: <code>python3 setup_runtime.py alkhal_bot_v6_2.zip</code>"))
        blocks.append(lines)
    else:
        blocks.append([plain("<i>كل البوتات ستُشغّل من هذا المجلد</i>")])

    text = panel("🧩", "كود التشغيل", "بوت الحماية الذي يشغّله المصنع لكل عميل", blocks,
                 footer("بدونه لا يعمل أي بوت"))
    kb = {"inline_keyboard": [
        [btn("🔄  إعادة الفحص / تجهيز", "a_runtime_fix")],
        [btn("↩  رجوع", "a_home")],
    ]}
    return send(bot, chat_id, text, kb, mid)


def deploy_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, btn, footer, esc, code
    from app import provision
    cur = deploy.current_version() or "الأساس (runtime/)"
    rows = deploy.history(4)
    blocks = [
        [
            section("الإصدار الحالي", "🧩"),
            row("الكود", code(cur), "📦"),
            row("كود التشغيل جاهز", "✅" if provision.runtime_ready() else "❌", "⚙️"),
            row("بوتات", num(int(db.scalar("SELECT COUNT(*) FROM bots", (), 0))), "🤖"),
        ],
    ]
    if rows:
        lines = [section("آخر عمليات النشر", "🕓")]
        icons = {"done": "✅", "failed": "❌", "rolled_back": "⏪",
                 "interrupted": "⚠️", "canary": "🧪", "staging": "⏳"}
        for r in rows:
            lines.append(plain("%s #%d · %s · %s/%s بوت" % (
                icons.get(r["status"], "•"), r["id"], esc(r["version"] or "—"),
                num(r["bots_moved"]), num(r["bots_total"]))))
        blocks.append(lines)

    blocks.append([
        section("كيف ترفع تحديثاً", "📤"),
        plain("1. ابعت ملف <b>ZIP</b> هنا في الخاص"),
        plain("2. يُفحص: محتوى · صياغة · اختبارات"),
        plain("3. كناري على بوت واحد ثم دفعات"),
        plain("4. أي فشل → تراجع آلي"),
    ])

    text = panel("🚀", "تحديث الكود", "نشر آمن بلا توقف للبوتات", blocks,
                 footer("الملف يُقبل من المالك فقط"))
    kb = {"inline_keyboard": [
        [btn("⏪  تراجع عن آخر نشر", "a_deploy_rollback")],
        [btn("↩  رجوع", "a_home")],
    ]}
    return send(bot, chat_id, text, kb, mid)


def broadcast_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, btn, footer
    u, g, total = broadcast.count_targets()
    last = broadcast.last_broadcasts(3)
    blocks = [
        [
            section("الجمهور", "🎯"),
            row("المستخدمون", num(u), "👥"),
            row("المجموعات", num(g), "💬"),
            row("المجموع", "<b>" + num(total) + "</b>", "📣"),
        ],
    ]
    if last:
        lines = [section("آخر البثوث", "🕓")]
        for b in last:
            icon = {"done": "✅", "pending": "⏳", "cancelled": "🚫"}.get(b["status"], "•")
            lines.append(plain("%s #%d · %s/%s · %s" % (
                icon, b["id"], num(b["sent"]), num(b["total"]),
                b["created_at"][:16] if b["created_at"] else "")))
        blocks.append(lines)
    blocks.append([plain("<i>البث للمجموعات يُرسل ببوت كل عميل، لا ببوت المصنع</i>")])

    text = panel("📣", "البث", "رسالة واحدة لكل جمهورك", blocks,
                 footer("طابور بحد 20 رسالة/ثانية"))
    kb = {"inline_keyboard": [
        [btn("✍️  بث جديد", "a_bc_new")],
        [btn("↩  رجوع", "a_home")],
    ]}
    return send(bot, chat_id, text, kb, mid)


def _ask_broadcast(bot, chat_id):
    msg = bot.send_message(chat_id, "✍️ <b>أرسل نص الرسالة</b>\n"
                                    "يدعم HTML: <code>&lt;b&gt;</code> و<code>&lt;i&gt;</code>\n"
                                    "<i>للالغاء: الغاء</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, _confirm_broadcast)


def _confirm_broadcast(m):
    bot = _BOT
    text = (m.text or "").strip()
    if text.lower() in ("الغاء", "إلغاء"):
        return send(bot, m.chat.id, "❌ تم الإلغاء.")
    _BC_DRAFT[m.from_user.id] = text

    u, g, total = broadcast.count_targets()
    from app.ui import panel, section, row, plain, num, btn, footer, esc
    blocks = [
        [section("الرسالة", "📝"), plain("<i>" + esc(text[:300]) + "</i>")],
        [section("سيُرسل إلى", "🎯"),
         row("المستخدمون", num(u), "👥"),
         row("المجموعات", num(g), "💬")],
    ]
    kb = {"inline_keyboard": [
        [btn("📣  أرسل للكل (%s)" % num(total), "a_bc_go_all")],
        [btn("👥  للمستخدمين فقط", "a_bc_go_users"),
         btn("💬  للمجموعات فقط", "a_bc_go_groups")],
        [btn("❌  إلغاء", "a_bc_cancel")],
    ]}
    text2 = panel("📣", "تأكيد البث", "راجع قبل الإرسال — لا يمكن التراجع", blocks,
                  footer("الرسالة لا تُرسل مرتين"))
    return send(bot, m.chat.id, text2, kb)


_BC_DRAFT = {}


def _do_broadcast(bot, call, users=True, groups=True):
    text = _BC_DRAFT.pop(call.from_user.id, None)
    if not text:
        return _ack(bot, call, "انتهت صلاحية المسودة — ابدأ من جديد", True)
    bid, total = broadcast.create("manual", text, users=users, groups=groups,
                                  by=call.from_user.id)
    audit.log(call.from_user.id, "broadcast", bid, "%d هدف" % total)
    _ack(bot, call, "📣 بدأ الإرسال (%s)" % total)
    from app.ui import card, num
    send(bot, call.message.chat.id, card("📣 انطلق البث #%d" % bid, [
        "🎯 <b>الهدف:</b> " + num(total),
        "⏳ سيصلك تقرير عند الانتهاء.",
    ]))


# ═══ الوضع المجاني (مع رسالة إلزامية) ═══
def free_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, row, plain, num, btn, footer
    on = db.get_int("free_mode", 0) == 1
    free_used = int(db.scalar("SELECT COUNT(*) FROM users WHERE free_used=1", (), 0))
    blocks = [
        [
            section("الحالة", "🆓"),
            row("الوضع المجاني", "✅ مفعّل" if on else "⬜ معطّل", "🆓"),
            row("مدة البوت المجاني", num(db.get_int("free_days", 7)) + " يوم", "📅"),
            row("استخدمه", num(free_used) + " مستخدم", "👥"),
        ],
        [
            section("كيف يعمل", "❓"),
            plain("• كل مستخدم يأخذ <b>بوتاً واحداً</b> مجاناً"),
            plain("• التجديد يبقى بالنقاط دائماً"),
            plain("• إيقاف الوضع <b>لا يوقف</b> البوتات التي أُنشئت فيه"),
            plain("• التفعيل يرسل <b>رسالة إعلان</b> لكل المستخدمين والمجموعات"),
        ],
    ]
    text = panel("🆓", "الوضع المجاني", "بوت واحد مجاناً لكل مستخدم", blocks,
                 footer("الإعلان لا يمكن التراجع عنه"))
    if on:
        kb = {"inline_keyboard": [
            [btn("⬜  إيقاف الوضع", "a_free_toggle")],
            [btn("↩  رجوع", "a_home")],
        ]}
    else:
        kb = {"inline_keyboard": [
            [btn("✅  تفعيل + إعلان", "a_free_msg")],
            [btn("↩  رجوع", "a_home")],
        ]}
    return send(bot, chat_id, text, kb, mid)


def _ask_free_message(bot, chat_id):
    msg = bot.send_message(chat_id, "✍️ <b>أرسل رسالة الإعلان</b>\n"
                                    "ستُرسل لكل المستخدمين والمجموعات مع تفعيل الوضع المجاني.\n"
                                    "<i>للالغاء: الغاء</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, _confirm_free)


def _confirm_free(m):
    bot = _BOT
    text = (m.text or "").strip()
    if text.lower() in ("الغاء", "إلغاء"):
        return send(bot, m.chat.id, "❌ تم الإلغاء.")
    _BC_DRAFT[m.from_user.id] = text

    u, g, total = broadcast.count_targets()
    from app.ui import panel, section, row, plain, num, btn, footer, esc
    blocks = [
        [section("الإعلان", "📝"), plain("<i>" + esc(text[:300]) + "</i>")],
        [section("سيُرسل إلى", "🎯"),
         row("المستخدمون", num(u), "👥"),
         row("المجموعات", num(g), "💬"),
         row("ويُفعّل", "الوضع المجاني 🆓", "✅")],
    ]
    kb = {"inline_keyboard": [
        [btn("📣  أرسل وفعّل (%s)" % num(total), "a_free_go")],
        [btn("❌  إلغاء", "a_bc_cancel")],
    ]}
    text2 = panel("🆓", "تأكيد الإعلان", "خطوة أخيرة قبل الإرسال", blocks,
                  footer("الرسالة تُرسل مرة واحدة"))
    return send(bot, m.chat.id, text2, kb)


def _do_free(bot, call):
    text = _BC_DRAFT.pop(call.from_user.id, None)
    if not text:
        return _ack(bot, call, "انتهت صلاحية المسودة", True)
    bid, total = broadcast.create("free_mode", text, users=True, groups=True,
                                  by=call.from_user.id)
    db.set_setting("free_mode", 1)
    audit.log(call.from_user.id, "free_mode_on", bid, "%d هدف" % total)
    _ack(bot, call, "✅ فُعّل الوضع المجاني وبدأ الإعلان")
    from app.ui import card, num
    send(bot, call.message.chat.id, card("🆓 الوضع المجاني مفعّل", [
        "📣 <b>الإعلان:</b> بث #%d" % bid,
        "🎯 <b>الهدف:</b> " + num(total),
        "⏳ سيصلك تقرير عند الانتهاء.",
    ]))


# ══════════════════════════════════════════════════
#  التسجيل
# ══════════════════════════════════════════════════
def register(bot):
    bind(bot)

    def guard(call):
        if not users.is_admin(call.from_user.id):
            _ack(bot, call, "🔒 للمالك فقط", alert=True)
            return False
        return True

    @bot.callback_query_handler(func=lambda c: c.data == "a_home")
    def _home(c):
        if not guard(c):
            return
        _ack(bot, c)
        home(bot, c.message.chat.id, c.from_user.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_settings")
    def _settings(c):
        if not guard(c):
            return
        _ack(bot, c)
        settings_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_edit_"))
    def _edit(c):
        if not guard(c):
            return
        _ack(bot, c)
        key = c.data.replace("a_edit_", "")
        prompts = {
            "cost": ("bot_cost", "أرسل سعر إنشاء البوت بالنقاط:"),
            "ref": ("referral_points", "أرسل نقاط الإحالة:"),
            "days": ("bot_days", "أرسل مدة البوت بالأيام:"),
            "freedays": ("free_days", "أرسل مدة البوت المجاني بالأيام:"),
            "grace": ("grace_hours", "أرسل فترة السماح بالساعات:"),
        }
        if key not in prompts:
            return
        k, p = prompts[key]
        _ask_value(bot, c.message.chat.id, k, p)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_t_"))
    def _toggle(c):
        if not guard(c):
            return
        key = c.data.replace("a_t_", "")
        mapping = {"verify": ("verify_enabled", "التحقق"),
                   "channels": ("verify_channels", "فحص القنوات"),
                   "country": ("verify_country", "فحص الدولة"),
                   "notify": ("notify_new_user", "إشعار الدخول")}
        if key not in mapping:
            return
        k, label = mapping[key]
        msg = _flip(k, label)
        audit.log(c.from_user.id, "setting", k, msg)
        _ack(bot, c, msg)
        settings_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_bots")
    def _bots(c):
        if not guard(c):
            return
        _ack(bot, c)
        bots_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_bstart_"))
    def _bstart(c):
        if not guard(c):
            return
        rid = int(c.data.replace("a_bstart_", ""))
        okk, msg = bots.start_runtime(rid)
        _ack(bot, c, "▶️ " + ("تم التشغيل" if okk else str(msg)[:40]))
        bots_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_bstop_"))
    def _bstop(c):
        if not guard(c):
            return
        rid = int(c.data.replace("a_bstop_", ""))
        bots.stop_runtime(rid)
        audit.log(c.from_user.id, "bot_stop", rid)
        _ack(bot, c, "⏸️ أُوقف")
        bots_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_bext_"))
    def _bext(c):
        if not guard(c):
            return
        rid = int(c.data.replace("a_bext_", ""))
        bots.add_days(rid, 30)
        audit.log(c.from_user.id, "bot_extend", rid, "+30d")
        _ack(bot, c, "➕ 30 يوم")
        bots_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_channels")
    def _channels(c):
        if not guard(c):
            return
        _ack(bot, c)
        channels_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_chadd")
    def _chadd(c):
        if not guard(c):
            return
        _ack(bot, c)
        _ask_channel(bot, c.message.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_chdel_"))
    def _chdel(c):
        if not guard(c):
            return
        cid = int(c.data.replace("a_chdel_", ""))
        gates.remove_channel(cid)
        audit.log(c.from_user.id, "channel_remove", cid)
        _ack(bot, c, "🗑️ حُذفت")
        channels_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_chcheck")
    def _chcheck(c):
        if not guard(c):
            return
        _ack(bot, c)
        report = gates.channel_health(bot)
        lines = []
        for ch, healthy, status in report:
            lines.append(("✅ " if healthy else "❌ ") + (ch["title"] or str(ch["chat_id"]))
                         + " · " + esc(str(status)[:30]))
        if not lines:
            lines = ["ماكو قنوات مضافة."]
        send(bot, c.message.chat.id, screen("🩺 فحص القنوات", None, lines),
             {"inline_keyboard": [[btn("↩ رجوع", "a_channels")]]}, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_countries")
    def _countries(c):
        if not guard(c):
            return
        _ack(bot, c)
        countries_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data in ("a_coadd", "a_codel"))
    def _coadd(c):
        if not guard(c):
            return
        _ack(bot, c)
        _ask_country(bot, c.message.chat.id, remove=(c.data == "a_codel"))

    @bot.callback_query_handler(func=lambda c: c.data == "a_exc")
    def _exc(c):
        if not guard(c):
            return
        _ack(bot, c)
        exceptions_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_exadd")
    def _exadd(c):
        if not guard(c):
            return
        _ack(bot, c)
        _ask_exception(bot, c.message.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_exdel_"))
    def _exdel(c):
        if not guard(c):
            return
        uid = int(c.data.replace("a_exdel_", ""))
        users.remove_exception(uid)
        audit.log(c.from_user.id, "exception_remove", uid)
        _ack(bot, c, "➖ أُزيل")
        exceptions_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_admins")
    def _admins(c):
        if not guard(c):
            return
        _ack(bot, c)
        admins_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_adadd")
    def _adadd(c):
        if not guard(c):
            return
        _ack(bot, c)
        _ask_admin(bot, c.message.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_addel_"))
    def _addel(c):
        if not guard(c):
            return
        uid = int(c.data.replace("a_addel_", ""))
        users.remove_admin(uid)
        audit.log(c.from_user.id, "admin_remove", uid)
        _ack(bot, c, "➖ أُزيل")
        admins_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_points")
    def _points(c):
        if not guard(c):
            return
        _ack(bot, c)
        points_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_stats")
    def _stats(c):
        if not guard(c):
            return
        _ack(bot, c)
        stats_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_free")
    def _free(c):
        if not guard(c):
            return
        _ack(bot, c)
        free_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_coupons")
    def _cp(c):
        if not guard(c):
            return
        _ack(bot, c)
        coupons_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_cp_"))
    def _cp_gen(c):
        if not guard(c):
            return
        n = int(c.data.split("_")[-1])
        codes = coupons.generate(n, by=c.from_user.id)
        audit.log(c.from_user.id, "coupons", None, "%d كود" % n)
        _ack(bot, c, "🎟️ توليد %d" % n)
        from app.ui import card, code
        send(bot, c.message.chat.id, card("🎟️ أكواد جديدة", [
            code(x) for x in codes
        ] + ["", "💎 كل كود: %d نقطة" % db.get_int("coupon_points", 30)]))
        coupons_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_backup")
    def _bk(c):
        if not guard(c):
            return
        _ack(bot, c)
        backup_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_backup_now")
    def _bk_now(c):
        if not guard(c):
            return
        _ack(bot, c, "💾 جاري النسخ")
        path = backup.create("manual")
        audit.log(c.from_user.id, "backup", None, path)
        backup.send_to_telegram(bot, c.from_user.id, path)
        backup_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_audit")
    def _au(c):
        if not guard(c):
            return
        _ack(bot, c)
        audit_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_report")
    def _rp(c):
        if not guard(c):
            return
        _ack(bot, c)
        send(bot, c.message.chat.id, report.build_text(), None, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_cust")
    def _cust(c):
        if not guard(c):
            return
        _ack(bot, c)
        customers_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_cust_p_"))
    def _cust_page(c):
        if not guard(c):
            return
        _ack(bot, c)
        customers_screen(bot, c.message.chat.id, c.message.message_id,
                         int(c.data.split("_")[-1]))

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("a_cust_")
                                 and (c.data or "").split("_")[-1].isdigit()
                                 and not (c.data or "").startswith(("a_cust_p_", "a_cust_phone_")))
    def _cust_one(c):
        if not guard(c):
            return
        _ack(bot, c)
        customer_screen(bot, c.message.chat.id, int(c.data.split("_")[-1]),
                        c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_cust_phone_"))
    def _cust_phone(c):
        if not guard(c):
            return
        uid = int(c.data.split("_")[-1])
        phone = users.phone_of(uid)
        audit.log(c.from_user.id, "phone_view", uid)
        if not phone:
            return _ack(bot, c, "ما عند هذا المستخدم رقم", True)
        from app.ui import card, code
        send(bot, c.message.chat.id, card("📱 رقم العميل", [
            "🆔 " + code(uid),
            "📱 " + code(phone),
        ]), {"inline_keyboard": [[btn("📇 ملف العميل", "a_cust_%d" % uid)]]})

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_tok_"))
    def _tok(c):
        if not guard(c):
            return
        rid = int(c.data.split("_")[-1])
        b = bots.get(rid)
        if not b:
            return _ack(bot, c, "غير موجود", True)
        try:
            token = bots.decrypt(b["token_enc"])
        except Exception:
            return _ack(bot, c, "تعذّر فك التشفير", True)
        audit.log(c.from_user.id, "token_view", rid)
        _ack(bot, c, "🔑")
        from app.ui import card, code
        send(bot, c.message.chat.id, card("🔑 توكن @%s" % (b["username"] or "bot"), [
            code(token),
            "",
            "⚠️ <i>من يملكه يملك البوت — لا تشاركه.</i>",
        ]), {"inline_keyboard": [[btn("📇 ملف العميل", "a_cust_%d" % b["owner_id"])]]})

    @bot.callback_query_handler(func=lambda c: c.data == "a_cust_export")
    def _cust_export(c):
        if not guard(c):
            return
        _ack(bot, c, "📤 جاري التصدير")
        from app.ui import card, num
        send(bot, c.message.chat.id, card("📤 تصدير العملاء", [
            "اختر نوع الملف:",
            "• <b>الأرقام فقط</b> — آمن للمشاركة",
            "• <b>مع التوكنات</b> — حسّاس، احتفظ به بعيداً",
        ]), {"inline_keyboard": [
            [btn("📱 أرقام فقط", "a_exp_plain")],
            [btn("🔑 أرقام + توكنات", "a_exp_tokens")],
            [btn("↩ رجوع", "a_cust")],
        ]})

    @bot.callback_query_handler(func=lambda c: c.data in ("a_exp_plain", "a_exp_tokens"))
    def _exp(c):
        if not guard(c):
            return
        with_tokens = c.data == "a_exp_tokens"
        path = export_csv(include_tokens=with_tokens)
        audit.log(c.from_user.id, "export_customers", None,
                  "with_tokens" if with_tokens else "phones_only")
        _ack(bot, c, "📤 أُرسل")
        try:
            with open(path, "rb") as f:
                bot.send_document(c.from_user.id, f,
                                  caption="📇 ملف العملاء" +
                                  (" (مع التوكنات)" if with_tokens else " (أرقام فقط)"))
        except Exception as e:
            send(bot, c.message.chat.id, "⚠️ تعذّر الإرسال: " + str(e)[:80])

    @bot.callback_query_handler(func=lambda c: c.data == "a_scripts")
    def _sc(c):
        if not users.is_owner(c.from_user.id):
            return _ack(bot, c, "🔒 السكربتات للمالك فقط", True)
        _ack(bot, c)
        scripts_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_sc_start_"))
    def _sc_start(c):
        if not users.is_owner(c.from_user.id):
            return _ack(bot, c, "🔒 للمالك فقط", True)
        sid = int(c.data.split("_")[-1])
        try:
            okk, msg = scripts.start(sid)
        except scripts.ScriptError as e:
            return _ack(bot, c, "❌ " + str(e)[:80], True)
        audit.log(c.from_user.id, "script_start", sid)
        _ack(bot, c, "▶️ " + msg)
        scripts_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_sc_stop_"))
    def _sc_stop(c):
        if not users.is_owner(c.from_user.id):
            return _ack(bot, c, "🔒 للمالك فقط", True)
        sid = int(c.data.split("_")[-1])
        scripts.stop(sid)
        audit.log(c.from_user.id, "script_stop", sid)
        _ack(bot, c, "⏸ أُوقف")
        scripts_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_sc_out_"))
    def _sc_out(c):
        if not users.is_owner(c.from_user.id):
            return _ack(bot, c, "🔒 للمالك فقط", True)
        sid = int(c.data.split("_")[-1])
        row = scripts.get(sid)
        if not row:
            return _ack(bot, c, "غير موجود", True)
        _ack(bot, c)
        text = scripts.output(sid, 60) or "(ماكو إخراج بعد)"
        from app.ui import card, esc, code
        send(bot, c.message.chat.id, card("📜 إخراج %s" % esc(row["name"] or "script"), [
            code(text[-3000:]),
        ]), {"inline_keyboard": [[btn("🔄 تحديث", "a_sc_out_%d" % sid)],
                                 [btn("↩ رجوع", "a_scripts")]]},
            c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_sc_del_"))
    def _sc_del(c):
        if not users.is_owner(c.from_user.id):
            return _ack(bot, c, "🔒 للمالك فقط", True)
        sid = int(c.data.split("_")[-1])
        scripts.delete(sid)
        audit.log(c.from_user.id, "script_delete", sid)
        _ack(bot, c, "🗑 حُذف")
        scripts_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_runtime")
    def _rt(c):
        if not guard(c):
            return
        _ack(bot, c)
        runtime_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_runtime_fix")
    def _rt_fix(c):
        if not guard(c):
            return
        from app import provision
        okk, msg = provision.auto_setup()
        audit.log(c.from_user.id, "runtime_setup", None, msg)
        _ack(bot, c, "✅ جاهز" if okk else "❌ " + msg[:60], not okk)
        runtime_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_deploy")
    def _dep(c):
        if not users.is_owner(c.from_user.id):
            return _ack(bot, c, "🔒 التحديث للمالك فقط", True)
        _ack(bot, c)
        deploy_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_deploy_rollback")
    def _dep_rb(c):
        if not users.is_owner(c.from_user.id):
            return _ack(bot, c, "🔒 للمالك فقط", True)
        rows = db.all_rows("SELECT * FROM deploys WHERE status='done' ORDER BY id DESC LIMIT 1")
        if not rows:
            return _ack(bot, c, "لا يوجد نشر ناجح للتراجع عنه", True)
        try:
            moved = deploy.rollback(rows[0]["id"], by=c.from_user.id)
        except deploy.DeployError as e:
            return _ack(bot, c, str(e)[:100], True)
        _ack(bot, c, "⏪ رجع %d بوت" % moved)
        deploy_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_bc")
    def _bc(c):
        if not guard(c):
            return
        _ack(bot, c)
        broadcast_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_bc_new")
    def _bc_new(c):
        if not guard(c):
            return
        _ack(bot, c)
        _ask_broadcast(bot, c.message.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_bc_cancel")
    def _bc_cancel(c):
        if not guard(c):
            return
        _BC_DRAFT.pop(c.from_user.id, None)
        _ack(bot, c, "❌ أُلغي")
        home(bot, c.message.chat.id, c.from_user.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data in ("a_bc_go_all", "a_bc_go_users",
                                                          "a_bc_go_groups"))
    def _bc_go(c):
        if not guard(c):
            return
        if c.data == "a_bc_go_all":
            _do_broadcast(bot, c, True, True)
        elif c.data == "a_bc_go_users":
            _do_broadcast(bot, c, True, False)
        else:
            _do_broadcast(bot, c, False, True)

    @bot.callback_query_handler(func=lambda c: c.data == "a_free_msg")
    def _free_msg(c):
        if not guard(c):
            return
        _ack(bot, c)
        _ask_free_message(bot, c.message.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data == "a_free_go")
    def _free_go(c):
        if not guard(c):
            return
        _do_free(bot, c)

    @bot.callback_query_handler(func=lambda c: c.data == "a_free_toggle")
    def _free_toggle(c):
        if not guard(c):
            return
        new = 0 if db.get_int("free_mode", 0) == 1 else 1
        db.set_setting("free_mode", new)
        _ack(bot, c, "✅ مفعّل" if new else "⬜ معطّل")
        free_screen(bot, c.message.chat.id, c.message.message_id)

    # ── إجراءات على المستخدمين (من الإشعار) ──
    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_allow_"))
    def _allow(c):
        if not guard(c):
            return
        uid = int(c.data.replace("a_allow_", ""))
        users.add_exception(uid, by=c.from_user.id, note="from_notification")
        _ack(bot, c, "✅ تم السماح")
        try:
            send(bot, uid, card("✅ تم السماح لك", ["أرسل /start للبدء."]))
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_deny_"))
    def _deny(c):
        if not guard(c):
            return
        _ack(bot, c, "⛔ رُفض (لم يُحظر)")
        uid = int(c.data.replace("a_deny_", ""))
        try:
            send(bot, uid, card("⛔ لم يُسمح لك بالدخول", ["تواصل مع الدعم إذا ترى أن هناك خطأ."]))
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_block_"))
    def _block(c):
        if not guard(c):
            return
        uid = int(c.data.replace("a_block_", ""))
        users.block(uid)
        _ack(bot, c, "🚫 محظور")

    # ── الرد على تذاكر الدعم ──
    @bot.callback_query_handler(func=lambda c: c.data.startswith("a_reply_"))
    def _reply(c):
        if not guard(c):
            return
        _ack(bot, c)
        tid = int(c.data.replace("a_reply_", ""))
        msg = bot.send_message(c.from_user.id, "✍️ اكتب الرد على التذكرة #%d" % tid)
        bot.register_next_step_handler(msg, _send_reply, tid)

    def _send_reply(m, tid):
        text = (m.text or "").strip()
        if not text:
            return
        row = db.one("SELECT * FROM tickets WHERE id=?", (tid,))
        if not row:
            return send(bot, m.chat.id, error("التذكرة غير موجودة."))
        db.run("INSERT INTO ticket_msgs (ticket_id, admin_id, text) VALUES (?,?,?)",
               (tid, m.from_user.id, text))
        db.run("UPDATE tickets SET updated_at=datetime('now') WHERE id=?", (tid,))
        send(bot, row["user_id"], card("📞 رد الدعم", [esc(text[:800])]))
        send(bot, m.chat.id, ok("أُرسل الرد."))

    logger.info("admin registered")
