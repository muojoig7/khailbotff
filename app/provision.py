"""provision.py — تشغيل بوت العميل فعلياً (عملية مستقلة لكل عميل)

مبدأ الفصل: هذا الملف **لا يستورد** كود بوت الحماية ولا يعرف عنه شيئاً.
كل ما يفعله: ينشئ مجلد النسخة، ويمرّر متغيرات البيئة، ويشغّل
`python <RUNTIME_DIR>/main.py` كعملية مستقلة.

    data/instances/t12/
        data/alkhal.db      قاعدة العميل
        bot.log             سجله
        instance.json       معلومات (بلا توكن)

الأمان: التوكن يُفكّ من القاعدة ويُمرَّر كمتغير بيئة فقط — لا يُكتب في أي ملف.
"""
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

from app import db

logger = logging.getLogger(__name__)

RUNTIME_DIR = os.getenv("FACTORY_RUNTIME_DIR") or os.path.join(os.getcwd(), "runtime")
RELEASES_DIR = os.getenv("FACTORY_RELEASES") or os.path.join(os.getcwd(), "data", "releases")
INSTANCES_DIR = os.getenv("FACTORY_INSTANCES") or os.path.join(os.getcwd(), "data", "instances")

# مقابض العمليات في هذه الجلسة (لإنهاء الزومبي)
_PROCS = {}


def release_dir(version):
    """مجلد إصدار منشور: data/releases/runtime/<version>."""
    return os.path.join(RELEASES_DIR, "runtime", str(version))


def runtime_for(bot_row):
    """كود التشغيل الذي يعمل عليه هذا البوت (إصدار أو الأساس)."""
    rel = bot_row["release"] if "release" in bot_row.keys() else None
    return release_dir(rel) if rel else RUNTIME_DIR


def runtime_ready(bot_row=None):
    d = runtime_for(bot_row) if bot_row is not None else RUNTIME_DIR
    return os.path.isfile(os.path.join(d, "main.py"))


FIX_HINT = ("ثبّت كود التشغيل: ضع ملف alkhal_bot*.zip بجانب المصنع ثم نفّذ:\n"
            "python3 setup_runtime.py alkhal_bot_v6_2.zip")


