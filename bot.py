"""run.py — تشغيل بوت المصنع

    export FACTORY_TOKEN=123:abc
    export FACTORY_OWNER_ID=123456789
    python3 run.py

بوت مستقل تماماً: لا يستورد شيئاً من بوت الحماية، وله قاعدة بياناته الخاصة.
"""
import logging
import os
import sys
import threading
import time

from telebot import TeleBot

import config
from app import (admin, audit, backup, bots, broadcast, db, deploy, flows, gates,
                 provision, report, scripts, users)
from app.runner import Runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("factory")


# ══════════════════════════════════════════════════
#  بناء البوت (بلا شبكة — للاختبارات)
# ══════════════════════════════════════════════════
def build_bot(token=None):
    bot = TeleBot(token or config.BOT_TOKEN or "1:TEST", threaded=True)
    flows.bind(bot)

    # ── /start ──
    @bot.message_handler(commands=["start"])
    def _start(m):
        flows.start(bot, m)

    # ── مشاركة الرقم ──
    @bot.message_handler(content_types=["contact"])
    def _contact(m):
        flows.on_contact(bot, m)

    # ── بوابة القنوات ──
    @bot.callback_query_handler(func=lambda c: c.data == "g_check")
    def _check(c):
        flows.on_check(bot, c)

    @bot.callback_query_handler(func=lambda c: c.data == "g_noop")
    def _noop(c):
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass

    # ── الواجهة الرئيسية ──
    @bot.callback_query_handler(func=lambda c: c.data == "m_home")
    def _home(c):
        _ack(bot, c)
        user = users.get(c.from_user.id)
        if not user or user["state"] != users.STATE_VERIFIED:
            return flows.continue_flow(bot, c.message.chat.id, c.from_user.id)
        flows.main_menu(bot, c.message.chat.id, c.from_user.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "m_create")
    def _create(c):
        _ack(bot, c)
        flows.create_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "m_mybots")
    def _mybots(c):
        _ack(bot, c)
        flows.my_bots(bot, c.message.chat.id, c.from_user.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("m_bot_"))
    def _bot(c):
        _ack(bot, c)
        flows.bot_panel(bot, c.message.chat.id, c.from_user.id,
                        int(c.data.split("_")[-1]), c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("m_renew_"))
    def _renew(c):
        _ack(bot, c, "🔄 جاري التجديد")
        flows.renew(bot, c.message.chat.id, c.from_user.id,
                    int(c.data.split("_")[-1]), c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("m_start_"))
    def _bstart(c):
        _ack(bot, c, "▶️ جاري التشغيل")
        flows.start_bot(bot, c.message.chat.id, c.from_user.id,
                        int(c.data.split("_")[-1]))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("m_stop_"))
    def _bstop(c):
        _ack(bot, c, "⏸️ جاري الإيقاف")
        flows.stop_bot(bot, c.message.chat.id, c.from_user.id,
                       int(c.data.split("_")[-1]))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("m_del_"))
    def _bdel(c):
        rid = int(c.data.split("_")[-1])
        row = bots.get(rid)
        if not row or row["owner_id"] != c.from_user.id:
            return _ack(bot, c, "غير موجود", True)
        _ack(bot, c, "🗑️ جاري الحذف")
        flows.delete_bot(bot, c.message.chat.id, c.from_user.id, rid)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("m_token_"))
    def _token(c):
        if not users.is_owner(c.from_user.id):
            return _ack(bot, c, "🔒 التوكن للمالك فقط", True)
        rid = int(c.data.split("_")[-1])
        row = bots.get(rid)
        if not row:
            return _ack(bot, c, "غير موجود", True)
        try:
            token = bots.decrypt(row["token_enc"])
        except Exception as e:
            return _ack(bot, c, "تعذّر فك التشفير", True)
        audit.log(c.from_user.id, "token_view", rid)
        _ack(bot, c, "🔑")
        from app.ui import card, code, esc
        bot.send_message(c.message.chat.id, card("🔑 توكن البوت", [
            "🤖 " + code("@" + (row["username"] or "bot")),
            "",
            code(token),
            "",
            "⚠️ <i>من يملك هذا التوكن يملك البوت كاملاً — لا تشاركه.</i>",
        ]), parse_mode="HTML",
            reply_markup={"inline_keyboard": [[
                {"text": "🗑 حذف الرسالة", "callback_data": "m_delmsg"}]]})

    @bot.callback_query_handler(func=lambda c: c.data == "m_delmsg")
    def _delmsg(c):
        try:
            bot.delete_message(c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        _ack(bot, c, "🗑")

    @bot.callback_query_handler(func=lambda c: c.data == "m_coupon")
    def _coupon(c):
        _ack(bot, c)
        flows.coupon_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "m_support")
    def _support(c):
        _ack(bot, c)
        flows.support_screen(bot, c.message.chat.id, c.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "m_ref")
    def _ref(c):
        _ack(bot, c)
        flows.referral(bot, c.message.chat.id, c.from_user.id, c.message.message_id)

    # ── لوحة المالك ──
    admin.register(bot)

    # ── استقبال تحديث ZIP (المالك فقط، في الخاص) ──
    @bot.message_handler(content_types=["document"],
                         func=lambda m: m.chat.type == "private")
    def _doc(m):
        if not users.is_owner(m.from_user.id):
            return
        doc = m.document
        name = (doc.file_name or "").lower()
        if not name.endswith((".zip", ".py")):
            return bot.reply_to(m, "⚠️ أرسل ملف <code>.py</code> أو <code>.zip</code>.",
                                parse_mode="HTML")
        if (doc.file_size or 0) > deploy.MAX_SIZE:
            return bot.reply_to(m, "⚠️ الملف كبير (الحد %d ميغا)." % (deploy.MAX_SIZE // 1048576))

        os.makedirs("data/uploads", exist_ok=True)
        ext = ".py" if name.endswith(".py") else ".zip"
        path = os.path.join("data/uploads", "upload-%d%s" % (int(time.time()), ext))
        try:
            info = bot.get_file(doc.file_id)
            data = bot.download_file(info.file_path)
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:
            return bot.reply_to(m, "⚠️ فشل تنزيل الملف: %s" % str(e)[:80])

        # ── ملف .py = سكربت يشغّله المالك ──
        if name.endswith(".py"):
            try:
                row = scripts.create_from_file(path, name=doc.file_name, owner_id=m.from_user.id)
            except scripts.ScriptError as e:
                return bot.reply_to(m, "❌ " + str(e)[:150])
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            audit.log(m.from_user.id, "script_add", row["id"], row["name"])
            bot.reply_to(m, "🐍 <b>وصل السكربت:</b> <code>%s</code>\n\n"
                            "تحكم به من لوحة المالك ← 🐍 السكربتات."
                       % row["name"], parse_mode="HTML")
            return

        # ── أرشيف فيه manifest.json = تحديث كود (نشر) ──
        has_manifest = False
        try:
            import zipfile as _zip
            with _zip.ZipFile(path) as z:
                has_manifest = "manifest.json" in z.namelist()
        except Exception:
            pass

        # ── إن كان كود التشغيل ناقصاً: هذا الملف هو كود التشغيل، نجهّزه ──
        if not provision.runtime_ready():
            okk, msg = provision.install_runtime_from_zip(path)
            try:
                os.remove(path)
            except OSError:
                pass
            if okk:
                bot.reply_to(m, "✅ <b>تم تجهيز كود التشغيل</b>\n\n"
                                "سيبدأ المشرف تشغيل البوتات خلال 15 ثانية — لا تحتاج "
                                "إعادة إنشاء أي بوت.", parse_mode="HTML")
                # محاولة تشغيل فورية بدل انتظار الدورة
                threading.Thread(target=_start_pending, daemon=True).start()
            else:
                bot.reply_to(m, "❌ فشل التجهيز: " + str(msg)[:200])
            return

        # ── أرشيف بلا manifest = سكربت ──
        if not has_manifest:
            try:
                row = scripts.create_from_zip(path, name=doc.file_name, owner_id=m.from_user.id)
            except scripts.ScriptError as e:
                return bot.reply_to(m, "❌ " + str(e)[:150])
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            audit.log(m.from_user.id, "script_add", row["id"], row["name"])
            bot.reply_to(m, "🐍 <b>وصل السكربت:</b> <code>%s</code>\n\n"
                            "تحكم به من لوحة المالك ← 🐍 السكربتات."
                       % row["name"], parse_mode="HTML")
            return

        bot.reply_to(m, "🚀 وصل الملف — بدأت الفحص. سأخبرك بكل مرحلة.")
        threading.Thread(target=_run_deploy, args=(bot, m.from_user.id, path),
                         daemon=True).start()

    def _start_pending():
        for row in db.all_rows("SELECT * FROM bots WHERE status IN ('pending','error','conflict')"):
            try:
                provision.start(row, wait=1.0)
            except Exception as e:
                logger.warning("start pending %s: %s", row["id"], e)

    def _run_deploy(bot, owner_id, path):
        def say(step):
            try:
                bot.send_message(owner_id, "• " + step)
            except Exception:
                pass
        try:
            res = deploy.deploy(path, by=owner_id, log=say)
        except deploy.DeployError as e:
            try:
                bot.send_message(owner_id, "❌ <b>فشل التحديث</b>\n\n" + str(e)[:900],
                                 parse_mode="HTML")
            except Exception:
                pass
            return
        except Exception as e:
            logger.exception("deploy")
            try:
                bot.send_message(owner_id, "❌ فشل غير متوقع: " + str(e)[:200])
            except Exception:
                pass
            return
        try:
            bot.send_message(owner_id,
                             "✅ <b>تم النشر</b>\n\n"
                             "📦 الإصدار: <code>%s</code>\n"
                             "🤖 البوتات: %d\n"
                             "🧪 الاختبارات: %s\n"
                             "💾 نسخة احتياطية: <code>%s</code>" % (
                                 res["version"], res["bots"], res["tests"][:60],
                                 os.path.basename(res["backup"])),
                             parse_mode="HTML")
        except Exception:
            pass
        try:
            os.remove(path)
        except OSError:
            pass

    # ── أمر مساعد ──
    @bot.message_handler(commands=["id", "me"])
    def _id(m):
        bot.reply_to(m, "🆔 <code>%d</code>" % m.from_user.id, parse_mode="HTML")

    logger.info("handlers registered: %d", len(bot.message_handlers))
    return bot


def _ack(bot, c, text=None):
    try:
        if text:
            bot.answer_callback_query(c.id, text)
        else:
            bot.answer_callback_query(c.id)
    except Exception:
        pass


# ══════════════════════════════════════════════════
#  إطفاء البوتات المنتهية (بعد فترة السماح)
# ══════════════════════════════════════════════════
def main():
    config.check_config()
    db.init()
    bot = build_bot()
    try:
        n = deploy.mark_interrupted()
        if n:
            logger.warning("%d نشر غير مكتمل — عُلّم كـ interrupted", n)
    except Exception as e:
        logger.error("mark_interrupted: %s", e)

    def notify(chat_id, text):
        try:
            bot.send_message(chat_id, text, parse_mode="HTML",
                             disable_web_page_preview=True)
        except Exception as e:
            logger.warning("notify %s: %s", chat_id, e)

    # كود التشغيل: محاولة تجهيز تلقائي من أي ZIP موجود، وإلا إشعار واضح
    ready, why = provision.auto_setup()
    if not ready:
        try:
            bot.send_message(config.OWNER_ID,
                             "⚠️ <b>كود التشغيل غير جاهز</b>\n\n"
                             "البوتات لن تعمل حتى تجهّزه.\n\n"
                             "<b>الحل:</b> ضع ملف <code>alkhal_bot_v6_2.zip</code> "
                             "بجانب المصنع ثم نفّذ:\n"
                             "<code>python3 setup_runtime.py alkhal_bot_v6_2.zip</code>\n\n"
                             "أو أعد تشغيل المصنع وسيجهّزه تلقائياً إن وجد الملف.",
                             parse_mode="HTML")
        except Exception as e:
            logger.warning("notify runtime: %s", e)
    else:
        logger.info("runtime: %s", why)

    runner = Runner(notify=notify)
    threading.Thread(target=runner.loop, daemon=True).start()

    worker = broadcast.Worker(bot)
    threading.Thread(target=worker.loop, daemon=True).start()

    backups = backup.Scheduler(bot)
    threading.Thread(target=backups.loop, daemon=True).start()

    weekly = report.Scheduler(bot)
    threading.Thread(target=weekly.loop, daemon=True).start()
    logger.info("runtime dir: %s (جاهز: %s)", provision.RUNTIME_DIR,
                provision.runtime_ready())

    me = None
    try:
        me = bot.get_me()
    except Exception as e:
        logger.warning("getMe: %s", e)
    logger.info("🚀 بوت المصنع يعمل%s", (" · @" + me.username) if me else "")

    # إعادة محاولة عند انقطاع الشبكة (بدل أن يموت المصنع)
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=25, skip_pending=True)
            return
        except KeyboardInterrupt:
            logger.info("إيقاف المصنع…")
            runner.stop()
            worker.stop()
            backups.stop()
            weekly.stop()
            runner.stop_all()
            raise
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("Unauthorized", "Not Found", "401", "404")):
                logger.error("⛔ التوكن مرفوض من تيليجرام: %s", msg)
                raise SystemExit(2)
            logger.error("انقطاع في الاستقبال (%s) — إعادة المحاولة بعد 10 ثوان", msg[:120])
            time.sleep(10)


if __name__ == "__main__":
    main()
