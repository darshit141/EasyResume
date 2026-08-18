from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from billing.models import Coupon, CouponRedemption, Subscription


class CouponError(Exception):
    code = "COUPON_INVALID"
    message = "This code isn't valid."

    def __init__(self, message=None, code=None):
        self.message = message or self.message
        self.code = code or self.code
        super().__init__(self.message)


class CouponNotFoundError(CouponError):
    code = "COUPON_NOT_FOUND"
    message = "We couldn't find that code."


class CouponExhaustedError(CouponError):
    code = "COUPON_EXHAUSTED"
    message = "This code has already been fully redeemed."


class CouponAlreadyRedeemedError(CouponError):
    code = "COUPON_ALREADY_REDEEMED"
    message = "You've already redeemed this code."


class CouponService:
    @staticmethod
    def redeem(user, code):
        """Redeem `code` for `user`, granting Pro access.

        Every check happens inside one atomic block guarded by
        select_for_update, so two simultaneous redemptions of the last slot
        on a multi-use code can't both succeed — the DB row lock forces the
        second request to wait and then see the first one's redemption.
        """
        normalized = (code or "").strip().upper()
        if not normalized:
            raise CouponNotFoundError()

        with transaction.atomic():
            try:
                coupon = Coupon.objects.select_for_update().get(code=normalized, is_active=True)
            except Coupon.DoesNotExist as exc:
                raise CouponNotFoundError() from exc

            if coupon.redemptions_used >= coupon.max_redemptions:
                raise CouponExhaustedError()

            try:
                CouponRedemption.objects.create(coupon=coupon, user=user)
            except IntegrityError as exc:
                # unique_together(coupon, user) — this user already has a row.
                raise CouponAlreadyRedeemedError() from exc

            subscription, _ = Subscription.objects.get_or_create(user=user)
            subscription.plan_id = "pro"
            subscription.status = "active"
            subscription.current_period_end = (
                timezone.now() + timedelta(days=coupon.duration_days)
                if coupon.duration_days
                else None
            )
            subscription.save(update_fields=["plan_id", "status", "current_period_end", "updated_at"])

        return subscription
