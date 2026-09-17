"""Encryption at rest for secret SystemConfiguration values.

Provider API keys are editable from the admin Configuration tab, so a revoked
or expired token can be replaced without a redeploy. They are stored encrypted
(Fernet: AES-128-CBC with an HMAC-SHA256 integrity check), so a database dump
or backup is not a list of working credentials.

The encryption key is CONFIG_ENCRYPTION_KEY when set, otherwise one derived
from SECRET_KEY. Set CONFIG_ENCRYPTION_KEY in production: without it, rotating
SECRET_KEY makes every stored key unreadable. That failure is contained -
get_config_value logs it and falls back to the environment's key - but the
keys then have to be entered again.

The stored format, ``enc:v1:<token>``, is written by a data migration, so v1
must stay decryptable for as long as that migration exists.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIX = "enc:v1:"


class SecretDecryptionError(Exception):
    """A stored secret could not be decrypted with the current key."""


def is_secret_key(key):
    """Which configuration keys hold credentials."""
    return bool(key) and (key.endswith("_API_KEY") or "SECRET" in key)


# Each kind of stored secret derives its own key from the same material.
# Without this separation a ciphertext from one table would decrypt in the
# other, so a user who could get a value written into SystemConfiguration
# could have it read back as somebody's personal provider key, and vice
# versa. The strings are part of the stored format and must never change.
DOMAIN_SYSTEM_CONFIGURATION = "system-configuration"
DOMAIN_USER_PROVIDER_KEY = "user-provider-key"


def _fernet(domain=DOMAIN_SYSTEM_CONFIGURATION):
    material = getattr(settings, "CONFIG_ENCRYPTION_KEY", "") or getattr(settings, "SECRET_KEY", "") or ""
    if not material:
        raise SecretDecryptionError("Neither CONFIG_ENCRYPTION_KEY nor SECRET_KEY is configured.")
    # Domain-separated, so the derived key is never the raw SECRET_KEY reused
    # for a second purpose.
    digest = hashlib.sha256(f"intelliresume:{domain}:{material}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_value(plaintext, domain=DOMAIN_SYSTEM_CONFIGURATION):
    """Ciphertext for storage. Already-encrypted input is returned unchanged,
    so saving a row twice can never encrypt it twice."""
    if not plaintext:
        return ""
    if is_encrypted(plaintext):
        return plaintext
    return PREFIX + _fernet(domain).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_value(stored, domain=DOMAIN_SYSTEM_CONFIGURATION):
    """Plaintext for a stored value.

    A value without the prefix is returned as it is: rows written before
    encryption existed stay readable until the migration encrypts them.
    """
    if not is_encrypted(stored):
        return stored
    try:
        return _fernet(domain).decrypt(stored[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptionError("Stored secret could not be decrypted.") from exc
