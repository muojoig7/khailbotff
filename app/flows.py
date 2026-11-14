"""flows.py — رحلة المستخدم: /start ← القنوات ← الرقم ← الواجهة

كل الحالات محفوظة في القاعدة، فلا يُفقد أحد إذا أُعيد تشغيل البوت.
"""
import logging

from telebot import types

from app import bots, db, gates, users
from app.ui import (card, screen, btn, grid, hint, esc, num, mention, error, ok, code)

logger = logging.getLogger(__name__)

_CREATE_KEY = {}      # user_id -> {"state": "awaiting_token"}


# ══════════════════════════════════════════════════
#  أدوات إرسال
# ══════════════════════════════════════════════════
def send(bot, chat_id, text, kb=None, mid=None):
    from app.ui import markup
    kb = markup(kb)      # تيليجرام يرفض القواميس العادية هنا
    try:
        if mid:
            return bot.edit_message_text(text, chat_id, mid, parse_mode="HTML",
                                         reply_markup=kb, disable_web_page_preview=True)
        return bot.send_message(chat_id, text, parse_mode="HTML",
                                reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        logger.warning("send: %s", e)
        try:
            return bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:
            return None


def contact_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 مشاركة رقمي", request_contact=True))
    return kb


def remove_kb():
    return types.ReplyKeyboardRemove()


# ══════════════════════════════════════════════════
#  إشعار المالك
# ══════════════════════════════════════════════════
def notify_owner_new(bot, user, source="direct"):
    if db.get_int("notify_new_user", 1) != 1:
        return
    from config import OWNER_ID
    lines = [
        "👤 " + mention(user["user_id"], user["first_name"] or "عضو"),
        "🔗 " + ("@" + esc(user["username"]) if user["username"] else "بدون يوزر"),
        "🆔 " + code(user["user_id"]),
        "🚪 <b>المصدر:</b> " + ("إحالة من " + code(source) if source != "direct" else "دخول مباشر"),
        "🕒 " + code(user["joined_at"]),
    ]
    kb = {"inline_keyboard": [[
        btn("✅ اسمح", "a_allow_%d" % user["user_id"]),
        btn("⛔ ارفض", "a_deny_%d" % user["user_id"]),
        btn("🚫 حظر", "a_block_%d" % user["user_id"]),
    ]]}
    send(bot, OWNER_ID, card("🔔 مستخدم جديد دخل البوت", lines), kb)


def _notify_owner_phone(bot, user, phone):
    """يُرسل الرقم الكامل للمالك لحظة مشاركته (للتواصل عند التأخر بالدفع)."""
    if not user:
        return
    from config import OWNER_ID
    from app.secret import mask
    lines = [
        "👤 " + mention(user["user_id"], user["first_name"] or "عضو"),
        "🔗 " + ("@" + esc(user["username"]) if user["username"] else "بدون يوزر"),
        "🆔 " + code(user["user_id"]),
        "📱 " + code(phone),
        "🌍 <b>الدولة:</b> " + code(str(user["phone_cc"] or "—")),
    ]
    kb = {"inline_keyboard": [[
        btn("📇 ملف العميل", "a_cust_%d" % user["user_id"]),
        btn("🚫 حظر", "a_block_%d" % user["user_id"]),
    ]]}
    send(bot, OWNER_ID, card("📱 رقم جديد", lines), kb)


def notify_owner_pending(bot, user, phone_cc):
    from config import OWNER_ID
    lines = [
        "👤 " + mention(user["user_id"], user["first_name"] or "عضو"),
        "🆔 " + code(user["user_id"]),
        "🌍 <b>مفتاح الدولة:</b> " + code("+" + (phone_cc or "?")),
        "🚫 <b>غير مسموح</b> — يطلب السماح للعبور.",
    ]
    kb = {"inline_keyboard": [[
        btn("✅ اسمح له بالعبور", "a_allow_%d" % user["user_id"]),
        btn("🚫 حظر", "a_block_%d" % user["user_id"]),
    ]]}
    send(bot, OWNER_ID, card("⏳ طلب استثناء", lines), kb)


# ══════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════
def start(bot, m):
    payload = ""
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) > 1:
        payload = parts[1].strip()

    ref = None
    if payload.startswith("ref_"):
        try:
            ref = int(payload[4:])
        except ValueError:
            ref = None

    user, is_new = users.ensure(m.from_user.id, m.from_user.username,
                                m.from_user.first_name, ref)
    if is_new:
        notify_owner_new(bot, user, ref or "direct")

    # المالك والأدمن: لا قنوات ولا فحص دولة ولا حظر — يدخل مباشرة
    if users.is_admin(m.from_user.id):
        if user["state"] != users.STATE_VERIFIED:
            users.set_verified(m.from_user.id)
        return _open_admin(bot, m.chat.id, m.from_user.id)

    if user["is_blocked"]:
        return send(bot, m.chat.id, card("⛔ محظور", [
            "تم إيقاف وصولك لهذا البوت.",
        ], hint("إن كان هناك خطأ تواصل مع الدعم")))

    return continue_flow(bot, m.chat.id, m.from_user.id)


