"""Simple encryption utilities for API key storage.

Uses Fernet (AES-128-CBC + HMAC) from the cryptography library.
The secret key is generated once and stored in a .secret file.

Usage:
    from epub_translator.crypto import encrypt, decrypt
    encrypted = encrypt("sk-my-api-key")
    original = decrypt(encrypted)
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet

# .secret file is stored next to config.yaml (project root)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
_SECRET_FILE = os.path.join(_PROJECT_ROOT, ".secret")


def _get_or_create_key() -> bytes:
    """Load existing secret key or generate a new one."""
    if os.path.exists(_SECRET_FILE):
        with open(_SECRET_FILE, "rb") as f:
            return f.read().strip()

    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(_SECRET_FILE) or ".", exist_ok=True)
    with open(_SECRET_FILE, "wb") as f:
        f.write(key)
    return key


def encrypt(plain_text: str) -> str:
    """Encrypt a string and return 'enc:' prefixed base64 ciphertext."""
    if not plain_text:
        return ""
    f = Fernet(_get_or_create_key())
    return "enc:" + f.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt(cipher_text: str) -> str:
    """Decrypt an 'enc:' prefixed ciphertext. Returns original if not encrypted."""
    if not cipher_text or not cipher_text.startswith("enc:"):
        return cipher_text
    try:
        f = Fernet(_get_or_create_key())
        return f.decrypt(cipher_text[4:].encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def is_encrypted(value: str) -> bool:
    """Check if a value is encrypted."""
    return bool(value and value.startswith("enc:"))
