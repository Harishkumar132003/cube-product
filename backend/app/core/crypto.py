"""Encryption for stored database passwords.

The key is generated on first use into backend/var/secret.key, which is
gitignored. This keeps plaintext credentials out of the SQLite file; it is not
a substitute for a real secret manager before anything ships.
"""

from cryptography.fernet import Fernet

from app.core.config import settings


def _load_key() -> bytes:
    path = settings.secret_key_path
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        path.write_bytes(key)
        path.chmod(0o600)
        return key
    return path.read_bytes()


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _cipher().decrypt(token.encode()).decode()