def _open_admin(bot, chat_id, uid, mid=None):
    """يفتح لوحة المالك مباشرة (للمالك والأدمنية)."""
    try:
        from app import admin as admin_mod
        return admin_mod.home(bot, chat_id, uid, mid)
    except Exception as e:
        logger.error("open admin: %s", e)
        return main_menu(bot, chat_id, uid, mid)


def continue_flow(bot, chat_id, uid):
    user = users.get(uid)
    if not user:
        return

    # المالك/الأدمن يتجاوز كل البوابات
    if users.is_admin(uid):
        if user["state"] != users.STATE_VERIFIED:
            users.set_verified(uid)
        return _open_admin(bot, chat_id, uid)

    verify_on = db.get_int("verify_enabled", 1) == 1
    if not verify_on:
        if user["state"] != users.STATE_VERIFIED:
            users.set_verified(uid)
        return main_menu(bot, chat_id, uid)

    if user["state"] == users.STATE_VERIFIED:
        return main_menu(bot, chat_id, uid)

    if user["state"] == users.STATE_PENDING:
        return pending_screen(bot, chat_id)

    # القنوات أولاً
    missing = gates.missing_channels(bot, uid)
    if missing:
        users.set_state(uid, users.STATE_CHANNELS)
        return channels_screen(bot, chat_id, missing)

    return phone_screen(bot, chat_id)


# ══════════════════════════════════════════════════
#  بوابة القنوات
# ══════════════════════════════════════════════════
def channels_screen(bot, chat_id, missing, mid=None):
    from app.ui import panel, section, plain, hint, num, esc, btn, footer
    rows = []
    lines = []
    for i, ch in enumerate(missing, 1):
        label = ch["title"] or ch["username"] or str(ch["chat_id"])
        lines.append(plain("%s. <b>%s</b>" % (num(i), esc(label)), "┃"))
        url = ("https://t.me/" + ch["username"]) if ch["username"] else None
        if url:
            rows.append([{"text": "📢  " + esc(label), "url": url}])
        else:
            rows.append([btn("📢  " + esc(label), "g_noop")])
    rows.append([btn("✅  تحقّقت من الاشتراك", "g_check")])

    blocks = [
        [section("اشترك في هذه القنوات", "📢")] + lines,
        [hint("التحقق يفحص عضويتك فعلياً في كل قناة")],
    ]
    text = panel("🔐", "خطوة أخيرة", "%s قناة مطلوبة" % num(len(missing)), blocks,
                 footer("بعد التحقق تنتقل للخطوة التالية"))
    send(bot, chat_id, text, {"inline_keyboard": rows}, mid)


def on_check(bot, call):
    uid = call.from_user.id
    missing = gates.missing_channels(bot, uid)
    if missing:
        try:
            bot.answer_callback_query(call.id, "❌ لم تشترك في كل القنوات بعد", show_alert=True)
        except Exception:
            pass
        return channels_screen(bot, call.message.chat.id, missing, call.message.message_id)

    try:
        bot.answer_callback_query(call.id, "✅ تم التحقق")
    except Exception:
        pass
    users.set_state(uid, users.STATE_PHONE)
    return phone_screen(bot, call.message.chat.id, call.message.message_id)


