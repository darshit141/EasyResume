"""Tests for the Max tier and configurable reply tracking.

Two things these exist to defend:

* A second paid tier must not be treated as free anywhere. Every "is this
  user on pro?" check that stayed hardcoded would show ads to, or withhold
  paid behaviour from, the most expensive subscribers on the system.
* Which reply-tracking strategy a plan gets is an operator decision made in
  the admin tab, not a constant. The Gmail route depends on a compliance
  assessment that can lapse or arrive late, and switching it off must not
  need a deploy.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from api.models import SystemConfiguration
from authentication.models import User
from billing.entitlements import entitlements_for, is_paid_plan
from billing.models import Subscription
from billing.plans import PLANS_BY_ID, get_price
from billing.services.entitlements_service import EntitlementsService


def config(key, value, value_type="string"):
    SystemConfiguration.objects.update_or_create(
        key=key,
        defaults={
            "value": value,
            "value_type": value_type,
            "category": "limits",
            "description": "",
        },
    )


class MaxPlanCatalogTests(TestCase):
    def test_max_is_in_the_catalog(self):
        plan = PLANS_BY_ID.get("max")

        self.assertIsNotNone(plan)
        self.assertEqual(plan["name"], "Max")

    def test_max_is_priced_above_pro_in_every_currency_and_period(self):
        for currency in ("EUR", "GBP"):
            for period in ("monthly", "annual"):
                pro = get_price("pro", currency, period)["amount_cents"]
                mx = get_price("max", currency, period)["amount_cents"]
                self.assertGreater(mx, pro, f"{currency}/{period}")

    def test_max_price_ids_are_blank_until_configured(self):
        """Same rule as Pro: an unset Stripe price must fail checkout rather
        than silently charge the wrong amount."""
        self.assertEqual(get_price("max", "EUR", "monthly")["stripe_price_id"], "")

    def test_price_override_env_key_is_per_plan(self):
        """The helper used to hardcode PRO_ into the amount's env key, so a
        second tier would have read Pro's override as its own.

        Exercised through `_price` directly rather than by reloading the
        module: reloading rebuilds objects other modules already hold
        references to, which silently breaks unrelated tests later in the
        run.
        """
        from billing.plans import _price

        with patch.dict(os.environ, {"PRO_PRICE_EUR_MONTHLY_CENTS": "111"}):
            pro = _price("pro", "EUR", "monthly", 1499, "UNSET")
            mx = _price("max", "EUR", "monthly", 2999, "UNSET")

        self.assertEqual(pro["amount_cents"], 111)
        self.assertEqual(mx["amount_cents"], 2999)


class MaxEntitlementTests(TestCase):
    def test_max_resolves_to_its_own_entitlements(self):
        ent = entitlements_for("max", True)

        self.assertEqual(ent["plan_id"], "max")
        self.assertFalse(ent["ads"])
        self.assertTrue(ent["docx_export"])

    def test_max_gets_everything_pro_does(self):
        pro = entitlements_for("pro", True)
        mx = entitlements_for("max", True)

        for key in (
            "docx_export",
            "tailored_resume_version_history",
            "resume_template_customization",
        ):
            self.assertEqual(mx[key], pro[key], key)
        self.assertIsNone(mx["ai_calls_per_month"])

    def test_max_sends_more_applications_than_pro(self):
        self.assertGreater(
            entitlements_for("max", True)["job_applications_per_month"],
            entitlements_for("pro", True)["job_applications_per_month"],
        )

    def test_inactive_max_subscription_falls_back_to_free(self):
        ent = entitlements_for("max", False)

        self.assertEqual(ent["plan_id"], "free")
        self.assertTrue(ent["ads"])

    def test_unknown_plan_id_falls_back_to_free(self):
        self.assertEqual(
            entitlements_for("enterprise-that-does-not-exist", True)["plan_id"], "free"
        )

    def test_is_paid_plan_covers_every_paid_tier(self):
        self.assertTrue(is_paid_plan("pro"))
        self.assertTrue(is_paid_plan("max"))
        self.assertFalse(is_paid_plan("free"))
        self.assertFalse(is_paid_plan(None))


class ReplyTrackingConfigTests(TestCase):
    def test_defaults_form_a_ladder(self):
        self.assertEqual(entitlements_for(None, False)["reply_tracking"], "none")
        self.assertEqual(entitlements_for("pro", True)["reply_tracking"], "alias")
        self.assertEqual(entitlements_for("max", True)["reply_tracking"], "gmail")

    def test_operator_can_move_gmail_down_to_pro_without_a_deploy(self):
        config("REPLY_TRACKING_PRO", "gmail")

        self.assertEqual(entitlements_for("pro", True)["reply_tracking"], "gmail")

    def test_operator_can_drop_max_back_to_alias_while_casa_is_pending(self):
        config("REPLY_TRACKING_MAX", "alias")

        self.assertEqual(entitlements_for("max", True)["reply_tracking"], "alias")

    def test_free_can_be_given_alias_tracking(self):
        config("REPLY_TRACKING_FREE", "alias")

        self.assertEqual(entitlements_for(None, False)["reply_tracking"], "alias")

    def test_master_switch_off_beats_every_per_plan_setting(self):
        config("REPLY_TRACKING_PRO", "gmail")
        config("REPLY_TRACKING_MAX", "gmail")
        config("REPLY_TRACKING_ENABLED", "false", "boolean")

        self.assertEqual(entitlements_for("pro", True)["reply_tracking"], "none")
        self.assertEqual(entitlements_for("max", True)["reply_tracking"], "none")

    def test_unrecognised_strategy_resolves_to_none(self):
        """Guessing would mean either promising tracking we don't perform,
        or reading a mailbox under a setting nobody chose."""
        config("REPLY_TRACKING_MAX", "imap-someday")

        self.assertEqual(entitlements_for("max", True)["reply_tracking"], "none")

    def test_strategy_is_case_and_whitespace_tolerant(self):
        config("REPLY_TRACKING_MAX", "  Gmail  ")

        self.assertEqual(entitlements_for("max", True)["reply_tracking"], "gmail")

    def test_application_cap_is_admin_tunable_per_paid_plan(self):
        config("MAX_JOB_APPLICATIONS_PER_MONTH", "750", "integer")

        self.assertEqual(entitlements_for("max", True)["job_applications_per_month"], 750)


class MaxSubscriberBehaviourTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Max Sub", email="max@example.com", phone="555", password="pw"
        )
        Subscription.objects.create(user=self.user, plan_id="max", status="active")

    def test_max_subscriber_is_not_shown_ads(self):
        self.assertFalse(EntitlementsService.for_user(self.user)["ads"])

    def test_max_subscriber_is_not_offered_rewarded_ads(self):
        """The check behind this was `plan_id == "pro"`, which would have
        offered ad-for-credit deals to the highest-paying tier."""
        from billing.services.rewarded_ad_providers import get_rewarded_ad_provider
        from billing.views.credits_view import _rewarded_ad_state

        state = _rewarded_ad_state(self.user, get_rewarded_ad_provider())

        self.assertFalse(state["enabled"])

    def test_usage_summary_reports_the_tier_and_its_reply_tracking(self):
        summary = EntitlementsService.usage_summary(self.user)

        self.assertEqual(summary["plan_id"], "max")
        self.assertEqual(summary["reply_tracking"], "gmail")
        self.assertFalse(summary["shows_ads"])

    def test_max_subscriber_can_send_beyond_the_pro_cap(self):
        # 250 is over Pro's 200 ceiling but under Max's 500, so this must
        # not raise.
        with patch.object(
            EntitlementsService, "applications_sent_this_month", return_value=250
        ):
            EntitlementsService.check_can_send_application(self.user)


class UnpublishedPlanTests(TestCase):
    """A tier can be finished, tested and deployed before it is advertised.

    Max is deliberately unpublished: two of its marketing bullets describe
    work that does not exist yet, and putting those on a public pricing
    page would advertise what we cannot deliver.
    """

    def test_max_is_not_published_by_default(self):
        from billing.plans import published_plans

        self.assertNotIn("max", {plan["id"] for plan in published_plans()})

    def test_public_pricing_page_does_not_show_max(self):
        from rest_framework.test import APIClient

        response = APIClient().get("/api/billing/plans/")
        ids = {plan["id"] for plan in response.data}

        self.assertEqual(ids, {"free", "pro"})

    def test_checkout_refuses_an_unpublished_plan(self):
        """Hiding it from the page while still allowing checkout by posting
        the id would be theatre."""
        from billing.services.stripe_service import BillingError, StripeService

        with self.assertRaises(BillingError):
            StripeService.create_checkout_session(
                user=User.objects.create_user(
                    name="Buyer", email="buyer@example.com", phone="9", password="pw"
                ),
                plan_id="max",
                currency="EUR",
                billing_period="monthly",
            )

    def test_max_entitlements_still_work_for_an_existing_subscriber(self):
        """Unpublishing closes the shopfront; it must not strip anyone who
        already has the plan."""
        self.assertEqual(entitlements_for("max", True)["plan_id"], "max")
