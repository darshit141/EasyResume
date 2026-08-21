from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import User
from billing.models import AIUsageLog
from resumes.models import JobResumeCuration, Resume
from resumes.services.cover_letter_ai_service import (
    CoverLetterAIService,
    CoverLetterGroundingError,
)
from resumes.services.llm_service import LLMModelUnavailableError, LLMSchemaValidationError
from resumes.tests_job_curation import (
    VALID_JOB_ANALYSIS,
    VALID_REQUEST,
    gap_analysis,
    gap_item,
    poll_job,
)
from resumes.tests_resume_curation import CANDIDATE_PARSED_DATA, QueuedLLMService


def valid_cover_letter(**overrides):
    """Every candidate fact here appears in CANDIDATE_PARSED_DATA, and every
    job reference appears in the job title/description — so this passes
    grounding."""
    result = {
        "greeting": "Dear Hiring Manager",
        "opening_paragraph": (
            "I am writing to apply for the Senior Java Backend Engineer role."
        ),
        "body_paragraphs": [
            "At Acme Corp I built event-driven services with Java, Spring Boot, and Kafka.",
            "I also delivered an Order Processing Pipeline using Kafka and PostgreSQL.",
        ],
        "closing_paragraph": "I would welcome the chance to discuss the role further.",
    }
    result.update(overrides)
    return result


class CoverLetterGroundingTests(TestCase):
    """The grounding contract: candidate facts must come from the profile,
    while quoting the job posting back is allowed."""

    def ground(self, raw):
        return CoverLetterAIService.validate_and_ground(
            raw,
            CANDIDATE_PARSED_DATA,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            job_requirements=VALID_JOB_ANALYSIS,
        )

    def test_valid_letter_passes_and_is_normalised(self):
        result = self.ground(valid_cover_letter())

        self.assertEqual(result["greeting"], "Dear Hiring Manager")
        self.assertEqual(len(result["body_paragraphs"]), 2)

    def test_signature_comes_from_verified_data_not_the_model(self):
        # The schema has no signature field at all, so the only possible
        # source is the verified profile.
        result = self.ground(valid_cover_letter())
        self.assertEqual(result["signature_name"], "Jane Doe")

    def test_invented_employer_is_rejected(self):
        raw = valid_cover_letter(
            body_paragraphs=["I spent four years at Globex Industries leading platform work."]
        )
        with self.assertRaises(CoverLetterGroundingError) as ctx:
            self.ground(raw)
        self.assertTrue(any("Globex" in v for v in ctx.exception.violations))

    def test_invented_technology_is_rejected(self):
        raw = valid_cover_letter(
            body_paragraphs=["I have deep production experience with Kubernetes and Terraform."]
        )
        with self.assertRaises(CoverLetterGroundingError):
            self.ground(raw)

    def test_invented_metric_is_rejected(self):
        raw = valid_cover_letter(
            body_paragraphs=["At Acme Corp I improved throughput by 87 percent."]
        )
        with self.assertRaises(CoverLetterGroundingError) as ctx:
            self.ground(raw)
        self.assertTrue(any("87" in v for v in ctx.exception.violations))

    def test_standard_salutation_is_not_treated_as_an_unverified_fact(self):
        # "Hiring"/"Manager" appear in neither the resume nor the posting, but
        # they are courtesy formulae, not claims — see SALUTATION_ALLOWLIST.
        self.ground(valid_cover_letter(greeting="Dear Hiring Manager"))  # must not raise
        self.ground(valid_cover_letter(greeting="To Whom It May Concern"))

    def test_invented_addressee_name_is_still_rejected(self):
        # The salutation exemption must not become a hole to smuggle a
        # fabricated person through.
        with self.assertRaises(CoverLetterGroundingError):
            self.ground(valid_cover_letter(greeting="Dear Ms. Priyanka Sharma"))

    def test_quoting_the_job_posting_is_allowed(self):
        # "REST APIs" and "Django" come from the job analysis/description, not
        # the candidate — referencing the employer's own stated needs must not
        # be treated as fabricating a candidate fact.
        raw = valid_cover_letter(
            body_paragraphs=["I read that the team values REST APIs and Django experience."]
        )
        self.ground(raw)  # must not raise

    def test_empty_body_paragraphs_are_dropped(self):
        raw = valid_cover_letter(body_paragraphs=["At Acme Corp I used Java.", "   ", ""])
        result = self.ground(raw)
        self.assertEqual(len(result["body_paragraphs"]), 1)

    def test_wrong_shape_is_a_schema_error(self):
        with self.assertRaises(LLMSchemaValidationError):
            self.ground({"greeting": "Hi"})

    def test_non_string_paragraph_is_a_schema_error(self):
        with self.assertRaises(LLMSchemaValidationError):
            self.ground(valid_cover_letter(body_paragraphs=["ok", 42]))

    def test_too_many_body_paragraphs_is_a_schema_error(self):
        with self.assertRaises(LLMSchemaValidationError):
            self.ground(valid_cover_letter(body_paragraphs=["a"] * 5))


class CoverLetterAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-cl@example.com", phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe", email="john-cl@example.com", phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        Resume.objects.create(
            user=self.user,
            file=SimpleUploadedFile("resume.pdf", b"resume"),
            original_filename="resume.pdf",
            status="completed",
            parsed_data=CANDIDATE_PARSED_DATA,
        )

    def create_curation(self, user=None, **overrides):
        defaults = dict(
            user=user or self.user,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=VALID_JOB_ANALYSIS,
            gap_analysis_data=gap_analysis([gap_item("Java", "required_skill", "MATCHED", ["Java"])]),
        )
        defaults.update(overrides)
        return JobResumeCuration.objects.create(**defaults)

    def url(self, curation):
        return f"/api/resumes/job-curations/{curation.id}/cover-letter/"

    def generate(self, curation, payload=None, llm=None):
        llm = llm or QueuedLLMService([valid_cover_letter()])
        with patch(
            "resumes.services.cover_letter_ai_service.get_llm_service", return_value=llm
        ):
            return self.client.post(self.url(curation), payload or {}, format="json")

    def test_generates_and_persists_a_cover_letter(self):
        curation = self.create_curation()

        response = self.generate(curation)

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "completed")
        self.assertEqual(job_status.data["result"]["cover_letter_data"]["greeting"], "Dear Hiring Manager")
        curation.refresh_from_db()
        self.assertTrue(curation.cover_letter_data)

    def test_second_call_returns_cached_letter_without_calling_ai(self):
        curation = self.create_curation()
        self.generate(curation)

        llm = QueuedLLMService([])  # would raise if called
        response = self.generate(curation, llm=llm)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(llm.call_count, 0)

    def test_regenerate_calls_ai_again(self):
        curation = self.create_curation()
        self.generate(curation)

        llm = QueuedLLMService([valid_cover_letter(greeting="Dear Talent Team")])
        response = self.generate(curation, payload={"regenerate": True}, llm=llm)

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "completed")
        self.assertEqual(llm.call_count, 1)
        self.assertEqual(job_status.data["result"]["cover_letter_data"]["greeting"], "Dear Talent Team")

    def test_cached_read_does_not_consume_ai_quota(self):
        curation = self.create_curation()
        self.generate(curation)
        before = AIUsageLog.objects.filter(user=self.user).count()

        self.generate(curation, llm=QueuedLLMService([]))

        self.assertEqual(AIUsageLog.objects.filter(user=self.user).count(), before)

    def test_generation_records_ai_usage(self):
        curation = self.create_curation()

        self.generate(curation)

        self.assertEqual(
            AIUsageLog.objects.filter(
                user=self.user, action=AIUsageLog.ACTION_COVER_LETTER
            ).count(),
            1,
        )

    def test_failed_regeneration_keeps_the_existing_letter(self):
        curation = self.create_curation()
        self.generate(curation)
        original = JobResumeCuration.objects.get(id=curation.id).cover_letter_data

        response = self.generate(
            curation,
            payload={"regenerate": True},
            llm=QueuedLLMService(exc=LLMModelUnavailableError()),
        )

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "failed")
        self.assertEqual(job_status.data["error_code"], "LLM_MODEL_UNAVAILABLE")
        curation.refresh_from_db()
        self.assertEqual(curation.cover_letter_data, original)
        self.assertEqual(curation.cover_letter_error_code, "LLM_MODEL_UNAVAILABLE")

    def test_ungrounded_output_is_rejected_after_retries(self):
        curation = self.create_curation()
        bad = valid_cover_letter(body_paragraphs=["I led a team at Globex Industries."])

        response = self.generate(curation, llm=QueuedLLMService([bad, bad]))

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "failed")
        self.assertEqual(job_status.data["error_code"], "COVER_LETTER_GROUNDING_FAILED")

    def test_retry_succeeds_after_one_ungrounded_attempt(self):
        curation = self.create_curation()
        bad = valid_cover_letter(body_paragraphs=["I led a team at Globex Industries."])
        llm = QueuedLLMService([bad, valid_cover_letter()])

        response = self.generate(curation, llm=llm)

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "completed")
        self.assertEqual(llm.call_count, 2)

    def test_another_users_curation_returns_404(self):
        curation = self.create_curation(user=self.other_user)
        response = self.generate(curation)
        self.assertEqual(response.status_code, 404)

    def test_requires_authentication(self):
        curation = self.create_curation()
        client = APIClient()
        response = client.post(self.url(curation), {}, format="json")
        self.assertEqual(response.status_code, 401)


class ApplicationStatusAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-track@example.com", phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe", email="john-track@example.com", phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def create_curation(self, user=None):
        return JobResumeCuration.objects.create(
            user=user or self.user,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=VALID_JOB_ANALYSIS,
        )

    def url(self, curation):
        return f"/api/resumes/job-curations/{curation.id}/application/"

    def test_defaults_to_saved(self):
        curation = self.create_curation()
        response = self.client.get(self.url(curation))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["application_status"], "saved")
        self.assertIsNone(response.data["applied_at"])

    def test_updates_status(self):
        curation = self.create_curation()

        response = self.client.patch(
            self.url(curation), {"application_status": "applied"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["application_status"], "applied")

    def test_updates_date_and_notes(self):
        curation = self.create_curation()

        response = self.client.patch(
            self.url(curation),
            {"applied_at": "2026-08-18", "application_notes": "Referred by a friend."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.data["applied_at"]), "2026-08-18")
        self.assertEqual(response.data["application_notes"], "Referred by a friend.")

    def test_partial_update_leaves_other_fields_alone(self):
        curation = self.create_curation()
        self.client.patch(
            self.url(curation),
            {"application_status": "interviewing", "application_notes": "Round 1 done."},
            format="json",
        )

        self.client.patch(self.url(curation), {"application_status": "offer"}, format="json")

        curation.refresh_from_db()
        self.assertEqual(curation.application_status, "offer")
        self.assertEqual(curation.application_notes, "Round 1 done.")

    def test_invalid_status_is_rejected(self):
        curation = self.create_curation()
        response = self.client.patch(
            self.url(curation), {"application_status": "ghosted"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_tracking_never_consumes_ai_quota(self):
        curation = self.create_curation()
        self.client.patch(self.url(curation), {"application_status": "applied"}, format="json")
        self.assertEqual(AIUsageLog.objects.filter(user=self.user).count(), 0)

    def test_another_users_curation_is_not_readable(self):
        curation = self.create_curation(user=self.other_user)
        self.assertEqual(self.client.get(self.url(curation)).status_code, 404)

    def test_another_users_curation_is_not_writable(self):
        curation = self.create_curation(user=self.other_user)
        response = self.client.patch(
            self.url(curation), {"application_status": "offer"}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        curation.refresh_from_db()
        self.assertEqual(curation.application_status, "saved")

    def test_curation_reports_which_ai_work_already_exists(self):
        # Drives the UI's "is this already done?" decision, so it can show
        # cached work without re-running anything and ask before spending a
        # credit on work that hasn't been done.
        curation = self.create_curation()

        response = self.client.get(f"/api/resumes/job-curations/{curation.id}/")

        self.assertFalse(response.data["has_gap_analysis"])
        self.assertFalse(response.data["has_tailored_resume"])
        self.assertFalse(response.data["has_cover_letter"])

    def test_existing_ai_work_is_reported_as_present(self):
        curation = self.create_curation()
        curation.gap_analysis_data = gap_analysis([gap_item("Java", "required_skill", "MATCHED")])
        curation.cover_letter_data = {"greeting": "Dear Hiring Manager"}
        curation.save()

        response = self.client.get(f"/api/resumes/job-curations/{curation.id}/")

        self.assertTrue(response.data["has_gap_analysis"])
        self.assertTrue(response.data["has_cover_letter"])
        self.assertFalse(response.data["has_tailored_resume"])

    def test_viewing_a_cached_gap_analysis_spends_no_ai_credit(self):
        curation = self.create_curation()
        curation.gap_analysis_data = gap_analysis([gap_item("Java", "required_skill", "MATCHED")])
        curation.save()

        with patch("resumes.views.job_curation_view.JobGapAnalysisService") as mock_service:
            mock_service.return_value.get_or_create_gap_analysis.return_value = curation
            response = self.client.get(f"/api/resumes/job-curations/{curation.id}/gap-analysis/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AIUsageLog.objects.filter(user=self.user).count(), 0)

    def test_status_appears_in_the_curation_list(self):
        curation = self.create_curation()
        self.client.patch(self.url(curation), {"application_status": "applied"}, format="json")

        response = self.client.get("/api/resumes/job-curations/")

        self.assertEqual(response.data[0]["application_status"], "applied")
