"""Tests for the Resend HTTP API email backend.

The behaviour that matters most is that a failure is never mistaken for a
success. An OTP that was never delivered but reported as sent is worse than
a visible error: the user waits for a code that does not exist, and nothing
in the logs says why.
"""

from unittest.mock import patch

from django.core.mail import EmailMessage, send_mail
from django.test import TestCase, override_settings

from config.email_backends import ResendAPIEmailBackend

BACKEND = "config.email_backends.ResendAPIEmailBackend"


class _Response:
    def __init__(self, status_code=200, text='{"id":"abc"}'):
        self.status_code = status_code
        self.text = text


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_key")
class ResendBackendSendTests(TestCase):
    def test_sends_via_the_http_api(self):
        with patch("config.email_backends.requests.post", return_value=_Response()) as post:
            sent = send_mail("Subject", "Body", "from@patrixel.com", ["to@example.com"])

        self.assertEqual(sent, 1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], "https://api.resend.com/emails")

    def test_uses_https_so_it_is_unaffected_by_the_smtp_port_block(self):
        # The entire reason this backend exists.
        with patch("config.email_backends.requests.post", return_value=_Response()) as post:
            send_mail("S", "B", "from@patrixel.com", ["to@example.com"])

        self.assertTrue(post.call_args.args[0].startswith("https://"))

    def test_payload_carries_subject_body_and_recipients(self):
        import json

        with patch("config.email_backends.requests.post", return_value=_Response()) as post:
            send_mail("Your code", "123456", "from@patrixel.com", ["a@example.com", "b@example.com"])

        payload = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(payload["subject"], "Your code")
        self.assertEqual(payload["text"], "123456")
        self.assertEqual(payload["to"], ["a@example.com", "b@example.com"])

    def test_api_key_is_sent_as_a_bearer_token(self):
        with patch("config.email_backends.requests.post", return_value=_Response()) as post:
            send_mail("S", "B", "from@patrixel.com", ["to@example.com"])

        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"], "Bearer re_test_key"
        )

    def test_a_message_with_no_recipients_is_skipped(self):
        with patch("config.email_backends.requests.post") as post:
            backend = ResendAPIEmailBackend()
            sent = backend.send_messages([EmailMessage("S", "B", "from@patrixel.com", [])])

        self.assertEqual(sent, 0)
        post.assert_not_called()


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_key")
class ResendBackendFailureTests(TestCase):
    """A failure must never be reported as a send."""

    def test_an_api_rejection_raises_rather_than_reporting_success(self):
        # e.g. an unverified sending domain - the exact failure mode hit
        # before patrixel.com was verified.
        rejection = _Response(403, '{"message":"domain is not verified"}')
        with patch("config.email_backends.requests.post", return_value=rejection):
            with self.assertRaises(RuntimeError):
                send_mail("S", "B", "from@patrixel.com", ["to@example.com"], fail_silently=False)

    def test_a_network_error_raises_rather_than_reporting_success(self):
        import requests

        with patch(
            "config.email_backends.requests.post",
            side_effect=requests.RequestException("connection reset"),
        ):
            with self.assertRaises(requests.RequestException):
                send_mail("S", "B", "from@patrixel.com", ["to@example.com"], fail_silently=False)

    def test_fail_silently_suppresses_the_error_and_counts_nothing_sent(self):
        rejection = _Response(422, '{"message":"invalid recipient"}')
        with patch("config.email_backends.requests.post", return_value=rejection):
            sent = send_mail("S", "B", "from@patrixel.com", ["to@example.com"], fail_silently=True)

        self.assertEqual(sent, 0)

    def test_a_missing_api_key_raises_instead_of_silently_dropping_mail(self):
        # Reporting success with no key configured would mean users waiting
        # for codes that were never sent, with nothing explaining why.
        with override_settings(RESEND_API_KEY=""):
            with self.assertRaises(ValueError):
                send_mail("S", "B", "from@patrixel.com", ["to@example.com"], fail_silently=False)


class BackendSelectionTests(TestCase):
    """Transport preference: Resend API, then SMTP, then console."""

    def test_resend_is_preferred_when_its_key_is_present(self):
        from importlib import reload
        import config.settings as settings_module

        with patch.dict(
            "os.environ", {"RESEND_API_KEY": "re_x", "EMAIL_HOST": "smtp.example.com"}
        ):
            reloaded = reload(settings_module)
            self.assertEqual(
                reloaded.EMAIL_BACKEND, "config.email_backends.ResendAPIEmailBackend"
            )

    def test_falls_back_to_console_when_nothing_is_configured(self):
        from importlib import reload
        import config.settings as settings_module

        with patch.dict("os.environ", {"RESEND_API_KEY": "", "EMAIL_HOST": ""}):
            reloaded = reload(settings_module)
            self.assertEqual(
                reloaded.EMAIL_BACKEND, "django.core.mail.backends.console.EmailBackend"
            )


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_key")
class ResendBackendAttachmentTests(TestCase):
    """Direct Apply sends a resume PDF. Before this, Django would accept
    `attach()` and the backend would quietly drop it — the employer would
    receive an application with no resume and nothing would look wrong."""

    def _payload(self, post):
        import json

        return json.loads(post.call_args.kwargs["data"])

    def test_attachment_is_sent_base64_encoded(self):
        import base64

        message = EmailMessage("S", "B", "from@patrixel.com", ["to@example.com"])
        message.attach("resume.pdf", b"%PDF-1.4 fake", "application/pdf")

        with patch("config.email_backends.requests.post", return_value=_Response()) as post:
            message.send()

        attachments = self._payload(post)["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["filename"], "resume.pdf")
        self.assertEqual(base64.b64decode(attachments[0]["content"]), b"%PDF-1.4 fake")

    def test_message_without_attachments_omits_the_field(self):
        with patch("config.email_backends.requests.post", return_value=_Response()) as post:
            send_mail("S", "B", "from@patrixel.com", ["to@example.com"])

        self.assertNotIn("attachments", self._payload(post))

    def test_text_attachment_is_encoded_rather_than_rejected(self):
        import base64

        message = EmailMessage("S", "B", "from@patrixel.com", ["to@example.com"])
        message.attach("notes.txt", "plain text", "text/plain")

        with patch("config.email_backends.requests.post", return_value=_Response()) as post:
            message.send()

        attachments = self._payload(post)["attachments"]
        self.assertEqual(base64.b64decode(attachments[0]["content"]), b"plain text")