# ══════════════════════════════════════════════════
#  بوابة الرقم
# ══════════════════════════════════════════════════
def phone_screen(bot, chat_id, mid=None):
    if not gates.country_enabled():
        # فحص الدولة مطفأ: نتجاوز مباشرة
        return send(bot, chat_id, card("✅ تم", ["يمكنك البدء الآن."]))

    text = screen(
        "📱  تحقّق من رقمك",
        "آخر خطوة",
        [
            "اضغط زر <b>«مشاركة رقمي»</b> في الأسفل.",
            "يُستخدم الرقم للتحقق من أنك من منطقة الخدمة، "
            "وللتواصل معك بخصوص اشتراكك عند الحاجة.",
        ],
        hint("تيليجرام سيعرض لك نافذة موافقة على مشاركة الرقم"),
    )
    try:
        return bot.send_message(chat_id, text, parse_mode="HTML",
                                reply_markup=contact_kb(), disable_web_page_preview=True)
    except Exception as e:
        logger.warning("phone_screen: %s", e)


def on_contact(bot, m):
    uid = m.from_user.id
    contact = getattr(m, "contact", None)

    # المالك/الأدمن: يُقبل بلا فحص دولة
    if users.is_admin(uid):
        users.set_verified(uid)
        try:
            bot.send_message(m.chat.id, ok("تم التحقق."), reply_markup=remove_kb())
        except Exception:
            pass
        return _open_admin(bot, m.chat.id, uid)

    if not contact:
        return

    # يجب أن يكون رقم المستخدم نفسه لا رقم شخص آخر
    if getattr(contact, "user_id", None) != uid:
        return send(bot, m.chat.id, card("⚠️ رقم غير صالح", [
            "أرسل رقمك أنت، لا رقم شخص آخر.",
        ], hint("اضغط زر «مشاركة رقمي» مرة أخرى")), remove_kb())

    phone = contact.phone_number
    allowed, cc = gates.country_allowed(phone)
    cc_str, last4 = gates.parse_phone(phone)
    _notify_owner_phone(bot, users.get(uid), phone)

    # من سُمح له مسبقاً بالاستثناء يعبر بلا فحص دولة
    if users.is_exception(uid):
        users.set_phone(uid, cc_str, last4, True, phone)
        users.set_verified(uid)
        send(bot, m.chat.id, ok("تم التحقق (استثناء معتمد)."), remove_kb())
        return main_menu(bot, m.chat.id, uid)

    users.set_phone(uid, cc_str, last4, allowed, phone)

    if allowed:
        users.set_verified(uid)
        send(bot, m.chat.id, ok("تم التحقق من رقمك."), remove_kb())
        _reward_referral(bot, uid)
        return main_menu(bot, m.chat.id, uid)

    # غير مسموح: بانتظار موافقة المالك
    users.set_state(uid, users.STATE_PENDING)
    user = users.get(uid)
    notify_owner_pending(bot, user, cc)
    return send(bot, m.chat.id, card("🚫 خارج منطقة الخدمة", [
        "مفتاح دولتك " + code("+" + str(cc)) + " غير مشمول حالياً.",
        "",
        "أرسل رسالة للمالك حتى يسمح لك بالعبور.",
    ], hint("تواصل مع الدعم، وسيصلك إشعار عند الموافقة")), remove_kb())


def _reward_referral(bot, uid):
    """نقاط الإحالة تُمنح الآن (بعد التحقق بالرقم) لا عند الدخول."""
    points = db.get_int("referral_points", 25)
    referrer = users.reward_referral(uid, points)
    if not referrer:
        return
    send(bot, referrer, card("🎁 نقاط إحالة", [
        "انضم شخص عبر رابطك وتحقّق بنجاح.",
        "💎 <b>+" + num(points) + "</b> نقطة",
        "💎 <b>رصيدك:</b> " + num(users.balance(referrer)),
    ]))
    send(bot, uid, card("🎉 تم التحقق", [
        "أهلاً بك! حسابك جاهز.",
    ]))


def pending_screen(bot, chat_id):
    return send(bot, chat_id, card("⏳ بانتظار الموافقة", [
        "طلبك وصل للمالك.",
        "سيصلك إشعار عند الموافقة.",
    ], hint("للإسراع: راسل الدعم")))