def install_runtime_from_zip(zip_path, target=None):
    """يفكّ ZIP بوت الحماية داخل مجلد التشغيل (بأمان). يرجع (نجاح؟، رسالة)."""
    import zipfile
    target = target or RUNTIME_DIR
    if not os.path.isfile(zip_path):
        return False, "الملف غير موجود: " + zip_path
    if not zipfile.is_zipfile(zip_path):
        return False, "الملف ليس ZIP صالحاً."

    tmp = os.path.join(target, "_unzip_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    root = os.path.realpath(tmp)
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                dest = os.path.realpath(os.path.join(tmp, name))
                if dest != root and not dest.startswith(root + os.sep):
                    continue
                if name.lower().endswith((".key", ".env")):
                    continue
                z.extract(name, tmp)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, "فشل الفكّ: %s" % e

    entries = [e for e in os.listdir(tmp) if not e.startswith(".")]
    src = tmp
    if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
        src = os.path.join(tmp, entries[0])

    if not os.path.isfile(os.path.join(src, "main.py")):
        shutil.rmtree(tmp, ignore_errors=True)
        return False, "الأرشيف لا يحتوي main.py — هذا ليس كود بوت الحماية."

    os.makedirs(target, exist_ok=True)
    for item in os.listdir(src):
        s_path = os.path.join(src, item)
        d_path = os.path.join(target, item)
        try:
            if os.path.isdir(s_path):
                shutil.rmtree(d_path, ignore_errors=True)
                shutil.copytree(s_path, d_path)
            else:
                shutil.copy2(s_path, d_path)
        except Exception as e:
            logger.warning("install %s: %s", item, e)

    shutil.rmtree(tmp, ignore_errors=True)
    for junk in ("data/alkhal.db", "data/alkhal.db-wal", "data/alkhal.db-shm"):
        try:
            os.remove(os.path.join(target, junk))
        except OSError:
            pass
    return runtime_ready(), "تم تجهيز كود التشغيل في " + target


def auto_setup(folder=None):
    """إن كان كود التشغيل ناقصاً، نحاول تجهيزه من أول ZIP مناسب في المجلد."""
    if runtime_ready():
        return True, "جاهز"
    folder = folder or os.getcwd()
    try:
        candidates = sorted(
            [f for f in os.listdir(folder)
             if f.lower().endswith(".zip") and "alkhal_bot" in f.lower()],
            reverse=True)
    except OSError:
        candidates = []
    for name in candidates:
        okk, msg = install_runtime_from_zip(os.path.join(folder, name))
        if okk:
            logger.info("runtime auto-installed from %s", name)
            return True, "جُهّز تلقائياً من %s" % name
    return False, "لا يوجد ملف alkhal_bot*.zip بجانب المصنع"


def instance_dir(bot_row_id):
    return os.path.join(INSTANCES_DIR, "t%d" % int(bot_row_id))


def prepare(bot_row):
    """ينشئ مجلدات النسخة. لا يلمس أي كود."""
    d = instance_dir(bot_row["id"])
    for sub in ("", "data", "logs", "temp", "backups"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    meta = os.path.join(d, "instance.json")
    if not os.path.exists(meta):
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({
                "factory_bot_row": bot_row["id"],
                "telegram_bot_id": bot_row["bot_id"],
                "username": bot_row["username"],
                "owner": bot_row["owner_id"],
                "created_at": bot_row["created_at"],
            }, f, ensure_ascii=False, indent=2)
    return d


def env_for(bot_row, token):
    env = os.environ.copy()
    env.update({
        "BOT_TOKEN": token,
        "SUPER_ADMIN_ID": str(bot_row["owner_id"]),
        "DB_PATH": os.path.join(instance_dir(bot_row["id"]), "data", "alkhal.db"),
        "BOT_ID": "1",
        "PYTHONPATH": runtime_for(bot_row),
        "PYTHONUNBUFFERED": "1",
        "FACTORY_INSTANCE": str(bot_row["id"]),
    })
    return env


def is_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    # الزومبي ليس حياً
    try:
        with open("/proc/%d/stat" % int(pid), "r") as f:
            state = f.read().split(") ", 1)[1][0]
        if state == "Z":
            return False
    except Exception:
        pass
    return True


def start(bot_row, wait=0.0):
    """يشغّل العملية. يرجع (نجاح؟، رسالة)."""
    from app import bots

    if is_alive(bot_row["pid"] if "pid" in bot_row.keys() else None):
        return True, "يعمل بالفعل"

    rt = runtime_for(bot_row)
    if not runtime_ready(bot_row):
        msg = ("كود التشغيل غير موجود في %s — %s" % (rt, FIX_HINT))
        db.run("UPDATE bots SET status='pending', last_error=? WHERE id=?", (msg, bot_row["id"]))
        return False, msg

    try:
        token = bots.decrypt(bot_row["token_enc"])
    except Exception as e:
        msg = "تعذّر فك التوكن: %s" % e
        db.run("UPDATE bots SET status='error', last_error=? WHERE id=?", (msg, bot_row["id"]))
        return False, msg

    d = prepare(bot_row)
    log_path = os.path.join(d, "bot.log")
    _trim_log(log_path)
    try:
        with open(log_path, "ab") as logf:
            proc = subprocess.Popen(
                [sys.executable, os.path.join(rt, "main.py")],
                cwd=d,
                env=env_for(bot_row, token),
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except Exception as e:
        msg = "فشل التشغيل: %s" % e
        db.run("UPDATE bots SET status='error', last_error=? WHERE id=?", (msg, bot_row["id"]))
        return False, msg

    _PROCS[bot_row["id"]] = proc
    db.run("UPDATE bots SET pid=?, status='active', started_at=datetime('now'), last_error=NULL "
           "WHERE id=?", (proc.pid, bot_row["id"]))

    if wait:
        time.sleep(wait)
        if not is_alive(proc.pid):
            tail = _tail(log_path)
            db.run("UPDATE bots SET status='error', last_error=? WHERE id=?",
                   (tail[:500], bot_row["id"]))
            return False, tail[:300]
    return True, "تم التشغيل"


def stop(bot_row):
    """يوقف العملية (وبناتها) وينتظر خروجها."""
    pid = bot_row["pid"] if "pid" in bot_row.keys() else None
    if pid and is_alive(pid):
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except Exception:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
        deadline = time.time() + 8
        while time.time() < deadline and is_alive(pid):
            time.sleep(0.15)
        if is_alive(pid):
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
            except Exception:
                pass
    proc = _PROCS.pop(bot_row["id"], None)
    if proc is not None:
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    db.run("UPDATE bots SET pid=NULL WHERE id=?", (bot_row["id"],))
    return True


def restart(bot_row):
    stop(bot_row)
    row = db.one("SELECT * FROM bots WHERE id=?", (bot_row["id"],))
    return start(row)


def _trim_log(path, max_mb=5, keep_mb=1):
    """يقلّم سجل النسخة إذا تجاوز الحجم — يمنع امتلاء القرص."""
    try:
        size = os.path.getsize(path)
        if size < max_mb * 1024 * 1024:
            return False
        with open(path, "rb") as f:
            f.seek(max(0, size - keep_mb * 1024 * 1024))
            data = f.read()
        with open(path, "wb") as f:
            f.write(b"... [trimmed] ...\n" + data)
        return True
    except OSError:
        return False


def _tail(path, limit=2000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def log_tail(bot_row, limit=1200):
    return _tail(os.path.join(instance_dir(bot_row["id"]), "bot.log"), limit)


def read_group(bot_row):
    """يقرأ اسم المجموعة من قاعدة العميل (بعد أن يكتب «تفعيل»)."""
    path = os.path.join(instance_dir(bot_row["id"]), "data", "alkhal.db")
    if not os.path.exists(path):
        return None, None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT group_id, group_title FROM bots WHERE id=1").fetchone()
        conn.close()
        if row and row["group_id"]:
            return row["group_id"], row["group_title"]
    except Exception:
        pass
    return None, None


def sync_group(bot_row):
    """يحدّث مجموعة العميل في قاعدة المصنع إن وُجدت."""
    gid, title = read_group(bot_row)
    if gid:
        db.run("UPDATE bots SET group_id=?, group_title=? WHERE id=?", (gid, title, bot_row["id"]))
    return gid, title


def purge(bot_row):
    """يحذف كل ملفات النسخة (بعد الإيقاف)."""
    stop(bot_row)
    d = instance_dir(bot_row["id"])
    try:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass
    return True
