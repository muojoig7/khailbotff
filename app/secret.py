"""secret.py — تشفير ما يجب أن يبقى سرياً (توكنات البوتات وأرقام الهواتف)

مفتاح واحد في data/secret.key بصلاحيات 600. لا يُكتب أي سرّ في ملف آخر.
"""
import base64
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

SECRET_FILE = os.getenv("FACTORY_SECRET") or os.path.join("data", "secret.key")


def _fernet():
    try:
        from cryptography.fernet import Fernet
        if not os.path.isfile(SECRET_FILE):
            os.makedirs(os.path.dirname(SECRET_FILE) or ".", exist_ok=True)
            with open(SECRET_FILE, "wb") as f:
                f.write(base64.urlsafe_b64encode(os.urandom(32)))
            try:
                os.chmod(SECRET_FILE, 0o600)
            except OSError:
                pass
        with open(SECRET_FILE, "rb") as f:
            return Fernet(f.read().strip())
    except Exception:
        return None


def encrypt(text):
    text = "" if text is None else str(text)
    f = _fernet()
    if f is not None:
        return "f1:" + f.encrypt(text.encode()).decode()
    k = hashlib.sha256(b"alkhal-factory-fallback").digest()
    raw = text.encode()
    return "x1:" + base64.urlsafe_b64encode(
        bytes(a ^ k[i % len(k)] for i, a in enumerate(raw))).decode()


def decrypt(enc):
    if not enc:
        return ""
    if enc.startswith("f1:"):
        f = _fernet()
        if f is None:
            raise ValueError("cryptography غير مثبّتة")
        return f.decrypt(enc[3:].encode()).decode()
    if enc.startswith("x1:"):
        k = hashlib.sha256(b"alkhal-factory-fallback").digest()
        raw = base64.urlsafe_b64decode(enc[3:].encode())
        return bytes(a ^ k[i % len(k)] for i, a in enumerate(raw)).decode()
    return enc


def mask(text, keep=4):
    """يخفي الوسط: +9647701****67"""
    t = str(text or "")
    if len(t) <= keep:
        return "*" * len(t)
    return t[:keep] + "*" * max(3, len(t) - keep - 2) + t[-2:]
