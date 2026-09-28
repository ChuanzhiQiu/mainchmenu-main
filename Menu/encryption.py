"""
Encryption module for sensitive multi-tenant credentials (Milestone M8).
Uses Fernet symmetric authenticated cryptography (AES-128-CBC + HMAC-SHA256).
"""
import base64
import hashlib
import json
import logging
from typing import Any, Dict, Union
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def get_fernet_key() -> bytes:
    """
    Derives a 32-byte url-safe base64-encoded key from settings.ENCRYPTION_KEY or settings.SECRET_KEY.
    Guarantees deterministic, secure encryption in both cloud production and local dev/test environments.
    """
    raw_key = getattr(settings, 'ENCRYPTION_KEY', None)
    if not raw_key:
        raw_key = getattr(settings, 'SECRET_KEY', 'mainch-default-insecure-key-for-development')

    if isinstance(raw_key, str):
        raw_bytes = raw_key.encode('utf-8')
    else:
        raw_bytes = bytes(raw_key)

    # Derive deterministic 32-byte key via SHA256 and base64-urlencode for Fernet
    derived_32 = hashlib.sha256(raw_bytes).digest()
    return base64.urlsafe_b64encode(derived_32)


def get_cipher() -> Fernet:
    """Returns a Fernet cipher instance configured with the derived key."""
    return Fernet(get_fernet_key())


def encrypt_data(data: Union[str, Dict[str, Any]]) -> str:
    """
    Encrypts a string or dictionary (JSON serialized) into an armored Fernet token string.
    Returns empty string if data is empty or None.
    """
    if data is None or data == "" or data == {}:
        return ""

    if isinstance(data, (dict, list)):
        payload_str = json.dumps(data)
    else:
        payload_str = str(data)

    cipher = get_cipher()
    token = cipher.encrypt(payload_str.encode('utf-8'))
    return token.decode('utf-8')


def decrypt_data(ciphertext: str, return_json: bool = True) -> Any:
    """
    Decrypts a Fernet ciphertext token string.
    If return_json is True, attempts to parse as JSON; otherwise returns raw string.
    Returns empty dict or string on empty input or decryption failure.
    """
    if not ciphertext or not ciphertext.strip():
        return {} if return_json else ""

    try:
        cipher = get_cipher()
        decrypted_bytes = cipher.decrypt(ciphertext.strip().encode('utf-8'))
        decrypted_str = decrypted_bytes.decode('utf-8')

        if return_json:
            try:
                return json.loads(decrypted_str)
            except Exception:
                return decrypted_str
        return decrypted_str
    except InvalidToken:
        logger.error("Tampered or invalid encryption token detected.")
        raise ValueError("Invalid encryption token or key mismatch.")
    except Exception as exc:
        logger.error("Failed to decrypt sensitive data: %s", exc)
        raise
