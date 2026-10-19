from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import User
from billing.models import AIUsageLog


class AdminAIModelStatsAPITests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            name="Admin",
            email="admin-aimodels@example.com",
            phone="1234567890",
            password="safe-password",
            is_staff=True,
        )
        self.regular_user = User.objects.create_user(
            name="Jane User",
            email="jane-aimodels@example.com",
            phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()
        self.url = "/api/admin/ai-model-stats/"

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_requires_staff(self):
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_empty_stats_returns_zero_counts(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_calls"], 0)
        self.assertEqual(response.data["models"], [])
        self.assertEqual(response.data["providers"], [])
        self.assertEqual(response.data["actions"], [])
        self.assertEqual(response.data["recent_calls"], [])

    def test_aggregates_models_providers_actions_and_latencies(self):
        # Create usage logs
        AIUsageLog.objects.create(
            user=self.regular_user,
            action=AIUsageLog.ACTION_TAILORED_RESUME_GENERATION,
            provider="groq",
            model_name="llama-3.3-70b-versatile",
            latency_ms=1200,
        )
        AIUsageLog.objects.create(
            user=self.regular_user,
            action=AIUsageLog.ACTION_TAILORED_RESUME_GENERATION,
            provider="groq",
            model_name="llama-3.3-70b-versatile",
            latency_ms=1400,
        )
        AIUsageLog.objects.create(
            user=self.regular_user,
            action=AIUsageLog.ACTION_JOB_ANALYSIS,
            provider="gemini",
            model_name="gemini-2.5-flash",
            latency_ms=800,
        )
        AIUsageLog.objects.create(
            user=self.regular_user,
            action=AIUsageLog.ACTION_GAP_ANALYSIS,
            provider="dahl",
            model_name="MiniMaxAI/MiniMax-M2.7",
            latency_ms=1500,
        )

        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_calls"], 4)

        # Check models aggregation
        models_by_name = {m["model_name"]: m for m in response.data["models"]}
        self.assertIn("llama-3.3-70b-versatile", models_by_name)
        llama = models_by_name["llama-3.3-70b-versatile"]
        self.assertEqual(llama["total_calls"], 2)
        self.assertEqual(llama["provider"], "groq")
        self.assertEqual(llama["avg_latency_ms"], 1300)

        self.assertIn("gemini-2.5-flash", models_by_name)
        gemini = models_by_name["gemini-2.5-flash"]
        self.assertEqual(gemini["total_calls"], 1)
        self.assertEqual(gemini["provider"], "gemini")
        self.assertEqual(gemini["avg_latency_ms"], 800)

        # Check providers aggregation
        providers_by_name = {p["provider"]: p for p in response.data["providers"]}
        self.assertEqual(providers_by_name["groq"]["total_calls"], 2)
        self.assertEqual(providers_by_name["gemini"]["total_calls"], 1)
        self.assertEqual(providers_by_name["dahl"]["total_calls"], 1)

        # Check actions aggregation
        actions_by_name = {a["action"]: a for a in response.data["actions"]}
        self.assertEqual(actions_by_name[AIUsageLog.ACTION_TAILORED_RESUME_GENERATION]["total_calls"], 2)
        self.assertEqual(actions_by_name[AIUsageLog.ACTION_JOB_ANALYSIS]["total_calls"], 1)
        self.assertEqual(actions_by_name[AIUsageLog.ACTION_GAP_ANALYSIS]["total_calls"], 1)

        # Check recent calls
        self.assertEqual(len(response.data["recent_calls"]), 4)

    def test_range_filter_respects_time_window(self):
        now = timezone.now()
        # Older log (10 days ago)
        old_log = AIUsageLog.objects.create(
            user=self.regular_user,
            action=AIUsageLog.ACTION_JOB_ANALYSIS,
            provider="groq",
            model_name="llama-3.3-70b-versatile",
            latency_ms=1000,
        )
        AIUsageLog.objects.filter(id=old_log.id).update(created_at=now - timedelta(days=10))

        # Recent log (1 hour ago)
        recent_log = AIUsageLog.objects.create(
            user=self.regular_user,
            action=AIUsageLog.ACTION_GAP_ANALYSIS,
            provider="gemini",
            model_name="gemini-2.5-flash",
            latency_ms=900,
        )
        AIUsageLog.objects.filter(id=recent_log.id).update(created_at=now - timedelta(hours=1))

        self.client.force_authenticate(user=self.staff_user)

        # Filter: 24h should only include the recent log
        resp_24h = self.client.get(self.url, {"range": "24h"})
        self.assertEqual(resp_24h.data["total_calls"], 1)
        self.assertEqual(resp_24h.data["models"][0]["model_name"], "gemini-2.5-flash")

        # Filter: 7d should also only include the recent log
        resp_7d = self.client.get(self.url, {"range": "7d"})
        self.assertEqual(resp_7d.data["total_calls"], 1)

        # Filter: 30d should include both
        resp_30d = self.client.get(self.url, {"range": "30d"})
        self.assertEqual(resp_30d.data["total_calls"], 2)

        # Filter: all should include both
        resp_all = self.client.get(self.url, {"range": "all"})
        self.assertEqual(resp_all.data["total_calls"], 2)
