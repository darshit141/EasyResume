"""Earned bonus credits — the ledger and the rules around it.

A credit is one extra AI-backed action *and* one extra tailored resume for
the free tier, live only while unexpired. All the policy — how many an ad is
worth, the per-day cap, how long a credit lasts — is operator-configurable
through ``SystemConfiguration`` so it can be tuned against real usage
without a deploy.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from api.utils.config import get_config_value
from billing.models import CreditGrant


def _int_config(key, default):
    try:
        return int(get_config_value(key, getattr(settings, key, default)))
    except (TypeError, ValueError):
        return default


class CreditService:
    @staticmethod
    def credits_per_rewarded_ad() -> int:
        return max(0, _int_config("REWARDED_AD_CREDITS_PER_VIEW", 1))

    @staticmethod
    def rewarded_ads_daily_cap() -> int:
        return max(0, _int_config("REWARDED_AD_MAX_PER_DAY", 3))

    @staticmethod
    def credit_ttl() -> timedelta:
        return timedelta(days=max(1, _int_config("REWARDED_AD_CREDIT_TTL_DAYS", 7)))

    @staticmethod
    def active_balance(user) -> int:
        """Total unexpired earned credits for this user."""
        now = timezone.now()
        total = CreditGrant.objects.filter(user=user, expires_at__gt=now).aggregate(
            total=Sum("amount")
        )["total"]
        return int(total or 0)

    @staticmethod
    def next_expiry(user):
        now = timezone.now()
        grant = (
            CreditGrant.objects.filter(user=user, expires_at__gt=now)
            .order_by("expires_at")
            .first()
        )
        return grant.expires_at if grant else None

    @classmethod
    def rewarded_ads_watched_today(cls, user) -> int:
        start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return CreditGrant.objects.filter(
            user=user,
            reason=CreditGrant.REASON_REWARDED_AD,
            granted_at__gte=start,
        ).count()

    @classmethod
    def can_watch_rewarded_ad(cls, user) -> bool:
        cap = cls.rewarded_ads_daily_cap()
        if cap <= 0:
            return False
        return cls.rewarded_ads_watched_today(user) < cap

    @classmethod
    def grant_rewarded_ad_credit(cls, user, *, amount, provider, external_id):
        """Write a rewarded-ad grant. Idempotent on ``external_id`` — a
        retried callback returns the existing grant instead of stacking a
        second one. Enforces the per-day cap. Returns ``(grant, created)``.
        """
        if not external_id:
            raise ValueError("A rewarded-ad grant needs the provider's transaction id.")

        existing = CreditGrant.objects.filter(
            reason=CreditGrant.REASON_REWARDED_AD, external_id=external_id
        ).first()
        if existing is not None:
            return existing, False

        credited = amount if amount and amount > 0 else cls.credits_per_rewarded_ad()
        if credited <= 0:
            raise ValueError("Rewarded ads are configured to grant zero credits.")

        with transaction.atomic():
            # Re-check the cap inside the transaction against a locked view
            # of today's rows, so two callbacks racing can't both slip past.
            start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today = CreditGrant.objects.select_for_update().filter(
                user=user,
                reason=CreditGrant.REASON_REWARDED_AD,
                granted_at__gte=start,
            ).count()
            if today >= cls.rewarded_ads_daily_cap():
                raise RewardedAdCapReachedError(
                    "This account has reached today's rewarded-ad limit."
                )
            try:
                grant = CreditGrant.objects.create(
                    user=user,
                    amount=credited,
                    reason=CreditGrant.REASON_REWARDED_AD,
                    provider=provider or "",
                    external_id=external_id,
                    expires_at=timezone.now() + cls.credit_ttl(),
                )
            except IntegrityError:
                # Lost a race on the unique (reason, external_id) constraint.
                return (
                    CreditGrant.objects.get(
                        reason=CreditGrant.REASON_REWARDED_AD, external_id=external_id
                    ),
                    False,
                )
        return grant, True

    @classmethod
    def grant(cls, user, *, amount, reason, ttl=None):
        """A non-transactional grant for the reasons that have no external
        transaction (referral, profile completion, streak, admin)."""
        return CreditGrant.objects.create(
            user=user,
            amount=amount,
            reason=reason,
            expires_at=timezone.now() + (ttl or cls.credit_ttl()),
        )


class RewardedAdCapReachedError(Exception):
    """Today's per-user rewarded-ad limit is already used up."""
