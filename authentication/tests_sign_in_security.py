"""Sign-in hardening: account lockout, one-time-code attempt limits, the
resend cooldown, authenticator-app two-factor, and the admin controls for
all of it.

Limits are set through SystemConfiguration - what the admin panel writes -
rather than override_settings, so these exercise the same path a real
admin change takes.
"""

import time
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import AdminAuditLog, SystemConfiguration
from authentication.models import LoginOTP, PasswordResetOTP, TwoFactorBackupCode, User
from authentication.services import auth_security
from authentication.utils import totp

PASSWORD = "safe-password-123"
EMAIL = "sec@example.com"


def configure(key, value, value_type="integer"):
    SystemConfiguration.objects.update_or_create(
        key=key,
        defaults={
            "value": str(value).lower() if isinstance(value, bool) else str(value),
            "value_type": value_type,
            "category": "security",
            "description": "test",
        },
    )


class SecurityTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            name="Sec User", email=EMAIL, phone="5550001111", password=PASSWORD,
        )
        SystemConfiguration.objects.all().delete()
        mail.outbox.clear()

    def login(self, password=PASSWORD, email=EMAIL):
        return self.client.post(
            "/api/auth/login/", {"email": email, "password": password}, format="json"
        )

    def verify_login(self, code, email=EMAIL):
        return self.client.post(
            "/api/auth/login/verify-otp/", {"email": email, "otp": code}, format="json"
        )

    def enable_totp(self, user=None):
        """Walk the real setup flow; returns (secret, backup_codes)."""
        user = user or self.user
        self.client.force_authenticate(user)
        setup = self.client.post("/api/auth/2fa/totp/setup/")
        self.assertEqual(setup.status_code, 200, setup.data)
        secret = setup.data["secret"]
        confirm = self.client.post(
            "/api/auth/2fa/totp/confirm/", {"code": totp.code_for(secret)}, format="json"
        )
        self.assertEqual(confirm.status_code, 200, confirm.data)
        self.client.force_authenticate(None)
        # Confirming spent this 30s code (replay guard). A real user signs in
        # later with a fresh code; stand in for that by clearing the guard.
        User.objects.filter(pk=user.pk).update(totp_last_step=None)
        return secret, confirm.data["backup_codes"]


class AccountLockoutTests(SecurityTestCase):
    def test_locks_after_the_configured_number_of_wrong_passwords(self):
        configure("LOGIN_MAX_FAILED_ATTEMPTS", 3)

        for _ in range(2):
            self.assertEqual(self.login("wrong").status_code, 401)
        third = self.login("wrong")

        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.data["code"], "ACCOUNT_LOCKED")
        self.assertIn("Retry-After", third)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.locked_until)

    def test_a_locked_account_refuses_even_the_right_password(self):
        configure("LOGIN_MAX_FAILED_ATTEMPTS", 2)
        self.login("wrong")
        self.login("wrong")

        response = self.login()

        self.assertEqual(response.status_code, 429)
        self.assertFalse(LoginOTP.objects.filter(user=self.user).exists())

    def test_lock_expires_after_the_configured_minutes(self):
        configure("LOGIN_MAX_FAILED_ATTEMPTS", 1)
        configure("LOGIN_LOCKOUT_MINUTES", 10)
        self.login("wrong")
        self.user.refresh_from_db()
        remaining = (self.user.locked_until - timezone.now()).total_seconds()
        self.assertTrue(9 * 60 < remaining <= 10 * 60)

        self.user.locked_until = timezone.now() - timedelta(seconds=1)
        self.user.save()
        self.assertEqual(self.login().status_code, 200)

    def test_success_resets_the_counter(self):
        configure("LOGIN_MAX_FAILED_ATTEMPTS", 3)
        self.login("wrong")
        self.login("wrong")
        self.assertEqual(self.login().status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        # Two more wrong ones don't lock: the count started again.
        self.login("wrong")
        self.assertEqual(self.login("wrong").status_code, 401)

    def test_zero_turns_the_lockout_off(self):
        configure("LOGIN_MAX_FAILED_ATTEMPTS", 0)
        for _ in range(12):
            self.assertEqual(self.login("wrong").status_code, 401)
        self.assertEqual(self.login().status_code, 200)

    def test_unknown_email_gets_the_ordinary_error(self):
        response = self.login(email="nobody@example.com")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["message"], "Invalid email or password.")

    def test_password_reset_clears_the_lock(self):
        configure("LOGIN_MAX_FAILED_ATTEMPTS", 1)
        self.login("wrong")
        self.client.post("/api/auth/forgot-password/", {"email": EMAIL}, format="json")
        code = PasswordResetOTP.objects.get(user=self.user).otp
        self.client.post("/api/auth/verify-otp/", {"email": EMAIL, "otp": code}, format="json")
        reset = self.client.post(
            "/api/auth/reset-password/",
            {"email": EMAIL, "password": "Brand-new-pass-1!", "confirm_password": "Brand-new-pass-1!"},
            format="json",
        )
        self.assertEqual(reset.status_code, 200, reset.data)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.locked_until)


