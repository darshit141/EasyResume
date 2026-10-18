"""Site-wide daily AI ceiling (billing.services.ai_capacity)."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from billing.models import AIUsageLog, Subscription
from billing.services import ai_capacity
from billing.services.entitlements_service import AICapacityReachedError, EntitlementsService
from billing.tests_entitlements import _make_user


def _log(user, count):
    for _ in range(count):
        AIUsageLog.objects.create(user=user, action=AIUsageLog.ACTION_JOB_ANALYSIS, provider="groq")


@override_settings(AI_GLOBAL_DAILY_CAP=3, AI_CAPACITY_ALERT_PERCENT=60, FREE_AI_CALLS_PER_MONTH=100)
class DailyCapacityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = _make_user("alice@example.com")
        self.bob = _make_user("bob@example.com")

    def test_cap_counts_every_users_actions(self):
        _log(self.alice, 2)
        _log(self.bob, 1)
        with self.assertRaises(AICapacityReachedError) as ctx:
            EntitlementsService.check_can_use_ai(self.alice)
        payload = ctx.exception.to_payload()
        self.assertEqual(payload["code"], "AI_CAPACITY_REACHED")
        self.assertIn("resets_at", payload)

    def test_below_cap_is_allowed(self):
        _log(self.alice, 2)
        EntitlementsService.check_can_use_ai(self.bob)

    def test_paying_users_are_never_stopped(self):
        _log(self.alice, 5)
        Subscription.objects.create(user=self.bob, plan_id="pro", status="active")
        EntitlementsService.check_can_use_ai(self.bob)

    def test_own_key_users_are_never_stopped(self):
        _log(self.alice, 5)
        with patch.object(EntitlementsService, "uses_own_ai_key", return_value=True):
            EntitlementsService.check_can_use_ai(self.bob)

    @override_settings(AI_GLOBAL_DAILY_CAP=0)
    def test_zero_means_off(self):
        _log(self.alice, 50)
        EntitlementsService.check_can_use_ai(self.bob)

    def test_admin_configuration_overrides_live(self):
        from api.models import SystemConfiguration

        _log(self.alice, 3)
        SystemConfiguration.objects.update_or_create(
            key="AI_GLOBAL_DAILY_CAP", defaults={"value": "10", "value_type": "integer"}
        )
        EntitlementsService.check_can_use_ai(self.bob)

    def test_the_day_starts_at_midnight_india_time(self):
        zone = ZoneInfo("Asia/Kolkata")
        now = datetime(2026, 10, 15, 0, 30, tzinfo=zone)
        self.assertEqual(ai_capacity.day_start(now), datetime(2026, 10, 15, 0, 0, tzinfo=zone))
        self.assertEqual(ai_capacity.next_reset(now), datetime(2026, 10, 16, 0, 0, tzinfo=zone))

    def test_yesterdays_usage_does_not_count(self):
        _log(self.alice, 3)
        AIUsageLog.objects.update(created_at=ai_capacity.day_start() - timedelta(minutes=5))
        EntitlementsService.check_can_use_ai(self.bob)

    def test_ops_is_emailed_once_at_the_threshold_and_once_at_the_cap(self):
        EntitlementsService.record_ai_usage(self.alice, AIUsageLog.ACTION_JOB_ANALYSIS, "groq")
        self.assertEqual(len(mail.outbox), 0)  # 1 of 3 = 33%
        EntitlementsService.record_ai_usage(self.alice, AIUsageLog.ACTION_JOB_ANALYSIS, "groq")
        self.assertEqual(len(mail.outbox), 1)  # 2 of 3 = 67% >= 60%
        self.assertIn("67%", mail.outbox[0].subject)
        EntitlementsService.record_ai_usage(self.alice, AIUsageLog.ACTION_JOB_ANALYSIS, "groq")
        self.assertEqual(len(mail.outbox), 2)  # cap reached
        self.assertIn("reached", mail.outbox[1].subject)
        EntitlementsService.record_ai_usage(self.alice, AIUsageLog.ACTION_JOB_ANALYSIS, "groq")
        self.assertEqual(len(mail.outbox), 2)  # no repeat the same day
