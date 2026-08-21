"""Permanently deletes a user's account and everything derived from it —
GDPR Article 17 (right to erasure). See GDPR_DATA_FLOW.md for the full
data inventory this is expected to cover.

Almost everything cascades automatically through `on_delete=CASCADE`
foreign keys (resumes, resume versions, job curations and their versions,
AI usage logs, AI jobs, subscription, coupon redemptions, OTPs, policy
acceptances, cookie consent) the moment the User row is deleted — see each
model's own FK definition for the specific cascade. This service handles
the three things a cascading DELETE cannot: canceling a live external
Stripe subscription, removing uploaded files from storage (FileField
never deletes the underlying file automatically), and the separate
email-only newsletter subscription.
"""

import logging

from django.core.files.storage import default_storage
from django.db import transaction

logger = logging.getLogger(__name__)


class AccountDeletionService:
    @staticmethod
    def delete_account(user):
        from resumes.models import Resume

        file_names = list(
            Resume.objects.filter(user=user).exclude(file="").values_list("file", flat=True)
        )

        AccountDeletionService._cancel_stripe_subscription(user)
        AccountDeletionService._remove_newsletter_subscription(user)

        user_id = user.id
        email = user.email
        with transaction.atomic():
            user.delete()

        for file_name in file_names:
            try:
                default_storage.delete(file_name)
            except Exception:
                logger.warning(
                    "Could not remove file %s from storage during account deletion (user_id=%s).",
                    file_name, user_id,
                )

        logger.info("Account permanently deleted user_id=%s email=%s", user_id, email)

    @staticmethod
    def _cancel_stripe_subscription(user):
        from billing.models import Subscription

        subscription = Subscription.objects.filter(user=user).first()
        if not subscription or not subscription.stripe_subscription_id:
            return
        if subscription.status not in ("active", "past_due", "incomplete"):
            return

        try:
            from billing.services.stripe_service import StripeService

            StripeService.cancel_subscription(subscription.stripe_subscription_id)
        except Exception:
            # Never block account deletion on Stripe being unreachable/
            # unconfigured — the local records are still fully erased;
            # worst case a stale Stripe subscription needs manual
            # cancellation, which is recoverable, unlike a botched deletion.
            logger.exception(
                "Failed to cancel Stripe subscription %s during account deletion (user_id=%s).",
                subscription.stripe_subscription_id, user.id,
            )

    @staticmethod
    def _remove_newsletter_subscription(user):
        from blog.models import NewsletterSubscriber

        NewsletterSubscriber.objects.filter(email__iexact=user.email).delete()
