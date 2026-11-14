"""conftest.py — قاعدة مؤقتة وإعدادات اختبار لبوت المصنع"""
import os
import tempfile

os.environ.setdefault("FACTORY_TOKEN", "1:TEST-TOKEN")

import config  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="factory_v1_")
config.BOT_TOKEN = "1:TEST-TOKEN"
config.OWNER_ID = 999
config.DB_PATH = os.path.join(_TMP, "test.db")
config.BRAND = "مصنع الاختبار"

# سقوف عالية افتراضياً في الاختبارات (اختبارات السقف تضبط قيمها بنفسها)
config.DEFAULTS["max_bots"] = "500"
config.DEFAULTS["max_bots_per_user"] = "10"


def pytest_sessionfinish(session, exitstatus):
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)
