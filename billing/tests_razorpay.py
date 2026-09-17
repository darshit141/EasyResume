"""Rupee billing end to end: catalog, checkout, confirmation, webhooks, GST
invoices, and what happens to all of it when an account is deleted.

Razorpay itself is faked at the RazorpayService method boundary (or at
`requests` where the HTTP layer is the thing under test). Everything between
that boundary and the database is the real implementation.
"""

import hashlib
import hmac
import io
import json
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import User
from billing.models import (
    BillingProfile,
    GSTInvoice,
    InvoiceSequence,
    PlanPrice,
    PricingOffer,
    RazorpayWebhookEvent,
    Subscription,
)
from billing.plans import (
    PLANS,
    PLANS_BY_ID,
    UPI_AUTOPAY_MAX_PAISE,
    find_plan_by_razorpay_plan_id,
    format_price_display,
    get_price,
)
from billing.services.razorpay_service import RazorpayService
from billing.services.razorpay_sync_service import RazorpaySyncService

RZP = dict(
    RAZORPAY_KEY_ID="rzp_test_public",
    RAZORPAY_KEY_SECRET="rzp_test_secret",
    RAZORPAY_WEBHOOK_SECRET="rzp_webhook_secret",
)
GST_REGISTERED = dict(
    GST_ENABLED=True,
    GST_RATE_PERCENT="18",
    GST_SELLER_GSTIN="27AAPFU0939F1ZV",  # Maharashtra
    GST_SELLER_LEGAL_NAME="IntelliResume Pvt. Ltd.",
    GST_SELLER_ADDRESS="Pune, Maharashtra",
    GST_SAC_CODE="998314",
    GST_INVOICE_PREFIX="IR",
)
PRO_MONTHLY_PLAN = "plan_pro_inr_monthly"


def _make_user(email):
    return User.objects.create_user(
        name="Asha Rao",
        email=email,
        phone=f"7{abs(hash(email)) % 1000000000:09d}",
        password="safe-password",
    )


def _inr_plan_ids():
    """Configure the Pro INR monthly Razorpay plan for the duration of a test."""
    return patch.dict(
        PLANS_BY_ID["pro"]["prices"]["INR"]["monthly"], {"processor_price_id": PRO_MONTHLY_PLAN}
    )


def _entity(user, status="active", sub_id="sub_rzp_1", plan_id=PRO_MONTHLY_PLAN, current_end=None):
    return {
        "id": sub_id,
        "entity": "subscription",
        "plan_id": plan_id,
        "customer_id": "cust_rzp_1",
        "status": status,
        "current_start": int(time.time()),
        "current_end": current_end if current_end is not None else int(time.time()) + 30 * 86400,
        "notes": {
            "user_id": str(user.id),
            "plan_id": "pro",
            "currency": "INR",
            "billing_period": "monthly",
        },
    }


def _payment(payment_id="pay_rzp_1", amount=49900, status="captured"):
    return {
        "id": payment_id,
        "entity": "payment",
        "amount": amount,
        "currency": "INR",
        "status": status,
        "method": "upi",
        "created_at": int(time.time()),
    }


def _sign(secret, message):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class RupeeCatalogTests(TestCase):
    def test_every_paid_plan_has_monthly_and_annual_rupee_prices(self):
        for plan in PLANS:
            if not plan["prices"]:
                continue
            with self.subTest(plan=plan["id"]):
                self.assertIn("INR", plan["prices"])
                self.assertEqual(set(plan["prices"]["INR"]), {"monthly", "annual"})

    def test_rupee_prices_route_to_razorpay_and_include_tax(self):
        price = get_price("pro", "INR", "monthly")

        self.assertEqual(price["processor"], "razorpay")
        self.assertTrue(price["tax_inclusive"])
        self.assertEqual(price["amount_cents"], 49900)
        # Never a Stripe price: a stray one here would send INR to Stripe.
        self.assertEqual(price["stripe_price_id"], "")

    def test_euro_prices_still_route_to_stripe_and_exclude_tax(self):
        price = get_price("pro", "EUR", "monthly")

        self.assertEqual(price["processor"], "stripe")
        self.assertFalse(price["tax_inclusive"])

    def test_every_rupee_price_can_renew_over_upi_autopay(self):
        """RBI caps an AutoPay debit at Rs 15,000; a price above it would
        sell fine and then fail its first renewal."""
        for plan in PLANS:
            for period, price in plan.get("prices", {}).get("INR", {}).items():
                with self.subTest(plan=plan["id"], period=period):
                    self.assertLessEqual(price["amount_cents"], UPI_AUTOPAY_MAX_PAISE)

    def test_rupee_display_uses_indian_grouping(self):
        self.assertEqual(format_price_display(399900, "INR", "annual"), "₹3,999/yr")
        self.assertEqual(format_price_display(49900, "INR", "monthly"), "₹499/mo")

    def test_razorpay_plan_reverse_lookup(self):
        with _inr_plan_ids():
            self.assertEqual(
                find_plan_by_razorpay_plan_id(PRO_MONTHLY_PLAN), ("pro", "INR", "monthly")
            )
        self.assertIsNone(find_plan_by_razorpay_plan_id("plan_unknown"))
        self.assertIsNone(find_plan_by_razorpay_plan_id(""))

    def test_an_admin_price_row_supplies_the_razorpay_plan(self):
        PlanPrice.objects.create(
            plan_id="pro", currency="INR", billing_period="annual",
            amount_cents=349900, razorpay_plan_id="plan_admin_annual",
            # A Stripe id on an INR row must be ignored, not charged.
            stripe_price_id="price_should_not_be_used",
        )

        price = get_price("pro", "INR", "annual")
        self.assertEqual(price["amount_cents"], 349900)
        self.assertEqual(price["processor_price_id"], "plan_admin_annual")
        self.assertEqual(price["stripe_price_id"], "")
        self.assertEqual(find_plan_by_razorpay_plan_id("plan_admin_annual"), ("pro", "INR", "annual"))

    def test_a_stripe_offer_is_never_applied_to_rupees(self):
        now = timezone.now()
        PricingOffer.objects.create(
            campaign="diwali", label="Diwali", plan_id="pro", currency="INR",
            billing_period="monthly", amount_cents=19900, stripe_price_id="price_offer",
            starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=1),
        )

        price = get_price("pro", "INR", "monthly")
        self.assertEqual(price["amount_cents"], 49900)
        self.assertIsNone(price["offer"])


class RupeePlanListTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_rupee_plans_offer_upi(self):
        with _inr_plan_ids():
            response = self.client.get("/api/billing/plans/?currency=INR")

        self.assertEqual(response.status_code, 200)
        pro = next(plan for plan in response.data if plan["id"] == "pro")
        self.assertEqual(pro["currency"], "INR")
        self.assertEqual(pro["monthly"]["processor"], "razorpay")
        self.assertIn("upi", pro["monthly"]["payment_methods"])
        self.assertTrue(pro["monthly"]["purchasable"])
        self.assertFalse(pro["annual"]["purchasable"], "no Razorpay plan configured for annual")
        self.assertEqual(pro["monthly"]["display"], "₹499/mo")

    def test_tax_note_does_not_claim_gst_before_registration(self):
        response = self.client.get("/api/billing/plans/?currency=INR")

        pro = next(plan for plan in response.data if plan["id"] == "pro")
        self.assertEqual(pro["monthly"]["tax_note"], "Inclusive of all taxes")

    @override_settings(**GST_REGISTERED)
    def test_tax_note_names_the_gst_rate_once_registered(self):
        response = self.client.get("/api/billing/plans/?currency=INR")

        pro = next(plan for plan in response.data if plan["id"] == "pro")
        self.assertEqual(pro["monthly"]["tax_note"], "Includes 18% GST")

    @override_settings(GST_ENABLED=True, GST_SELLER_GSTIN="27AAPFU0939F1ZW")
    def test_an_invalid_seller_gstin_means_no_gst_is_advertised(self):
        response = self.client.get("/api/billing/plans/?currency=INR")

        pro = next(plan for plan in response.data if plan["id"] == "pro")
        self.assertEqual(pro["monthly"]["tax_note"], "Inclusive of all taxes")

    def test_euro_plans_keep_the_vat_note(self):
        response = self.client.get("/api/billing/plans/?currency=EUR")

        pro = next(plan for plan in response.data if plan["id"] == "pro")
        self.assertIn("VAT", pro["monthly"]["tax_note"])
        self.assertEqual(pro["monthly"]["payment_methods"], ["card"])


