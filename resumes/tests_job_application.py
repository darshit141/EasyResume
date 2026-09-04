"""Tests for Direct Apply — drafting, reviewing and sending applications.

The invariant these tests exist to defend is that **nothing reaches an
employer that the user did not read and approve first**, and that what does
reach them contains no claim the user's own resume can't support.
"""

from unittest.mock import patch

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobApplication, JobResumeCuration, Resume
from resumes.services.job_application_ai_service import (
    ApplicationEmailGroundingError,
    JobApplicationAIService,
)
from resumes.services.llm_service import LLMSchemaValidationError
from resumes.tests_job_curation import VALID_JOB_ANALYSIS, VALID_REQUEST
from resumes.tests_resume_curation import CANDIDATE_PARSED_DATA, QueuedLLMService

TAILORED_RESUME_DATA = {
    "summary": "Backend engineer with Java, Spring Boot, and Kafka experience.",
    "skills": ["Java", "Spring Boot", "Kafka"],
    "experience": [
        {"header": "Backend Engineer at Acme Corp", "bullets": ["Built services with Java and Kafka."]}
    ],
    "projects": [],
    "education": [],
    "certifications": [],
}


def valid_email(**overrides):
    """Every candidate fact here traces to CANDIDATE_PARSED_DATA and every
    job reference to the posting, so this passes grounding."""
    result = {
        "subject": "Application for Senior Java Backend Engineer - Jane Doe",
        "body": (
            "Dear Hiring Team,\n\n"
            "I am applying for the Senior Java Backend Engineer role. At Acme Corp "
            "I built event-driven services with Java, Spring Boot, and Kafka.\n\n"
            "My resume is attached. I would welcome the chance to discuss the role.\n\n"
            "Kind regards,\nJane Doe"
        ),
    }
    result.update(overrides)
    return result


