"""Covers the admin-configurable second factor: LOGIN_OTP_REQUIRED.

The setting is read through get_config_value at request time, so an admin
toggling it in the admin panel changes behaviour on the next login with no
redeploy. These tests drive it through SystemConfiguration (what the admin
panel actually writes) rather than override_settings, so they exercise the
same path a real toggle takes.
"""

from django.core import mail
from django.test import TestCase
from rest_framework.test import APIClient

from api.models import SystemConfiguration
from authentication.models import LoginOTP, User


def _set_otp_required(value: bool):
    SystemConfiguration.objects.update_or_create(
        key="LOGIN_OTP_REQUIRED",
        defaults={
            "value": "true" if value else "false",
            "value_type": "boolean",
            "category": "security",
            "description": "test",
        },
    )


class LoginOtpToggleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-toggle@example.com", phone="1234567890",
            password="safe-password",
        )
        SystemConfiguration.objects.all().delete()
        mail.outbox.clear()

    def _login(self):
        return self.client.post(
            "/api/auth/login/",
            {"email": "jane-toggle@example.com", "password": "safe-password"},
            format="json",
        )

    # --- default (two-factor) -------------------------------------------

    def test_defaults_to_requiring_otp_when_nothing_is_configured(self):
        # Security must not silently drop just because the setting exists.
        response = self._login()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])
        self.assertNotIn("access", response.data)
        self.assertEqual(LoginOTP.objects.filter(user=self.user).count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_otp_required_true_sends_a_code_and_withholds_tokens(self):
        _set_otp_required(True)

        response = self._login()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    # --- single-factor (admin turned OTP off) ---------------------------

    def test_otp_disabled_returns_tokens_directly(self):
        _set_otp_required(False)

        response = self._login()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["otp_required"])
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "jane-toggle@example.com")
        # is_staff must be present - the frontend's admin nav gate reads it.
        self.assertIn("is_staff", response.data["user"])

    def test_otp_disabled_sends_no_email_and_creates_no_otp(self):
        _set_otp_required(False)

        self._login()

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(LoginOTP.objects.filter(user=self.user).count(), 0)

    def test_disabling_otp_clears_a_stale_code_so_it_cannot_be_replayed(self):
        # A code issued while two-factor was on must not stay valid against
        # the verify-otp endpoint after an admin turns the requirement off.
        _set_otp_required(True)
        self._login()
        self.assertEqual(LoginOTP.objects.filter(user=self.user).count(), 1)

        _set_otp_required(False)
        self._login()

        self.assertEqual(LoginOTP.objects.filter(user=self.user).count(), 0)
        replay = self.client.post(
            "/api/auth/login/verify-otp/",
            {"email": "jane-toggle@example.com", "otp": "123456"},
            format="json",
        )
        self.assertNotEqual(replay.status_code, 200)

    # --- the password check itself is unaffected either way -------------

    def test_wrong_password_is_rejected_when_otp_is_disabled(self):
        _set_otp_required(False)

        response = self.client.post(
            "/api/auth/login/",
            {"email": "jane-toggle@example.com", "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.data)

    def test_wrong_password_is_rejected_when_otp_is_required(self):
        _set_otp_required(True)

        response = self.client.post(
            "/api/auth/login/",
            {"email": "jane-toggle@example.com", "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(LoginOTP.objects.filter(user=self.user).count(), 0)


class LoginOtpToggleAdminConfigTests(TestCase):
    """The toggle is only useful if an admin can actually reach it."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            email="otp-admin@example.com", name="Admin", phone="5550001111",
            password="adminpassword",
        )

    def test_setting_is_exposed_in_the_admin_config_endpoint(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.get("/api/admin/config/")
        item = next(x for x in response.json() if x["key"] == "LOGIN_OTP_REQUIRED")

        self.assertEqual(item["value_type"], "boolean")
        self.assertEqual(item["category"], "security")
        # Defaults to on, matching settings.LOGIN_OTP_REQUIRED.
        self.assertEqual(item["value"], "true")

    def test_admin_can_toggle_it_off_and_it_takes_effect(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.put(
            "/api/admin/config/",
            [{"key": "LOGIN_OTP_REQUIRED", "value": "false"}],
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        from api.utils.config import get_config_value
        self.assertFalse(get_config_value("LOGIN_OTP_REQUIRED", True))
