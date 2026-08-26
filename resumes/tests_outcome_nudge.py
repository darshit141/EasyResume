"""Tests for the outcome-capture loop.

The product's one compounding asset is knowing whether applications
actually succeeded. That data only exists if users are asked at the right
moment and answering costs them one click - so these tests cover the
asking, the answering, and the guardrails that stop the asking becoming
spam or the answering becoming a security hole.
"""

from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobResumeCuration
from resumes.services.outcome_nudge_service import (
    make_outcome_token,
    resolve_outcome_token,
    send_due_outcome_nudges,
)


def _user(email="applicant@example.com", phone="5551230000"):
    return User.objects.create_user(
        name="Jane Doe", email=email, phone=phone, password="safe-password"
    )


def _applied_curation(user, days_ago=8, status=None):
    return JobResumeCuration.objects.create(
        user=user,
        job_title="Backend Engineer",
        job_description="Django, Postgres",
        status="completed",
        application_status=status or JobResumeCuration.APPLICATION_STATUS_APPLIED,
        applied_at=timezone.now().date() - timedelta(days=days_ago),
    )


class OutcomeNudgeSendingTests(TestCase):
    def setUp(self):
        self.user = _user()
        mail.outbox.clear()

    def test_sends_a_nudge_for_an_application_past_the_window(self):
        curation = _applied_curation(self.user, days_ago=8)

        result = send_due_outcome_nudges(after_days=7)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Backend Engineer", mail.outbox[0].subject)
        curation.refresh_from_db()
        self.assertIsNotNone(curation.outcome_nudge_sent_at)

    def test_does_not_nudge_before_the_window(self):
        _applied_curation(self.user, days_ago=2)

        result = send_due_outcome_nudges(after_days=7)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_never_nudges_the_same_application_twice(self):
        # Outcome data is only worth collecting if asking for it doesn't
        # turn into a recurring annoyance.
        _applied_curation(self.user, days_ago=10)

        send_due_outcome_nudges(after_days=7)
        mail.outbox.clear()
        second = send_due_outcome_nudges(after_days=7)

        self.assertEqual(second["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_does_not_nudge_an_application_whose_outcome_is_known(self):
        _applied_curation(
            self.user, days_ago=30, status=JobResumeCuration.APPLICATION_STATUS_REJECTED
        )

        result = send_due_outcome_nudges(after_days=7)

        self.assertEqual(result["sent"], 0)

    def test_does_not_nudge_something_never_marked_applied(self):
        JobResumeCuration.objects.create(
            user=self.user,
            job_title="Saved Only",
            job_description="jd",
            status="completed",
            application_status=JobResumeCuration.APPLICATION_STATUS_SAVED,
        )

        result = send_due_outcome_nudges(after_days=7)

        self.assertEqual(result["sent"], 0)

    def test_email_contains_a_one_click_link_for_each_outcome(self):
        _applied_curation(self.user, days_ago=8)

        send_due_outcome_nudges(after_days=7)

        body = mail.outbox[0].body
        for status in ("interviewing", "offer", "rejected"):
            self.assertIn(f"s={status}", body)


class OutcomeTokenTests(TestCase):
    def setUp(self):
        self.user = _user(email="tok@example.com", phone="5551230001")
        self.curation = _applied_curation(self.user)

    def test_a_valid_token_resolves_to_its_curation(self):
        token = make_outcome_token(self.curation)

        self.assertEqual(resolve_outcome_token(token), self.curation)

    def test_a_tampered_token_resolves_to_nothing(self):
        token = make_outcome_token(self.curation) + "tampered"

        self.assertIsNone(resolve_outcome_token(token))

    def test_an_expired_token_resolves_to_nothing(self):
        from unittest.mock import patch

        token = make_outcome_token(self.curation)
        with patch(
            "resumes.services.outcome_nudge_service.NUDGE_TOKEN_MAX_AGE_SECONDS", -1
        ):
            self.assertIsNone(resolve_outcome_token(token))

    def test_a_token_for_a_deleted_curation_resolves_to_nothing(self):
        token = make_outcome_token(self.curation)
        self.curation.delete()

        self.assertIsNone(resolve_outcome_token(token))


class RecordOutcomeEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _user(email="rec@example.com", phone="5551230002")
        self.curation = _applied_curation(self.user)
        self.url = "/api/resumes/outcomes/record/"

    def test_records_an_outcome_without_any_login(self):
        # Requiring a login to answer "did you hear back?" is exactly the
        # friction that stops the data being collected.
        token = make_outcome_token(self.curation)

        response = self.client.post(
            self.url, {"token": token, "application_status": "interviewing"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.curation.refresh_from_db()
        self.assertEqual(self.curation.application_status, "interviewing")

    def test_rejects_an_invalid_token(self):
        response = self.client.post(
            self.url, {"token": "not-a-real-token", "application_status": "offer"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.curation.refresh_from_db()
        self.assertEqual(self.curation.application_status, "applied")

    def test_rejects_a_status_outside_the_allowed_set(self):
        token = make_outcome_token(self.curation)

        response = self.client.post(
            self.url, {"token": token, "application_status": "hired_somewhere_else"}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_token_cannot_set_an_outcome_on_a_different_application(self):
        other_user = _user(email="other@example.com", phone="5551230003")
        other_curation = _applied_curation(other_user)
        token = make_outcome_token(self.curation)

        self.client.post(
            self.url, {"token": token, "application_status": "offer"}, format="json"
        )

        other_curation.refresh_from_db()
        self.assertEqual(other_curation.application_status, "applied")


class OutcomeCaptureMetricTests(TestCase):
    """The capture rate is what says whether the reported rates mean
    anything at all."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            email="metrics-admin@example.com", name="Admin", phone="5559990000",
            password="adminpassword",
        )
        self.user = _user(email="m@example.com", phone="5551230004")

    def _metrics(self):
        self.client.force_authenticate(user=self.admin)
        return self.client.get("/api/admin/business-metrics/").data["customer_outcomes"]

    def test_capture_rate_is_null_with_no_submitted_applications(self):
        self.assertIsNone(self._metrics()["outcome_capture_rate"])

    def test_capture_rate_counts_resolved_over_submitted(self):
        _applied_curation(self.user, status=JobResumeCuration.APPLICATION_STATUS_APPLIED)
        _applied_curation(self.user, status=JobResumeCuration.APPLICATION_STATUS_INTERVIEWING)
        _applied_curation(self.user, status=JobResumeCuration.APPLICATION_STATUS_REJECTED)

        metrics = self._metrics()

        self.assertEqual(metrics["applications_submitted"], 3)
        self.assertEqual(metrics["outcomes_resolved"], 2)
        self.assertEqual(metrics["awaiting_outcome"], 1)
        self.assertAlmostEqual(metrics["outcome_capture_rate"], 0.6667, places=3)

    def test_rejections_count_as_resolved_not_as_failure_to_capture(self):
        # A rejection is data. Treating it as "no outcome" would make the
        # capture rate look worse the more honestly users report.
        _applied_curation(self.user, status=JobResumeCuration.APPLICATION_STATUS_REJECTED)

        self.assertEqual(self._metrics()["outcome_capture_rate"], 1.0)
