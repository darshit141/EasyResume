"""Site-wide ceiling on AI spend per day.

Per-account limits bound what one user can cost; they do nothing about how
many users there are. A launch spike or a wave of throwaway sign-ups
multiplies the bill with every account, so this caps the *total* number of
AI-backed actions served to unpaid accounts per calendar day, and emails
OPS_ALERT_EMAIL as usage approaches the cap and again when it is reached.

Paying subscribers and users on their own provider key are never stopped
by it (check_can_use_ai returns before reaching here for both): the first
are revenue, the second cost us nothing.

AI_GLOBAL_DAILY_CAP is read per call from the admin Configuration tab, so
it can be raised the moment it starts turning real users away. 0 = off.
"""

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail

from billing.models import AIUsageLog

logger = logging.getLogger(__name__)


def _config(key, default):
    from api.utils.config import get_config_value

    return get_config_value(key, getattr(settings, key, default))


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def daily_cap():
    return max(0, _int(_config("AI_GLOBAL_DAILY_CAP", 0), 0))


def alert_percent():
    value = _int(_config("AI_CAPACITY_ALERT_PERCENT", 80), 80)
    return value if 1 <= value <= 100 else 80


def _zone():
    try:
        return ZoneInfo(getattr(settings, "AI_CAPACITY_TIME_ZONE", "Asia/Kolkata"))
    except Exception:  # noqa: BLE001 - a bad zone name must not block AI
        return ZoneInfo("Asia/Kolkata")


def day_start(now=None):
    """Midnight today in the capacity time zone (India by default), so the
    cap resets when our users' day does, not at UTC midnight."""
    zone = _zone()
    local = (now or datetime.now(zone)).astimezone(zone)
    return datetime.combine(local.date(), time.min, tzinfo=zone)


def next_reset(now=None):
    return day_start(now) + timedelta(days=1)


def used_today(now=None):
    return AIUsageLog.objects.filter(created_at__gte=day_start(now)).count()


def is_reached(now=None):
    cap = daily_cap()
    return bool(cap) and used_today(now) >= cap


def maybe_alert(now=None):
    """After an AI action is recorded: email ops once a day when usage
    crosses the alert threshold, and once more when the cap is reached.
    Never raises - an alert must not fail the AI action it follows."""
    try:
        cap = daily_cap()
        if not cap:
            return
        used = used_today(now)
        day = day_start(now).date().isoformat()
        if used >= cap:
            level, headline = "full", f"AI capacity reached: {used} of {cap} actions today"
        elif used >= cap * alert_percent() / 100:
            level, headline = "warn", f"AI capacity at {round(used * 100 / cap)}%: {used} of {cap} today"
        else:
            return
        # cache.add is atomic: only the first process to cross a level sends.
        if not cache.add(f"ai-capacity-alert:{day}:{level}", True, timeout=26 * 3600):
            return
        send_mail(
            subject=f"[IntelliResume] {headline}",
            message=(
                f"{headline}.\n\n"
                + (
                    "Unpaid users are now being told to come back after midnight IST. "
                    if level == "full"
                    else "Unpaid users will be stopped when the cap is reached. "
                )
                + "Raise AI_GLOBAL_DAILY_CAP in the admin Configuration tab (Limits) "
                "if this is real demand rather than abuse."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.OPS_ALERT_EMAIL],
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("AI capacity alert failed.")
