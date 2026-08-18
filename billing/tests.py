from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import User
from billing.models import Subscription
from billing.plans import PLANS_BY_ID
from billing.services.subscription_sync_service import SubscriptionSyncService


class PlanListAPITests(TestCase):
    def test_plans_are_public_and_list_both_tiers(self):
        client = APIClient()
        response = client.get("/api/billing/plans/")

        self.assertEqual(response.status_code, 200)
        ids = {plan["id"] for plan in response.data}
        self.assertEqual(ids, {"free", "pro"})

    def test_plan_without_a_configured_price_is_not_purchasable(self):
        client = APIClient()
        response = client.get("/api/billing/plans/")

        for plan in response.data:
            if plan["id"] == "pro":
                self.assertFalse(plan["purchasable"])


class SubscriptionStatusAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-billing@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_authentication(self):
        client = APIClient()
        response = client.get("/api/billing/subscription/")
        self.assertEqual(response.status_code, 401)

    def test_new_user_has_no_subscription_by_default(self):
        response = self.client.get("/api/billing/subscription/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "none")
        self.assertFalse(response.data["is_active"])

    def test_active_subscription_is_reported_correctly(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")

        response = self.client.get("/api/billing/subscription/")

        self.assertEqual(response.data["plan_id"], "pro")
        self.assertTrue(response.data["is_active"])


class CreateCheckoutSessionAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-checkout@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_authentication(self):
        client = APIClient()
        response = client.post("/api/billing/checkout/", {"plan_id": "pro"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_invalid_plan_id_is_rejected(self):
        response = self.client.post("/api/billing/checkout/", {"plan_id": "enterprise"}, format="json")
        self.assertEqual(response.status_code, 400)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_returns_503_when_stripe_is_not_configured(self):
        response = self.client.post("/api/billing/checkout/", {"plan_id": "pro"}, format="json")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "STRIPE_NOT_CONFIGURED")

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_returns_400_when_plan_has_no_stripe_price_configured(self):
        # "free" plan never has a stripe_price_id (it's free) — a real
        # not-purchasable scenario without needing to monkeypatch the catalog.
        response = self.client.post("/api/billing/checkout/", {"plan_id": "free"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PLAN_NOT_PURCHASABLE")

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", FRONTEND_URL="http://localhost:5173")
    def test_creates_a_real_checkout_session_when_fully_configured(self):
        with patch.dict(PLANS_BY_ID["pro"], {"stripe_price_id": "price_test_123"}):
            with patch("billing.services.stripe_service.stripe.Customer.create") as mock_customer, \
                 patch("billing.services.stripe_service.stripe.checkout.Session.create") as mock_session:
                mock_customer.return_value = MagicMock(id="cus_test_123")
                mock_session.return_value = MagicMock(url="https://checkout.stripe.com/test-session")

                response = self.client.post("/api/billing/checkout/", {"plan_id": "pro"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["checkout_url"], "https://checkout.stripe.com/test-session")
        mock_session.assert_called_once()
        call_kwargs = mock_session.call_args.kwargs
        self.assertEqual(call_kwargs["mode"], "subscription")
        self.assertEqual(call_kwargs["metadata"]["user_id"], str(self.user.id))

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.stripe_customer_id, "cus_test_123")

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_reuses_existing_stripe_customer_on_a_second_checkout(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_existing")

        with patch.dict(PLANS_BY_ID["pro"], {"stripe_price_id": "price_test_123"}):
            with patch("billing.services.stripe_service.stripe.Customer.create") as mock_customer, \
                 patch("billing.services.stripe_service.stripe.checkout.Session.create") as mock_session:
                mock_session.return_value = MagicMock(url="https://checkout.stripe.com/test-session")

                self.client.post("/api/billing/checkout/", {"plan_id": "pro"}, format="json")

        mock_customer.assert_not_called()


class CreatePortalSessionAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-portal@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_returns_400_when_user_has_no_stripe_customer_yet(self):
        response = self.client.post("/api/billing/portal/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "NO_STRIPE_CUSTOMER")

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", FRONTEND_URL="http://localhost:5173")
    def test_creates_a_portal_session_for_an_existing_customer(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_existing")

        with patch("billing.services.stripe_service.stripe.billing_portal.Session.create") as mock_portal:
            mock_portal.return_value = MagicMock(url="https://billing.stripe.com/test-portal")
            response = self.client.post("/api/billing/portal/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["portal_url"], "https://billing.stripe.com/test-portal")


class StripeWebhookAPITests(TestCase):
    @override_settings(STRIPE_SECRET_KEY="", STRIPE_WEBHOOK_SECRET="")
    def test_returns_503_when_not_configured(self):
        client = APIClient()
        response = client.post("/api/billing/webhook/", data=b"{}", content_type="application/json")
        self.assertEqual(response.status_code, 503)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_fake")
    def test_rejects_an_invalid_signature(self):
        client = APIClient()
        response = client.post(
            "/api/billing/webhook/", data=b"{}", content_type="application/json",
            HTTP_STRIPE_SIGNATURE="bad-signature",
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_fake")
    def test_valid_event_is_applied_and_returns_200(self):
        client = APIClient()
        fake_event = {"type": "checkout.session.completed", "data": {"object": {}}}
        with patch("billing.services.stripe_service.stripe.Webhook.construct_event", return_value=fake_event), \
             patch("billing.views.billing_view.SubscriptionSyncService.apply_event") as mock_apply:
            response = client.post(
                "/api/billing/webhook/", data=b"{}", content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid-looking-signature",
            )

        self.assertEqual(response.status_code, 200)
        mock_apply.assert_called_once_with(fake_event)


class SubscriptionSyncServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-sync@example.com", phone="1234567890",
            password="safe-password",
        )

    def test_checkout_completed_activates_the_matching_local_subscription(self):
        Subscription.objects.create(user=self.user, status="none")

        event = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "customer": "cus_123",
                "subscription": "sub_123",
                "metadata": {"user_id": str(self.user.id), "plan_id": "pro"},
            }},
        }
        SubscriptionSyncService.apply_event(event)

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.plan_id, "pro")
        self.assertEqual(subscription.stripe_customer_id, "cus_123")
        self.assertEqual(subscription.stripe_subscription_id, "sub_123")

    def test_checkout_completed_with_unknown_user_id_does_not_raise(self):
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"customer": "cus_123", "metadata": {"user_id": "999999"}}},
        }
        SubscriptionSyncService.apply_event(event)  # should not raise

    def test_subscription_updated_syncs_status_by_customer_id(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", status="active")

        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {
                "id": "sub_123", "customer": "cus_123", "status": "past_due",
                "current_period_end": 1234567890,
            }},
        }
        SubscriptionSyncService.apply_event(event)

        subscription = Subscription.objects.get(stripe_customer_id="cus_123")
        self.assertEqual(subscription.status, "past_due")
        self.assertIsNotNone(subscription.current_period_end)

    def test_trialing_status_is_treated_as_active(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", status="none")

        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_123", "customer": "cus_123", "status": "trialing"}},
        }
        SubscriptionSyncService.apply_event(event)

        self.assertEqual(Subscription.objects.get(stripe_customer_id="cus_123").status, "active")

    def test_subscription_deleted_marks_canceled(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", status="active")

        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_123"}},
        }
        SubscriptionSyncService.apply_event(event)

        self.assertEqual(Subscription.objects.get(stripe_customer_id="cus_123").status, "canceled")

    def test_unhandled_event_type_is_ignored_without_error(self):
        event = {"type": "invoice.paid", "data": {"object": {}}}
        SubscriptionSyncService.apply_event(event)  # should not raise
