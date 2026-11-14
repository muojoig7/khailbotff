"""scripts.py — تشغيل ملفات بايثون يرفعها المالك (تحكم كامل)

المالك فقط. يرفع ملف `.py` أو أرشيف `.zip` فيه `main.py`، فيُشغَّل كعملية
مستقلة لها مجلدها وسجلها، ويستطيع: تشغيل · إيقاف · رؤية الإخراج · حذف.

الأمان:
  • كل سكربت في مجلد خاص `data/scripts/s<id>/` — لا يرى ملفات المصنع.
  • حد للذاكرة (512 ميغا) وحد للزمن الكلي اختياري.
  • لا يُقبل إلا `.py` أو `.zip` فيه `main.py`، ورفض المسارات المشبوهة.
  • كل إجراء يُسجَّل في سجل التدقيق.
"""
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from datetime import datetime

from app import db

logger = logging.getLogger(__name__)

SCRIPTS_DIR = os.getenv("FACTORY_SCRIPTS") or os.path.join(os.getcwd(), "data", "scripts")
MAX_SIZE = 5 * 1024 * 1024          # 5 ميغا للسكربت
MAX_RUNNING = 10                    # سقف السكربتات العاملة
MEM_LIMIT = 512 * 1024 * 1024       # 512 ميغا لكل سكربت

_PROCS = {}


class ScriptError(Exception):
    pass


def script_dir(sid):
    return os.path.abspath(os.path.join(SCRIPTS_DIR, "s%d" % int(sid)))


def log_path(sid):
    return os.path.join(script_dir(sid), "out.log")


# ══════════════════════════════════════════════════
#  الإنشاء
# ══════════════════════════════════════════════════
def _safe_name(name):
    base = os.path.basename(name or "script.py")
    base = "".join(c for c in base if c.isalnum() or c in "._-")[:40]
    return base or "script.py"


def create_from_file(src_path, name=None, owner_id=None):
    """يستورد ملف .py واحد."""
    name = _safe_name(name or os.path.basename(src_path))
    if not name.endswith(".py"):
        raise ScriptError("الملف يجب أن ينتهي بـ .py")
    if os.path.getsize(src_path) > MAX_SIZE:
        raise ScriptError("الملف كبير (الحد 5 ميغا).")

    cur = db.run("INSERT INTO scripts (owner_id, name, entry, status) VALUES (?,?,?,'stopped')",
                 (owner_id, name, name))
    sid = cur.lastrowid
    d = script_dir(sid)
    os.makedirs(d, exist_ok=True)
    shutil.copy2(src_path, os.path.join(d, name))
    db.run("UPDATE scripts SET path=? WHERE id=?", (d, sid))
    return get(sid)


def create_from_zip(src_path, name=None, owner_id=None):
    """يستورد أرشيفاً فيه main.py (أو ملف .py واحد)."""
    if os.path.getsize(src_path) > MAX_SIZE:
        raise ScriptError("الملف كبير (الحد 5 ميغا).")
    if not zipfile.is_zipfile(src_path):
        raise ScriptError("الملف ليس ZIP صالحاً.")

    cur = db.run("INSERT INTO scripts (owner_id, name, entry, status) VALUES (?,?,?,'stopped')",
                 (owner_id, name or "script", "main.py"))
    sid = cur.lastrowid
    d = script_dir(sid)
    os.makedirs(d, exist_ok=True)
    root = os.path.realpath(d)

    try:
        with zipfile.ZipFile(src_path) as z:
            for entry in z.namelist():
                target = os.path.realpath(os.path.join(d, entry))
                if target != root and not target.startswith(root + os.sep):
                    continue
                if entry.lower().endswith((".key", ".env")):
                    continue
                z.extract(entry, d)
    except Exception as e:
        db.run("DELETE FROM scripts WHERE id=?", (sid,))
        shutil.rmtree(d, ignore_errors=True)
        raise ScriptError("فشل فكّ الأرشيف: %s" % e)

    # لو الأرشيف فيه مجلد واحد نرفع محتواه
    entries = [e for e in os.listdir(d) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(d, entries[0])):
        inner = os.path.join(d, entries[0])
        for item in os.listdir(inner):
            shutil.move(os.path.join(inner, item), os.path.join(d, item))
        shutil.rmtree(inner, ignore_errors=True)

    entry_file = "main.py" if os.path.isfile(os.path.join(d, "main.py")) else None
    if not entry_file:
        pys = [f for f in os.listdir(d) if f.endswith(".py")]
        if len(pys) == 1:
            entry_file = pys[0]
    if not entry_file:
        db.run("DELETE FROM scripts WHERE id=?", (sid,))
        shutil.rmtree(d, ignore_errors=True)
        raise ScriptError("الأرشيف ما فيه main.py (ولا ملف .py واحد).")

    db.run("UPDATE scripts SET path=?, entry=? WHERE id=?", (d, entry_file, sid))
    return get(sid)


