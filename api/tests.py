from unittest.mock import patch
from django.test import TestCase
from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from resumes.services.llm_service import LLMModelUnavailableError
from api.models import SystemConfiguration
from api.utils.config import get_config_value
from authentication.models import User


class ReadinessCheckTests(TestCase):
    @patch("api.views.readiness._check_redis", return_value=True)
    @patch("api.views.readiness.get_llm_service")
    def test_ready_when_database_redis_and_llm_are_all_available(self, get_llm_service, _check_redis):
        response = self.client.get("/api/ready/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "READY", "database": "OK", "redis": "OK", "llm": "AVAILABLE"},
        )
        get_llm_service.return_value.check_model_available.assert_called_once()

    @patch("api.views.readiness._check_redis", return_value=True)
    @patch("api.views.readiness.get_llm_service")
    def test_not_ready_when_the_configured_model_is_missing(self, get_llm_service, _check_redis):
        get_llm_service.return_value.check_model_available.side_effect = LLMModelUnavailableError()

        response = self.client.get("/api/ready/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "NOT_READY", "database": "OK", "redis": "OK", "llm": "LLM_MODEL_UNAVAILABLE"},
        )

    @patch("api.views.readiness._check_redis", return_value=False)
    @patch("api.views.readiness.get_llm_service")
    def test_not_ready_when_redis_is_unreachable(self, get_llm_service, _check_redis):
        response = self.client.get("/api/ready/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["redis"], "UNREACHABLE")


class HealthCheckTests(TestCase):
    def test_healthy_when_database_is_reachable(self):
        response = self.client.get("/api/health/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "OK", "database": "OK"})


class SystemConfigurationTests(TestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()

    def test_fallback_to_settings(self):
        # When not in DB, returns setting value or default
        val = get_config_value("MAX_RESUMES_PER_USER", 99)
        self.assertEqual(val, settings.MAX_RESUMES_PER_USER)
        
        val_default = get_config_value("NON_EXISTENT_KEY", "fallback")
        self.assertEqual(val_default, "fallback")

    def test_parsing_types(self):
        # Integer
        c1 = SystemConfiguration.objects.create(
            key="TEST_INT", value="42", value_type="integer", category="general"
        )
        self.assertEqual(c1.parsed_value(), 42)

        # Float
        c2 = SystemConfiguration.objects.create(
            key="TEST_FLOAT", value="3.14", value_type="float", category="general"
        )
        self.assertEqual(c2.parsed_value(), 3.14)

        # Boolean
        c3 = SystemConfiguration.objects.create(
            key="TEST_BOOL_TRUE", value="true", value_type="boolean", category="general"
        )
        self.assertTrue(c3.parsed_value())
        c3_false = SystemConfiguration.objects.create(
            key="TEST_BOOL_FALSE", value="false", value_type="boolean", category="general"
        )
        self.assertFalse(c3_false.parsed_value())

        # List
        c4 = SystemConfiguration.objects.create(
            key="TEST_LIST", value="a, b , c", value_type="list", category="general"
        )
        self.assertEqual(c4.parsed_value(), ["a", "b", "c"])


class AdminPanelAPITests(APITestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()
        User.objects.all().delete()

        self.admin_user = User.objects.create_superuser(
            email="admin@example.com", name="Admin", phone="1234567890", password="adminpassword"
        )
        self.regular_user = User.objects.create_user(
            email="user@example.com", name="User", phone="0987654321", password="userpassword"
        )

    def test_config_permissions(self):
        # Non-admin is denied
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.get(reverse("admin-config"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Admin is allowed
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(reverse("admin-config"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_config_list_and_update(self):
        self.client.force_authenticate(user=self.admin_user)
        
        # GET returns defaults
        response = self.client.get(reverse("admin-config"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Find LLM_TIMEOUT in results
        item = next(x for x in response.json() if x["key"] == "LLM_TIMEOUT")
        self.assertEqual(item["value"], str(settings.LLM_TIMEOUT))
        self.assertFalse(item["is_custom"])

        # PUT updates settings
        update_data = [
            {"key": "LLM_TIMEOUT", "value": "120"},
            {"key": "MAX_RESUMES_PER_USER", "value": "15"}
        ]
        response = self.client.put(reverse("admin-config"), update_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify DB entry
        c = SystemConfiguration.objects.get(key="LLM_TIMEOUT")
        self.assertEqual(c.value, "120")
        self.assertEqual(c.parsed_value(), 120)

        # Verify helper uses DB value
        self.assertEqual(get_config_value("LLM_TIMEOUT"), 120)

    def test_provider_api_keys_are_admin_configurable_masked_and_encrypted(self):
        # Keys are editable in the admin panel so a revoked or expired token
        # can be replaced without a redeploy. They are never returned, and
        # never stored as plain text: a database dump must not be a list of
        # working credentials.
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.put(
            reverse("admin-config"),
            [{"key": "GROQ_API_KEY", "value": "gsk_live_example"}],
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        stored = SystemConfiguration.objects.get(key="GROQ_API_KEY").value
        self.assertNotIn("gsk_live_example", stored)
        self.assertEqual(get_config_value("GROQ_API_KEY"), "gsk_live_example")

        item = next(x for x in response.json() if x["key"] == "GROQ_API_KEY")
        self.assertEqual(item["value"], "********")
        self.assertTrue(item["is_secret"])
        self.assertNotIn("gsk_live_example", str(response.json()))

    def test_free_tier_limits_are_admin_configurable(self):
        # billing.entitlements.entitlements_for() reads these two keys via
        # get_config_value() so they can be retuned without a redeploy —
        # they must actually be reachable through this endpoint, or that
        # live-tuning support is dead code no admin can ever use.
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(reverse("admin-config"))
        keys = {item["key"] for item in response.json()}
        self.assertIn("FREE_AI_CALLS_PER_MONTH", keys)
        self.assertIn("FREE_TAILORED_RESUMES_PER_MONTH", keys)

        update_data = [
            {"key": "FREE_AI_CALLS_PER_MONTH", "value": "50"},
            {"key": "FREE_TAILORED_RESUMES_PER_MONTH", "value": "5"},
        ]
        response = self.client.put(reverse("admin-config"), update_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_config_value("FREE_AI_CALLS_PER_MONTH"), 50)
        self.assertEqual(get_config_value("FREE_TAILORED_RESUMES_PER_MONTH"), 5)

    def test_config_reset(self):
        SystemConfiguration.objects.create(
            key="LLM_TIMEOUT", value="120", value_type="integer", category="llm"
        )
        self.assertEqual(get_config_value("LLM_TIMEOUT"), 120)

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(reverse("admin-config-reset"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Check it reverted to settings.py
        self.assertEqual(get_config_value("LLM_TIMEOUT"), settings.LLM_TIMEOUT)

    def test_users_list_and_search(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(reverse("admin-users"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["count"], 2)

        # Search
        response = self.client.get(reverse("admin-users") + "?search=Admin")
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["email"], self.admin_user.email)

    def test_user_toggle_active_and_staff(self):
        self.client.force_authenticate(user=self.admin_user)

        # Toggle active
        url = reverse("admin-user-toggle-active", kwargs={"user_id": self.regular_user.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.json()["is_active"])

        # Toggle staff
        url_staff = reverse("admin-user-toggle-staff", kwargs={"user_id": self.regular_user.id})
        response = self.client.post(url_staff)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()["is_staff"])

        # Prevent self toggle active
        url_self = reverse("admin-user-toggle-active", kwargs={"user_id": self.admin_user.id})
        response = self.client.post(url_self)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["detail"], "You cannot deactivate your own account.")