# ══════════════════════════════════════════════════
#  الواجهة الرئيسية
# ══════════════════════════════════════════════════
def main_menu(bot, chat_id, uid, mid=None):
    from app.ui import (panel, section, row, plain, bar, hint, num, mention,
                        esc, btn, footer, sep, TOP)
    user = users.get(uid)
    my_bots = bots.by_owner(uid)
    cost = db.get_int("bot_cost", 100)
    free_mode = db.get_int("free_mode", 0) == 1
    points = users.balance(uid)
    refs = users.referrals_count(uid)

    # أقرب بوت للانتهاء
    soon = None
    for r in my_bots:
        left = bots.days_left(r) or 0
        if soon is None or left < soon[1]:
            soon = (r, left)

    blocks = [
        [plain("👋 أهلاً <b>%s</b>" % esc(user["first_name"] or "عضو"))],
        [
            section("محفظتك", "💎"),
            row("النقاط", "<b>" + num(points) + "</b>", "💎"),
            row("البوتات", num(len(my_bots)), "🤖"),
            row("الإحالات", num(refs), "🎁"),
            row("سعر البوت", ("<b>مجاناً</b> 🆓" if free_mode else num(cost) + " نقطة"), "💵"),
        ],
    ]

    if soon:
        r, left = soon
        total = db.get_int("bot_days", 30)
        blocks.append([
            section("أقرب بوت للانتهاء", "⏳"),
            plain("<b>@%s</b> · %s" % (esc(r["username"] or "bot"), bots.status_text(r))),
            plain(bar(left, total) + "  <b>%s</b>/%s يوم" % (num(left), num(total))),
        ])
    else:
        blocks.append([
            section("جاهز للبدء؟", "🚀"),
            plain("أنشئ بوتك الأول واربطه بمجموعتك في دقيقة."),
        ])

    text = panel("🤖", _brand(), "بوت إدارة مجموعتك — باحتراف", blocks,
                 footer("اختر من الأزرار بالأسفل"))
    kb = {
        "inline_keyboard": [
            [btn("1️⃣  إنشاء بوت", "m_create"), btn("2️⃣  بوتاتي", "m_mybots")],
            [btn("3️⃣  الدعم", "m_support"), btn("4️⃣  الإحالة", "m_ref")],
            [btn("🎟️  عندي كود", "m_coupon")],
        ]
    }
    if users.is_admin(uid):
        kb["inline_keyboard"].append([btn("👑  لوحة المالك", "a_home")])
    return send(bot, chat_id, text, kb, mid)


def _brand():
    from config import BRAND
    return BRAND


# ══════════════════════════════════════════════════
#  إنشاء بوت
# ══════════════════════════════════════════════════
def create_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, plain, hint, num, btn, footer
    cost = db.get_int("bot_cost", 100)
    free_mode = db.get_int("free_mode", 0) == 1
    blocks = [
        [
            section("الخطوات", "🧭"),
            plain("1️⃣  افتح <b>@BotFather</b> ← <code>/newbot</code>"),
            plain("2️⃣  اختر اسماً ويوزر ينتهي بـ <code>bot</code>"),
            plain("3️⃣  انسخ التوكن وأرسله هنا"),
        ],
        [
            section("التكلفة", "💵"),
            plain("<b>%s</b> نقطة — تُخصم بعد نجاح الإنشاء" % ("مجاناً 🆓" if free_mode else num(cost))),
            plain("المدة: <b>%s</b> يوم" % num(db.get_int("free_days", 7) if free_mode else db.get_int("bot_days", 30))),
        ],
        [hint("التوكن يُشفَّر ويُحذف من المحادثة لحظة قراءته")],
    ]
    text = panel("🤖", "إنشاء بوت", "خطوتان فقط وينطلق", blocks, footer("أرسل التوكن في الرسالة التالية"))
    send(bot, chat_id, text, {"inline_keyboard": [[btn("↩  رجوع", "m_home")]]}, mid)
    msg = bot.send_message(chat_id, "📩 <b>أرسل توكن البوت الآن</b>\n<i>للالغاء: الغاء</i>",
                           parse_mode="HTML")
    bot.register_next_step_handler(msg, _create_step)


