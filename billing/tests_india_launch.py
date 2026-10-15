"""India launch: rupee checkout through Razorpay only, Stripe switched off,
and every Razorpay credential editable from the admin panel.
"""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.models import SystemConfiguration
from api.utils.secrets import encrypt_value
from authentication.models import User
from billing.plans import (
    available_currencies,
    customer_currency,
    default_currency,
    find_plan_by_razorpay_plan_id,
    get_price,
)
from billing.services.razorpay_service import RazorpayNotConfiguredError, RazorpayService
from billing.services.stripe_service import StripeNotConfiguredError, StripeService

PLANS_URL = "/api/billing/plans/"
OPTIONS_URL = "/api/billing/payment-options/"
CHECKOUT_URL = "/api/billing/checkout/"


def _store(key, value, secret=False, value_type="string"):
    SystemConfiguration.objects.update_or_create(
        key=key,
        defaults={
            "value": encrypt_value(value) if secret else value,
            "value_type": value_type,
            "category": "payments",
        },
    )


@override_settings(STRIPE_ENABLED=False)
class StripeSwitchedOffTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_only_rupees_are_offered(self):
        self.assertEqual(available_currencies(), ["INR"])
        self.assertEqual(default_currency(), "INR")
        response = self.client.get(OPTIONS_URL)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["currencies"], ["INR"])
        self.assertEqual(body["default_currency"], "INR")
        self.assertFalse(body["launch_offer"]["active"])

    def test_pricing_page_quotes_rupees_even_if_euros_are_asked_for(self):
        for asked in ("EUR", "GBP", None, "nonsense"):
            response = self.client.get(PLANS_URL, {"currency": asked} if asked else {})
            self.assertEqual(response.status_code, 200)
            paid = [p for p in response.json() if p["monthly"]]
            self.assertTrue(paid)
            self.assertTrue(all(p["currency"] == "INR" for p in paid), asked)

    def test_euro_checkout_is_refused_before_any_processor_is_called(self):
        user = User.objects.create_user(
            email="eur-buyer@example.test", password="x-Strong-pass-1", is_active=True
        )
        self.client.force_authenticate(user)
        response = self.client.post(
            CHECKOUT_URL, {"plan_id": "pro", "currency": "EUR", "billing_period": "monthly"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "CURRENCY_UNAVAILABLE")

    @override_settings(STRIPE_SECRET_KEY="sk_test_123")
    def test_stripe_service_acts_unconfigured_even_with_a_key(self):
        with self.assertRaises(StripeNotConfiguredError):
            StripeService._require_configured()

    def test_admin_switch_turns_stripe_back_on_without_a_redeploy(self):
        _store("STRIPE_ENABLED", "true", value_type="boolean")
        self.assertEqual(available_currencies(), ["EUR", "GBP", "INR"])
        self.assertEqual(customer_currency("GBP"), "GBP")
        self.assertEqual(default_currency(), "EUR")


@override_settings(STRIPE_ENABLED=True)
class StripeSwitchedOnTests(TestCase):
    def test_all_currencies_are_offered(self):
        response = APIClient().get(OPTIONS_URL)
        self.assertEqual(response.json()["currencies"], ["EUR", "GBP", "INR"])
        self.assertEqual(response.json()["default_currency"], "EUR")


@override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="", RAZORPAY_WEBHOOK_SECRET="")
class RazorpayKeysFromAdminTests(TestCase):
    def test_unconfigured_until_keys_are_saved(self):
        self.assertFalse(RazorpayService.configured())
        with self.assertRaises(RazorpayNotConfiguredError):
            RazorpayService._require_configured()

    def test_keys_saved_in_admin_are_used_and_the_secret_is_encrypted(self):
        _store("RAZORPAY_KEY_ID", "rzp_test_public")
        _store("RAZORPAY_KEY_SECRET", "rzp_test_secret", secret=True)
        self.assertTrue(RazorpayService.configured())
        stored = SystemConfiguration.objects.get(key="RAZORPAY_KEY_SECRET").value
        self.assertNotIn("rzp_test_secret", stored)

    def test_admin_webhook_secret_verifies_signatures(self):
        import hashlib
        import hmac

        _store("RAZORPAY_WEBHOOK_SECRET", "whsec_admin", secret=True)
        body = b'{"event":"subscription.charged"}'
        good = hmac.new(b"whsec_admin", body, hashlib.sha256).hexdigest()
        self.assertTrue(RazorpayService.verify_webhook_signature(body, good))
        self.assertFalse(RazorpayService.verify_webhook_signature(body, "0" * 64))

    def test_no_webhook_secret_rejects_everything(self):
        self.assertFalse(RazorpayService.verify_webhook_signature(b"{}", "abc"))

    def test_keys_can_be_saved_through_the_admin_panel(self):
        admin = User.objects.create_user(
            email="pay-admin@example.test", password="x-Strong-pass-1", is_staff=True, is_active=True
        )
        client = APIClient()
        client.force_authenticate(admin)
        response = client.put(
            "/api/admin/config/",
            [
                {"key": "RAZORPAY_KEY_ID", "value": "rzp_live_abc"},
                {"key": "RAZORPAY_KEY_SECRET", "value": "super-secret"},
                {"key": "RAZORPAY_PLAN_ID_PRO_INR_MONTHLY", "value": "plan_admin_pro_m"},
            ],
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = {r["key"]: r for r in response.json()}
        self.assertEqual(rows["RAZORPAY_KEY_SECRET"]["value"], "********")
        self.assertEqual(rows["RAZORPAY_KEY_SECRET"]["category"], "payments")
        self.assertTrue(RazorpayService.configured())


class RazorpayPlanIdsFromAdminTests(TestCase):
    def test_plan_id_saved_in_admin_makes_the_price_purchasable(self):
        self.assertFalse(get_price("pro", "INR", "monthly")["processor_price_id"])
        _store("RAZORPAY_PLAN_ID_PRO_INR_MONTHLY", "plan_admin_pro_m")
        self.assertEqual(get_price("pro", "INR", "monthly")["processor_price_id"], "plan_admin_pro_m")
        self.assertEqual(find_plan_by_razorpay_plan_id("plan_admin_pro_m"), ("pro", "INR", "monthly"))


class FreePlanQuotasMatchAdminTests(TestCase):
    def _free_features(self):
        response = APIClient().get(PLANS_URL)
        return next(p for p in response.json() if p["id"] == "free")["features"]

    def test_pricing_page_quotes_the_enforced_free_limits(self):
        _store("FREE_TAILORED_RESUMES_PER_MONTH", "15", value_type="integer")
        _store("FREE_AI_CALLS_PER_MONTH", "15", value_type="integer")
        features = self._free_features()
        self.assertIn("15 job-specific tailored resumes / month", features)
        self.assertIn("15 AI-assisted actions / month", features)
        self.assertNotIn("2 job-specific tailored resumes / month", features)
