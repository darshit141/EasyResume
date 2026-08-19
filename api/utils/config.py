from django.conf import settings


def get_config_value(key, default=None):
    """
    Get config value from SystemConfiguration first, then settings.py.
    Gracefully falls back to settings.py if database table is not available
    or key is not set.
    """
    from api.models import SystemConfiguration
    try:
        config = SystemConfiguration.objects.get(key=key)
        return config.parsed_value()
    except Exception:
        # Fall back to settings.py on DoesNotExist, DatabaseError, or any other issue
        return getattr(settings, key, default)
