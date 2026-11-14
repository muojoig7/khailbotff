#!/usr/bin/env python3
"""setup_runtime.py — تجهيز كود التشغيل (بوت الحماية) داخل المصنع

المصنع لا يستورد كود بوت الحماية، بل يشغّله كعملية مستقلة من مجلد `runtime/`.
هذا السكربت يأخذ ملف ZIP لبوت الحماية ويفكّه هناك، ويتأكد أن `main.py` موجود.

    python3 setup_runtime.py /path/to/alkhal_bot_v6_0.zip
    python3 setup_runtime.py            # يعرض الحالة فقط
"""
import os
import shutil
import sys
import zipfile

RUNTIME = os.getenv("FACTORY_RUNTIME_DIR") or os.path.join(os.getcwd(), "runtime")


def status():
    ok = os.path.isfile(os.path.join(RUNTIME, "main.py"))
    print(("✅ " if ok else "❌ ") + "كود التشغيل: " + RUNTIME)
    if not ok:
        print("   ضع فيه كود بوت الحماية (main.py + core + plugins + database).")
        print("   مثال: python3 setup_runtime.py alkhal_bot_v6_0.zip")
    return ok


def install(zip_path):
    if not os.path.isfile(zip_path):
        print("❌ الملف غير موجود: " + zip_path)
        return 1

    os.makedirs(RUNTIME, exist_ok=True)
    tmp = os.path.join(RUNTIME, "_unzip_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                # حماية من zip slip
                target = os.path.realpath(os.path.join(tmp, name))
                if not target.startswith(os.path.realpath(tmp) + os.sep) and target != os.path.realpath(tmp):
                    print("⚠️ تجاهل مسار مشبوه: " + name)
                    continue
            z.extractall(tmp)
    except zipfile.BadZipFile:
        print("❌ الملف ليس ZIP صالحاً.")
        return 1

    # لو كان الأرشيف يحتوي مجلداً واحداً (alkhal_bot/) نرفع محتواه
    entries = [e for e in os.listdir(tmp) if not e.startswith(".")]
    src = tmp
    if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
        src = os.path.join(tmp, entries[0])

    if not os.path.isfile(os.path.join(src, "main.py")):
        print("❌ ما لقيت main.py داخل الأرشيف — تأكد أنه ملف بوت الحماية.")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1

    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(RUNTIME, item)
        if os.path.isdir(s):
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    shutil.rmtree(tmp, ignore_errors=True)
    # لا نريد قاعدة بيانات أو مفاتيح من الأرشيف
    for junk in ("data/alkhal.db", "data/alkhal.db-wal", "data/alkhal.db-shm"):
        try:
            os.remove(os.path.join(RUNTIME, junk))
        except OSError:
            pass

    print("✅ تم تجهيز كود التشغيل في: " + RUNTIME)
    return 0 if status() else 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(install(sys.argv[1]))
    sys.exit(0 if status() else 1)
