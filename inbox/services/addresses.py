"""The two kinds of address mail can reach us on, and telling them apart."""

import re
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from api.utils.config import get_config_value
from inbox.models import ReplyAddress, TrackingAddress

# No 0/o, 1/l/i: people read these off a screen and type them into Gmail.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_TOKEN_LENGTH = 16

_ADDRESS = re.compile(r"^(track|reply)-([a-z0-9]{8,32})@(.+)$")


def _new_token():
    return "".join(secrets.choice(_ALPHABET) for _ in range(_TOKEN_LENGTH))


def receiving_live():
    """Whether mail to our addresses is actually being received. Off until
    Cloudflare Email Routing is set up and verified end to end, because
    handing out an address that doesn't receive would lose recruiter mail."""
    return bool(get_config_value("INBOUND_EMAIL_ENABLED", settings.INBOUND_EMAIL_ENABLED))


def inbound_domain():
    return settings.INBOUND_EMAIL_DOMAIN.lower()


def tracking_address_for(user):
    """The user's tracking address row, created on first use."""
    existing = TrackingAddress.objects.filter(user=user).first()
    if existing:
        return existing
    for _ in range(5):
        try:
            with transaction.atomic():
                return TrackingAddress.objects.create(user=user, token=_new_token())
        except IntegrityError:
            existing = TrackingAddress.objects.filter(user=user).first()
            if existing:
                return existing
    raise RuntimeError("Could not allocate a tracking address.")


def rotate_tracking_address(user):
    address = tracking_address_for(user)
    address.token = _new_token()
    address.rotated_at = timezone.now()
    address.forwarding_confirmation_code = ""
    address.forwarding_confirmation_link = ""
    address.forwarding_confirmation_received_at = None
    address.first_email_received_at = None
    address.save()
    return address


def reply_address_for(application):
    existing = ReplyAddress.objects.filter(application=application).first()
    if existing:
        return existing
    for _ in range(5):
        try:
            with transaction.atomic():
                return ReplyAddress.objects.create(application=application, token=_new_token())
        except IntegrityError:
            existing = ReplyAddress.objects.filter(application=application).first()
            if existing:
                return existing
    raise RuntimeError("Could not allocate a reply address.")


def email_for(kind, token):
    return f"{kind}-{token}@{inbound_domain()}"


def resolve_recipient(address):
    """(route, user, application) for an address we own, or None.

    Only exact tokens on our own domain resolve; anything else is unknown
    and is rejected at the mail server, so mail to a guessed address bounces
    rather than being silently collected.
    """
    match = _ADDRESS.match((address or "").strip().lower())
    if not match or match.group(3) != inbound_domain():
        return None
    kind, token = match.group(1), match.group(2)
    if kind == "track":
        row = TrackingAddress.objects.select_related("user").filter(token=token).first()
        return ("tracking", row.user, None) if row else None
    row = (
        ReplyAddress.objects.select_related("application__user", "application__curation")
        .filter(token=token)
        .first()
    )
    return ("reply", row.application.user, row.application) if row else None
