"""Free launch offer (billing.launch): everything free, metered per window."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from billing import launch
from billing.entitlements import entitlements_for
from billing.models import AIUsageLog, Subscription
from billing.services.entitlements_service import EntitlementsService, PlanLimitReachedError
from billing.tests_entitlements import _make_user, _generate_versions
from resumes.models import JobResumeCurationVersion

LAUNCH_ON = dict(
    LAUNCH_FREE_ACCESS=True,
    LAUNCH_FREE_UNTIL="",
    LAUNCH_QUOTA_WINDOW_HOURS=12,
    LAUNCH_TAILORED_RESUMES_PER_WINDOW=3,
    LAUNCH_AI_CALLS_PER_WINDOW=5,
)


def _log_ai(user, count, hours_ago=0):
    for _ in range(count):
        row = AIUsageLog.objects.create(user=user, action=AIUsageLog.ACTION_JOB_ANALYSIS, provider="groq")
        if hours_ago:
            AIUsageLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=hours_ago))


class LaunchSwitchTests(TestCase):
    @override_settings(LAUNCH_FREE_ACCESS=False)
    def test_off_by_default_keeps_the_monthly_free_plan(self):
        ent = entitlements_for(None, False)
        self.assertFalse(ent["launch_offer"])
        self.assertIsNone(ent["quota_window_hours"])
        self.assertFalse(ent["docx_export"])

    @override_settings(**LAUNCH_ON)
    def test_on_unlocks_paid_features_with_window_quotas(self):
        ent = entitlements_for(None, False)
        self.assertTrue(ent["launch_offer"])
        self.assertEqual(ent["quota_window_hours"], 12)
        self.assertEqual(ent["tailored_resumes_per_month"], 3)
        self.assertEqual(ent["ai_calls_per_month"], 5)
        self.assertTrue(ent["docx_export"])
        self.assertTrue(ent["tailored_resume_version_history"])
        self.assertTrue(ent["resume_template_customization"])
        self.assertNotIn("openrouter", ent["allowed_ai_providers"])

    @override_settings(**LAUNCH_ON)
    def test_paid_subscribers_keep_their_own_plan(self):
        ent = entitlements_for("pro", True)
        self.assertEqual(ent["plan_id"], "pro")
        self.assertIsNone(ent["ai_calls_per_month"])
        self.assertFalse(ent["launch_offer"])

    @override_settings(**{**LAUNCH_ON, "LAUNCH_FREE_UNTIL": "2020-01-01"})
    def test_offer_switches_itself_off_after_its_end_date(self):
        self.assertFalse(launch.is_active())
        self.assertFalse(entitlements_for(None, False)["launch_offer"])

    @override_settings(**{**LAUNCH_ON, "LAUNCH_FREE_UNTIL": "not-a-date"})
    def test_an_unparseable_end_date_means_no_end_date(self):
        self.assertTrue(launch.is_active())

    @override_settings(**LAUNCH_ON)
    def test_admin_configuration_overrides_settings_live(self):
        from api.models import SystemConfiguration

        SystemConfiguration.objects.update_or_create(
            key="LAUNCH_AI_CALLS_PER_WINDOW", defaults={"value": "9", "value_type": "integer"}
        )
        self.assertEqual(entitlements_for(None, False)["ai_calls_per_month"], 9)
        SystemConfiguration.objects.update_or_create(
            key="LAUNCH_FREE_ACCESS", defaults={"value": "false", "value_type": "boolean"}
        )
        self.assertFalse(entitlements_for(None, False)["launch_offer"])


@override_settings(**LAUNCH_ON)
class LaunchWindowQuotaTests(TestCase):
    def setUp(self):
        self.user = _make_user("launch@example.com")

    def test_ai_calls_are_capped_per_rolling_window(self):
        _log_ai(self.user, 5)
        with self.assertRaises(PlanLimitReachedError) as ctx:
            EntitlementsService.check_can_use_ai(self.user)
        payload = ctx.exception.to_payload()
        self.assertEqual(payload["feature"], "ai_usage")
        self.assertEqual(payload["window_hours"], 12)
        self.assertTrue(payload["launch_offer"])
        self.assertNotIn("Upgrade", payload["detail"])

    def test_usage_older_than_the_window_no_longer_counts(self):
        _log_ai(self.user, 5, hours_ago=13)
        EntitlementsService.check_can_use_ai(self.user)  # does not raise

    def test_reset_time_is_when_the_oldest_use_ages_out(self):
        _log_ai(self.user, 1, hours_ago=10)
        _log_ai(self.user, 4)
        with self.assertRaises(PlanLimitReachedError) as ctx:
            EntitlementsService.check_can_use_ai(self.user)
        resets_at = ctx.exception.to_payload()["resets_at"]
        expected = timezone.now() + timedelta(hours=2)
        self.assertLess(abs((timezone.datetime.fromisoformat(resets_at) - expected).total_seconds()), 60)

    def test_tailored_resumes_are_capped_per_window(self):
        _generate_versions(self.user, 3)
        with self.assertRaises(PlanLimitReachedError) as ctx:
            EntitlementsService.check_can_generate_tailored_resume(self.user)
        self.assertEqual(ctx.exception.to_payload()["feature"], "tailored_resumes")

    def test_old_tailored_resumes_do_not_count(self):
        _generate_versions(self.user, 3)
        JobResumeCurationVersion.objects.update(created_at=timezone.now() - timedelta(hours=13))
        EntitlementsService.check_can_generate_tailored_resume(self.user)

    def test_docx_export_is_free_during_launch(self):
        EntitlementsService.check_feature(self.user, "docx_export")

    def test_usage_summary_reports_the_window(self):
        _log_ai(self.user, 2)
        summary = EntitlementsService.usage_summary(self.user)
        self.assertTrue(summary["launch_offer"])
        self.assertEqual(summary["quota_window_hours"], 12)
        self.assertEqual(summary["ai_calls_used"], 2)
        self.assertEqual(summary["ai_calls_remaining"], 3)
        self.assertEqual(summary["tailored_resumes_limit"], 3)


@override_settings(**LAUNCH_ON)
class LaunchCheckoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _make_user("buyer@example.com")
        self.client.force_authenticate(self.user)

    def test_checkout_is_closed(self):
        response = self.client.post(
            "/api/billing/checkout/", {"plan_id": "pro", "currency": "INR"}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "PAYMENTS_PAUSED")

    def test_payment_options_announce_the_offer(self):
        body = self.client.get("/api/billing/payment-options/").json()
        self.assertEqual(body["currencies"], [])
        self.assertTrue(body["launch_offer"]["active"])
        self.assertEqual(body["launch_offer"]["window_hours"], 12)

    def test_free_plan_card_describes_the_offer(self):
        plans = self.client.get("/api/billing/plans/?currency=INR").json()
        free = next(p for p in plans if p["id"] == "free")
        self.assertTrue(any("every 12 hours" in f for f in free["features"]))
