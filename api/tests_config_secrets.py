"""API keys in the admin panel: encrypted at rest, masked, and never lost.

Keys live in the database so a revoked or expired token is replaced from the
admin panel with no deploy. That is only acceptable if a database dump is not
a list of working credentials, a masked field can't overwrite a real key, and
no routine click wipes the keys the AI runs on.
"""

import importlib
import io
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from api.models import SystemConfiguration
from api.utils.config import get_config_value
from api.utils.secrets import (
    SecretDecryptionError,
    decrypt_value,
    encrypt_value,
    is_encrypted,
    is_secret_key,
)
from authentication.models import User
from resumes.services.llm_service import LLMAuthError

migration = importlib.import_module("api.migrations.0004_encrypt_and_seed_ai_configuration")


class SecretEncryptionTests(SimpleTestCase):
    def test_round_trip(self):
        stored = encrypt_value("AIza-real-key")
        self.assertTrue(is_encrypted(stored))
        self.assertNotIn("AIza-real-key", stored)
        self.assertEqual(decrypt_value(stored), "AIza-real-key")

    def test_encrypting_twice_does_not_double_encrypt(self):
        stored = encrypt_value("AIza-real-key")
        self.assertEqual(encrypt_value(stored), stored)

    def test_values_saved_before_encryption_existed_are_still_readable(self):
        self.assertEqual(decrypt_value("legacy-plaintext-key"), "legacy-plaintext-key")

    def test_a_changed_encryption_key_is_detected_not_returned_as_garbage(self):
        with override_settings(CONFIG_ENCRYPTION_KEY="first-key"):
            stored = encrypt_value("AIza-real-key")
        with override_settings(CONFIG_ENCRYPTION_KEY="second-key"):
            with self.assertRaises(SecretDecryptionError):
                decrypt_value(stored)

    def test_which_keys_are_secret(self):
        self.assertTrue(is_secret_key("GEMINI_API_KEY"))
        self.assertTrue(is_secret_key("STRIPE_WEBHOOK_SECRET"))
        self.assertFalse(is_secret_key("GEMINI_MODEL"))


