import hashlib
import hmac
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import User
from billing.models import CreditGrant, Subscription
from billing.services.credit_service import CreditService, RewardedAdCapReachedError
from billing.services.entitlements_service import EntitlementsService, PlanLimitReachedError
from billing.services.rewarded_ad_providers import (
    NullRewardedAdProvider,
    PartnerRewardedAdProvider,
    RewardedAdVerificationError,
    get_rewarded_ad_provider,
    issue_ticket,
    read_ticket,
)


def _user(email="c@example.com"):
    return User.objects.create_user(
        name="Cred Tester", email=email, phone=f"9{abs(hash(email)) % 1000000000:09d}",
        password="safe-password",
    )


PARTNER_ENV = dict(
    PARTNER_REWARDED_SCRIPT_URL="https://cdn.example.net/rv.js",
    PARTNER_REWARDED_PLACEMENT_ID="zone-42",
    PARTNER_REWARDED_SHOW_FN="rv.show",
    PARTNER_REWARDED_S2S_SECRET="shhh-secret",
)


class CreditLedgerTests(TestCase):
    def setUp(self):
        self.user = _user()

    def test_active_balance_sums_only_unexpired_grants(self):
        now = timezone.now()
        CreditGrant.objects.create(user=self.user, amount=2, reason="referral", expires_at=now + timedelta(days=1))
        CreditGrant.objects.create(user=self.user, amount=5, reason="referral", expires_at=now + timedelta(days=3))
        CreditGrant.objects.create(user=self.user, amount=9, reason="referral", expires_at=now - timedelta(minutes=1))
        self.assertEqual(CreditService.active_balance(self.user), 7)

    def test_rewarded_grant_is_idempotent_on_transaction_id(self):
        g1, created1 = CreditService.grant_rewarded_ad_credit(
            self.user, amount=1, provider="partner", external_id="txn-1"
        )
        g2, created2 = CreditService.grant_rewarded_ad_credit(
            self.user, amount=1, provider="partner", external_id="txn-1"
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(g1.pk, g2.pk)
        self.assertEqual(CreditGrant.objects.filter(user=self.user).count(), 1)

    @override_settings(REWARDED_AD_MAX_PER_DAY=2)
    def test_daily_cap_is_enforced(self):
        CreditService.grant_rewarded_ad_credit(self.user, amount=1, provider="partner", external_id="a")
        CreditService.grant_rewarded_ad_credit(self.user, amount=1, provider="partner", external_id="b")
        with self.assertRaises(RewardedAdCapReachedError):
            CreditService.grant_rewarded_ad_credit(self.user, amount=1, provider="partner", external_id="c")
        self.assertFalse(CreditService.can_watch_rewarded_ad(self.user))

    @override_settings(REWARDED_AD_CREDIT_TTL_DAYS=5)
    def test_grant_expiry_follows_configured_ttl(self):
        grant, _ = CreditService.grant_rewarded_ad_credit(
            self.user, amount=1, provider="partner", external_id="ttl"
        )
        delta = grant.expires_at - grant.granted_at
        self.assertGreater(delta, timedelta(days=4, hours=23))
        self.assertLess(delta, timedelta(days=5, minutes=1))


class EntitlementsWithCreditsTests(TestCase):
    def setUp(self):
        self.user = _user()

    @override_settings(FREE_TAILORED_RESUMES_PER_MONTH=2, FREE_AI_CALLS_PER_MONTH=3)
    def test_bonus_credit_raises_both_free_ceilings(self):
        from resumes.models import JobResumeCuration, JobResumeCurationVersion

        curation = JobResumeCuration.objects.create(
            user=self.user, job_title="Dev", job_description="x" * 40, status="completed",
        )
        for v in range(1, 3):  # use the 2 free tailored resumes
            JobResumeCurationVersion.objects.create(curation=curation, version=v)

        with self.assertRaises(PlanLimitReachedError):
            EntitlementsService.check_can_generate_tailored_resume(self.user)

        CreditGrant.objects.create(
            user=self.user, amount=1, reason="rewarded_ad",
            expires_at=timezone.now() + timedelta(days=3),
        )
        # Now the effective limit is 3 and only 2 are used — allowed again.
        EntitlementsService.check_can_generate_tailored_resume(self.user)

    def test_expired_credit_does_not_lift_the_ceiling(self):
        CreditGrant.objects.create(
            user=self.user, amount=5, reason="rewarded_ad",
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(EntitlementsService.bonus_credits(self.user), 0)

    @override_settings(FREE_AI_CALLS_PER_MONTH=3)
    def test_usage_summary_reports_bonus_and_folds_it_into_remaining(self):
        CreditGrant.objects.create(
            user=self.user, amount=2, reason="rewarded_ad",
            expires_at=timezone.now() + timedelta(days=3),
        )
        summary = EntitlementsService.usage_summary(self.user)
        self.assertEqual(summary["bonus_credits"], 2)
        self.assertEqual(summary["ai_calls_limit"], 5)
        self.assertEqual(summary["ai_calls_remaining"], 5)


class TicketTests(TestCase):
    def test_ticket_roundtrips_to_the_user_id(self):
        self.assertEqual(read_ticket(issue_ticket(77)), 77)

    def test_tampered_ticket_is_rejected(self):
        with self.assertRaises(RewardedAdVerificationError):
            read_ticket(issue_ticket(1) + "x")


class ProviderResolutionTests(TestCase):
    def test_disabled_feature_resolves_to_null_provider(self):
        with override_settings(REWARDED_ADS_ENABLED="False"):
            self.assertIsInstance(get_rewarded_ad_provider(), NullRewardedAdProvider)

    @override_settings(REWARDED_ADS_ENABLED="True", REWARDED_AD_PROVIDER="partner")
    def test_selected_but_unconfigured_provider_resolves_to_null(self):
        self.assertIsInstance(get_rewarded_ad_provider(), NullRewardedAdProvider)

    @override_settings(REWARDED_ADS_ENABLED="True", REWARDED_AD_PROVIDER="partner", **PARTNER_ENV)
    def test_configured_partner_provider_is_selected(self):
        provider = get_rewarded_ad_provider()
        self.assertIsInstance(provider, PartnerRewardedAdProvider)
        cfg = provider.client_config()
        self.assertEqual(cfg["placement_id"], "zone-42")
        self.assertNotIn("secret", str(cfg).lower())


class PartnerCallbackVerificationTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.provider = PartnerRewardedAdProvider()

    def _sig(self, ticket, amount, txn):
        return hmac.new(
            b"shhh-secret", f"{ticket}:{amount}:{txn}".encode(), hashlib.sha256
        ).hexdigest()

    @override_settings(**PARTNER_ENV)
    def test_valid_signature_yields_the_reward(self):
        ticket = issue_ticket(self.user.id)
        params = {"user_id": ticket, "amount": "1", "txn_id": "abc", "sig": self._sig(ticket, "1", "abc")}
        reward = self.provider.verify_callback(method="GET", params=params, body=b"")
        self.assertEqual(reward.user_id, self.user.id)
        self.assertEqual(reward.external_id, "abc")

    @override_settings(**PARTNER_ENV)
    def test_bad_signature_is_rejected(self):
        ticket = issue_ticket(self.user.id)
        params = {"user_id": ticket, "amount": "1", "txn_id": "abc", "sig": "deadbeef"}
        with self.assertRaises(RewardedAdVerificationError):
            self.provider.verify_callback(method="GET", params=params, body=b"")


@override_settings(REWARDED_ADS_ENABLED="True", REWARDED_AD_PROVIDER="partner", **PARTNER_ENV)
class RewardedAdEndpointTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _callback_url(self):
        return reverse("billing-rewarded-ad-callback")

    def _sig(self, ticket, amount, txn):
        return hmac.new(
            b"shhh-secret", f"{ticket}:{amount}:{txn}".encode(), hashlib.sha256
        ).hexdigest()

    def test_ticket_endpoint_returns_a_ticket_when_available(self):
        res = self.client.post(reverse("billing-rewarded-ad-ticket"))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("ticket", res.data)
        self.assertEqual(res.data["provider"], "partner")

    def test_pro_user_cannot_get_a_ticket(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")
        res = self.client.post(reverse("billing-rewarded-ad-ticket"))
        self.assertEqual(res.status_code, 409)

    def test_valid_callback_grants_a_credit_and_replay_is_idempotent(self):
        ticket = issue_ticket(self.user.id)
        anon = APIClient()
        qs = {"user_id": ticket, "amount": "1", "txn_id": "T1", "sig": self._sig(ticket, "1", "T1")}
        r1 = anon.get(self._callback_url(), qs)
        self.assertEqual(r1.status_code, 200, r1.data)
        self.assertEqual(r1.data["credited"], 1)
        self.assertFalse(r1.data["duplicate"])

        r2 = anon.get(self._callback_url(), qs)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.data["duplicate"])

        self.assertEqual(CreditService.active_balance(self.user), 1)

    def test_forged_callback_is_forbidden(self):
        ticket = issue_ticket(self.user.id)
        anon = APIClient()
        qs = {"user_id": ticket, "amount": "1", "txn_id": "T2", "sig": "not-a-real-signature"}
        res = anon.get(self._callback_url(), qs)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(CreditService.active_balance(self.user), 0)

    @override_settings(REWARDED_ADS_ENABLED="False")
    def test_callback_is_404_when_the_feature_is_off(self):
        anon = APIClient()
        res = anon.get(self._callback_url(), {"user_id": "x", "amount": "1", "txn_id": "T", "sig": "s"})
        self.assertEqual(res.status_code, 404)