class ApplicationEmailGroundingTests(TestCase):
    """Candidate facts must come from the profile; courtesy wording is exempt
    but anything numeric never is."""

    def ground(self, raw):
        return JobApplicationAIService.validate_and_ground(
            raw,
            candidate_source_data=CANDIDATE_PARSED_DATA,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            job_requirements=VALID_JOB_ANALYSIS,
            company_name="Acme Corp",
        )

    def test_valid_email_passes(self):
        result = self.ground(valid_email())

        self.assertIn("Acme Corp", result["body"])
        self.assertTrue(result["subject"].startswith("Application for"))

    def test_boilerplate_greeting_and_signoff_do_not_fail_grounding(self):
        # "Dear", "Kind regards", "Application" appear nowhere in the resume,
        # and grounding them would reject every well-formed email.
        result = self.ground(valid_email())

        self.assertIn("Kind regards", result["body"])

    def test_invented_employer_is_rejected(self):
        with self.assertRaises(ApplicationEmailGroundingError):
            self.ground(
                valid_email(
                    body="Dear Hiring Team,\n\nI led the platform team at Globex.\n\nKind regards,\nJane Doe"
                )
            )

    def test_invented_metric_is_rejected_despite_boilerplate_wording(self):
        # The number is the whole point: a digit-bearing token is never
        # treated as courtesy vocabulary, however politely it is phrased.
        with self.assertRaises(ApplicationEmailGroundingError) as ctx:
            self.ground(
                valid_email(
                    body=(
                        "Dear Hiring Team,\n\nPlease find attached my resume for the role. "
                        "I have 12 years of experience.\n\nKind regards,\nJane Doe"
                    )
                )
            )

        self.assertTrue(any("12" in violation for violation in ctx.exception.violations))

    def test_sentence_opening_common_word_is_not_a_violation(self):
        # "As" is capitalised for grammar, not because it names anything.
        # Before this was handled, roughly one draft in three failed here.
        result = self.ground(
            valid_email(
                body=(
                    "Dear Hiring Team,\n\nAs an engineer at Acme Corp I worked with "
                    "Java and Kafka.\n\nKind regards,\nJane Doe"
                )
            )
        )

        self.assertTrue(result["body"].startswith("Dear Hiring Team"))

    def test_invented_proper_noun_at_sentence_start_is_still_rejected(self):
        # The exemption must not become a hole: a made-up employer is caught
        # wherever it appears, including as the first word of a sentence.
        with self.assertRaises(ApplicationEmailGroundingError):
            self.ground(
                valid_email(
                    body="Dear Hiring Team,\n\nGlobex trained me well.\n\nKind regards,\nJane Doe"
                )
            )

    def test_invented_certification_is_rejected(self):
        with self.assertRaises(ApplicationEmailGroundingError):
            self.ground(
                valid_email(
                    body="Dear Hiring Team,\n\nI am a Kubernetes Certified Administrator.\n\nKind regards,\nJane Doe"
                )
            )

    def test_retry_is_told_what_was_rejected(self):
        """A blind retry reruns the same prompt and fails the same way. The
        second attempt must name the rejected claims."""
        bad = valid_email(
            body="Dear Hiring Team,\n\nI led engineering at Globex.\n\nKind regards,\nJane Doe"
        )
        llm = QueuedLLMService([bad, valid_email()])

        service = JobApplicationAIService(llm_service=llm)
        result = service.draft_email(
            candidate_source_data=CANDIDATE_PARSED_DATA,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            job_requirements=VALID_JOB_ANALYSIS,
        )

        self.assertEqual(llm.call_count, 2)
        self.assertNotIn("Globex", result["body"])
        second_prompt = llm.prompts[1]
        self.assertIn("PREVIOUS ATTEMPT WAS REJECTED", second_prompt)
        self.assertIn("Globex", second_prompt)

    def test_prompt_states_what_is_actually_attached(self):
        """Otherwise the model writes 'my resume and cover letter are
        attached' regardless of what is really being sent."""
        llm = QueuedLLMService([valid_email()])

        JobApplicationAIService(llm_service=llm).draft_email(
            candidate_source_data=CANDIDATE_PARSED_DATA,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            job_requirements=VALID_JOB_ANALYSIS,
            include_resume=True,
            include_cover_letter=False,
        )

        prompt = llm.prompts[0]
        self.assertIn("resume (PDF attachment)", prompt)
        self.assertNotIn("cover letter appended", prompt)

    def test_prompt_demands_paragraph_breaks(self):
        """Without this the model returns one run-on block and the employer
        receives a wall of text."""
        llm = QueuedLLMService([valid_email()])

        JobApplicationAIService(llm_service=llm).draft_email(
            candidate_source_data=CANDIDATE_PARSED_DATA,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            job_requirements=VALID_JOB_ANALYSIS,
        )

        self.assertIn("separate every paragraph with a blank line", llm.prompts[0])

    def test_prompt_forbids_claiming_attachments_when_there_are_none(self):
        llm = QueuedLLMService([valid_email()])

        JobApplicationAIService(llm_service=llm).draft_email(
            candidate_source_data=CANDIDATE_PARSED_DATA,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            job_requirements=VALID_JOB_ANALYSIS,
            include_resume=False,
            include_cover_letter=False,
        )

        self.assertIn("Do not claim anything is attached", llm.prompts[0])

    def test_malformed_payload_is_a_schema_error(self):
        with self.assertRaises(LLMSchemaValidationError):
            self.ground({"subject": "Only a subject"})

    def test_empty_body_is_a_schema_error(self):
        with self.assertRaises(LLMSchemaValidationError):
            self.ground(valid_email(body="   "))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class JobApplicationAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-app@example.com", phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe", email="john-app@example.com", phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.resume = Resume.objects.create(
            user=self.user,
            file=SimpleUploadedFile("resume.pdf", b"resume"),
            original_filename="resume.pdf",
            status="completed",
            parsed_data=CANDIDATE_PARSED_DATA,
        )
        mail.outbox = []

    def create_curation(self, user=None, **overrides):
        defaults = dict(
            user=user or self.user,
            resume=self.resume,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=VALID_JOB_ANALYSIS,
            tailored_resume_data=TAILORED_RESUME_DATA,
        )
        defaults.update(overrides)
        return JobResumeCuration.objects.create(**defaults)

    def draft(self, curation, payload=None, llm=None):
        llm = llm or QueuedLLMService([valid_email()])
        with patch(
            "resumes.services.job_application_ai_service.get_llm_service", return_value=llm
        ):
            return self.client.post(
                f"/api/resumes/job-curations/{curation.id}/apply/draft/",
                payload or {"recipient_email": "careers@acme.example"},
                format="json",
            )

    # -- drafting ---------------------------------------------------------

    def test_draft_creates_a_reviewable_draft_and_sends_nothing(self):
        curation = self.create_curation()

        response = self.draft(curation)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "draft")
        self.assertEqual(response.data["recipient_email"], "careers@acme.example")
        self.assertIn("Acme Corp", response.data["body"])
        # The whole point of the review gate.
        self.assertEqual(len(mail.outbox), 0)

    def test_draft_requires_a_tailored_resume_to_attach(self):
        curation = self.create_curation(tailored_resume_data={})

        response = self.draft(curation)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "TAILORED_RESUME_REQUIRED")

    def test_draft_rejects_a_curation_belonging_to_someone_else(self):
        curation = self.create_curation(user=self.other_user)

        response = self.draft(curation)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(JobApplication.objects.count(), 0)

    def test_ungrounded_draft_is_reported_not_saved(self):
        curation = self.create_curation()
        invented = valid_email(
            body="Dear Hiring Team,\n\nI led engineering at Globex for 9 years.\n\nKind regards,\nJane Doe"
        )
        llm = QueuedLLMService([invented, invented])

        response = self.draft(curation, llm=llm)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(JobApplication.objects.count(), 0)

    # -- editing ----------------------------------------------------------

    def test_user_can_rewrite_a_draft_before_sending(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        response = self.client.patch(
            f"/api/resumes/applications/{application_id}/",
            {"body": "My own words.", "subject": "My own subject"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["body"], "My own words.")

    def test_blank_body_is_rejected(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        response = self.client.patch(
            f"/api/resumes/applications/{application_id}/", {"body": "  "}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_draft_can_be_discarded(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        response = self.client.delete(f"/api/resumes/applications/{application_id}/")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(JobApplication.objects.count(), 0)

    # -- sending ----------------------------------------------------------

    def send(self, application_id):
        return self.client.post(f"/api/resumes/applications/{application_id}/send/")

    def test_send_delivers_the_email_with_the_resume_attached(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        response = self.send(application_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "sent")
        self.assertEqual(len(mail.outbox), 1)

        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["careers@acme.example"])
        # The employer must reply to the candidate, not into our no-reply void.
        self.assertEqual(sent.reply_to, [self.user.email])
        self.assertEqual(len(sent.attachments), 1)
        filename, content, mimetype = sent.attachments[0]
        self.assertTrue(filename.endswith(".pdf"))
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))

    @override_settings(APPLICATION_FROM_EMAIL="IntelliResume <apply@apply.example.com>")
    def test_applications_send_from_their_own_sender_identity(self):
        """Kept off the transactional sender so a spam complaint about an
        application can't take login codes down with it."""
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        self.send(application_id)

        self.assertEqual(mail.outbox[0].from_email, "IntelliResume <apply@apply.example.com>")

    def test_send_moves_the_job_to_applied_in_the_tracker(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        self.send(application_id)

        curation.refresh_from_db()
        self.assertEqual(curation.application_status, JobResumeCuration.APPLICATION_STATUS_APPLIED)
        self.assertEqual(curation.applied_at, timezone.now().date())

    def test_send_does_not_overwrite_a_status_the_user_already_set(self):
        curation = self.create_curation(
            application_status=JobResumeCuration.APPLICATION_STATUS_INTERVIEWING
        )
        application_id = self.draft(curation).data["id"]

        self.send(application_id)

        curation.refresh_from_db()
        self.assertEqual(
            curation.application_status, JobResumeCuration.APPLICATION_STATUS_INTERVIEWING
        )

    def test_sending_twice_does_not_email_the_employer_twice(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        self.send(application_id)
        second = self.send(application_id)

        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_sent_application_cannot_be_edited(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]
        self.send(application_id)

        response = self.client.patch(
            f"/api/resumes/applications/{application_id}/", {"body": "rewritten"}, format="json"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "APPLICATION_ALREADY_SENT")

    def test_sent_application_cannot_be_deleted(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]
        self.send(application_id)

        response = self.client.delete(f"/api/resumes/applications/{application_id}/")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(JobApplication.objects.count(), 1)

    def test_delivery_failure_is_reported_and_nothing_is_marked_sent(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        with patch(
            "resumes.services.job_application_service.EmailMessage.send",
            side_effect=RuntimeError("provider down"),
        ):
            response = self.send(application_id)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "APPLICATION_DELIVERY_FAILED")
        application = JobApplication.objects.get(id=application_id)
        self.assertEqual(application.status, JobApplication.STATUS_FAILED)
        self.assertIsNone(application.sent_at)

    def test_cover_letter_is_appended_only_when_asked_for(self):
        curation = self.create_curation(
            cover_letter_data={
                "greeting": "Dear Hiring Manager",
                "opening_paragraph": "I am applying for the role.",
                "body_paragraphs": ["I use Java and Kafka."],
                "closing_paragraph": "Thank you for your time.",
                "signature_name": "Jane Doe",
            }
        )
        application_id = self.draft(
            curation,
            payload={"recipient_email": "careers@acme.example", "include_cover_letter": True},
        ).data["id"]

        self.send(application_id)

        self.assertIn("Dear Hiring Manager", mail.outbox[0].body)

    def test_send_without_attachment_when_the_user_opted_out(self):
        curation = self.create_curation()
        application_id = self.draft(
            curation,
            payload={"recipient_email": "careers@acme.example", "include_resume": False},
        ).data["id"]

        self.send(application_id)

        self.assertEqual(len(mail.outbox[0].attachments), 0)

    # -- isolation and limits --------------------------------------------

    def test_cannot_send_another_users_application(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]
        self.client.force_authenticate(user=self.other_user)

        response = self.send(application_id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_list_only_returns_the_users_own_applications(self):
        curation = self.create_curation()
        self.draft(curation)
        JobApplication.objects.create(
            user=self.other_user,
            curation=self.create_curation(user=self.other_user),
            recipient_email="someone@else.example",
            subject="Theirs",
            body="Theirs",
        )

        response = self.client.get("/api/resumes/applications/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["recipient_email"], "careers@acme.example")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_monthly_send_cap_blocks_further_sends(self):
        curation = self.create_curation()
        application_id = self.draft(curation).data["id"]

        with patch(
            "billing.services.entitlements_service.EntitlementsService"
            ".applications_sent_this_month",
            return_value=999,
        ):
            response = self.send(application_id)

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "PLAN_LIMIT_REACHED")
        self.assertEqual(len(mail.outbox), 0)
