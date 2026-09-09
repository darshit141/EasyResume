"""Tracking applications the extension filled in.

Most of these are about the line between what we observed and what we
assume. Filling a form is not applying, a submit event is, and "no I
didn't" is neither — getting that wrong puts jobs in someone's tracker
that they never applied to, which makes the whole tracker untrustworthy.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from resumes.models import JobResumeCuration
from resumes.services.application_tracking_service import (
    ApplicationTrackingService,
    normalise_job_url,
)

User = get_user_model()


class NormaliseJobUrlTests(APITestCase):
    def test_strips_campaign_parameters(self):
        self.assertEqual(
            normalise_job_url("https://boards.greenhouse.io/acme/jobs/42?gh_src=abc&utm_source=li"),
            normalise_job_url("https://boards.greenhouse.io/acme/jobs/42"),
        )

    def test_keeps_parameters_that_identify_the_job(self):
        """The job id lives in the query on several large boards. Dropping
        the whole query would collapse every posting on a site into one."""
        self.assertNotEqual(
            normalise_job_url("https://www.linkedin.com/jobs/view/?currentJobId=111"),
            normalise_job_url("https://www.linkedin.com/jobs/view/?currentJobId=222"),
        )

    def test_ignores_scheme_www_trailing_slash_and_fragment(self):
        variants = [
            "https://www.lever.co/acme/job-1",
            "http://lever.co/acme/job-1/",
            "https://lever.co/acme/job-1#apply",
        ]
        keys = {normalise_job_url(url) for url in variants}
        self.assertEqual(len(keys), 1)

    def test_parameter_order_does_not_matter(self):
        self.assertEqual(
            normalise_job_url("https://x.com/j?a=1&b=2"),
            normalise_job_url("https://x.com/j?b=2&a=1"),
        )

    def test_rejects_unusable_values(self):
        for value in ["", None, "not a url", "javascript:alert(1)", "chrome://extensions"]:
            self.assertEqual(normalise_job_url(value), "")


class RecordAutofillTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@example.com", password="pw", name="A")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("tracking-autofill")

    def test_creates_a_tracked_but_unanalysed_row(self):
        response = self.client.post(
            self.url,
            {"job_title": "Backend Engineer", "job_url": "https://jobs.example.com/1"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        curation = JobResumeCuration.objects.get(id=response.data["id"])
        self.assertEqual(curation.status, JobResumeCuration.STATUS_SAVED)
        self.assertEqual(curation.source, JobResumeCuration.SOURCE_EXTENSION)
        self.assertFalse(curation.curate_resume)
        self.assertIsNotNone(curation.autofilled_at)

    def test_filling_does_not_count_as_applying(self):
        """The single most important rule here: we filled a form, which is
        not the same as the user having sent it."""
        response = self.client.post(self.url, {"job_title": "Backend Engineer"}, format="json")

        curation = JobResumeCuration.objects.get(id=response.data["id"])
        self.assertEqual(curation.application_status, JobResumeCuration.APPLICATION_STATUS_SAVED)
        self.assertIsNone(curation.applied_at)
        self.assertIsNone(curation.submit_detected_at)

    def test_no_ai_analysis_is_queued(self):
        """A credit must never be spent on work nobody asked for."""
        response = self.client.post(self.url, {"job_title": "Backend Engineer"}, format="json")

        curation = JobResumeCuration.objects.get(id=response.data["id"])
        self.assertEqual(curation.analysis_data, {})
        self.assertEqual(curation.gap_analysis_data, {})
        self.assertEqual(curation.tailored_resume_data, {})

    def test_filling_the_same_job_twice_does_not_duplicate_it(self):
        first = self.client.post(
            self.url,
            {"job_title": "Backend Engineer", "job_url": "https://jobs.example.com/1?utm_source=email"},
            format="json",
        )
        second = self.client.post(
            self.url,
            {"job_title": "Backend Engineer", "job_url": "https://jobs.example.com/1"},
            format="json",
        )

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(JobResumeCuration.objects.filter(user=self.user).count(), 1)

    def test_attaches_to_a_job_the_user_already_captured(self):
        """Filling the form for a job analysed on the website must land on
        that curation, not create a second row beside it."""
        existing = JobResumeCuration.objects.create(
            user=self.user,
            job_title="Backend Engineer",
            job_description="x" * 60,
            job_url="https://jobs.example.com/1",
            status="completed",
        )

        response = self.client.post(
            self.url,
            {"job_title": "Backend Engineer", "job_url": "https://jobs.example.com/1"},
            format="json",
        )

        self.assertEqual(response.data["id"], existing.id)
        existing.refresh_from_db()
        self.assertIsNotNone(existing.autofilled_at)
        # The analysed curation keeps its own status and provenance.
        self.assertEqual(existing.status, "completed")
        self.assertEqual(existing.source, JobResumeCuration.SOURCE_WEB)

    def test_never_matches_another_users_job(self):
        other = User.objects.create_user(email="b@example.com", password="pw", name="B")
        JobResumeCuration.objects.create(
            user=other,
            job_title="Backend Engineer",
            job_description="x" * 60,
            job_url="https://jobs.example.com/1",
        )

        response = self.client.post(
            self.url,
            {"job_title": "Backend Engineer", "job_url": "https://jobs.example.com/1"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(JobResumeCuration.objects.filter(user=other).count(), 1)

    def test_a_page_with_no_readable_posting_still_tracks(self):
        """An application form is usually not the posting, so there is often
        no description to be had. That must not lose the record."""
        response = self.client.post(self.url, {"job_title": "Careers - Acme"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["job_title"], "Careers - Acme")

    def test_a_title_is_required(self):
        response = self.client.post(self.url, {"job_url": "https://x.com/1"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unusable_url_is_not_stored(self):
        response = self.client.post(
            self.url, {"job_title": "Engineer", "job_url": "about:blank"}, format="json"
        )
        self.assertEqual(JobResumeCuration.objects.get(id=response.data["id"]).job_url, "")

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(self.url, {"job_title": "Engineer"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubmitDetectionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@example.com", password="pw", name="A")
        self.client.force_authenticate(user=self.user)
        self.curation = JobResumeCuration.objects.create(
            user=self.user,
            job_title="Backend Engineer",
            job_description="",
            status=JobResumeCuration.STATUS_SAVED,
            autofilled_at=timezone.now(),
        )
        self.url = reverse("tracking-submitted", args=[self.curation.id])

    def test_a_real_submit_marks_it_applied(self):
        self.client.post(self.url)

        self.curation.refresh_from_db()
        self.assertEqual(self.curation.application_status, JobResumeCuration.APPLICATION_STATUS_APPLIED)
        self.assertEqual(self.curation.applied_at, timezone.localdate())
        self.assertIsNotNone(self.curation.submit_detected_at)

    def test_repeated_reports_keep_the_first_observation(self):
        """A page can fire submit twice — a validation bounce, then the real
        one. The first sighting is the one that happened."""
        self.client.post(self.url)
        self.curation.refresh_from_db()
        first = self.curation.submit_detected_at

        self.client.post(self.url)
        self.curation.refresh_from_db()
        self.assertEqual(self.curation.submit_detected_at, first)

    def test_never_drags_a_later_stage_backwards(self):
        self.curation.application_status = JobResumeCuration.APPLICATION_STATUS_INTERVIEWING
        self.curation.save()

        self.client.post(self.url)

        self.curation.refresh_from_db()
        self.assertEqual(
            self.curation.application_status, JobResumeCuration.APPLICATION_STATUS_INTERVIEWING
        )
        # Still recorded that we saw it sent.
        self.assertIsNotNone(self.curation.submit_detected_at)

    def test_cannot_report_a_submit_on_someone_elses_application(self):
        other = User.objects.create_user(email="b@example.com", password="pw", name="B")
        self.client.force_authenticate(user=other)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.curation.refresh_from_db()
        self.assertIsNone(self.curation.submit_detected_at)


class PendingConfirmationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@example.com", password="pw", name="A")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("tracking-pending")

    def _filled(self, **kwargs):
        defaults = {
            "user": self.user,
            "job_title": "Backend Engineer",
            "job_description": "",
            "status": JobResumeCuration.STATUS_SAVED,
            "autofilled_at": timezone.now(),
        }
        return JobResumeCuration.objects.create(**{**defaults, **kwargs})

    def test_lists_a_fill_we_never_saw_submitted(self):
        job = self._filled()
        response = self.client.get(self.url)
        self.assertEqual([row["id"] for row in response.data], [job.id])

    def test_does_not_ask_about_something_we_detected(self):
        self._filled(submit_detected_at=timezone.now())
        self.assertEqual(self.client.get(self.url).data, [])

    def test_does_not_ask_about_something_the_user_already_filed(self):
        self._filled(application_status=JobResumeCuration.APPLICATION_STATUS_APPLIED)
        self.assertEqual(self.client.get(self.url).data, [])

    def test_does_not_ask_again_once_dismissed(self):
        job = self._filled()
        self.client.post(reverse("tracking-dismiss", args=[job.id]))
        self.assertEqual(self.client.get(self.url).data, [])

    def test_forgets_fills_older_than_the_window(self):
        """Asking about a tab from a month ago is noise, not tracking."""
        self._filled(autofilled_at=timezone.now() - timedelta(days=20))
        self.assertEqual(self.client.get(self.url).data, [])

    def test_ignores_jobs_the_extension_never_touched(self):
        JobResumeCuration.objects.create(
            user=self.user, job_title="Typed in by hand", job_description="x" * 60
        )
        self.assertEqual(self.client.get(self.url).data, [])

    def test_only_the_users_own(self):
        other = User.objects.create_user(email="b@example.com", password="pw", name="B")
        self._filled(user=other)
        self.assertEqual(self.client.get(self.url).data, [])

    def test_dismissing_claims_no_outcome(self):
        """"No" means stop asking. It must not record an application, and it
        must not delete a job the user might yet apply to."""
        job = self._filled()

        self.client.post(reverse("tracking-dismiss", args=[job.id]))

        job.refresh_from_db()
        self.assertEqual(job.application_status, JobResumeCuration.APPLICATION_STATUS_SAVED)
        self.assertIsNone(job.applied_at)
        self.assertIsNotNone(job.autofilled_at)
        self.assertTrue(JobResumeCuration.objects.filter(id=job.id).exists())

    def test_dismiss_is_scoped_to_the_owner(self):
        job = self._filled()
        other = User.objects.create_user(email="b@example.com", password="pw", name="B")
        self.client.force_authenticate(user=other)

        response = self.client.post(reverse("tracking-dismiss", args=[job.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        job.refresh_from_db()
        self.assertIsNone(job.autofill_prompt_dismissed_at)

    def test_confirming_yes_uses_the_existing_status_endpoint(self):
        """The extension's "yes" goes through the same endpoint the website
        uses, so there is one way an application becomes 'applied'."""
        job = self._filled()

        self.client.patch(
            reverse("job-curation-application-status", args=[job.id]),
            {"application_status": "applied", "applied_at": str(timezone.localdate())},
            format="json",
        )

        job.refresh_from_db()
        self.assertEqual(job.application_status, JobResumeCuration.APPLICATION_STATUS_APPLIED)
        # We did not observe this one; only the user's word is recorded.
        self.assertIsNone(job.submit_detected_at)


class ServiceGuardTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@example.com", password="pw", name="A")

    def test_a_blank_title_is_refused_rather_than_invented(self):
        with self.assertRaises(ValueError):
            ApplicationTrackingService.record_autofill(
                self.user, job_url="https://x.com/1", job_title="   "
            )
