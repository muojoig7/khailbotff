"""db.py — قاعدة بيانات المصنع (SQLite، مستقلة عن أي مشروع آخر)

- اتصال لكل خيط (سريع، لا فتح/إغلاق مع كل استعلام).
- كل عملية كتابة داخل معاملة (transaction).
- جداول المصنع: المستخدمون، النقاط، الإحالات، القنوات، الاستثناءات،
  البوتات، الدعم، الإعدادات، الأدمنية.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager

from config import DB_PATH, DEFAULTS

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    state TEXT DEFAULT 'new',
    phone_cc TEXT,
    phone_last4 TEXT,
    country_ok INTEGER DEFAULT 0,
    verified_at TEXT,
    exception INTEGER DEFAULT 0,
    exception_until TEXT,
    points INTEGER DEFAULT 0,
    referred_by INTEGER,
    referrals INTEGER DEFAULT 0,
    free_used INTEGER DEFAULT 0,
    is_blocked INTEGER DEFAULT 0,
    joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    role TEXT DEFAULT 'admin',
    added_by INTEGER,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channels (
    chat_id INTEGER PRIMARY KEY,
    username TEXT,
    title TEXT,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS exceptions (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS points_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    reason TEXT,
    ref TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER,
    referred_id INTEGER UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    rewarded INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER,
    token_enc TEXT,
    bot_id INTEGER UNIQUE,
    username TEXT,
    name TEXT,
    status TEXT DEFAULT 'pending',
    is_free INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT,
    pid INTEGER,
    started_at TEXT,
    last_error TEXT,
    restarts INTEGER DEFAULT 0,
    group_id INTEGER,
    group_title TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    status TEXT DEFAULT 'open',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ticket_msgs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER,
    admin_id INTEGER,
    text TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    text TEXT,
    total INTEGER DEFAULT 0,
    sent INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_by INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS broadcast_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id INTEGER,
    chat_id INTEGER,
    via TEXT,
    status TEXT DEFAULT 'pending',
    error TEXT,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS deploys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT,
    path TEXT,
    zip_sha TEXT,
    size INTEGER,
    status TEXT DEFAULT 'pending',
    bots_total INTEGER DEFAULT 0,
    bots_moved INTEGER DEFAULT 0,
    error TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor INTEGER,
    action TEXT,
    target TEXT,
    details TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    points INTEGER,
    uses_left INTEGER DEFAULT 1,
    created_by INTEGER,
    used_by INTEGER,
    used_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT,
    size INTEGER,
    kind TEXT,
    sent INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER,
    name TEXT,
    entry TEXT DEFAULT 'main.py',
    path TEXT,
    pid INTEGER,
    status TEXT DEFAULT 'stopped',
    last_error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT
);

CREATE TABLE IF NOT EXISTS purged (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_row INTEGER,
    username TEXT,
    owner_id INTEGER,
    purged_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bq_pending ON broadcast_queue(broadcast_id, status);
CREATE INDEX IF NOT EXISTS idx_users_state ON users(state);
CREATE INDEX IF NOT EXISTS idx_bots_owner ON bots(owner_id);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON points_ledger(user_id);
"""


def _open():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = _open()
        _local.c = c
    return c


def close():
    c = getattr(_local, "c", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        _local.c = None


@contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        raise


MIGRATIONS = [
    ("bots", "pid", "INTEGER"),
    ("bots", "started_at", "TEXT"),
    ("bots", "last_error", "TEXT"),
    ("bots", "restarts", "INTEGER DEFAULT 0"),
    ("bots", "group_id", "INTEGER"),
    ("bots", "group_title", "TEXT"),
    ("bots", "release", "TEXT"),
    ("bots", "retry_at", "TEXT"),
    ("users", "phone_enc", "TEXT"),
]


def _migrate(c):
    """يضيف الأعمدة الناقصة في قواعد النسخ القديمة (آمن للتكرار)."""
    for table, column, decl in MIGRATIONS:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(%s)" % table).fetchall()]
        if column not in cols:
            try:
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
            except Exception:
                pass


def init():
    with tx() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        for k, v in DEFAULTS.items():
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))


# ── استعلامات مساعدة ──
def one(sql, args=()):
    return conn().execute(sql, args).fetchone()


def all_rows(sql, args=()):
    return conn().execute(sql, args).fetchall()


def run(sql, args=()):
    with tx() as c:
        return c.execute(sql, args)


def scalar(sql, args=(), default=0):
    r = one(sql, args)
    if not r:
        return default
    v = r[0]
    return default if v is None else v


# ── الإعدادات ──
def get_setting(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    if r:
        return r["value"]
    return default if default is not None else DEFAULTS.get(key, "")


def set_setting(key, value):
    run("INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def get_int(key, default=0):
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def get_list(key, sep=","):
    raw = get_setting(key, "") or ""
    return [x.strip() for x in raw.split(sep) if x.strip()]
