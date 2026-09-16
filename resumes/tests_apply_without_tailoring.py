"""Applying without tailoring first.

Tailoring costs an AI credit and is the user's choice, so it must not be a
precondition for applying. A job whose analysis never ran - the extension
tracked it, or the provider was down - must still be appliable with the
resume the account already has.
"""

from unittest.mock import patch

from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase

from authentication.models import User
from resumes.models import JobApplication, JobResumeCuration, Resume
from resumes.services.job_application_service import (
    ApplicationNotSendableError,
    JobApplicationService,
)

PARSED = {
    "personal_information": {"name": "Jane Doe", "email": "jane@example.com", "phone": "+1 555 0100"},
    "summary": "Backend engineer.",
    "skills": ["Python", "Django"],
    "experience": [{"header": "Engineer | Acme | 2020-2024", "bullets": ["Built the API."]}],
    "education": [{"header": "BSc Computer Science | State University | 2016-2020", "bullets": []}],
}
DRAFTED = {"subject": "Application for Backend Engineer", "body": "Dear Hiring Team,\n\nPlease find my resume attached."}


def _user(email="apply@example.com"):
    return User.objects.create_user(
        name="Jane Doe", email=email, phone=f"9{abs(hash(email)) % 1000000000:09d}", password="safe-password"
    )


class ApplyWithMasterResumeTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.resume = Resume.objects.create(
            user=self.user, original_filename="Jane-Doe-CV.pdf", status="completed", parsed_data=PARSED
        )
        # A job that was never analysed: no requirements, no tailored resume.
        self.curation = JobResumeCuration.objects.create(
            user=self.user,
            job_title="Backend Engineer",
            job_description="We need a backend engineer with Python experience.",
            status=JobResumeCuration.STATUS_SAVED,
        )
        patcher = patch("resumes.services.job_application_service.JobApplicationAIService")
        self.ai = patcher.start()
        self.ai.return_value.draft_email.return_value = DRAFTED
        self.addCleanup(patcher.stop)

    def _draft(self, **kwargs):
        return JobApplicationService().create_draft(
            user=self.user,
            curation_id=self.curation.id,
            recipient_email="careers@example.com",
            **kwargs,
        )

    def test_a_job_with_no_analysis_can_still_be_applied_to(self):
        """The whole point: no tailored resume, no analysis, still appliable."""
        application = self._draft()

        self.assertEqual(application.resume_source, JobApplication.RESUME_SOURCE_MASTER)
        self.assertEqual(application.status, JobApplication.STATUS_DRAFT)

    def test_missing_requirements_are_not_an_error(self):
        """build_job_requirements raises when the analysis is empty; drafting
        must treat that as "nothing to lead with", not as a failure."""
        self._draft()

        requirements = self.ai.return_value.draft_email.call_args.kwargs["job_requirements"]
        self.assertEqual(requirements, [])

    def test_the_tailored_resume_is_preferred_when_one_exists(self):
        self.curation.tailored_resume_data = PARSED
        self.curation.save(update_fields=["tailored_resume_data"])

        self.assertEqual(self._draft().resume_source, JobApplication.RESUME_SOURCE_TAILORED)

    def test_asking_for_the_tailored_resume_when_there_is_none_is_still_refused(self):
        """Defaulting is a convenience; an explicit request must not silently
        send something other than what was asked for."""
        with self.assertRaises(ApplicationNotSendableError) as raised:
            self._draft(resume_source="tailored")

        self.assertEqual(raised.exception.code, "TAILORED_RESUME_REQUIRED")

    def test_applying_with_no_resume_at_all_is_refused(self):
        Resume.objects.all().delete()

        with self.assertRaises(ApplicationNotSendableError) as raised:
            self._draft()

        self.assertEqual(raised.exception.code, "MASTER_RESUME_REQUIRED")

    def test_the_uploaded_file_is_what_the_employer_receives(self):
        self.resume.file.save("Jane-Doe-CV.pdf", ContentFile(b"%PDF-1.4 the real thing"), save=True)
        self.addCleanup(self.resume.file.delete, save=False)
        application = self._draft()

        JobApplicationService.send(self.user, application.id)

        name, content, content_type = mail.outbox[0].attachments[0]
        self.assertEqual(name, "Jane-Doe-CV.pdf")
        self.assertEqual(content, b"%PDF-1.4 the real thing")
        self.assertEqual(content_type, "application/pdf")

    def test_a_resume_built_here_has_no_file_so_the_profile_is_rendered(self):
        application = self._draft()

        JobApplicationService.send(self.user, application.id)

        name, content, content_type = mail.outbox[0].attachments[0]
        self.assertEqual(name, "resume.pdf")
        self.assertEqual(content_type, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))

    def test_sending_marks_the_job_applied_in_the_tracker(self):
        application = self._draft()

        JobApplicationService.send(self.user, application.id)

        self.curation.refresh_from_db()
        self.assertEqual(self.curation.application_status, JobResumeCuration.APPLICATION_STATUS_APPLIED)
        self.assertIsNotNone(self.curation.applied_at)

    def test_the_tailored_resume_is_still_sent_as_a_generated_pdf(self):
        self.curation.tailored_resume_data = PARSED
        self.curation.save(update_fields=["tailored_resume_data"])
        application = self._draft()

        JobApplicationService.send(self.user, application.id)

        name, content, content_type = mail.outbox[0].attachments[0]
        self.assertTrue(name.startswith("tailored_resume_"))
        self.assertEqual(content_type, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))

    def test_applying_without_an_attachment_needs_no_resume_at_all(self):
        """A message-only application is a legitimate choice, so the resume
        checks must not fire when nothing is being attached."""
        Resume.objects.all().update(parsed_data=PARSED)
        application = self._draft(include_resume=False)
        Resume.objects.all().delete()

        JobApplicationService.send(self.user, application.id)

        self.assertEqual(mail.outbox[0].attachments, [])
