from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from authentication.models import LoginOTP, PasswordResetOTP, User
from authentication.services.otp_cleanup_service import delete_expired_otps


class OTPCleanupServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-otp-cleanup@example.com", phone="1234567890",
            password="safe-password",
        )

    def test_deletes_only_expired_login_otps(self):
        LoginOTP.objects.create(
            user=self.user, otp="111111", expires_at=timezone.now() - timedelta(minutes=1)
        )
        live = LoginOTP.objects.create(
            user=self.user, otp="222222", expires_at=timezone.now() + timedelta(minutes=5)
        )

        result = delete_expired_otps()

        self.assertEqual(result["login_otps_deleted"], 1)
        self.assertEqual(list(LoginOTP.objects.all()), [live])

    def test_deletes_only_expired_password_reset_otps(self):
        PasswordResetOTP.objects.create(
            user=self.user, otp="333333", expires_at=timezone.now() - timedelta(minutes=1)
        )
        live = PasswordResetOTP.objects.create(
            user=self.user, otp="444444", expires_at=timezone.now() + timedelta(minutes=5)
        )

        result = delete_expired_otps()

        self.assertEqual(result["password_reset_otps_deleted"], 1)
        self.assertEqual(list(PasswordResetOTP.objects.all()), [live])

    def test_no_expired_rows_deletes_nothing(self):
        LoginOTP.objects.create(
            user=self.user, otp="555555", expires_at=timezone.now() + timedelta(minutes=5)
        )

        result = delete_expired_otps()

        self.assertEqual(result, {"login_otps_deleted": 0, "password_reset_otps_deleted": 0})
        self.assertEqual(LoginOTP.objects.count(), 1)


class CleanupExpiredOTPsCommandTests(TestCase):
    def test_command_reports_deleted_counts(self):
        user = User.objects.create_user(
            name="Jane Doe", email="jane-otp-command@example.com", phone="1234567891",
            password="safe-password",
        )
        LoginOTP.objects.create(
            user=user, otp="666666", expires_at=timezone.now() - timedelta(minutes=1)
        )

        out = StringIO()
        call_command("cleanup_expired_otps", stdout=out)

        self.assertIn("1 expired login OTP", out.getvalue())
        self.assertEqual(LoginOTP.objects.count(), 0)