class AdminPanelKeyTests(APITestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()
        self.admin = User.objects.create_superuser(
            email="admin@example.com", name="Admin", phone="1234567890", password="adminpassword"
        )
        self.client.force_authenticate(user=self.admin)

    def _put(self, *items):
        return self.client.put(reverse("admin-config"), list(items), format="json")

    def test_every_provider_key_and_ai_setting_is_editable(self):
        keys = {item["key"]: item for item in self.client.get(reverse("admin-config")).json()}

        for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "KIRA_API_KEY"):
            self.assertTrue(keys[key]["is_secret"], key)
        for key in (
            "LLM_FALLBACK_PROVIDER", "GEMINI_FALLBACK_MODELS", "GEMINI_MODEL_DISCOVERY",
            "GEMINI_AUTO_FALLBACK_FAMILIES", "GEMINI_AUTO_EXCLUDE", "GEMINI_MAX_MODEL_ATTEMPTS",
        ):
            self.assertIn(key, keys)
            self.assertFalse(keys[key]["is_secret"], key)

    def test_a_saved_key_is_encrypted_in_the_database_and_used_decrypted(self):
        self._put({"key": "GEMINI_API_KEY", "value": "  AIza-new-key  "})

        stored = SystemConfiguration.objects.get(key="GEMINI_API_KEY").value
        self.assertNotIn("AIza-new-key", stored)
        self.assertEqual(get_config_value("GEMINI_API_KEY"), "AIza-new-key")

    def test_submitting_the_mask_keeps_the_real_key(self):
        """The form shows ******** for a stored key. Sending that back must not
        replace a working key with eight asterisks."""
        self._put({"key": "GEMINI_API_KEY", "value": "AIza-new-key"})

        response = self._put({"key": "GEMINI_API_KEY", "value": "********"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_config_value("GEMINI_API_KEY"), "AIza-new-key")

    @override_settings(GEMINI_API_KEY="env-key")
    def test_clearing_a_key_hands_it_back_to_the_environment(self):
        self._put({"key": "GEMINI_API_KEY", "value": "AIza-new-key"})

        self._put({"key": "GEMINI_API_KEY", "value": ""})

        self.assertFalse(SystemConfiguration.objects.filter(key="GEMINI_API_KEY").exists())
        self.assertEqual(get_config_value("GEMINI_API_KEY"), "env-key")

    def test_reset_to_defaults_keeps_stored_keys(self):
        """A click meant to undo a bad setting must not take the AI down."""
        self._put(
            {"key": "GEMINI_API_KEY", "value": "AIza-new-key"},
            {"key": "GEMINI_MODEL", "value": "gemini-from-panel"},
        )

        self.client.post(reverse("admin-config-reset"))

        self.assertEqual(get_config_value("GEMINI_API_KEY"), "AIza-new-key")
        self.assertFalse(SystemConfiguration.objects.filter(key="GEMINI_MODEL").exists())

    def test_a_model_change_is_live_without_a_restart(self):
        self._put({"key": "GEMINI_MODEL", "value": "gemini-from-panel"})
        self.assertEqual(get_config_value("GEMINI_MODEL"), "gemini-from-panel")


class MigrationTests(TestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()

    def test_plaintext_keys_left_from_before_are_encrypted_in_place(self):
        SystemConfiguration.objects.create(key="GROQ_API_KEY", value="gsk_old", value_type="string", category="llm")
        SystemConfiguration.objects.create(key="LLM_TIMEOUT", value="90", value_type="integer", category="llm")

        migration.encrypt_plaintext_secrets(SystemConfiguration)

        key_row = SystemConfiguration.objects.get(key="GROQ_API_KEY")
        self.assertTrue(is_encrypted(key_row.value))
        self.assertEqual(decrypt_value(key_row.value), "gsk_old")
        self.assertEqual(SystemConfiguration.objects.get(key="LLM_TIMEOUT").value, "90")

    @override_settings(
        LLM_PROVIDER="gemini",
        LLM_FALLBACK_PROVIDER="",
        GEMINI_MODEL="gemini-from-env",
        GEMINI_FALLBACK_MODELS=["gemini-a", "gemini-b"],
        GROQ_MODEL="",
        KIRA_MODEL="",
        OLLAMA_MODEL="",
        GEMINI_API_KEY="AIza-env-key",
        GROQ_API_KEY="",
        KIRA_API_KEY="",
    )
    def test_the_running_configuration_is_copied_into_the_panel(self):
        SystemConfiguration.objects.create(
            key="GEMINI_MODEL", value="already-chosen-in-panel", value_type="string", category="llm"
        )

        migration.seed_from_settings(SystemConfiguration)

        rows = {row.key: row for row in SystemConfiguration.objects.all()}
        self.assertEqual(rows["LLM_PROVIDER"].value, "gemini")
        self.assertEqual(rows["GEMINI_FALLBACK_MODELS"].value, "gemini-a,gemini-b")
        self.assertEqual(decrypt_value(rows["GEMINI_API_KEY"].value), "AIza-env-key")
        self.assertNotIn("AIza-env-key", rows["GEMINI_API_KEY"].value)
        # A value already chosen in the panel is never overwritten...
        self.assertEqual(rows["GEMINI_MODEL"].value, "already-chosen-in-panel")
        # ...and nothing is created for settings that are empty.
        for empty in ("LLM_FALLBACK_PROVIDER", "GROQ_API_KEY", "KIRA_API_KEY", "GROQ_MODEL"):
            self.assertNotIn(empty, rows)


@override_settings(
    LLM_PROVIDER="gemini",
    LLM_FALLBACK_PROVIDER="",
    GEMINI_API_KEY="super-secret-value-123",
    GEMINI_MODEL="gemini-3.8-flash",
)
class CheckAICommandTests(TestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()

    @patch("resumes.services.llm_service.GeminiLLMService.available_models")
    def test_reports_the_chain_without_revealing_the_key(self, available_models):
        available_models.return_value = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]
        out = io.StringIO()

        call_command("check_ai", "--no-generate", stdout=out)

        output = out.getvalue()
        self.assertIn("Models are tried in this order", output)
        self.assertIn("gemini-3.8-flash", output)
        self.assertIn("GEMINI_API_KEY", output)
        self.assertNotIn("super-secret-value-123", output)

    @patch("resumes.services.llm_service.GeminiLLMService.available_models")
    def test_a_rejected_key_fails_the_command_with_the_fix(self, available_models):
        available_models.side_effect = LLMAuthError()
        out = io.StringIO()

        with self.assertRaises(CommandError):
            call_command("check_ai", "--no-generate", stdout=out)
        self.assertIn("aistudio.google.com", out.getvalue())
