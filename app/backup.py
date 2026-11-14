"""backup.py — نسخ احتياطي دوري لقاعدة المصنع

نسخة كل (backup_hours) ساعة، تُرسل إلى المالك في تيليجرام، والاحتفاظ بآخر
(backup_keep). تُستخدم sqlite3.backup() فتكون متسقة حتى والبوت شغّال.
"""
import logging
import os
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime, timezone

from app import db

logger = logging.getLogger(__name__)

BACKUP_DIR = os.path.join("data", "backups")
MAX_TELEGRAM = 45 * 1024 * 1024


def create(kind="auto"):
    from config import DB_PATH
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw = os.path.join(BACKUP_DIR, "factory-%s.db" % stamp)

    if os.path.isfile(DB_PATH):
        src = sqlite3.connect(DB_PATH, timeout=10)
        dst = sqlite3.connect(raw)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()

    path = os.path.join(BACKUP_DIR, "factory-%s-%s.zip" % (kind, stamp))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.isfile(raw):
            z.write(raw, "factory.db")
            os.remove(raw)
        z.writestr("manifest.txt", "backup=%s\nkind=%s\n" % (stamp, kind))

    db.run("INSERT INTO backups (path, size, kind) VALUES (?,?,?)",
           (path, os.path.getsize(path), kind))
    rotate()
    return path


def rotate():
    keep = db.get_int("backup_keep", 14)
    rows = db.all_rows("SELECT * FROM backups ORDER BY id DESC")
    for row in rows[keep:]:
        try:
            os.remove(row["path"])
        except OSError:
            pass
        db.run("DELETE FROM backups WHERE id=?", (row["id"],))


def last(limit=5):
    return db.all_rows("SELECT * FROM backups ORDER BY id DESC LIMIT ?", (limit,))


def send_to_telegram(bot, chat_id, path):
    try:
        if os.path.getsize(path) > MAX_TELEGRAM:
            bot.send_message(chat_id, "⚠️ النسخة كبيرة — احتفظنا بها على الخادم: %s" % path)
            return False
        with open(path, "rb") as f:
            bot.send_document(chat_id, f, caption="💾 نسخة احتياطية · " +
                              datetime.now().strftime("%Y-%m-%d %H:%M"))
        db.run("UPDATE backups SET sent=1 WHERE path=?", (path,))
        return True
    except Exception as e:
        logger.warning("backup send: %s", e)
        return False


class Scheduler:
    def __init__(self, bot, interval_check=600):
        self.bot = bot
        self.interval_check = interval_check
        self._stop = False

    def stop(self):
        self._stop = True

    def loop(self):
        logger.info("backup scheduler started")
        while not self._stop:
            try:
                self.tick()
            except Exception as e:
                logger.error("backup loop: %s", e)
            for _ in range(int(self.interval_check * 10)):
                if self._stop:
                    return
                time.sleep(0.1)

    def tick(self):
        hours = max(1, db.get_int("backup_hours", 6))
        row = db.one("SELECT created_at FROM backups ORDER BY id DESC LIMIT 1")
        if row:
            try:
                last_t = datetime.fromisoformat(row["created_at"])
            except Exception:
                last_t = None
            if last_t and (datetime.now() - last_t).total_seconds() < hours * 3600:
                return False
        path = create("auto")
        from config import OWNER_ID
        send_to_telegram(self.bot, OWNER_ID, path)
        logger.info("backup created: %s", path)
        return True


def restore(zip_path, target=None):
    from config import DB_PATH
    target = target or DB_PATH
    if not os.path.isfile(zip_path):
        raise SystemExit("الملف غير موجود: " + zip_path)
    tmp = target + ".restore"
    with zipfile.ZipFile(zip_path) as z:
        with z.open("factory.db") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
    os.replace(tmp, target)
    print("✅ تم استبدال قاعدة المصنع بـ " + zip_path)
    return target
