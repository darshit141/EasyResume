"""Free launch offer: every feature free for everyone, metered per window.

While the offer is on, an account without a paid subscription gets the paid
feature set (Word export, version history, layout customisation) at no
charge, and checkout is closed. What keeps the AI bill bounded is a short
rolling window rather than a monthly allowance: by default 3 tailored
resumes and 15 AI-backed actions per 12 hours.

Everything here is read per call through the admin Configuration tab
(``api.utils.config.get_config_value``), falling back to ``settings``, so
the offer can be switched on or off, and its limits retuned, without a
code change or a redeploy. Existing paid subscribers are untouched: their
own plan always wins.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone


def _config(key, default):
    from api.utils.config import get_config_value

    return get_config_value(key, getattr(settings, key, default))


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "t", "y")


def _as_positive_int(value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def offer_ends_at():
    """The moment the offer switches itself off, or None for no end date.

    LAUNCH_FREE_UNTIL is a plain date (YYYY-MM-DD) and means "free through
    the end of that day" in the server's time zone. Anything unparseable is
    treated as no end date rather than ending the offer by accident.
    """
    raw = (str(_config("LAUNCH_FREE_UNTIL", "") or "")).strip()
    if not raw:
        return None
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None
    end = datetime.combine(day + timedelta(days=1), time.min)
    return timezone.make_aware(end, timezone.get_current_timezone())


def is_active(now=None):
    if not _as_bool(_config("LAUNCH_FREE_ACCESS", False)):
        return False
    ends_at = offer_ends_at()
    return ends_at is None or (now or timezone.now()) < ends_at


def window_hours():
    return _as_positive_int(_config("LAUNCH_QUOTA_WINDOW_HOURS", 12), 12)


def tailored_resumes_per_window():
    return _as_positive_int(_config("LAUNCH_TAILORED_RESUMES_PER_WINDOW", 3), 3)


def ai_calls_per_window():
    return _as_positive_int(_config("LAUNCH_AI_CALLS_PER_WINDOW", 15), 15)


def shows_ads():
    return _as_bool(_config("LAUNCH_SHOW_ADS", True))


def summary():
    """What the public plans endpoint tells the pricing page."""
    active = is_active()
    ends_at = offer_ends_at() if active else None
    return {
        "active": active,
        "ends_at": ends_at.isoformat() if ends_at else None,
        "window_hours": window_hours(),
        "tailored_resumes_per_window": tailored_resumes_per_window(),
        "ai_calls_per_window": ai_calls_per_window(),
    }
