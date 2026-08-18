from unittest.mock import patch

from django.core import mail
from django.test import TestCase

from authentication.models import User
from authentication.services.forgot_password_service import ForgotPasswordService
from authentication.services.login_service import LoginService
from authentication.utils.otp_mailer import send_otp_email


class SendOtpEmailTests(TestCase):
    def test_login_purpose_sends_a_real_email_with_the_code(self):
        result = send_otp_email("jane@example.com", "123456", purpose="login")

        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["jane@example.com"])
        self.assertIn("login code", sent.subject)
        self.assertIn("123456", sent.body)

    def test_password_reset_purpose_uses_different_copy(self):
        send_otp_email("jane@example.com", "654321", purpose="password reset")

        sent = mail.outbox[0]
        self.assertIn("password reset code", sent.subject)
        self.assertIn("654321", sent.body)

    def test_delivery_failure_is_swallowed_and_returns_false(self):
        with patch("authentication.utils.otp_mailer.send_mail", side_effect=RuntimeError("smtp down")):
            result = send_otp_email("jane@example.com", "123456", purpose="login")
        self.assertFalse(result)


class OtpEmailWiringTests(TestCase):
    """Confirms the actual login/forgot-password services send a real email
    (not just print to console) — the wiring, not just the mailer utility."""

    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-otp@example.com", phone="1234567890",
            password="safe-password",
        )

    def test_login_sends_an_otp_email(self):
        mail.outbox.clear()
        result = LoginService.login_user("jane-otp@example.com", "safe-password")

        self.assertTrue(result["success"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["jane-otp@example.com"])

    def test_forgot_password_sends_an_otp_email(self):
        mail.outbox.clear()
        otp = ForgotPasswordService.send_otp("jane-otp@example.com")

        self.assertIsNotNone(otp)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(otp, mail.outbox[0].body)