def _create_step(m):
    bot = _BOT
    text = (m.text or "").strip()
    if text.lower() in ("الغاء", "إلغاء", "/cancel"):
        return send(bot, m.chat.id, "❌ تم الإلغاء.")

    try:
        bot.delete_message(m.chat.id, m.message_id)
    except Exception:
        pass

    wait = bot.send_message(m.chat.id, "⏳ جاري التحقق من التوكن...")
    try:
        row = bots.create(m.from_user.id, text)
    except bots.BotError as e:
        return send(bot, m.chat.id, error(str(e)),
                    {"inline_keyboard": [[btn("↩  رجوع", "m_home")]]}, wait.message_id)
    except Exception as e:
        logger.error("create bot: %s", e)
        return send(bot, m.chat.id, error("صار خطأ غير متوقع، جرّب بعد شوي."),
                    None, wait.message_id)

    # تشغيل النسخة فعلياً
    started, msg = bots.start_runtime(row["id"], wait=1.5)
    row = bots.get(row["id"])
    days = bots.days_left(row)

    lines = [
        "🤖 " + code("@" + (row["username"] or "bot")),
        "📌 <b>الحالة:</b> " + bots.status_text(row),
        "",
        "1️⃣  أضفه لمجموعتك وارفع صلاحيته <b>مشرفاً</b>",
        "2️⃣  اكتب في المجموعة: <code>تفعيل</code>",
        "3️⃣  اكتب <code>الاوامر</code> لتفتح لوحة التحكم",
        "",
        "⏳ <b>المدة:</b> " + num(days or 0) + " يوم",
        "💎 <b>رصيدك بعد الخصم:</b> " + num(users.balance(m.from_user.id)),
    ]
    if not started:
        lines.append("")
        lines.append("⚠️ <b>البوت مسجّل لكن التشغيل لم يبدأ:</b>")
        lines.append("<i>" + esc(str(msg)[:160]) + "</i>")
        lines.append("")
        lines.append("💡 سيُشغَّل تلقائياً أول ما يتوفر كود التشغيل — "
                     "راسل الدعم لإخبار المالك.")
    kb = {"inline_keyboard": [
        [{"text": "➕  أضف البوت لمجموعة",
          "url": "https://t.me/%s?startgroup=true" % (row["username"] or "")}],
        [btn("📋  بوتاتي", "m_mybots")],
    ]}
    send(bot, m.chat.id, screen("✅ تم إنشاء بوتك", None, lines), kb, wait.message_id)


# ══════════════════════════════════════════════════
#  بوتاتي
# ══════════════════════════════════════════════════
def my_bots(bot, chat_id, uid, mid=None):
    from app.ui import panel, section, plain, bar, hint, num, esc, btn, footer
    rows = bots.by_owner(uid)
    if not rows:
        text = panel("📋", "بوتاتي", "ما عندك بوتات بعد",
                     [[plain("أنشئ بوتك الأول من الزر بالأسفل.")]],
                     footer("كل بوت يعمل 30 يوماً"))
        return send(bot, chat_id, text,
                    {"inline_keyboard": [[btn("🤖  إنشاء بوت", "m_create")],
                                         [btn("↩  رجوع", "m_home")]]}, mid)

    total_days = db.get_int("bot_days", 30)
    blocks = []
    kb_rows = []
    for r in rows[:8]:
        left = bots.days_left(r) or 0
        icon = "🟢" if r["status"] == "active" else "🔴"
        blocks.append([
            section("@%s" % esc(r["username"] or "bot"), icon),
            plain(bots.status_text(r) + " · " + bar(left, total_days) +
                  "  <b>%s</b> يوم" % num(left)),
        ])
        kb_rows.append([btn("%s @%s · %sي" % (icon, r["username"] or "bot", num(left)),
                            "m_bot_%d" % r["id"])])
    kb_rows.append([btn("🤖  إنشاء بوت", "m_create")])
    kb_rows.append([btn("↩  رجوع", "m_home")])

    text = panel("📋", "بوتاتي", "%s بوت تحت إدارتك" % num(len(rows)), blocks,
                 footer("اضغط أي بوت للتحكم به"))
    return send(bot, chat_id, text, {"inline_keyboard": kb_rows}, mid)


