"""Tests for admin-editable prices and time-boxed promotional offers.

The invariant worth defending above all others: **what we display and what
Stripe charges cannot disagree**. A discount that exists only on the
pricing page would show one number and take another from the customer's
card. That is misleading pricing, not a cosmetic bug, so most of these
tests are about the ways an offer must refuse to apply.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import PlanPrice, PricingOffer
from billing.plans import catalog_price, find_plan_by_stripe_price_id, get_price


def make_offer(**overrides):
    now = timezone.now()
    defaults = {
        "campaign": "Diwali 2026",
        "label": "Diwali Offer",
        "badge": "33% OFF",
        "plan_id": "pro",
        "currency": "EUR",
        "billing_period": "monthly",
        "amount_cents": 999,
        "stripe_price_id": "price_diwali_eur_monthly",
        "starts_at": now - timedelta(days=1),
        "ends_at": now + timedelta(days=6),
    }
    defaults.update(overrides)
    return PricingOffer.objects.create(**defaults)


class BasePriceOverrideTests(TestCase):
    def test_empty_database_falls_back_to_the_built_in_catalog(self):
        """An empty table must leave the product working exactly as before."""
        price = get_price("pro", "EUR", "monthly")

        self.assertEqual(price["amount_cents"], catalog_price("pro", "EUR", "monthly")["amount_cents"])
        self.assertIsNone(price["offer"])

    def test_admin_price_overrides_the_catalog(self):
        PlanPrice.objects.create(
            plan_id="pro", currency="EUR", billing_period="monthly",
            amount_cents=1799, stripe_price_id="price_new",
        )

        price = get_price("pro", "EUR", "monthly")

        self.assertEqual(price["amount_cents"], 1799)
        self.assertEqual(price["stripe_price_id"], "price_new")

    def test_inactive_admin_price_is_ignored(self):
        PlanPrice.objects.create(
            plan_id="pro", currency="EUR", billing_period="monthly",
            amount_cents=1799, stripe_price_id="price_new", is_active=False,
        )

        self.assertEqual(
            get_price("pro", "EUR", "monthly")["amount_cents"],
            catalog_price("pro", "EUR", "monthly")["amount_cents"],
        )

    def test_admin_price_without_a_stripe_id_is_not_purchasable(self):
        """Displaying a price we cannot charge is safer than charging a
        different one, so the combination goes unpurchasable rather than
        silently falling back to the old Stripe price."""
        PlanPrice.objects.create(
            plan_id="pro", currency="EUR", billing_period="monthly",
            amount_cents=1799, stripe_price_id="",
        )

        price = get_price("pro", "EUR", "monthly")

        self.assertEqual(price["amount_cents"], 1799)
        self.assertEqual(price["stripe_price_id"], "")

    def test_free_plan_still_has_no_price(self):
        self.assertIsNone(get_price("free", "EUR", "monthly"))


class OfferApplicationTests(TestCase):
    def test_live_offer_replaces_the_amount_and_the_stripe_price(self):
        make_offer()

        price = get_price("pro", "EUR", "monthly")

        self.assertEqual(price["amount_cents"], 999)
        # The charge follows the display. This is the whole point.
        self.assertEqual(price["stripe_price_id"], "price_diwali_eur_monthly")
        self.assertEqual(price["original_amount_cents"], 1499)
        self.assertEqual(price["offer"]["label"], "Diwali Offer")
        self.assertEqual(price["offer"]["badge"], "33% OFF")

    def test_offer_only_applies_to_its_own_currency_and_period(self):
        make_offer()

        self.assertIsNone(get_price("pro", "EUR", "annual")["offer"])
        self.assertIsNone(get_price("pro", "GBP", "monthly")["offer"])

    def test_offer_only_applies_to_its_own_plan(self):
        make_offer()

        self.assertIsNone(get_price("max", "EUR", "monthly")["offer"])

    def test_offer_stacks_on_top_of_an_admin_price(self):
        PlanPrice.objects.create(
            plan_id="pro", currency="EUR", billing_period="monthly",
            amount_cents=1999, stripe_price_id="price_new",
        )
        make_offer(amount_cents=1200)

        price = get_price("pro", "EUR", "monthly")

        self.assertEqual(price["amount_cents"], 1200)
        # Struck-through price is the current standard one, not the
        # long-gone catalog literal.
        self.assertEqual(price["original_amount_cents"], 1999)


class OfferRefusesToApplyTests(TestCase):
    """Every way an offer must decline rather than mislead."""

    def test_offer_without_a_stripe_price_never_activates(self):
        """The most important one: it would advertise a discount we have no
        way to actually charge."""
        make_offer(stripe_price_id="")

        price = get_price("pro", "EUR", "monthly")

        self.assertIsNone(price["offer"])
        self.assertEqual(price["amount_cents"], 1499)

    def test_offer_before_its_start_date_is_not_applied(self):
        now = timezone.now()
        make_offer(starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=8))

        self.assertIsNone(get_price("pro", "EUR", "monthly")["offer"])

    def test_expired_offer_is_not_applied(self):
        now = timezone.now()
        make_offer(starts_at=now - timedelta(days=8), ends_at=now - timedelta(days=1))

        self.assertIsNone(get_price("pro", "EUR", "monthly")["offer"])

    def test_offer_switched_off_ends_the_sale_immediately(self):
        make_offer(is_active=False)

        self.assertIsNone(get_price("pro", "EUR", "monthly")["offer"])

    def test_offer_priced_above_the_standard_price_is_ignored(self):
        """An 'offer' that costs more is a misconfiguration. Charging above
        the normal rate because a row said so is the one outcome worth
        refusing outright."""
        make_offer(amount_cents=2500)

        price = get_price("pro", "EUR", "monthly")

        self.assertIsNone(price["offer"])
        self.assertEqual(price["amount_cents"], 1499)

    def test_offer_equal_to_the_standard_price_is_ignored(self):
        make_offer(amount_cents=1499)

        self.assertIsNone(get_price("pro", "EUR", "monthly")["offer"])

    def test_overlapping_campaigns_give_the_customer_the_cheaper_one(self):
        make_offer(campaign="Diwali", amount_cents=1200, stripe_price_id="price_a")
        make_offer(campaign="Black Friday", amount_cents=899, stripe_price_id="price_b")

        price = get_price("pro", "EUR", "monthly")

        self.assertEqual(price["amount_cents"], 899)
        self.assertEqual(price["stripe_price_id"], "price_b")


class OfferWebhookLookupTests(TestCase):
    def test_a_subscription_bought_on_an_offer_price_maps_back_to_its_plan(self):
        """Without this a sale customer pays and their plan is never set —
        they get nothing for their money."""
        make_offer()

        self.assertEqual(
            find_plan_by_stripe_price_id("price_diwali_eur_monthly"),
            ("pro", "EUR", "monthly"),
        )

    def test_a_subscription_bought_on_an_admin_price_maps_back_to_its_plan(self):
        PlanPrice.objects.create(
            plan_id="max", currency="GBP", billing_period="annual",
            amount_cents=18900, stripe_price_id="price_admin_max",
        )

        self.assertEqual(
            find_plan_by_stripe_price_id("price_admin_max"), ("max", "GBP", "annual")
        )

    def test_an_unknown_price_is_still_unrecognised(self):
        self.assertIsNone(find_plan_by_stripe_price_id("price_someone_elses"))


class PricingAPITests(TestCase):
    def test_offer_is_visible_on_the_public_pricing_endpoint(self):
        make_offer()

        response = APIClient().get("/api/billing/plans/?currency=EUR")
        pro = next(p for p in response.data if p["id"] == "pro")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(pro["monthly"]["amount_cents"], 999)
        self.assertEqual(pro["monthly"]["offer"]["label"], "Diwali Offer")
        self.assertEqual(pro["monthly"]["original_amount_cents"], 1499)
        self.assertTrue(pro["monthly"]["purchasable"])

    def test_no_offer_leaves_the_promotional_fields_null(self):
        response = APIClient().get("/api/billing/plans/?currency=EUR")
        pro = next(p for p in response.data if p["id"] == "pro")

        self.assertIsNone(pro["monthly"]["offer"])
        self.assertIsNone(pro["monthly"]["original_amount_cents"])

    def test_checkout_uses_the_offer_price_the_customer_was_shown(self):
        """Display and charge read from the same resolver — this asserts
        they stay joined."""
        make_offer()

        shown = APIClient().get("/api/billing/plans/?currency=EUR").data
        pro = next(p for p in shown if p["id"] == "pro")
        charged = get_price("pro", "EUR", "monthly")

        self.assertEqual(pro["monthly"]["amount_cents"], charged["amount_cents"])
        self.assertEqual(charged["stripe_price_id"], "price_diwali_eur_monthly")
