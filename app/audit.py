"""audit.py — سجل تدقيق لكل إجراء إداري"""
import logging

from app import db

logger = logging.getLogger(__name__)


def log(actor, action, target=None, details=None):
    try:
        db.run("INSERT INTO audit (actor, action, target, details) VALUES (?,?,?,?)",
               (actor, str(action)[:60], str(target)[:60] if target is not None else None,
                str(details)[:300] if details is not None else None))
    except Exception as e:
        logger.warning("audit: %s", e)


def recent(limit=20):
    return db.all_rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))


def count():
    return int(db.scalar("SELECT COUNT(*) FROM audit", (), 0))