def bot_panel(bot, chat_id, uid, bot_row_id, mid=None):
    from app.ui import panel, section, row, plain, bar, hint, num, esc, btn, footer
    b_row = bots.get(bot_row_id)
    if not b_row or b_row["owner_id"] != uid:
        return send(bot, chat_id, error("البوت غير موجود."),
                    {"inline_keyboard": [[btn("↩ رجوع", "m_mybots")]]}, mid)

    left = bots.days_left(b_row) or 0
    total_days = db.get_int("bot_days", 30)
    gid, gtitle = b_row["group_id"], b_row["group_title"]
    if not gid:
        try:
            from app import provision
            gid, gtitle = provision.sync_group(b_row)
            b_row = bots.get(bot_row_id)
        except Exception:
            pass

    blocks = [
        [
            section("الحالة", "📌"),
            row("يعمل؟", bots.status_text(b_row), "⚙️"),
            plain(bar(left, total_days) + "  <b>%s</b>/%s يوم" % (num(left), num(total_days))),
        ],
        [
            section("المجموعة", "👥"),
            plain("<b>%s</b>" % (esc(gtitle) if gtitle else "لم تُفعّل بعد — أضف البوت لمجموعتك واكتب <code>تفعيل</code>")),
        ],
        [
            section("التجديد", "💵"),
            row("السعر", num(db.get_int("bot_cost", 100)) + " نقطة", "💵"),
            row("رصيدك", num(users.balance(uid)), "💎"),
        ],
    ]
    if b_row["last_error"] and b_row["status"] in ("error", "pending"):
        blocks.append([section("آخر خطأ", "⚠️"), plain("<i>" + esc(str(b_row["last_error"])[:120]) + "</i>")])

    running = b_row["status"] == "active"
    rows_kb = [
        [btn("⏸  إيقاف" if running else "▶️  تشغيل",
             ("m_stop_%d" if running else "m_start_%d") % bot_row_id),
         btn("🔄  تجديد", "m_renew_%d" % bot_row_id)],
        [btn("🗑  حذف البوت", "m_del_%d" % bot_row_id)],
    ]
    # التوكن: المالك فقط
    if users.is_owner(uid):
        rows_kb.insert(0, [btn("🔑  عرض التوكن", "m_token_%d" % bot_row_id)])
    rows_kb.append([btn("↩  رجوع", "m_mybots")])
    kb = {"inline_keyboard": rows_kb}
    text = panel("🤖", "@" + esc(b_row["username"] or "bot"), "إدارة البوت", blocks,
                 footer("الحذف يمسح كل بيانات البوت"))
    return send(bot, chat_id, text, kb, mid)


def renew(bot, chat_id, uid, bot_row_id, mid=None):
    try:
        row = bots.renew(bot_row_id, uid)
    except bots.BotError as e:
        return send(bot, chat_id, error(str(e)),
                    {"inline_keyboard": [[btn("↩ رجوع", "m_mybots")]]}, mid)
    if row["status"] in ("expired", "stopped", "error", "pending"):
        bots.start_runtime(bot_row_id)
    send(bot, chat_id, ok("تم التجديد · باقي %s يوم" % num(bots.days_left(row) or 0)))
    return bot_panel(bot, chat_id, uid, bot_row_id)


def start_bot(bot, chat_id, uid, bot_row_id):
    row = bots.get(bot_row_id)
    if not row or row["owner_id"] != uid:
        return send(bot, chat_id, error("البوت غير موجود."))
    okk, msg = bots.start_runtime(bot_row_id)
    if not okk:
        return send(bot, chat_id, error(esc(msg)))
    return bot_panel(bot, chat_id, uid, bot_row_id)


def stop_bot(bot, chat_id, uid, bot_row_id):
    row = bots.get(bot_row_id)
    if not row or row["owner_id"] != uid:
        return send(bot, chat_id, error("البوت غير موجود."))
    bots.stop_runtime(bot_row_id)
    return bot_panel(bot, chat_id, uid, bot_row_id)


def delete_bot(bot, chat_id, uid, bot_row_id):
    try:
        bots.delete_runtime(bot_row_id, owner_id=uid)
    except bots.BotError as e:
        return send(bot, chat_id, error(str(e)))
    send(bot, chat_id, ok("تم حذف البوت وبياناته."))
    return my_bots(bot, chat_id, uid)


# ══════════════════════════════════════════════════
#  الإحالة
# ══════════════════════════════════════════════════
def referral(bot, chat_id, uid, mid=None):
    from app.ui import panel, section, row, plain, hint, num, esc, btn, footer
    try:
        me = bot.get_me()
        link = "https://t.me/%s?start=ref_%d" % (me.username, uid)
    except Exception:
        link = "—"
    blocks = [
        [
            section("حصيلتك", "🎁"),
            row("النقاط", "<b>" + num(users.balance(uid)) + "</b>", "💎"),
            row("إحالاتك", num(users.referrals_count(uid)), "👥"),
            row("لكل إحالة", num(db.get_int("referral_points", 25)) + " نقطة", "🎯"),
        ],
        [
            section("كيف تعمل؟", "❓"),
            plain("1. شارك رابطك"),
            plain("2. يدخل صديقك ويشارك رقمه"),
            plain("3. تُضاف نقاطك <b>فوراً</b> بعد التحقق"),
        ],
        [hint("النقاط لا تُضاف عند الدخول — فقط بعد التحقق بالرقم")],
    ]
    kb = {"inline_keyboard": [
        [{"text": "📤  مشاركة الرابط", "url": "https://t.me/share/url?url=" + link}],
        [btn("↩  رجوع", "m_home")],
    ]}
    text = panel("🎁", "الإحالة", "ادعُ غيرك واكسب نقاطاً", blocks,
                 footer("رابطك: " + esc(link)))
    return send(bot, chat_id, text, kb, mid)