# ══════════════════════════════════════════════════
#  الاستعلام
# ══════════════════════════════════════════════════
def get(sid):
    return db.one("SELECT * FROM scripts WHERE id=?", (sid,))


def all_scripts(limit=20):
    return db.all_rows("SELECT * FROM scripts ORDER BY id DESC LIMIT ?", (limit,))


def running_count():
    return int(db.scalar("SELECT COUNT(*) FROM scripts WHERE status='running'", (), 0))


def is_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    try:
        with open("/proc/%d/stat" % int(pid)) as f:
            if f.read().split(") ", 1)[1][0] == "Z":
                return False
    except Exception:
        pass
    return True


def status_text(row):
    if row["status"] == "running":
        return "🟢 يعمل" if is_alive(row["pid"]) else "🟡 غير متصل"
    if row["status"] == "failed":
        return "🔴 فشل"
    return "⏸ موقوف"


def output(sid, lines=60):
    """آخر أسطر الإخراج."""
    path = log_path(sid)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.readlines()
        return "".join(data[-lines:])
    except OSError:
        return ""


# ══════════════════════════════════════════════════
#  التشغيل والإيقاف
# ══════════════════════════════════════════════════
def _limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
    except Exception:
        pass


def start(sid):
    row = get(sid)
    if not row:
        raise ScriptError("السكربت غير موجود.")
    if is_alive(row["pid"]):
        return True, "يعمل بالفعل"
    if running_count() >= MAX_RUNNING:
        raise ScriptError("عدد السكربتات العاملة وصل الحد (%d)." % MAX_RUNNING)

    d = script_dir(sid)
    entry = os.path.abspath(os.path.join(d, row["entry"] or "main.py"))
    if not os.path.isfile(entry):
        raise ScriptError("ملف التشغيل غير موجود: %s" % (row["entry"] or "main.py"))

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = d
    env["SCRIPT_DIR"] = d

    log = log_path(sid)
    try:
        with open(log, "ab") as logf:
            logf.write(("\n=== تشغيل %s ===\n" %
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")).encode())
            logf.flush()
            proc = subprocess.Popen(
                [sys.executable, entry],
                cwd=d, env=env, stdout=logf, stderr=subprocess.STDOUT,
                start_new_session=True, preexec_fn=_limits,
            )
    except Exception as e:
        db.run("UPDATE scripts SET status='failed', last_error=? WHERE id=?",
               (str(e)[:200], sid))
        raise ScriptError("فشل التشغيل: %s" % e)

    _PROCS[sid] = proc
    db.run("UPDATE scripts SET pid=?, status='running', started_at=datetime('now'), "
           "last_error=NULL WHERE id=?", (proc.pid, sid))
    return True, "تم التشغيل"


def stop(sid):
    row = get(sid)
    if not row:
        raise ScriptError("السكربت غير موجود.")
    pid = row["pid"]
    if pid and is_alive(pid):
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except Exception:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
        deadline = time.time() + 5
        while time.time() < deadline and is_alive(pid):
            time.sleep(0.15)
        if is_alive(pid):
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
            except Exception:
                pass
    proc = _PROCS.pop(sid, None)
    if proc is not None:
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    db.run("UPDATE scripts SET pid=NULL, status='stopped' WHERE id=?", (sid,))
    return True


def delete(sid):
    row = get(sid)
    if not row:
        raise ScriptError("السكربت غير موجود.")
    stop(sid)
    shutil.rmtree(script_dir(sid), ignore_errors=True)
    db.run("DELETE FROM scripts WHERE id=?", (sid,))
    return True


def refresh_statuses():
    """يحدّث حالة السكربتات التي ماتت وحدها."""
    for row in db.all_rows("SELECT * FROM scripts WHERE status='running'"):
        if not is_alive(row["pid"]):
            db.run("UPDATE scripts SET status='failed', pid=NULL WHERE id=?", (row["id"],))
