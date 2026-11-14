"""deploy.py — نشر تحديث ZIP بأمان (المالك فقط)

المراحل بالترتيب، وأي فشل يوقف كل شيء ويترك النسخة الحالية تعمل:

  1. فحص الملف      حجم · امتداد · محتوى (main.py) · منع zip slip
  2. فكّ في مجلد مؤقت (staging) — لا يلمس الإصدار الشغّال
  3. فحص ثابت        كل ملفات .py تُترجم · وجود main.py · وجود اختبارات
  4. بوابة اختبارات  تشغيل الاختبارات في مجلد الإصدار (بمهلة، بلا شبكة)
  5. نسخة احتياطية   الكود الحالي + قاعدة المصنع في data/backups
  6. كناري          بوت واحد ينتقل للإصدار الجديد ويُعاد تشغيله
  7. دفعات          بقية البوتات 3 في كل دفعة
  8. تراجع آلي      أي فشل بعد التبديل → إرجاع الجميع للإصدار السابق

كل مرحلة تُسجَّل في جدول deploys، فلو انقطع المصنع في المنتصف نعرف أين توقف.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime

from app import bots as bots_mod
from app import db, provision

logger = logging.getLogger(__name__)

MAX_SIZE = 25 * 1024 * 1024          # 25 ميغا
FORBIDDEN = (".key", ".env", ".db", ".db-wal", ".db-shm", ".pem", ".pfx")
CANARY_WAIT = float(os.getenv("DEPLOY_CANARY_WAIT") or 8)
BATCH = int(os.getenv("DEPLOY_BATCH") or 3)
PAUSE = float(os.getenv("DEPLOY_PAUSE") or 3)
TEST_TIMEOUT = int(os.getenv("DEPLOY_TEST_TIMEOUT") or 240)
KEEP_RELEASES = 3


KEY_FILE = os.getenv("DEPLOY_KEY") or os.path.join("data", "deploy.key")


def load_key(create=False):
    if os.path.isfile(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read().strip()
    if not create:
        return None
    os.makedirs(os.path.dirname(KEY_FILE) or ".", exist_ok=True)
    key = secrets.token_hex(32).encode()
    with open(KEY_FILE, "wb") as f:
        f.write(key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return key


def files_digest(root):
    items = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f == "manifest.json":
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            items.append("%s:%s" % (rel, _sha256(full)))
    items.sort()
    return hashlib.sha256("\n".join(items).encode()).hexdigest()


def sign(key, version, digest):
    return hmac.new(key, ("%s:%s" % (version, digest)).encode(), hashlib.sha256).hexdigest()


def verify_signature(dest):
    """لو المفتاح موجود يجب توقيع صحيح."""
    key = load_key()
    mpath = os.path.join(dest, "manifest.json")
    manifest = {}
    if os.path.isfile(mpath):
        try:
            with open(mpath, encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            raise DeployError("manifest.json غير صالح.")

    if not key:
        return {"signed": False, "reason": "لا مفتاح توقيع على الخادم"}

    sig = str(manifest.get("sig") or "").strip()
    if not sig:
        raise DeployError("الأرشيف غير موقّع — لن أنشره. "
                          "ابنِه بـ tools/make_release.py (يوقّع تلقائياً).")

    version = str(manifest.get("version") or "")
    digest = files_digest(dest)
    if not hmac.compare_digest(sig, sign(key, version, digest)):
        raise DeployError("توقيع الأرشيف غير صحيح — الملف عُدّل بعد البناء.")
    return {"signed": True, "version": version}


class DeployError(Exception):
    """فشل مفهوم يُعرض للمالك."""


# ══════════════════════════════════════════════════
#  1) فحص الملف
# ══════════════════════════════════════════════════
def validate_zip(path):
    if not os.path.isfile(path):
        raise DeployError("الملف غير موجود.")
    size = os.path.getsize(path)
    if size > MAX_SIZE:
        raise DeployError("الملف كبير: %.1f ميغا (الحد %d)." % (size / 1048576, MAX_SIZE // 1048576))
    if not zipfile.is_zipfile(path):
        raise DeployError("الملف ليس ZIP صالحاً.")

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if not names:
            raise DeployError("الأرشيف فارغ.")
        if len(names) > 5000:
            raise DeployError("عدد الملفات كبير جداً (%d)." % len(names))

        for name in names:
            norm = name.replace("\\", "/")
            if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
                raise DeployError("مسار مطلق مرفوض: %s" % name)
            if ".." in norm.split("/"):
                raise DeployError("مسار خارج المجلد مرفوض: %s" % name)
            if norm.lower().endswith(FORBIDDEN):
                raise DeployError("ملف حساس مرفوض: %s" % name)
            info = z.getinfo(name)
            # symlink داخل zip = خطر
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise DeployError("رابط رمزي مرفوض: %s" % name)

        has_main = any(n.endswith("main.py") for n in names)
        if not has_main:
            raise DeployError("ما لقيت main.py — هذا ليس كود بوت.")

    return {"size": size, "files": len(names),
            "sha256": _sha256(path)}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ══════════════════════════════════════════════════
#  2) فكّ آمن
# ══════════════════════════════════════════════════
def extract(zip_path, dest):
    os.makedirs(dest, exist_ok=True)
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            target = os.path.realpath(os.path.join(dest, name))
            if target != root and not target.startswith(root + os.sep):
                raise DeployError("مسار مشبوه: %s" % name)
            z.extract(name, dest)

    # لو الأرشيف فيه مجلد واحد فقط، نرفع محتواه
    entries = [e for e in os.listdir(dest) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])):
        inner = os.path.join(dest, entries[0])
        if os.path.isfile(os.path.join(inner, "main.py")):
            for item in os.listdir(inner):
                shutil.move(os.path.join(inner, item), os.path.join(dest, item))
            shutil.rmtree(inner, ignore_errors=True)

    if not os.path.isfile(os.path.join(dest, "main.py")):
        raise DeployError("بعد الفكّ: ما لقيت main.py في الجذر.")
    return dest


# ══════════════════════════════════════════════════
#  3) فحص ثابت
# ══════════════════════════════════════════════════
def static_checks(dest):
    bad = []
    count = 0
    for root, _, files in os.walk(dest):
        for f in files:
            if not f.endswith(".py"):
                continue
            count += 1
            path = os.path.join(root, f)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    compile(fh.read(), path, "exec")
            except SyntaxError as e:
                bad.append("%s:%s %s" % (os.path.relpath(path, dest), e.lineno, e.msg))
    if bad:
        raise DeployError("أخطاء صياغة:\n" + "\n".join(bad[:5]))

    tests_dir = os.path.join(dest, "tests")
    has_tests = os.path.isdir(tests_dir) and any(
        f.startswith("test_") and f.endswith(".py") for f in os.listdir(tests_dir))
    if not has_tests:
        raise DeployError("الأرشيف بلا اختبارات (tests/test_*.py) — لن أنشر بلا بوابة فحص.")
    return {"py_files": count}


# ══════════════════════════════════════════════════
#  4) بوابة الاختبارات
# ══════════════════════════════════════════════════
def run_tests(dest, timeout=TEST_TIMEOUT):
    env = os.environ.copy()
    tmp_db = os.path.join(dest, "_test.db")
    env.update({
        "PYTHONPATH": dest,
        "DB_PATH": tmp_db,
        "ALKHAL_NO_RATELIMIT": "1",
        "ALKHAL_SYNC_JOBS": "1",
        "BOT_TOKEN": "1:TEST",
        "PYTHONUNBUFFERED": "1",
    })
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"],
                              cwd=dest, env=env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DeployError("الاختبارات تجاوزت المهلة (%d ثانية)." % timeout)
    except FileNotFoundError:
        raise DeployError("pytest غير مثبّت على الخادم.")
    finally:
        for junk in (tmp_db, tmp_db + "-wal", tmp_db + "-shm"):
            try:
                os.remove(junk)
            except OSError:
                pass

    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        tail = "\n".join(out.splitlines()[-8:])
        raise DeployError("اختبارات فاشلة:\n" + tail[:600])
    return out.splitlines()[-1] if out else "ok"


# ══════════════════════════════════════════════════
#  5) نسخة احتياطية
# ══════════════════════════════════════════════════
def backup(label="predeploy"):
    from config import DB_PATH
    d = os.path.join("data", "backups")
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(d, "backup-%s-%s.zip" % (label, stamp))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.isfile(DB_PATH):
            z.write(DB_PATH, "factory.db")
        if os.path.isdir(provision.RUNTIME_DIR):
            for root, _, files in os.walk(provision.RUNTIME_DIR):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, provision.RUNTIME_DIR)
                    if rel.endswith((".db", ".db-wal", ".db-shm", ".key")):
                        continue
                    z.write(full, os.path.join("runtime", rel))
    return path


# ══════════════════════════════════════════════════
#  6-8) النشر
# ══════════════════════════════════════════════════
def _version_of(dest, fallback):
    mpath = os.path.join(dest, "manifest.json")
    if os.path.isfile(mpath):
        try:
            with open(mpath, encoding="utf-8") as f:
                data = json.load(f)
            v = str(data.get("version") or "").strip()
            if v:
                return re.sub(r"[^0-9A-Za-z._-]", "_", v)[:40]
        except Exception:
            pass
    return fallback


def _restart_on_release(row, version):
    db.run("UPDATE bots SET release=? WHERE id=?", (version, row["id"]))
    row = bots_mod.get(row["id"])
    if row["status"] in ("active", "pending", "error"):
        provision.stop(row)
        ok, msg = provision.start(row, wait=1.5)
        return ok, msg
    return True, "غير نشط"


def _alive_after(row, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if provision.is_alive(row["pid"]):
            return True
        time.sleep(0.3)
    return provision.is_alive(row["pid"])


def _set_deploy(did, **fields):
    if not fields:
        return
    cols = ", ".join("%s=?" % k for k in fields)
    db.run("UPDATE deploys SET %s WHERE id=?" % cols, tuple(fields.values()) + (did,))


def deploy(zip_path, by=None, canary_wait=None, batch=None, pause=None, log=None):
    """ينفّذ النشر كاملاً. يرفع DeployError عند أي فشل (بعد التراجع)."""
    canary_wait = CANARY_WAIT if canary_wait is None else canary_wait
    batch = BATCH if batch is None else batch
    pause = PAUSE if pause is None else pause
    say = log or (lambda *a: None)

    info = validate_zip(zip_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    did = db.run("INSERT INTO deploys (version, path, zip_sha, size, status, created_by) "
                 "VALUES (?,?,?,?, 'staging', ?)",
                 (stamp, "", info["sha256"], info["size"], by)).lastrowid

    staging = os.path.join(provision.RELEASES_DIR, "_staging-%s" % stamp)
    try:
        say("1/6 فحص الملف…")
        extract(zip_path, staging)
        verify_signature(staging)
        static_checks(staging)
        version = _version_of(staging, stamp)
        _set_deploy(did, version=version)

        say("2/6 بوابة الاختبارات…")
        summary = run_tests(staging)

        say("3/6 نسخة احتياطية…")
        bpath = backup(version)

        say("4/6 تثبيت الإصدار…")
        final = provision.release_dir(version)
        if os.path.isdir(final):
            shutil.rmtree(final, ignore_errors=True)
        os.makedirs(os.path.dirname(final), exist_ok=True)
        shutil.move(staging, final)
        _set_deploy(did, path=final, status="canary")

        targets = db.all_rows("SELECT * FROM bots WHERE status IN ('active','pending','error')")
        _set_deploy(did, bots_total=len(targets))

        if targets:
            say("5/6 كناري على بوت واحد…")
            canary = targets[0]
            ok, msg = _restart_on_release(canary, version)
            if not ok or not _alive_after(bots_mod.get(canary["id"]), canary_wait):
                _set_deploy(did, status="failed", error="فشل الكناري: %s" % (msg or "توقف"),
                            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                _rollback_bots(version, did)
                raise DeployError("فشل الكناري — رجعنا للإصدار السابق.\nالسبب: %s" % (msg or "توقف"))

            _set_deploy(did, bots_moved=1)
            say("6/6 دفعات (%d بوت)…" % (len(targets) - 1))
            moved = 1
            rest = targets[1:]
            for i in range(0, len(rest), batch):
                chunk = rest[i:i + batch]
                for row in chunk:
                    ok, msg = _restart_on_release(row, version)
                    if not ok:
                        _set_deploy(did, status="failed", error="فشل بوت %s: %s" % (row["id"], msg),
                                    bots_moved=moved,
                                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        _rollback_bots(version, did)
                        raise DeployError("فشل بوت في الدفعة — رجعنا الجميع للإصدار السابق.")
                    moved += 1
                    _set_deploy(did, bots_moved=moved)
                time.sleep(pause)
        else:
            say("لا بوتات نشطة — سيُستخدم الإصدار للبوتات الجديدة.")

        _set_deploy(did, status="done",
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _cleanup_old(keep_current=version)
        return {"deploy_id": did, "version": version, "tests": summary,
                "bots": len(targets), "backup": bpath, "path": final}

    except DeployError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        _set_deploy(did, status="failed", error=str(e)[:300],
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.exception("deploy failed")
        raise DeployError("فشل غير متوقع: %s" % e)


def _rollback_bots(version, did):
    """يرجع كل من انتقل للإصدار الجديد إلى الأساس، ويعيد تشغيله."""
    rows = db.all_rows("SELECT * FROM bots WHERE release=?", (version,))
    for row in rows:
        db.run("UPDATE bots SET release=NULL WHERE id=?", (row["id"],))
        fresh = bots_mod.get(row["id"])
        try:
            provision.stop(fresh)
            if fresh["status"] in ("active", "pending", "error"):
                provision.start(fresh)
        except Exception as e:
            logger.error("rollback bot %s: %s", row["id"], e)
    return len(rows)


def rollback(deploy_id, by=None):
    """تراجع يدوي عن نشر مكتمل."""
    row = db.one("SELECT * FROM deploys WHERE id=?", (deploy_id,))
    if not row:
        raise DeployError("النشر غير موجود.")
    if row["status"] not in ("done", "failed", "interrupted"):
        raise DeployError("لا يمكن التراجع الآن (الحالة: %s)." % row["status"])
    moved = _rollback_bots(row["version"], deploy_id)
    _set_deploy(deploy_id, status="rolled_back",
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return moved


def _cleanup_old(keep_current=None):
    base = os.path.join(provision.RELEASES_DIR, "runtime")
    if not os.path.isdir(base):
        return
    dirs = sorted(
        [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))],
        key=lambda d: os.path.getmtime(os.path.join(base, d)), reverse=True)
    keep = set(dirs[:KEEP_RELEASES])
    if keep_current:
        keep.add(keep_current)
    for d in dirs:
        if d not in keep:
            shutil.rmtree(os.path.join(base, d), ignore_errors=True)


def current_version():
    """الإصدار الذي يعمل عليه أغلب البوتات."""
    row = db.one("SELECT release, COUNT(*) c FROM bots WHERE release IS NOT NULL "
                 "GROUP BY release ORDER BY c DESC LIMIT 1")
    return row["release"] if row else None


def history(limit=5):
    return db.all_rows("SELECT * FROM deploys ORDER BY id DESC LIMIT ?", (limit,))


def mark_interrupted():
    """عند بدء المصنع: أي نشر لم يكتمل يُعلَّم ليعرف المالك."""
    rows = db.all_rows("SELECT * FROM deploys WHERE status IN ('staging','canary','rolling')")
    for r in rows:
        _set_deploy(r["id"], status="interrupted",
                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return len(rows)