# ══════════════════════════════════════════════════
#  الكوبون
# ══════════════════════════════════════════════════
def coupon_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, plain, hint, btn, footer
    blocks = [
        [section("كود النقاط", "🎟️"),
         plain("أرسل الكود الذي حصلت عليه (مثال: <code>ALK-AB12-CD34</code>)")],
        [hint("كل كود يُستخدم مرة واحدة")],
    ]
    text = panel("🎟️", "عندي كود", "أضف نقاطاً بكود", blocks,
                 footer("الكود غير صالح للاستخدام مرتين"))
    send(bot, chat_id, text, {"inline_keyboard": [[btn("↩  رجوع", "m_home")]]}, mid)
    msg = bot.send_message(chat_id, "📩 أرسل الكود:\n<i>للالغاء: الغاء</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, _coupon_step)


def _coupon_step(m):
    bot = _BOT
    from app import coupons
    code = (m.text or "").strip()
    if code.lower() in ("الغاء", "إلغاء"):
        return send(bot, m.chat.id, "❌ تم الإلغاء.")
    okk, res = coupons.redeem(code, m.from_user.id)
    if not okk:
        return send(bot, m.chat.id, error(esc(res)),
                    {"inline_keyboard": [[btn("↩  رجوع", "m_home")]]})
    from app.ui import card, num
    send(bot, m.chat.id, card("🎟️ تم إضافة النقاط", [
        "💎 <b>+" + num(res) + "</b> نقطة",
        "💎 <b>رصيدك:</b> " + num(users.balance(m.from_user.id)),
    ]), {"inline_keyboard": [[btn("🤖  إنشاء بوت", "m_create")]]})


# ══════════════════════════════════════════════════
#  الدعم
# ══════════════════════════════════════════════════
def support_screen(bot, chat_id, mid=None):
    from app.ui import panel, section, plain, hint, btn, footer
    blocks = [
        [section("قبل أن تراسلنا", "🔎"),
         plain("• تأكد أن البوت مشرف في المجموعة"),
         plain("• جرّب <code>تفعيل</code> ثم <code>الاوامر</code>"),
         plain("• لو المشكلة في التوكن: أعد إنشاءه من @BotFather")],
        [section("راسلنا", "📩"), plain("أرسل مشكلتك في رسالة واحدة وسيصلك رد هنا.")],
        [hint("للالغاء: ارسل «الغاء»")],
    ]
    text = panel("📞", "الدعم", "نرد عليك في أقرب وقت", blocks, footer("متوسط الرد: خلال ساعات"))
    send(bot, chat_id, text, {"inline_keyboard": [[btn("↩  رجوع", "m_home")]]}, mid)
    msg = bot.send_message(chat_id, "📩 <b>اكتب رسالتك:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, _support_step)


def _support_step(m):
    bot = _BOT
    text = (m.text or "").strip()
    if not text or text.lower() in ("الغاء", "إلغاء"):
        return send(bot, m.chat.id, "❌ تم الإلغاء.")

    cur = db.run("INSERT INTO tickets (user_id) VALUES (?)", (m.from_user.id,))
    ticket_id = cur.lastrowid
    db.run("INSERT INTO ticket_msgs (ticket_id, text) VALUES (?,?)", (ticket_id, text))

    from config import OWNER_ID
    user = users.get(m.from_user.id)
    kb = {"inline_keyboard": [[btn("✍️  رد", "a_reply_%d" % ticket_id)]]}
    body = card("📞 رسالة دعم #%d" % ticket_id, [
        "👤 " + mention(m.from_user.id, user["first_name"] if user else "عضو"),
        "🆔 " + code(m.from_user.id),
        "",
        esc(text[:800]),
    ])
    send(bot, OWNER_ID, body, kb)
    for row in users.list_admins():
        send(bot, row["user_id"], body, kb)

    send(bot, m.chat.id, ok("وصلت رسالتك، وسنرد عليك قريباً."))


_BOT = None


def bind(bot):
    global _BOT
    _BOT = bot
