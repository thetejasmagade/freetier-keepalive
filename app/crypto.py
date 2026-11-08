import base64
import hashlib

from cryptography.fernet import Fernet

from .config import settings


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def encrypt(text: str) -> bytes:
    return _fernet().encrypt(text.encode())


def decrypt(token: bytes) -> str:
    return _fernet().decrypt(bytes(token)).decode()
