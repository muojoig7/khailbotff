"""config.py — إعدادات بوت المصنع (مستقل تماماً عن بوت الحماية)

كل شيء من متغيرات البيئة، ولا يوجد أي استيراد من مشروع بوت الحماية.

    FACTORY_TOKEN     توكن بوت المصنع (إلزامي)
    OWNER_ID          معرّف المالك في تيليجرام (إلزامي)
    DB_PATH           مسار قاعدة المصنع (افتراضي data/factory.db)
    BRAND             الاسم الظاهر في الواجهة
    SUPPORT_USERNAME  حساب الدعم الذي يظهر للعملاء
"""
import os

# ⚠️ لا تكتب التوكن هنا إن كنت ستشارك الملف أو ترفعه لمستودع.
# الأفضل: متغير بيئة. وإن كتبته هنا فلا تنسخ الملف لأي مكان.
BOT_TOKEN = os.getenv("FACTORY_TOKEN") or "9228848091:AAEMMib87kUUBavbzDH50BrmKQihgmc7a7g"
OWNER_ID = int(os.getenv("FACTORY_OWNER_ID") or os.getenv("OWNER_ID") or 8818060362)
DB_PATH = os.getenv("FACTORY_DB") or "data/factory.db"
BRAND = os.getenv("FACTORY_BRAND") or "الخال"
SUPPORT_USERNAME = os.getenv("FACTORY_SUPPORT") or ""

# ── المرحلة 2: تشغيل بوتات العملاء ──
# مجلد كود التشغيل (بوت الحماية). المصنع لا يستورده، فقط يشغّله كعملية.
# جهّزه بـ:  python3 setup_runtime.py alkhal_bot_v6_0.zip
RUNTIME_DIR = os.getenv("FACTORY_RUNTIME_DIR") or "runtime"
INSTANCES_DIR = os.getenv("FACTORY_INSTANCES") or "data/instances"

# القيم الافتراضية (المالك يغيّرها من لوحته — لا تُعدّل هنا)
DEFAULTS = {
    "bot_cost": "100",             # نقاط إنشاء بوت
    "referral_points": "25",       # نقاط الإحالة (بعد التحقق)
    "free_days": "7",              # مدة البوت في الوضع المجاني
    "bot_days": "30",              # عمر البوت
    "grace_hours": "4",            # فترة السماح بعد الانتهاء
    "verify_enabled": "1",         # التحقق (قنوات + دولة) مفعّل؟
    "notify_new_user": "1",        # إشعار فوري لكل داخل
    "free_mode": "0",              # الوضع المجاني
    "countries": "964,962,963,965,966,967,968,970,971,972,973,974,20,212,213,216,218,249,967",
    "verify_channels": "1",        # فحص القنوات مفعّل؟
    "verify_country": "1",         # فحص الدولة مفعّل؟
    "max_bots": "25",              # سقف النسخ على الخادم
    "max_bots_per_user": "1",      # سقف البوتات لكل حساب
    "backup_hours": "6",           # كل كم ساعة نسخة احتياطية
    "backup_keep": "14",           # كم نسخة نحتفظ
    "notify_expiry": "1",          # إشعار العميل عند الانتهاء
    "coupon_points": "30",         # نقاط الكوبون
    "weekly_report": "1",          # تقرير أسبوعي
}


def check_config():
    problems = []
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        problems.append("FACTORY_TOKEN — توكن بوت المصنع من @BotFather")
    if not OWNER_ID:
        problems.append("FACTORY_OWNER_ID — معرّفك الرقمي في تيليجرام")
    if problems:
        raise SystemExit(
            "⛔ لا يمكن تشغيل بوت المصنع — إعدادات ناقصة:\n"
            + "\n".join("• " + p for p in problems)
            + "\n\nمثال:\n  export FACTORY_TOKEN=123:abc\n  export FACTORY_OWNER_ID=123456789\n"
        )
