"""ui.py — تصميم بوت المصنع (الإصدار 2 — لوحات كبيرة)

لغة التصميم:
    ✦ ━━━━━━━━━━━━━━━━━ ✦      ← إطار علوي
    🤖  العنوان
    وصف قصير
    ✦ ━━━━━━━━━━━━━━━━━ ✦

    ┃ 📊 القسم
    ┃ 💎 النقاط · 1,250
    ┃ 🤖 البوتات · 3

    ▰▰▰▰▰▰▱▱▱▱  18/30 يوم
    💡 تلميح

كل النصوص تمر على esc() حتى لا يكسرها اسم فيه < أو &.
"""
import html

DIV = "━━━━━━━━━━━━━"
TOP = "✦ ━━━━━━━━━━━━━━━━━ ✦"
BAR = "▰"
BAR_EMPTY = "▱"


# ══════════════════════════════════════════════════
#  أساسيات
# ══════════════════════════════════════════════════
def esc(t):
    return html.escape(str(t if t is not None else ""), quote=False)


def code(t):
    return "<code>%s</code>" % esc(t)


def num(v):
    try:
        return "{:,}".format(int(v))
    except (TypeError, ValueError):
        return str(v)


def mention(uid, name=None):
    return '<a href="tg://user?id=%s">%s</a>' % (uid, esc(name or "عضو"))


def bar(value, total, width=10):
    """شريط تقدّم نصي: ▰▰▰▰▱▱▱▱"""
    try:
        total = max(1, int(total))
        value = max(0, min(int(value), total))
    except (TypeError, ValueError):
        return BAR * width
    filled = int(round(width * value / total))
    return BAR * filled + BAR_EMPTY * (width - filled)


def hero(title, subtitle=None, icon="🤖"):
    out = TOP + "\n<b>" + icon + "  " + title + "</b>"
    if subtitle:
        out += "\n<i>" + subtitle + "</i>"
    return out + "\n" + TOP


def section(title, icon="▸"):
    return "<b>" + icon + "  " + title + "</b>"


def row(key, value, icon="•"):
    return "┃ " + icon + " <b>" + key + "</b> · " + str(value)


def plain(text, icon="┃"):
    return icon + " " + text


def kv(key, value, emoji=""):
    head = (emoji + "  ") if emoji else ""
    return "%s<b>%s</b> · %s" % (head, key, value)


def hint(text):
    return "💡 <i>%s</i>" % text


def ok(text):
    return "✅ " + text


def error(text):
    return "⚠️ " + text


def sep():
    return "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"


def footer(text="مصنع بوتات الخال"):
    return "<i>✨ " + esc(text) + "</i>"


# ══════════════════════════════════════════════════
#  أزرار
# ══════════════════════════════════════════════════
def btn(text, data):
    return {"text": text, "callback_data": data}


def url_btn(text, url):
    return {"text": text, "url": url}


def grid(items, per_row=2, back_row=None):
    rows, row = [], []
    for text, data in items:
        row.append(btn(text, data))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if back_row:
        rows.append(back_row)
    return {"inline_keyboard": rows}


def back(data, text="↩  رجوع"):
    return [btn(text, data)]


def markup(kb):
    """يحوّل قاموس كيبورد إلى كائن تيليجرام.

    مهم: pyTelegramBotAPI لا يقبل قاموساً عادياً في reply_markup — يرسله
    كنص غير صالح فيرفضه تيليجرام: «can't parse reply keyboard markup JSON».
    """
    if kb is None:
        return None
    if not isinstance(kb, dict):      # أي كائن تيليجرام جاهز
        return kb
    from telebot import types
    rows = []
    for row in kb.get("inline_keyboard", []):
        rows.append([types.InlineKeyboardButton(**b) for b in row])
    return types.InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════
#  قوالب جاهزة
# ══════════════════════════════════════════════════
def card(title, lines=(), footer_text=None):
    out = "<b>%s</b>\n%s" % (title, DIV)
    body = [str(l) for l in lines if l is not None and str(l) != ""]
    if body:
        out += "\n" + "\n".join(body)
    out += "\n" + DIV
    if footer_text:
        out += "\n" + footer_text
    return out


def screen(title, subtitle=None, body=(), foot=None):
    out = "<b>%s</b>" % title
    if subtitle:
        out += "\n<i>%s</i>" % subtitle
    out += "\n" + DIV
    rows = [str(b) for b in body if b is not None and str(b) != ""]
    if rows:
        out += "\n" + "\n".join(rows)
    out += "\n" + DIV
    if foot:
        out += "\n" + foot
    return out


def panel(icon, title, subtitle, blocks, foot=None):
    """لوحة كبيرة: هيرو + أقسام متعددة."""
    out = hero(title, subtitle, icon)
    for b in blocks:
        if not b:
            continue
        if isinstance(b, (list, tuple)):
            out += "\n\n" + "\n".join(str(x) for x in b if x is not None)
        else:
            out += "\n\n" + str(b)
    out += "\n" + TOP
    if foot:
        out += "\n" + foot
    return out