class LoginCodeLimitTests(SecurityTestCase):
    def test_wrong_codes_count_down_then_void_the_code(self):
        configure("OTP_MAX_ATTEMPTS", 3)
        self.login()
        real = LoginOTP.objects.get(user=self.user).otp
        wrong = "000000" if real != "000000" else "111111"

        first = self.verify_login(wrong)
        self.assertEqual(first.status_code, 401)
        self.assertEqual(first.data["attempts_remaining"], 2)
        self.verify_login(wrong)
        last = self.verify_login(wrong)
        self.assertEqual(last.status_code, 429)
        self.assertEqual(last.data["code"], "OTP_ATTEMPTS_EXCEEDED")

        # The real code no longer works either: a new one is needed.
        self.assertNotEqual(self.verify_login(real).status_code, 200)
        self.assertFalse(LoginOTP.objects.filter(user=self.user).exists())

    def test_the_right_code_still_works_within_the_limit(self):
        self.login()
        real = LoginOTP.objects.get(user=self.user).otp
        self.verify_login("000000" if real != "000000" else "111111")
        response = self.verify_login(real)
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_expiry_follows_the_setting_and_the_email_says_so(self):
        configure("OTP_EXPIRY_MINUTES", 12)
        self.login()
        otp = LoginOTP.objects.get(user=self.user)
        remaining = (otp.expires_at - timezone.now()).total_seconds()
        self.assertTrue(11 * 60 < remaining <= 12 * 60)
        self.assertIn("12 minutes", mail.outbox[-1].body)

    def test_resend_cooldown_keeps_the_code_already_sent(self):
        configure("OTP_RESEND_COOLDOWN_SECONDS", 60)
        self.login()
        first = LoginOTP.objects.get(user=self.user)

        again = self.login()

        self.assertEqual(again.status_code, 200)
        self.assertGreater(again.data["resend_available_in"], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(LoginOTP.objects.get(user=self.user).pk, first.pk)

    def test_a_new_code_is_sent_once_the_cooldown_has_passed(self):
        configure("OTP_RESEND_COOLDOWN_SECONDS", 30)
        self.login()
        LoginOTP.objects.filter(user=self.user).update(
            created_at=timezone.now() - timedelta(seconds=31)
        )
        self.login()
        self.assertEqual(len(mail.outbox), 2)


class PasswordResetCodeLimitTests(SecurityTestCase):
    def verify(self, code):
        return self.client.post("/api/auth/verify-otp/", {"email": EMAIL, "otp": code}, format="json")

    def test_wrong_codes_void_the_reset_code(self):
        configure("OTP_MAX_ATTEMPTS", 2)
        self.client.post("/api/auth/forgot-password/", {"email": EMAIL}, format="json")
        real = PasswordResetOTP.objects.get(user=self.user).otp
        wrong = "000000" if real != "000000" else "111111"

        self.assertEqual(self.verify(wrong).status_code, 400)
        self.assertEqual(self.verify(wrong).status_code, 429)
        self.assertNotEqual(self.verify(real).status_code, 200)

    def test_forgot_password_does_not_reveal_whether_an_account_exists(self):
        known = self.client.post("/api/auth/forgot-password/", {"email": EMAIL}, format="json")
        unknown = self.client.post(
            "/api/auth/forgot-password/", {"email": "nobody@example.com"}, format="json"
        )
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(known.data, unknown.data)


class AuthenticatorAppTests(SecurityTestCase):
    def test_setup_needs_a_working_code_before_it_changes_sign_in(self):
        self.client.force_authenticate(self.user)
        setup = self.client.post("/api/auth/2fa/totp/setup/")
        self.assertTrue(setup.data["qr_svg"].startswith("data:image/svg+xml"))
        self.assertTrue(setup.data["otpauth_uri"].startswith("otpauth://totp/"))

        bad = self.client.post("/api/auth/2fa/totp/confirm/", {"code": "000000"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.totp_enabled)
        # The secret is stored encrypted, never as the setup key itself.
        self.assertNotIn(setup.data["secret"], self.user.totp_secret)

    def test_login_asks_for_the_app_code_instead_of_emailing_one(self):
        secret, _ = self.enable_totp()
        mail.outbox.clear()

        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])
        self.assertEqual(response.data["otp_method"], "totp")
        self.assertEqual(len(mail.outbox), 0)

        done = self.verify_login(totp.code_for(secret))
        self.assertEqual(done.status_code, 200)
        self.assertIn("access", done.data)

    def test_a_code_cannot_be_replayed(self):
        secret, _ = self.enable_totp()
        # Use up the current step once...
        User.objects.filter(pk=self.user.pk).update(totp_last_step=None)
        self.login()
        code = totp.code_for(secret)
        self.assertEqual(self.verify_login(code).status_code, 200)
        # ...then the same code on a fresh sign-in is refused.
        self.login()
        self.assertEqual(self.verify_login(code).status_code, 401)

    def test_a_backup_code_works_exactly_once(self):
        _, backups = self.enable_totp()
        self.assertEqual(len(backups), auth_security.BACKUP_CODE_COUNT)

        self.login()
        self.assertEqual(self.verify_login(backups[0].upper()).status_code, 200)
        self.login()
        self.assertEqual(self.verify_login(backups[0]).status_code, 401)
        self.assertEqual(
            TwoFactorBackupCode.objects.filter(user=self.user, used_at__isnull=True).count(),
            auth_security.BACKUP_CODE_COUNT - 1,
        )

    def test_wrong_app_codes_are_limited_too(self):
        configure("OTP_MAX_ATTEMPTS", 2)
        self.enable_totp()
        self.login()
        self.verify_login("000000")
        self.assertEqual(self.verify_login("000001").status_code, 429)
        self.assertFalse(LoginOTP.objects.filter(user=self.user).exists())

    def test_turning_the_site_switch_off_falls_back_to_the_emailed_code(self):
        self.enable_totp()
        configure("TOTP_2FA_ENABLED", False, "boolean")
        response = self.login()
        self.assertEqual(response.data["otp_method"], "email")
        # Enrolment is kept for when it's turned back on.
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)

    def test_setup_is_refused_while_the_site_switch_is_off(self):
        configure("TOTP_2FA_ENABLED", False, "boolean")
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post("/api/auth/2fa/totp/setup/").status_code, 403)

    def test_it_applies_even_when_the_emailed_code_is_switched_off(self):
        secret, _ = self.enable_totp()
        configure("LOGIN_OTP_REQUIRED", False, "boolean")
        response = self.login()
        self.assertEqual(response.data["otp_method"], "totp")
        self.assertNotIn("access", response.data)

    def test_disabling_needs_the_password_and_a_code(self):
        secret, _ = self.enable_totp()
        self.client.force_authenticate(self.user)
        url = "/api/auth/2fa/totp/disable/"

        no_password = self.client.post(url, {"code": totp.code_for(secret), "password": "nope"}, format="json")
        self.assertEqual(no_password.status_code, 400)

        User.objects.filter(pk=self.user.pk).update(totp_last_step=None)
        ok = self.client.post(url, {"code": totp.code_for(secret), "password": PASSWORD}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertFalse(ok.data["totp_enabled"])
        self.assertFalse(TwoFactorBackupCode.objects.filter(user=self.user).exists())

    def test_status_reports_the_setup(self):
        self.enable_totp()
        self.client.force_authenticate(self.user)
        data = self.client.get("/api/auth/2fa/").data
        self.assertTrue(data["available"])
        self.assertTrue(data["active"])
        self.assertEqual(data["backup_codes_remaining"], auth_security.BACKUP_CODE_COUNT)

    def test_regenerating_backup_codes_replaces_the_old_ones(self):
        secret, old = self.enable_totp()
        self.client.force_authenticate(self.user)
        User.objects.filter(pk=self.user.pk).update(totp_last_step=None)
        response = self.client.post(
            "/api/auth/2fa/backup-codes/", {"code": totp.code_for(secret)}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotEqual(set(response.data["backup_codes"]), set(old))
        self.client.force_authenticate(None)
        self.login()
        self.assertEqual(self.verify_login(old[0]).status_code, 401)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="cid.apps.googleusercontent.com", GOOGLE_OAUTH_EXTRA_CLIENT_IDS=[])
    def test_google_sign_in_also_asks_for_the_app_code(self):
        secret, _ = self.enable_totp()
        claims = {
            "sub": "g-sub", "email": EMAIL, "email_verified": True, "name": "Sec User",
            "aud": "cid.apps.googleusercontent.com", "iss": "https://accounts.google.com",
        }
        target = "authentication.services.google_auth_service.google_id_token.verify_oauth2_token"
        with patch(target, return_value=claims):
            response = self.client.post("/api/auth/google/", {"credential": "t"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])
        self.assertEqual(response.data["otp_method"], "totp")
        self.assertNotIn("access", response.data)
        done = self.verify_login(totp.code_for(secret))
        self.assertEqual(done.status_code, 200)


class TotpAlgorithmTests(TestCase):
    def test_matches_the_rfc_6238_reference_vector(self):
        # RFC 6238 appendix B, SHA-1 key "12345678901234567890", T=59s.
        import base64

        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        self.assertEqual(totp.code_for(secret, now=59), "287082")
        self.assertEqual(totp.matching_step(secret, "287082", now=59), 1)

    def test_tolerates_one_step_of_clock_drift(self):
        secret = totp.generate_secret()
        now = time.time()
        previous = totp.code_for(secret, now=now - totp.STEP_SECONDS)
        self.assertIsNotNone(totp.matching_step(secret, previous, now=now))
        too_old = totp.code_for(secret, now=now - 3 * totp.STEP_SECONDS)
        if too_old != totp.code_for(secret, now=now):
            self.assertIsNone(totp.matching_step(secret, too_old, now=now))


class AdminSecurityControlsTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            name="Admin", email="admin-sec@example.com", phone="5550002222",
            password=PASSWORD, is_staff=True,
        )

    def test_security_settings_are_listed_and_editable(self):
        self.client.force_authenticate(self.admin)
        listed = {row["key"]: row for row in self.client.get("/api/admin/config/").data}
        for key in (
            "LOGIN_MAX_FAILED_ATTEMPTS", "LOGIN_LOCKOUT_MINUTES", "OTP_MAX_ATTEMPTS",
            "OTP_EXPIRY_MINUTES", "OTP_RESEND_COOLDOWN_SECONDS", "TOTP_2FA_ENABLED",
        ):
            self.assertEqual(listed[key]["category"], "security", key)
        self.assertEqual(listed["LOGIN_MAX_FAILED_ATTEMPTS"]["value"], "5")

        self.client.put(
            "/api/admin/config/", [{"key": "LOGIN_MAX_FAILED_ATTEMPTS", "value": "7"}], format="json"
        )
        self.assertEqual(auth_security.get_policy().max_failed_logins, 7)

    def test_out_of_range_values_are_clamped(self):
        configure("OTP_EXPIRY_MINUTES", 0)
        configure("OTP_MAX_ATTEMPTS", 100000)
        policy = auth_security.get_policy()
        self.assertEqual(policy.otp_expiry_minutes, 1)
        self.assertEqual(policy.otp_max_attempts, 20)

    def test_admin_can_unlock_and_remove_two_factor(self):
        self.enable_totp()
        self.user.locked_until = timezone.now() + timedelta(minutes=10)
        self.user.save()
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/admin/users/{self.user.id}/security-reset/",
            {"unlock": True, "remove_two_factor": True}, format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_locked"])
        self.assertFalse(response.data["two_factor_enabled"])
        self.assertEqual(
            AdminAuditLog.objects.filter(target=f"user:{self.user.id}").count(), 2
        )
