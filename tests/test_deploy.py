"""test_deploy.py — المرحلة 4: نشر تحديث ZIP بأمان

نستخدم «كود تشغيل مزيّف» (main.py ينام) + اختبار بسيط، فنتحقق من:
  • رفض الأرشيفات الخطرة (zip slip، مسارات مطلقة، ملفات حساسة، بلا main.py)
  • رفض ما لا يمرّ بوابة الاختبارات أو فيه خطأ صياغة
  • النشر الناجح: كناري ثم دفعات، وكل بوت يعمل على الإصدار الجديد
  • فشل الكناري → تراجع آلي ولا يبقى أي بوت على الإصدار المعطوب
  • التراجع اليدوي
  • تنظيف الإصدارات القديمة
"""
import os
import zipfile

import pytest

from app import bots, db, deploy, provision, users

GOOD_MAIN = "import time\ntime.sleep(120)\n"
GOOD_TEST = "def test_ok():\n    assert True\n"
BAD_TEST = "def test_bad():\n    assert False\n"
CRASH_MAIN = "import sys\nsys.exit(3)\n"


@pytest.fixture(scope="module", autouse=True)
def _setup(tmp_path_factory):
    db.init()
    tmp = tmp_path_factory.mktemp("deploy")
    base = tmp / "runtime"
    base.mkdir()
    (base / "main.py").write_text(GOOD_MAIN)
    provision.RUNTIME_DIR = str(base)
    provision.RELEASES_DIR = str(tmp / "releases")
    provision.INSTANCES_DIR = str(tmp / "instances")
    deploy.CANARY_WAIT = 2.0
    deploy.PAUSE = 0.1
    db.set_setting("bot_cost", 100)
    db.set_setting("free_mode", 0)
    yield
    for row in db.all_rows("SELECT * FROM bots WHERE pid IS NOT NULL"):
        try:
            provision.stop(row)
        except Exception:
            pass


def _make_zip(path, files, main=GOOD_MAIN, test=GOOD_TEST):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("main.py", main)
        if test is not None:
            z.writestr("tests/test_ok.py", test)
        for name, content in files.items():
            z.writestr(name, content)
    return str(path)


def _mk_bot(owner, tg_id, status="active"):
    users.ensure(owner, "o%d" % owner, "مالك")
    if users.balance(owner) < 100:
        users.add_points(owner, 500, "t")
    row = bots.create(owner, "%06d:offline_token_value_1234567890" % tg_id, verify=False)
    db.run("UPDATE bots SET status=? WHERE id=?", (status, row["id"]))
    return bots.get(row["id"])


# ══════════════════════════════════════════════════
#  الفحص
# ══════════════════════════════════════════════════
def test_rejects_non_zip(tmp_path):
    p = tmp_path / "x.zip"
    p.write_text("not a zip")
    with pytest.raises(deploy.DeployError):
        deploy.validate_zip(str(p))