class BillingProfileTests(TestCase):
    def setUp(self):
        self.user = _make_user("profile@example.in")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_empty_profile_reads_as_blank(self):
        response = self.client.get("/api/billing/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state_code"], "")

    def test_a_consumer_saves_just_a_state(self):
        response = self.client.put("/api/billing/profile/", {"state_code": "29"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state_name"], "Karnataka")
        self.assertEqual(BillingProfile.objects.get(user=self.user).state_code, "29")

    def test_an_unknown_state_is_refused(self):
        response = self.client.put("/api/billing/profile/", {"state_code": "99"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("state_code", response.data)

    def test_a_business_gstin_sets_the_state_from_the_gstin(self):
        response = self.client.put(
            "/api/billing/profile/",
            {
                "state_code": "29",
                "gstin": "07aagff2194n1z1",
                "legal_name": "Acme Hiring LLP",
                "address": "Connaught Place, New Delhi",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["gstin"], "07AAGFF2194N1Z1")
        self.assertEqual(response.data["state_code"], "07")

    def test_a_gstin_needs_a_legal_name_and_address(self):
        response = self.client.put(
            "/api/billing/profile/", {"gstin": "07AAGFF2194N1Z1"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("legal_name", response.data)
        self.assertIn("address", response.data)

    def test_a_mistyped_gstin_is_refused(self):
        response = self.client.put(
            "/api/billing/profile/",
            {"gstin": "07AAGFF2194N1Z2", "legal_name": "Acme", "address": "Delhi"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("gstin", response.data)

    def test_gst_info_is_public_and_lists_states(self):
        response = APIClient().get("/api/billing/gst/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["registered"])
        codes = {state["code"] for state in response.data["states"]}
        self.assertIn("27", codes)
        self.assertNotIn("28", codes, "legacy Andhra Pradesh code is not a choice")


@override_settings(**RZP)
class RupeeCheckoutTests(TestCase):
    def setUp(self):
        self.user = _make_user("checkout@example.in")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        BillingProfile.objects.create(user=self.user, state_code="27")

    def _checkout(self, **extra):
        body = {"plan_id": "pro", "currency": "INR", "billing_period": "monthly", **extra}
        return self.client.post("/api/billing/checkout/", body, format="json")

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
    def test_unconfigured_razorpay_is_reported_not_crashed(self):
        with _inr_plan_ids():
            response = self._checkout()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "RAZORPAY_NOT_CONFIGURED")

    def test_a_billing_state_is_required_first(self):
        BillingProfile.objects.filter(user=self.user).delete()

        with _inr_plan_ids():
            response = self._checkout()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "BILLING_STATE_REQUIRED")

    def test_an_unconfigured_rupee_plan_is_not_purchasable(self):
        response = self._checkout(billing_period="annual")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PLAN_NOT_PURCHASABLE")

    def test_checkout_opens_a_razorpay_subscription(self):
        created = {"id": "sub_new", "status": "created", "short_url": "https://rzp.io/i/abc"}
        with _inr_plan_ids(), patch.object(RazorpayService, "create_subscription", return_value=created) as create:
            response = self._checkout()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["processor"], "razorpay")
        self.assertEqual(response.data["checkout_url"], "https://rzp.io/i/abc")
        self.assertEqual(response.data["razorpay"]["subscription_id"], "sub_new")
        self.assertEqual(response.data["razorpay"]["key_id"], "rzp_test_public")
        self.assertEqual(response.data["razorpay"]["amount"], 49900)
        self.assertNotIn("rzp_test_secret", json.dumps(response.data))

        plan_id, total_count = create.call_args.args[:2]
        notes = create.call_args.kwargs["notes"]
        self.assertEqual(plan_id, PRO_MONTHLY_PLAN)
        self.assertEqual(total_count, 120)
        self.assertEqual(notes["user_id"], str(self.user.id))

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.razorpay_subscription_id, "sub_new")
        # Starting checkout grants nothing.
        self.assertFalse(subscription.is_active)
        self.assertEqual(subscription.payment_processor, "")

    def test_an_annual_mandate_counts_years_not_months(self):
        created = {"id": "sub_year", "status": "created", "short_url": ""}
        annual = patch.dict(
            PLANS_BY_ID["pro"]["prices"]["INR"]["annual"], {"processor_price_id": "plan_pro_inr_annual"}
        )
        with annual, patch.object(RazorpayService, "create_subscription", return_value=created) as create:
            self._checkout(billing_period="annual")

        self.assertEqual(create.call_args.args[1], 10)

    def test_an_active_subscriber_cannot_open_a_second_mandate(self):
        Subscription.objects.create(
            user=self.user, plan_id="pro", status="active", payment_processor="razorpay",
            current_period_end=timezone.now() + timedelta(days=10),
        )

        with _inr_plan_ids(), patch.object(RazorpayService, "create_subscription") as create:
            response = self._checkout()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "ALREADY_SUBSCRIBED")
        create.assert_not_called()

    def test_an_abandoned_checkout_is_cancelled_before_a_new_one(self):
        Subscription.objects.create(user=self.user, razorpay_subscription_id="sub_abandoned")
        created = {"id": "sub_fresh", "status": "created", "short_url": ""}

        with _inr_plan_ids(), \
                patch.object(RazorpayService, "fetch_subscription", return_value={"status": "created"}), \
                patch.object(RazorpayService, "cancel_subscription") as cancel, \
                patch.object(RazorpayService, "create_subscription", return_value=created):
            self._checkout()

        cancel.assert_called_once_with("sub_abandoned")
        self.assertEqual(Subscription.objects.get(user=self.user).razorpay_subscription_id, "sub_fresh")

    def test_euro_checkout_still_goes_to_stripe(self):
        with patch("billing.views.billing_view.StripeService.create_checkout_session",
                   return_value="https://checkout.stripe.com/c/x") as stripe_checkout:
            response = self.client.post(
                "/api/billing/checkout/",
                {"plan_id": "pro", "currency": "EUR", "billing_period": "monthly"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["processor"], "stripe")
        self.assertEqual(response.data["checkout_url"], "https://checkout.stripe.com/c/x")
        stripe_checkout.assert_called_once()


@override_settings(**RZP)
class RazorpayHTTPLayerTests(TestCase):
    def test_requests_are_authenticated_with_the_key_pair(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"id": "sub_x"}
        with patch("billing.services.razorpay_service.requests.request", return_value=response) as request:
            RazorpayService.fetch_subscription("sub_x")

        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["auth"], ("rzp_test_public", "rzp_test_secret"))
        self.assertEqual(request.call_args.args[1], "https://api.razorpay.com/v1/subscriptions/sub_x")

    def test_an_error_response_becomes_a_safe_billing_error(self):
        from billing.services.razorpay_service import RazorpayAPIError

        response = MagicMock(status_code=400)
        response.json.return_value = {"error": {"description": "The id provided does not exist"}}
        with patch("billing.services.razorpay_service.requests.request", return_value=response):
            with self.assertRaises(RazorpayAPIError):
                RazorpayService.fetch_subscription("sub_missing")

    def test_a_network_failure_becomes_a_safe_billing_error(self):
        import requests

        from billing.services.razorpay_service import RazorpayAPIError

        with patch("billing.services.razorpay_service.requests.request", side_effect=requests.ConnectionError()):
            with self.assertRaises(RazorpayAPIError):
                RazorpayService.fetch_subscription("sub_x")

    def test_checkout_signature_is_payment_then_subscription(self):
        good = _sign("rzp_test_secret", b"pay_1|sub_1")
        swapped = _sign("rzp_test_secret", b"sub_1|pay_1")

        self.assertTrue(RazorpayService.verify_checkout_signature("pay_1", "sub_1", good))
        self.assertFalse(RazorpayService.verify_checkout_signature("pay_1", "sub_1", swapped))
        self.assertFalse(RazorpayService.verify_checkout_signature("pay_1", "sub_1", ""))

    @override_settings(RAZORPAY_WEBHOOK_SECRET="")
    def test_webhooks_fail_closed_without_a_secret(self):
        body = b'{"event":"subscription.activated"}'
        self.assertFalse(RazorpayService.verify_webhook_signature(body, _sign("", body)))


@override_settings(**RZP)
class RupeeConfirmationTests(TestCase):
    def setUp(self):
        self.user = _make_user("confirm@example.in")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        BillingProfile.objects.create(user=self.user, state_code="27")
        Subscription.objects.create(user=self.user, razorpay_subscription_id="sub_rzp_1")

    def _confirm(self, signature=None, payment_id="pay_rzp_1", sub_id="sub_rzp_1"):
        signature = signature or _sign("rzp_test_secret", f"{payment_id}|{sub_id}".encode())
        return self.client.post(
            "/api/billing/razorpay/confirm/",
            {
                "razorpay_payment_id": payment_id,
                "razorpay_subscription_id": sub_id,
                "razorpay_signature": signature,
            },
            format="json",
        )

    def test_a_forged_signature_grants_nothing(self):
        with patch.object(RazorpayService, "fetch_subscription") as fetch:
            response = self._confirm(signature="0" * 64)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PAYMENT_NOT_VERIFIED")
        fetch.assert_not_called()
        self.assertFalse(Subscription.objects.get(user=self.user).is_active)

    def test_a_subscription_belonging_to_someone_else_grants_nothing(self):
        stranger = _make_user("stranger@example.in")
        with patch.object(RazorpayService, "fetch_subscription", return_value=_entity(stranger)):
            response = self._confirm()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Subscription.objects.get(user=self.user).is_active)

    def test_an_unpaid_subscription_grants_nothing(self):
        with patch.object(RazorpayService, "fetch_subscription", return_value=_entity(self.user, status="created")):
            response = self._confirm()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Subscription.objects.get(user=self.user).is_active)

    def test_a_verified_paid_subscription_activates_the_plan_and_invoices_it(self):
        with _inr_plan_ids(), \
                patch.object(RazorpayService, "fetch_subscription", return_value=_entity(self.user)), \
                patch.object(RazorpayService, "fetch_payment", return_value=_payment()):
            response = self._confirm()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_active"])
        self.assertEqual(response.data["payment_processor"], "razorpay")

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.plan_id, "pro")
        self.assertEqual(subscription.currency, "INR")
        self.assertEqual(subscription.billing_period, "monthly")
        self.assertIsNotNone(subscription.current_period_end)

        from billing.services.entitlements_service import EntitlementsService

        self.assertEqual(EntitlementsService.for_user(self.user)["plan_id"], "pro")
        self.assertEqual(GSTInvoice.objects.filter(user=self.user).count(), 1)

    def test_authenticated_without_dates_gets_a_bounded_period(self):
        """Access must never be open-ended while Razorpay has yet to stamp
        the first cycle's dates."""
        entity = _entity(self.user, status="authenticated")
        entity["current_end"] = None
        with patch.object(RazorpayService, "fetch_subscription", return_value=entity), \
                patch.object(RazorpayService, "fetch_payment", return_value=_payment()):
            self._confirm()

        end = Subscription.objects.get(user=self.user).current_period_end
        self.assertIsNotNone(end)
        self.assertLess(end, timezone.now() + timedelta(days=32))


@override_settings(**RZP)
class RazorpayWebhookTests(TestCase):
    def setUp(self):
        self.user = _make_user("webhook@example.in")
        BillingProfile.objects.create(user=self.user, state_code="29")
        Subscription.objects.create(user=self.user, razorpay_subscription_id="sub_rzp_1")
        self.client = APIClient()

    def _post(self, event_type, entity=None, payment=None, event_id="evt_1", created_at=None, secret="rzp_webhook_secret", extra=None):
        payload = {}
        if entity is not None:
            payload["subscription"] = {"entity": entity}
        if payment is not None:
            payload["payment"] = {"entity": payment}
        payload.update(extra or {})
        body = json.dumps({
            "entity": "event",
            "event": event_type,
            "payload": payload,
            "created_at": created_at or int(time.time()),
        }).encode()
        return self.client.generic(
            "POST",
            "/api/billing/razorpay/webhook/",
            body,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=_sign(secret, body),
            HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    def _subscription(self):
        return Subscription.objects.get(user=self.user)

    def test_a_bad_signature_is_rejected_and_nothing_changes(self):
        response = self._post("subscription.activated", _entity(self.user), secret="wrong")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self._subscription().is_active)
        self.assertFalse(RazorpayWebhookEvent.objects.exists())

    def test_activation_grants_access(self):
        with _inr_plan_ids():
            response = self._post("subscription.activated", _entity(self.user))

        self.assertEqual(response.status_code, 200)
        subscription = self._subscription()
        self.assertTrue(subscription.is_active)
        self.assertEqual(subscription.payment_processor, "razorpay")
        self.assertEqual(subscription.razorpay_customer_id, "cust_rzp_1")
        self.assertIsNotNone(RazorpayWebhookEvent.objects.get(event_id="evt_1").processed_at)

    def test_a_redelivered_event_is_not_reapplied(self):
        self._post("subscription.activated", _entity(self.user))
        with patch.object(RazorpaySyncService, "apply_event") as apply:
            response = self._post("subscription.activated", _entity(self.user))

        self.assertTrue(response.data["duplicate"])
        apply.assert_not_called()

    def test_charged_issues_exactly_one_invoice_per_payment(self):
        now = int(time.time())
        first = self._post("subscription.charged", _entity(self.user), _payment(), event_id="evt_a", created_at=now)
        # Razorpay can deliver the same payment under a different event id
        # (a retry after a timeout); it must still be one invoice — and the
        # second delivery must succeed, or Razorpay retries it forever.
        second = self._post("subscription.charged", _entity(self.user), _payment(), event_id="evt_b", created_at=now + 1)

        self.assertEqual((first.status_code, second.status_code), (200, 200))

        self.assertEqual(GSTInvoice.objects.filter(razorpay_payment_id="pay_rzp_1").count(), 1)
        self.assertEqual(InvoiceSequence.objects.get().last_number, 1, "no serial burned by the duplicate")

    def test_a_renewal_failure_keeps_access_while_retrying_and_emails_the_customer(self):
        self._post("subscription.activated", _entity(self.user), event_id="evt_a")
        with patch("billing.services.dunning_mailer.send_payment_failed_email") as email:
            self._post(
                "subscription.pending", _entity(self.user, status="pending"),
                _payment(status="failed"), event_id="evt_b", created_at=int(time.time()) + 5,
            )

        self.assertTrue(self._subscription().is_active)
        email.assert_called_once()
        self.assertEqual(email.call_args.kwargs["processor"], "razorpay")

    def test_halted_ends_access(self):
        self._post("subscription.activated", _entity(self.user), event_id="evt_a")
        self._post(
            "subscription.halted", _entity(self.user, status="halted"),
            event_id="evt_b", created_at=int(time.time()) + 5,
        )

        subscription = self._subscription()
        self.assertEqual(subscription.status, "past_due")
        self.assertFalse(subscription.is_active)

    def test_cancelling_from_the_upi_app_ends_access(self):
        self._post("subscription.activated", _entity(self.user), event_id="evt_a")
        self._post(
            "subscription.cancelled", _entity(self.user, status="cancelled"),
            event_id="evt_b", created_at=int(time.time()) + 5,
        )

        self.assertEqual(self._subscription().status, "canceled")

    def test_an_older_event_arriving_late_cannot_undo_a_newer_one(self):
        now = int(time.time())
        self._post("subscription.cancelled", _entity(self.user, status="cancelled"), event_id="evt_new", created_at=now)
        self._post("subscription.charged", _entity(self.user), _payment(), event_id="evt_old", created_at=now - 60)

        self.assertEqual(self._subscription().status, "canceled")

    def test_the_owner_is_found_by_note_when_the_row_holds_a_newer_checkout(self):
        Subscription.objects.filter(user=self.user).update(razorpay_subscription_id="sub_newer_unpaid")

        self._post("subscription.activated", _entity(self.user, sub_id="sub_older_paid"))

        subscription = self._subscription()
        self.assertTrue(subscription.is_active)
        self.assertEqual(subscription.razorpay_subscription_id, "sub_older_paid")

    def test_an_active_stripe_subscriber_is_not_overwritten(self):
        Subscription.objects.filter(user=self.user).update(
            payment_processor="stripe", status="active", plan_id="max", currency="EUR",
            razorpay_subscription_id="",
        )

        self._post("subscription.activated", _entity(self.user))

        subscription = self._subscription()
        self.assertEqual((subscription.payment_processor, subscription.plan_id, subscription.currency),
                         ("stripe", "max", "EUR"))

    def test_an_unknown_subscription_is_ignored_not_errored(self):
        stranger_entity = _entity(self.user, sub_id="sub_nobody")
        stranger_entity["notes"] = {}

        response = self._post("subscription.activated", stranger_entity)

        self.assertEqual(response.status_code, 200)

    def test_a_full_refund_ends_access_and_a_partial_one_does_not(self):
        self._post("subscription.charged", _entity(self.user), _payment(), event_id="evt_charge")

        partial = {"refund": {"entity": {"payment_id": "pay_rzp_1", "amount": 1000}},
                   "payment": {"entity": {**_payment(), "amount_refunded": 1000}}}
        self._post("refund.processed", event_id="evt_partial", extra=partial)
        self.assertTrue(self._subscription().is_active)

        full = {"refund": {"entity": {"payment_id": "pay_rzp_1", "amount": 49900}},
                "payment": {"entity": {**_payment(), "amount_refunded": 49900}}}
        self._post("refund.processed", event_id="evt_full", extra=full)
        self.assertEqual(self._subscription().status, "canceled")

    def test_a_processing_failure_returns_500_so_razorpay_retries(self):
        with patch.object(RazorpaySyncService, "apply_event", side_effect=RuntimeError("boom")):
            response = self._post("subscription.activated", _entity(self.user))

        self.assertEqual(response.status_code, 500)
        record = RazorpayWebhookEvent.objects.get(event_id="evt_1")
        self.assertIsNone(record.processed_at)
        self.assertIn("boom", record.error)

    def test_late_stripe_events_cannot_touch_a_rupee_subscriber(self):
        from billing.services.subscription_sync_service import SubscriptionSyncService

        self._post("subscription.activated", _entity(self.user))
        Subscription.objects.filter(user=self.user).update(stripe_customer_id="cus_old")

        SubscriptionSyncService.apply_event({
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_old", "id": "sub_stripe_old"}},
        })

        self.assertTrue(self._subscription().is_active)


class GSTInvoiceTests(TestCase):
    def setUp(self):
        self.user = _make_user("invoice@example.in")
        self.subscription = Subscription.objects.create(
            user=self.user, plan_id="pro", billing_period="monthly", status="active",
            payment_processor="razorpay", razorpay_subscription_id="sub_rzp_1",
        )

    def _issue(self, payment_id="pay_1", amount=49900, issued_at=None):
        from billing.services.gst_invoice_service import GSTInvoiceService

        return GSTInvoiceService.issue(
            user=self.user, payment_id=payment_id, razorpay_subscription_id="sub_rzp_1",
            amount_paise=amount, plan_id="pro", billing_period="monthly", issued_at=issued_at,
        )

    def test_before_registration_it_is_a_plain_invoice_with_no_tax(self):
        BillingProfile.objects.create(user=self.user, state_code="27")

        invoice = self._issue()

        self.assertEqual(invoice.kind, GSTInvoice.KIND_INVOICE)
        self.assertEqual(invoice.total, 49900)
        self.assertEqual(invoice.taxable_value, 49900)
        self.assertEqual(invoice.cgst + invoice.sgst + invoice.igst, 0)
        self.assertEqual(invoice.seller_gstin, "")
        self.assertIn("not registered", invoice.note)

    @override_settings(**GST_REGISTERED)
    def test_same_state_customer_gets_cgst_and_sgst(self):
        BillingProfile.objects.create(user=self.user, state_code="27")

        invoice = self._issue()

        self.assertEqual(invoice.kind, GSTInvoice.KIND_TAX_INVOICE)
        self.assertEqual((invoice.taxable_value, invoice.cgst, invoice.sgst, invoice.igst), (42288, 3806, 3806, 0))
        self.assertEqual(invoice.seller_gstin, "27AAPFU0939F1ZV")
        self.assertEqual(invoice.sac_code, "998314")
        self.assertEqual(invoice.place_of_supply_name, "Maharashtra")

    @override_settings(**GST_REGISTERED)
    def test_other_state_customer_gets_igst(self):
        BillingProfile.objects.create(user=self.user, state_code="33")

        invoice = self._issue()

        self.assertEqual((invoice.cgst, invoice.sgst, invoice.igst), (0, 0, 7612))
        self.assertEqual(invoice.place_of_supply_name, "Tamil Nadu")

    @override_settings(**GST_REGISTERED)
    def test_a_business_invoice_carries_its_gstin_name_and_address(self):
        BillingProfile.objects.create(
            user=self.user, state_code="07", gstin="07AAGFF2194N1Z1",
            legal_name="Acme Hiring LLP", address="New Delhi",
        )

        invoice = self._issue()

        self.assertEqual(invoice.buyer_gstin, "07AAGFF2194N1Z1")
        self.assertEqual(invoice.buyer_name, "Acme Hiring LLP")
        self.assertEqual(invoice.buyer_address, "New Delhi")
        self.assertEqual(invoice.place_of_supply_code, "07")
        self.assertEqual(invoice.igst, 7612)
        self.assertEqual(invoice.total, 49900, "a business pays what a consumer pays")

    @override_settings(**GST_REGISTERED)
    def test_a_missing_state_falls_back_to_the_suppliers_state(self):
        invoice = self._issue()

        self.assertEqual(invoice.place_of_supply_code, "27")
        self.assertEqual(invoice.igst, 0)

    def test_numbers_are_consecutive_and_restart_each_financial_year(self):
        BillingProfile.objects.create(user=self.user, state_code="27")
        ist = dt_timezone(timedelta(hours=5, minutes=30))

        first = self._issue("pay_a", issued_at=datetime(2027, 3, 31, 12, 0, tzinfo=ist))
        second = self._issue("pay_b", issued_at=datetime(2027, 3, 31, 23, 0, tzinfo=ist))
        # 20:00 UTC on 31 March is already 1 April in India: new year, new series.
        third = self._issue("pay_c", issued_at=datetime(2027, 3, 31, 20, 0, tzinfo=dt_timezone.utc))

        self.assertEqual(first.number, "IR/2627/000001")
        self.assertEqual(second.number, "IR/2627/000002")
        self.assertEqual(third.number, "IR/2728/000001")
        self.assertEqual(third.financial_year, "2027-28")

    def test_the_invoice_keeps_what_it_said_after_the_profile_changes(self):
        profile = BillingProfile.objects.create(user=self.user, state_code="27", legal_name="Asha Rao")
        invoice = self._issue()

        profile.state_code = "29"
        profile.legal_name = "Someone Else"
        profile.save()
        invoice.refresh_from_db()

        self.assertEqual(invoice.buyer_name, "Asha Rao")

    @override_settings(**GST_REGISTERED)
    def test_the_pdf_is_a_real_text_document_with_the_tax_lines(self):
        from pypdf import PdfReader

        from billing.services.gst_invoice_service import GSTInvoiceService

        BillingProfile.objects.create(user=self.user, state_code="27")
        invoice = self._issue()

        pdf = GSTInvoiceService.render_pdf(invoice)
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)

        self.assertTrue(pdf.startswith(b"%PDF"))
        for expected in ("TAX INVOICE", invoice.number, "27AAPFU0939F1ZV", "CGST @ 9%", "SGST @ 9%", "Rs. 499.00", "998314"):
            self.assertIn(expected, text)

    def test_a_plain_invoice_pdf_explains_why_there_is_no_tax(self):
        from pypdf import PdfReader

        from billing.services.gst_invoice_service import GSTInvoiceService

        invoice = self._issue()
        text = PdfReader(io.BytesIO(GSTInvoiceService.render_pdf(invoice))).pages[0].extract_text()

        self.assertIn("INVOICE", text)
        self.assertNotIn("TAX INVOICE", text)
        self.assertNotIn("CGST", text)
        self.assertIn("not registered", text)


class InvoiceEndpointTests(TestCase):
    def setUp(self):
        from billing.services.gst_invoice_service import GSTInvoiceService

        self.user = _make_user("owner@example.in")
        self.other = _make_user("other@example.in")
        self.invoice = GSTInvoiceService.issue(
            user=self.user, payment_id="pay_owner", razorpay_subscription_id="sub_1",
            amount_paise=49900, plan_id="pro", billing_period="monthly",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_list_shows_only_your_invoices(self):
        response = self.client.get("/api/billing/invoices/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["number"] for row in response.data], [self.invoice.number])
        self.assertEqual(response.data[0]["total_display"], "₹499.00")

        other = APIClient()
        other.force_authenticate(self.other)
        self.assertEqual(other.get("/api/billing/invoices/").data, [])

    def test_download_returns_a_pdf(self):
        response = self.client.get(f"/api/billing/invoices/{self.invoice.id}/pdf/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("IntelliResume-IR-", response["Content-Disposition"])
        self.assertNotIn("/", response["Content-Disposition"].split("filename=")[1])

    def test_someone_elses_invoice_is_not_found(self):
        other = APIClient()
        other.force_authenticate(self.other)

        response = other.get(f"/api/billing/invoices/{self.invoice.id}/pdf/")

        self.assertEqual(response.status_code, 404)

    def test_invoices_require_sign_in(self):
        self.assertEqual(APIClient().get("/api/billing/invoices/").status_code, 401)


@override_settings(**RZP)
class RupeeCancellationTests(TestCase):
    def setUp(self):
        self.user = _make_user("cancel@example.in")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.period_end = timezone.now() + timedelta(days=12)
        Subscription.objects.create(
            user=self.user, plan_id="pro", status="active", payment_processor="razorpay",
            razorpay_subscription_id="sub_rzp_1", current_period_end=self.period_end,
        )

    def test_cancelling_stops_renewal_but_keeps_the_paid_period(self):
        with patch.object(RazorpayService, "cancel_subscription") as cancel:
            response = self.client.post("/api/billing/subscription/cancel/")

        self.assertEqual(response.status_code, 200)
        cancel.assert_called_once_with("sub_rzp_1", at_cycle_end=True)
        self.assertTrue(response.data["is_active"])
        self.assertTrue(response.data["cancel_at_period_end"])

    def test_a_stripe_subscriber_is_pointed_elsewhere(self):
        Subscription.objects.filter(user=self.user).update(payment_processor="stripe")

        with patch.object(RazorpayService, "cancel_subscription") as cancel:
            response = self.client.post("/api/billing/subscription/cancel/")

        self.assertEqual(response.status_code, 400)
        cancel.assert_not_called()

    def test_the_stripe_portal_says_rupee_plans_are_managed_in_settings(self):
        response = self.client.post("/api/billing/portal/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PORTAL_NOT_AVAILABLE")

    def test_status_reports_the_processor(self):
        response = self.client.get("/api/billing/subscription/")

        self.assertEqual(response.data["payment_processor"], "razorpay")
        self.assertFalse(response.data["cancel_at_period_end"])


@override_settings(**RZP)
class RupeeAccountLifecycleTests(TestCase):
    def setUp(self):
        from billing.services.gst_invoice_service import GSTInvoiceService

        self.user = _make_user("leaving@example.in")
        BillingProfile.objects.create(user=self.user, state_code="27", gstin="", legal_name="")
        Subscription.objects.create(
            user=self.user, plan_id="pro", status="active", payment_processor="razorpay",
            razorpay_subscription_id="sub_leaving",
            current_period_end=timezone.now() + timedelta(days=5),
        )
        self.invoice = GSTInvoiceService.issue(
            user=self.user, payment_id="pay_leaving", razorpay_subscription_id="sub_leaving",
            amount_paise=49900, plan_id="pro", billing_period="monthly",
        )

    def test_export_includes_billing_details_and_invoices(self):
        from authentication.services.account_export_service import AccountExportService

        export = AccountExportService.build_export(self.user)

        self.assertEqual(export["billing_profile"]["state_code"], "27")
        self.assertEqual([row["number"] for row in export["invoices"]], [self.invoice.number])

    def test_deletion_cancels_the_mandate_immediately(self):
        from authentication.services.account_deletion_service import AccountDeletionService

        with patch.object(RazorpayService, "cancel_subscription") as cancel:
            AccountDeletionService.delete_account(self.user)

        cancel.assert_called_once_with("sub_leaving", at_cycle_end=False)

    def test_deletion_keeps_the_invoice_as_a_tax_record(self):
        """Seventy-two months, by law — with the user link gone and the
        printed buyer details intact."""
        from authentication.services.account_deletion_service import AccountDeletionService

        with patch.object(RazorpayService, "cancel_subscription"):
            AccountDeletionService.delete_account(self.user)

        invoice = GSTInvoice.objects.get(pk=self.invoice.pk)
        self.assertIsNone(invoice.user_id)
        self.assertEqual(invoice.buyer_email, "leaving@example.in")
        self.assertFalse(BillingProfile.objects.exists())

    def test_deletion_proceeds_even_if_razorpay_is_down(self):
        from authentication.services.account_deletion_service import AccountDeletionService
        from billing.services.razorpay_service import RazorpayAPIError

        with patch.object(RazorpayService, "cancel_subscription", side_effect=RazorpayAPIError()):
            AccountDeletionService.delete_account(self.user)

        self.assertFalse(User.objects.filter(email="leaving@example.in").exists())
