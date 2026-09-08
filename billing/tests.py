from unittest.mock import MagicMock, patch

import stripe
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import User
from billing.models import Subscription
from billing.plans import PLANS_BY_ID, published_plans
from billing.services.subscription_sync_service import SubscriptionSyncService


class PlanListAPITests(TestCase):
    def test_plans_are_public_and_list_every_published_tier(self):
        client = APIClient()
        response = client.get("/api/billing/plans/")

        self.assertEqual(response.status_code, 200)
        ids = {plan["id"] for plan in response.data}
        # Asserted against the published catalog, not a hardcoded set: a
        # tier can be built and deployed before it is advertised, so this
        # should track what customers can actually see and buy.
        self.assertEqual(ids, {plan["id"] for plan in published_plans()})
        self.assertIn("free", ids)

    def test_plan_without_a_configured_price_is_not_purchasable(self):
        # Explicitly blank every Pro Price ID for this test rather than
        # relying on the ambient environment never having one configured —
        # a real STRIPE_PRICE_ID_PRO_* in .env (e.g. for live Stripe
        # testing) would otherwise make this test's assumption false.
        client = APIClient()
        with patch.dict(PLANS_BY_ID["pro"]["prices"]["EUR"]["monthly"], {"stripe_price_id": ""}), \
             patch.dict(PLANS_BY_ID["pro"]["prices"]["EUR"]["annual"], {"stripe_price_id": ""}), \
             patch.dict(PLANS_BY_ID["pro"]["prices"]["GBP"]["monthly"], {"stripe_price_id": ""}), \
             patch.dict(PLANS_BY_ID["pro"]["prices"]["GBP"]["annual"], {"stripe_price_id": ""}):
            response = client.get("/api/billing/plans/")

            for plan in response.data:
                if plan["id"] == "pro":
                    self.assertFalse(plan["monthly"]["purchasable"])
                    self.assertFalse(plan["annual"]["purchasable"])

    def test_free_plan_has_no_prices(self):
        client = APIClient()
        response = client.get("/api/billing/plans/")

        free_plan = next(plan for plan in response.data if plan["id"] == "free")
        self.assertIsNone(free_plan["monthly"])
        self.assertIsNone(free_plan["annual"])

    def test_currency_query_param_resolves_prices(self):
        client = APIClient()
        with patch.dict(
            PLANS_BY_ID["pro"]["prices"]["GBP"]["monthly"], {"stripe_price_id": "price_gbp_monthly"}
        ):
            response = client.get("/api/billing/plans/?currency=GBP")

        pro_plan = next(plan for plan in response.data if plan["id"] == "pro")
        self.assertEqual(pro_plan["currency"], "GBP")
        self.assertTrue(pro_plan["monthly"]["display"].startswith("£"))

    def test_unsupported_currency_falls_back_to_eur(self):
        client = APIClient()
        response = client.get("/api/billing/plans/?currency=USD")

        pro_plan = next(plan for plan in response.data if plan["id"] == "pro")
        self.assertEqual(pro_plan["currency"], "EUR")


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
        with patch.dict(PLANS_BY_ID["pro"]["prices"]["EUR"]["monthly"], {"stripe_price_id": "price_test_123"}):
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
        self.assertEqual(call_kwargs["line_items"], [{"price": "price_test_123", "quantity": 1}])

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.stripe_customer_id, "cus_test_123")

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_reuses_existing_stripe_customer_on_a_second_checkout(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_existing")

        with patch.dict(PLANS_BY_ID["pro"]["prices"]["EUR"]["monthly"], {"stripe_price_id": "price_test_123"}):
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
    def test_real_stripe_event_object_is_handled_not_just_a_plain_dict(self):
        # Every other test in this class mocks construct_event to return a
        # plain dict, which is NOT what the real Stripe SDK returns (it
        # returns a stripe.Event/StripeObject, which supports [] access but
        # not .get()) — that mismatch let a real bug ship: every actual
        # Stripe webhook 500'd on `event.get("id")` despite this whole test
        # class being green. stripe.Event.construct_from reproduces the
        # real SDK's return type, so this test would have caught it.
        client = APIClient()
        real_shaped_event = stripe.Event.construct_from(
            {
                "id": "evt_real_shape",
                "type": "checkout.session.completed",
                "data": {"object": {"id": "cs_test", "customer": "cus_test", "metadata": {}}},
            },
            "sk_test_fake",
        )
        with patch("billing.services.stripe_service.stripe.Webhook.construct_event", return_value=real_shaped_event), \
             patch("billing.views.billing_view.SubscriptionSyncService.apply_event") as mock_apply:
            response = client.post(
                "/api/billing/webhook/", data=b"{}", content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid-looking-signature",
            )

        self.assertEqual(response.status_code, 200)
        # apply_event must receive a plain dict (matching every other
        # sync-service test's fixtures), not the raw StripeObject.
        called_with = mock_apply.call_args[0][0]
        self.assertIsInstance(called_with, dict)
        self.assertEqual(called_with["id"], "evt_real_shape")
        self.assertEqual(called_with["type"], "checkout.session.completed")

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

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_fake")
    def test_event_with_an_id_is_logged_and_marked_processed(self):
        from billing.models import StripeWebhookEvent

        client = APIClient()
        fake_event = {
            "id": "evt_123", "type": "checkout.session.completed", "data": {"object": {}},
        }
        with patch("billing.services.stripe_service.stripe.Webhook.construct_event", return_value=fake_event), \
             patch("billing.views.billing_view.SubscriptionSyncService.apply_event"):
            response = client.post(
                "/api/billing/webhook/", data=b"{}", content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid-looking-signature",
            )

        self.assertEqual(response.status_code, 200)
        webhook_event = StripeWebhookEvent.objects.get(stripe_event_id="evt_123")
        self.assertEqual(webhook_event.event_type, "checkout.session.completed")
        self.assertIsNotNone(webhook_event.processed_at)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_fake")
    def test_redelivered_event_is_not_reprocessed(self):
        client = APIClient()
        fake_event = {
            "id": "evt_dup", "type": "checkout.session.completed", "data": {"object": {}},
        }
        with patch("billing.services.stripe_service.stripe.Webhook.construct_event", return_value=fake_event), \
             patch("billing.views.billing_view.SubscriptionSyncService.apply_event") as mock_apply:
            client.post(
                "/api/billing/webhook/", data=b"{}", content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid-looking-signature",
            )
            second_response = client.post(
                "/api/billing/webhook/", data=b"{}", content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid-looking-signature",
            )

        self.assertEqual(second_response.status_code, 200)
        self.assertTrue(second_response.data["duplicate"])
        mock_apply.assert_called_once()

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_fake")
    def test_processing_failure_returns_500_and_records_the_error(self):
        from billing.models import StripeWebhookEvent

        client = APIClient()
        fake_event = {
            "id": "evt_fail", "type": "checkout.session.completed", "data": {"object": {}},
        }
        with patch("billing.services.stripe_service.stripe.Webhook.construct_event", return_value=fake_event), \
             patch(
                 "billing.views.billing_view.SubscriptionSyncService.apply_event",
                 side_effect=RuntimeError("boom"),
             ):
            response = client.post(
                "/api/billing/webhook/", data=b"{}", content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid-looking-signature",
            )

        self.assertEqual(response.status_code, 500)
        webhook_event = StripeWebhookEvent.objects.get(stripe_event_id="evt_fail")
        self.assertIsNone(webhook_event.processed_at)
        self.assertIn("boom", webhook_event.error)


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

    def test_subscription_updated_resyncs_plan_and_billing_period_from_new_price(self):
        # Simulates a monthly -> annual switch made through the Stripe
        # Customer Portal (the only upgrade/downgrade path this app offers)
        # rather than through our own checkout — nothing in our own code
        # ever sets billing_period="annual" here, so this only passes if
        # the webhook handler itself re-derives it from the new price.
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id="cus_123",
            status="active",
            plan_id="pro",
            currency="EUR",
            billing_period="monthly",
        )

        with patch.dict(PLANS_BY_ID["pro"]["prices"]["EUR"]["annual"], {"stripe_price_id": "price_eur_annual"}):
            event = {
                "type": "customer.subscription.updated",
                "data": {"object": {
                    "id": "sub_123",
                    "customer": "cus_123",
                    "status": "active",
                    "items": {"data": [{"price": {"id": "price_eur_annual"}}]},
                }},
            }
            SubscriptionSyncService.apply_event(event)

        subscription = Subscription.objects.get(stripe_customer_id="cus_123")
        self.assertEqual(subscription.plan_id, "pro")
        self.assertEqual(subscription.currency, "EUR")
        self.assertEqual(subscription.billing_period, "annual")

    def test_subscription_updated_with_unrecognized_price_leaves_plan_fields_unchanged(self):
        # A price that isn't in our catalog (stale, or created manually in
        # the Stripe Dashboard outside this app) must not blank out fields
        # we already trust — better to keep the last-known-good value than
        # silently corrupt it.
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id="cus_123",
            status="active",
            plan_id="pro",
            currency="EUR",
            billing_period="monthly",
        )

        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "items": {"data": [{"price": {"id": "price_not_in_our_catalog"}}]},
            }},
        }
        SubscriptionSyncService.apply_event(event)

        subscription = Subscription.objects.get(stripe_customer_id="cus_123")
        self.assertEqual(subscription.plan_id, "pro")
        self.assertEqual(subscription.currency, "EUR")
        self.assertEqual(subscription.billing_period, "monthly")

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
        event = {"type": "some.future.event", "data": {"object": {}}}
        SubscriptionSyncService.apply_event(event)  # should not raise

    def test_invoice_payment_failed_does_not_change_status(self):
        # Stripe's own status transition (surfaced via
        # customer.subscription.updated) remains the actual source of
        # truth for access — a failed invoice alone must not revoke it.
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", status="active")

        event = {
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_123", "id": "in_123"}},
        }
        SubscriptionSyncService.apply_event(event)

        self.assertEqual(Subscription.objects.get(stripe_customer_id="cus_123").status, "active")

    def test_invoice_payment_failed_emails_the_customer(self):
        from django.core import mail

        Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", status="active")

        event = {
            "type": "invoice.payment_failed",
            "data": {"object": {
                "customer": "cus_123", "id": "in_123",
                "hosted_invoice_url": "https://invoice.stripe.com/i/acct_123/test_abc",
            }},
        }
        SubscriptionSyncService.apply_event(event)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("didn't go through", mail.outbox[0].subject)
        self.assertIn("https://invoice.stripe.com/i/acct_123/test_abc", mail.outbox[0].body)

    def test_invoice_payment_failed_for_unknown_customer_sends_no_email(self):
        from django.core import mail

        event = {
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_unknown", "id": "in_123"}},
        }
        SubscriptionSyncService.apply_event(event)

        self.assertEqual(len(mail.outbox), 0)

    def test_invoice_paid_recovers_a_past_due_subscription(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", status="past_due")

        event = {
            "type": "invoice.paid",
            "data": {"object": {"customer": "cus_123", "id": "in_123"}},
        }
        SubscriptionSyncService.apply_event(event)

        self.assertEqual(Subscription.objects.get(stripe_customer_id="cus_123").status, "active")

    def test_full_refund_cancels_the_subscription(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", status="active")

        event = {
            "type": "charge.refunded",
            "data": {"object": {"customer": "cus_123", "amount": 1499, "amount_refunded": 1499}},
        }
        SubscriptionSyncService.apply_event(event)

        self.assertEqual(Subscription.objects.get(stripe_customer_id="cus_123").status, "canceled")

    def test_partial_refund_does_not_cancel_the_subscription(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", status="active")

        event = {
            "type": "charge.refunded",
            "data": {"object": {"customer": "cus_123", "amount": 1499, "amount_refunded": 500}},
        }
        SubscriptionSyncService.apply_event(event)

        self.assertEqual(Subscription.objects.get(stripe_customer_id="cus_123").status, "active")

    def test_checkout_completed_fetches_period_end_from_stripe(self):
        Subscription.objects.create(user=self.user, status="none")

        event = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "customer": "cus_123",
                "subscription": "sub_123",
                "metadata": {"user_id": str(self.user.id), "plan_id": "pro"},
            }},
        }
        fake_stripe_subscription = {"current_period_end": 4102444800}  # 2100-01-01
        with patch(
            "billing.services.stripe_service.StripeService.get_subscription",
            return_value=fake_stripe_subscription,
        ):
            SubscriptionSyncService.apply_event(event)

        subscription = Subscription.objects.get(user=self.user)
        self.assertIsNotNone(subscription.current_period_end)