def test_rejects_missing_main(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("core/a.py", "x=1")
    with pytest.raises(deploy.DeployError) as e:
        deploy.validate_zip(str(p))
    assert "main.py" in str(e.value)


def test_rejects_zip_slip(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("main.py", GOOD_MAIN)
        z.writestr("../../etc/passwd", "hacked")
    with pytest.raises(deploy.DeployError) as e:
        deploy.validate_zip(str(p))
    assert "خارج المجلد" in str(e.value)


def test_rejects_absolute_path(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("main.py", GOOD_MAIN)
        z.writestr("/etc/cron.d/x", "hacked")
    with pytest.raises(deploy.DeployError):
        deploy.validate_zip(str(p))


def test_rejects_sensitive_files(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("main.py", GOOD_MAIN)
        z.writestr("data/secret.key", "k")
    with pytest.raises(deploy.DeployError) as e:
        deploy.validate_zip(str(p))
    assert "حساس" in str(e.value)


def test_rejects_without_tests(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("main.py", GOOD_MAIN)
    dest = tmp_path / "out"
    deploy.extract(str(p), str(dest))
    with pytest.raises(deploy.DeployError) as e:
        deploy.static_checks(str(dest))
    assert "اختبارات" in str(e.value)


def test_rejects_syntax_error(tmp_path):
    p = tmp_path / "x.zip"
    _make_zip(p, {"core/broken.py": "def f(:\n  pass\n"})
    dest = tmp_path / "out"
    deploy.extract(str(p), str(dest))
    with pytest.raises(deploy.DeployError) as e:
        deploy.static_checks(str(dest))
    assert "صياغة" in str(e.value)


def test_rejects_failing_tests(tmp_path):
    p = tmp_path / "x.zip"
    _make_zip(p, {}, test=BAD_TEST)
    dest = tmp_path / "out"
    deploy.extract(str(p), str(dest))
    deploy.static_checks(str(dest))
    with pytest.raises(deploy.DeployError) as e:
        deploy.run_tests(str(dest), timeout=120)
    assert "فاشلة" in str(e.value)


def test_extract_flattens_single_folder(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("alkhal_bot/main.py", GOOD_MAIN)
        z.writestr("alkhal_bot/tests/test_ok.py", GOOD_TEST)
    dest = tmp_path / "out"
    deploy.extract(str(p), str(dest))
    assert os.path.isfile(os.path.join(dest, "main.py"))
    assert os.path.isfile(os.path.join(dest, "tests", "test_ok.py"))


# ══════════════════════════════════════════════════
#  النشر الكامل
# ══════════════════════════════════════════════════
def test_full_deploy_moves_all_bots(tmp_path):
    a = _mk_bot(3101, 810001)
    b = _mk_bot(3102, 810002)
    provision.start(a, wait=0.6)
    provision.start(b, wait=0.6)

    p = tmp_path / "rel.zip"
    _make_zip(p, {"manifest.json": '{"version": "1.0.1"}'})
    res = deploy.deploy(str(p))

    assert res["version"] == "1.0.1"
    assert res["bots"] >= 2
    assert deploy.current_version() == "1.0.1"
    for row in (bots.get(a["id"]), bots.get(b["id"])):
        assert row["release"] == "1.0.1", "بوت لم ينتقل للإصدار الجديد"
        assert provision.is_alive(row["pid"]), "البوت لم يعمل بعد الترحيل"
    assert os.path.isfile(res["backup"]), "لا نسخة احتياطية"

    row = db.one("SELECT * FROM deploys WHERE id=?", (res["deploy_id"],))
    assert row["status"] == "done"
    assert row["zip_sha"]


def test_failed_canary_rolls_back(tmp_path):
    c = _mk_bot(3103, 810003)
    provision.start(c, wait=0.6)

    p = tmp_path / "bad.zip"
    _make_zip(p, {"manifest.json": '{"version": "9.9.9"}'}, main=CRASH_MAIN)

    with pytest.raises(deploy.DeployError) as e:
        deploy.deploy(str(p))
    assert "الكناري" in str(e.value)

    row = bots.get(c["id"])
    assert row["release"] is None, "بوت بقي على الإصدار المعطوب بعد التراجع"
    last = db.one("SELECT * FROM deploys ORDER BY id DESC LIMIT 1")
    assert last["status"] == "failed"


def test_manual_rollback(tmp_path):
    d = _mk_bot(3104, 810004)
    provision.start(d, wait=0.6)
    p = tmp_path / "rel2.zip"
    _make_zip(p, {"manifest.json": '{"version": "2.0.0"}'})
    res = deploy.deploy(str(p))
    assert bots.get(d["id"])["release"] == "2.0.0"

    moved = deploy.rollback(res["deploy_id"])
    assert moved >= 1
    assert bots.get(d["id"])["release"] is None
    last = db.one("SELECT * FROM deploys WHERE id=?", (res["deploy_id"],))
    assert last["status"] == "rolled_back"


def test_old_releases_cleaned(tmp_path):
    base = os.path.join(provision.RELEASES_DIR, "runtime")
    os.makedirs(base, exist_ok=True)
    for i in range(6):
        d = os.path.join(base, "old-%d" % i)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "main.py"), "w").write("x=1")

    deploy._cleanup_old(keep_current=None)
    left = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    assert len(left) <= deploy.KEEP_RELEASES + 1


def test_interrupted_marked(tmp_path):
    db.run("INSERT INTO deploys (version, status) VALUES ('x','rolling')")
    assert deploy.mark_interrupted() >= 1
    row = db.one("SELECT * FROM deploys WHERE version='x'")
    assert row["status"] == "interrupted"


def test_deploy_rejects_before_touching_bots(tmp_path):
    before = {r["id"]: r["release"] for r in db.all_rows("SELECT * FROM bots")}
    p = tmp_path / "notests.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("main.py", GOOD_MAIN)
    with pytest.raises(deploy.DeployError):
        deploy.deploy(str(p))
    after = {r["id"]: r["release"] for r in db.all_rows("SELECT * FROM bots")}
    assert before == after, "البوتات تغيّرت رغم رفض الملف"
