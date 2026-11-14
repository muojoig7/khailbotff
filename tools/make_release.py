#!/usr/bin/env python3
"""make_release.py — بناء ملف تحديث (ZIP) من مشروع البوت

    python3 tools/make_release.py ../alkhal_bot_v6_1.zip 1.7.0
    python3 tools/make_release.py ../alkhal_bot         1.7.0

يخرج ملفاً في `releases/alkhal_bot-<version>.zip` فيه `manifest.json`
بالرقم، ويستثني قواعد البيانات والمفاتيح والسجلات.
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime

SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "data", "logs", "temp",
             "backups", "factory_data", "runtime", "venv", ".venv"}
SKIP_EXT = (".pyc", ".db", ".db-wal", ".db-shm", ".key", ".log")


KEY_FILE = os.getenv("DEPLOY_KEY") or os.path.join("data", "deploy.key")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def files_digest(files):
    items = sorted("%s:%s" % (rel, sha256(full)) for full, rel in files)
    return hashlib.sha256("\n".join(items).encode()).hexdigest()


def load_key():
    if os.path.isfile(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read().strip()
    return None


def collect(src_dir):
    out = []
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(SKIP_EXT):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, src_dir).replace("\\", "/")
            out.append((full, rel))
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    version = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y.%m.%d-%H%M")

    tmp = tempfile.mkdtemp(prefix="rel_")
    if src.lower().endswith(".zip"):
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        entries = [e for e in os.listdir(tmp) if not e.startswith(".")]
        root = os.path.join(tmp, entries[0]) if len(entries) == 1 and os.path.isdir(
            os.path.join(tmp, entries[0])) else tmp
    else:
        root = src

    if not os.path.isfile(os.path.join(root, "main.py")):
        print("❌ ما لقيت main.py في المصدر.")
        return 1
    if not os.path.isdir(os.path.join(root, "tests")):
        print("⚠️ المصدر بلا tests/ — المصنع سيرفض النشر.")
        return 1

    os.makedirs("releases", exist_ok=True)
    out = os.path.join("releases", "alkhal_bot-%s.zip" % version)
    files = collect(root)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in files:
            z.write(full, rel)
        manifest = {
            "name": "alkhal_bot",
            "version": version,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "files": len(files),
        }
        key = load_key()
        if key:
            digest = files_digest(files)
            manifest["digest"] = digest
            manifest["sig"] = hmac.new(
                key, ("%s:%s" % (version, digest)).encode(), hashlib.sha256).hexdigest()
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    size = os.path.getsize(out) / 1024.0
    signed = "موقّع ✅" if load_key() else "غير موقّع ⚠️ (لا يوجد data/deploy.key)"
    print("✅ %s  (%.0f كيلوبايت · %d ملف · إصدار %s · %s)"
          % (out, size, len(files), version, signed))
    print("ارفعه لبوت المصنع في الخاص — سيُفحص ويُنشر بالكناري.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
