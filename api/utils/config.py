import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def get_config_value(key, default=None):
    """
    Get config value from SystemConfiguration first, then settings.py.
    Gracefully falls back to settings.py if database table is not available
    or key is not set.

    Secret values (provider API keys) are stored encrypted and decrypted here.
    One that can't be decrypted - the encryption key changed - falls back to
    the settings.py value and is logged, rather than being handed to a
    provider as if the ciphertext were the key.
    """
    from api.models import SystemConfiguration
    from api.utils.secrets import SecretDecryptionError, decrypt_value, is_secret_key

    try:
        config = SystemConfiguration.objects.get(key=key)
        value = config.parsed_value()
    except Exception:
        # Fall back to settings.py on DoesNotExist, DatabaseError, or any other issue
        return getattr(settings, key, default)

    if is_secret_key(key) and isinstance(value, str):
        try:
            return decrypt_value(value)
        except SecretDecryptionError:
            logger.error(
                "Stored %s could not be decrypted (CONFIG_ENCRYPTION_KEY or SECRET_KEY changed?) - "
                "using the environment value. Re-enter it in the admin Configuration tab.",
                key,
            )
            return getattr(settings, key, default)
    return value
