from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import User


class FeedbackAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/feedback/"

    def test_anonymous_visitor_can_report_a_bug(self):
        response = self.client.post(
            self.url,
            {
                "category": "bug",
                "message": "The job link import silently fails.",
                "email": "visitor@example.com",
                "page_url": "https://intelliresume.online/app/job-curations/new",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["sales@patrixel.com"])
        self.assertIn("Bug report", sent.subject)
        self.assertIn("job link import silently fails", sent.body)
        self.assertIn("visitor@example.com", sent.body)
        self.assertEqual(sent.reply_to, ["visitor@example.com"])

    def test_logged_in_user_does_not_need_to_supply_an_email(self):
        user = User.objects.create_user(
            name="Priya", email="priya@example.com", phone="4444444444", password="safe-password",
        )
        self.client.force_authenticate(user=user)

        response = self.client.post(
            self.url, {"category": "feature", "message": "Let me export to LinkedIn."}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        sent = mail.outbox[0]
        self.assertIn("priya@example.com", sent.body)
        self.assertIn("Priya", sent.body)
        self.assertEqual(sent.reply_to, ["priya@example.com"])
        self.assertIn(f"User ID: {user.id}", sent.body)

    def test_missing_message_is_rejected(self):
        response = self.client.post(self.url, {"category": "bug"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_blank_message_is_rejected(self):
        response = self.client.post(self.url, {"category": "bug", "message": "   "}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_category_is_rejected(self):
        response = self.client.post(
            self.url, {"category": "not-a-real-category", "message": "Hi"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_anonymous_report_without_an_email_still_sends(self):
        # No way to reply, but the report itself shouldn't be lost over it.
        response = self.client.post(
            self.url, {"category": "bug", "message": "Something broke."}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(mail.outbox[0].reply_to or None)

    def test_delivery_failure_is_reported_not_swallowed(self):
        with patch(
            "api.views.feedback_views.send_feedback_email", side_effect=RuntimeError("boom")
        ):
            response = self.client.post(
                self.url, {"category": "bug", "message": "Something broke."}, format="json"
            )

        self.assertEqual(response.status_code, 502)

    def test_contact_message_from_a_visitor_is_sent_with_their_name(self):
        response = self.client.post(
            self.url,
            {
                "category": "contact",
                "name": "Sam Rivera",
                "email": "sam@example.com",
                "message": "Do you offer a plan for career coaches?",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        sent = mail.outbox[0]
        self.assertIn("Contact message", sent.subject)
        self.assertIn("Sam Rivera", sent.body)
        self.assertEqual(sent.reply_to, ["sam@example.com"])

    def test_contact_message_without_an_email_is_rejected(self):
        response = self.client.post(
            self.url, {"category": "contact", "message": "Please call me back."}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)
        self.assertEqual(len(mail.outbox), 0)
