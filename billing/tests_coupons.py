from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import User
from billing.models import Coupon, CouponRedemption, Subscription
from billing.services.coupon_service import (
    CouponAlreadyRedeemedError,
    CouponExhaustedError,
    CouponNotFoundError,
    CouponService,
)


def _make_user(email):
    return User.objects.create_user(
        name="Jane Doe", email=email, phone=f"9{abs(hash(email)) % 1000000000:09d}",
        password="safe-password",
    )


class CouponModelTests(TestCase):
    def test_code_is_normalized_to_uppercase_on_save(self):
        coupon = Coupon.objects.create(code=" launch2026 ")
        self.assertEqual(coupon.code, "LAUNCH2026")

    def test_redemptions_remaining_reflects_actual_redemptions(self):
        coupon = Coupon.objects.create(code="BETA", max_redemptions=3)
        CouponRedemption.objects.create(coupon=coupon, user=_make_user("a@example.com"))

        self.assertEqual(coupon.redemptions_used, 1)
        self.assertEqual(coupon.redemptions_remaining, 2)


class CouponServiceTests(TestCase):
    def setUp(self):
        self.user = _make_user("redeemer@example.com")

    def test_redeeming_a_valid_code_grants_active_pro(self):
        Coupon.objects.create(code="WELCOME")

        subscription = CouponService.redeem(self.user, "welcome")  # lowercase, must still match

        self.assertEqual(subscription.plan_id, "pro")
        self.assertTrue(subscription.is_active)
        self.assertIsNone(subscription.current_period_end)

    def test_redeeming_creates_an_audit_row(self):
        Coupon.objects.create(code="WELCOME")
        CouponService.redeem(self.user, "WELCOME")

        self.assertTrue(CouponRedemption.objects.filter(user=self.user, coupon__code="WELCOME").exists())

    def test_unknown_code_raises_not_found(self):
        with self.assertRaises(CouponNotFoundError):
            CouponService.redeem(self.user, "DOES-NOT-EXIST")

    def test_inactive_code_is_treated_as_not_found(self):
        Coupon.objects.create(code="DISABLED", is_active=False)
        with self.assertRaises(CouponNotFoundError):
            CouponService.redeem(self.user, "DISABLED")

    def test_single_use_code_cannot_be_redeemed_by_a_second_user(self):
        coupon = Coupon.objects.create(code="ONCE", max_redemptions=1)
        CouponService.redeem(self.user, "ONCE")

        other = _make_user("second@example.com")
        with self.assertRaises(CouponExhaustedError):
            CouponService.redeem(other, "ONCE")

        # Only the first redemption ever took effect.
        self.assertEqual(coupon.redemptions.count(), 1)

    def test_same_user_cannot_redeem_the_same_code_twice(self):
        Coupon.objects.create(code="ONCE", max_redemptions=5)
        CouponService.redeem(self.user, "ONCE")

        with self.assertRaises(CouponAlreadyRedeemedError):
            CouponService.redeem(self.user, "ONCE")

    def test_multi_use_code_allows_a_configured_number_of_distinct_users(self):
        Coupon.objects.create(code="BETA-COHORT", max_redemptions=3)
        for i in range(3):
            CouponService.redeem(_make_user(f"beta{i}@example.com"), "BETA-COHORT")

        with self.assertRaises(CouponExhaustedError):
            CouponService.redeem(_make_user("beta4@example.com"), "BETA-COHORT")

    def test_time_limited_code_sets_an_expiry(self):
        Coupon.objects.create(code="TRIAL14", duration_days=14)

        subscription = CouponService.redeem(self.user, "TRIAL14")

        self.assertIsNotNone(subscription.current_period_end)
        self.assertTrue(subscription.is_active)
        expected = timezone.now() + timedelta(days=14)
        self.assertAlmostEqual(subscription.current_period_end, expected, delta=timedelta(seconds=10))

    def test_expired_trial_grant_is_no_longer_active(self):
        Coupon.objects.create(code="TRIAL14", duration_days=14)
        subscription = CouponService.redeem(self.user, "TRIAL14")

        subscription.current_period_end = timezone.now() - timedelta(days=1)
        subscription.save()

        self.assertFalse(Subscription.objects.get(user=self.user).is_active)

    def test_redeeming_upgrades_an_existing_free_subscription_row(self):
        Subscription.objects.create(user=self.user, plan_id="", status="none")
        Coupon.objects.create(code="WELCOME")

        CouponService.redeem(self.user, "WELCOME")

        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Subscription.objects.get(user=self.user).plan_id, "pro")


class CouponRedeemAPITests(TestCase):
    def setUp(self):
        self.user = _make_user("api-redeemer@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_redeeming_via_the_api_activates_pro(self):
        Coupon.objects.create(code="WELCOME")

        response = self.client.post("/api/billing/coupons/redeem/", {"code": "WELCOME"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["plan_id"], "pro")
        self.assertTrue(response.data["is_active"])

    def test_unknown_code_returns_404(self):
        response = self.client.post("/api/billing/coupons/redeem/", {"code": "NOPE"}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "COUPON_NOT_FOUND")

    def test_exhausted_code_returns_400(self):
        Coupon.objects.create(code="ONCE", max_redemptions=1)
        CouponService.redeem(_make_user("first@example.com"), "ONCE")

        response = self.client.post("/api/billing/coupons/redeem/", {"code": "ONCE"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "COUPON_EXHAUSTED")

    def test_requires_authentication(self):
        client = APIClient()
        response = client.post("/api/billing/coupons/redeem/", {"code": "WELCOME"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_entitlements_reflect_coupon_granted_pro(self):
        Coupon.objects.create(code="WELCOME")
        self.client.post("/api/billing/coupons/redeem/", {"code": "WELCOME"}, format="json")

        entitlements = self.client.get("/api/billing/entitlements/")

        self.assertEqual(entitlements.data["plan_id"], "pro")
        self.assertIsNone(entitlements.data["ai_calls_limit"])
        self.assertFalse(entitlements.data["shows_ads"])
