"""PDF export tests.

The assertion that matters is that the text comes back OUT of the generated
file. A PDF that merely opens and looks correct can still be an image with
no text layer, which an applicant tracking system reads as a blank page -
a silent failure of the product's core promise. Every test here extracts
the text again rather than trusting that a file was produced.
"""

import io

from django.test import TestCase
from pypdf import PdfReader
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobResumeCuration
from resumes.services.tailored_resume_pdf_service import TailoredResumePdfService

SAMPLE = {
    "personal_information": {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "555-0100",
    },
    "summary": "Backend engineer with 8 years in Python & Django.",
    "skills": ["Python", "Django", "PostgreSQL", "Kubernetes"],
    "experience": [
        {"tailored_text": "Led migration to microservices, cutting p95 latency 40%."},
        {"tailored_text": "Held infrastructure spend <5% of revenue while doubling traffic."},
    ],
    "education": [{"tailored_text": "BSc Computer Science, University of Manchester"}],
    "certifications": [{"tailored_text": "Certified Kubernetes Administrator"}],
}


def _extract(buffer):
    buffer.seek(0)
    reader = PdfReader(buffer)
    return "".join((page.extract_text() or "") for page in reader.pages)


class PdfTextLayerTests(TestCase):
    def test_generated_pdf_has_an_extractable_text_layer(self):
        # The whole point: an image-only PDF would produce nothing here.
        text = _extract(TailoredResumePdfService.build_pdf(SAMPLE))

        self.assertTrue(text.strip(), "PDF produced no extractable text at all")

    def test_contact_details_are_machine_readable(self):
        # An ATS that cannot find an email address treats the candidate as
        # having no contact information.
        text = _extract(TailoredResumePdfService.build_pdf(SAMPLE))

        self.assertIn("Jane Doe", text)
        self.assertIn("jane@example.com", text)
        self.assertIn("555-0100", text)

    def test_every_skill_is_extractable(self):
        # Skills are the most keyword-dense line on a resume - the part an
        # ATS keyword scan actually leans on.
        text = _extract(TailoredResumePdfService.build_pdf(SAMPLE))

        for skill in SAMPLE["skills"]:
            self.assertIn(skill, text)

    def test_experience_bullets_are_extractable(self):
        text = _extract(TailoredResumePdfService.build_pdf(SAMPLE))

        self.assertIn("microservices", text)
        self.assertIn("40%", text)

    def test_ampersands_and_angle_brackets_survive(self):
        # "R&D" and "<5%" are ordinary resume text but are markup to the PDF
        # renderer - unescaped they raise or silently truncate the line.
        text = _extract(TailoredResumePdfService.build_pdf(SAMPLE))

        self.assertIn("Python & Django", text)
        self.assertIn("<5%", text)

    def test_all_sections_are_present(self):
        text = _extract(TailoredResumePdfService.build_pdf(SAMPLE))

        for heading in ("PROFESSIONAL SUMMARY", "CORE SKILLS", "EDUCATION", "CERTIFICATIONS"):
            self.assertIn(heading, text)

    def test_missing_sections_are_skipped_without_error(self):
        text = _extract(
            TailoredResumePdfService.build_pdf(
                {"personal_information": {"name": "Minimal"}, "summary": "Short."}
            )
        )

        self.assertIn("Minimal", text)
        self.assertNotIn("CERTIFICATIONS", text)

    def test_completely_empty_data_still_produces_a_valid_pdf(self):
        buffer = TailoredResumePdfService.build_pdf({})

        self.assertGreater(len(buffer.getvalue()), 0)
        self.assertIn("Candidate", _extract(buffer))

    def test_filename_is_derived_safely_from_the_job_title(self):
        name = TailoredResumePdfService.build_filename("Senior Engineer / Backend!")

        self.assertTrue(name.endswith(".pdf"))
        for char in "/!":
            self.assertNotIn(char, name)


class PdfExportEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            name="Jane Doe", email="pdf@example.com", phone="5551239999",
            password="safe-password",
        )
        self.curation = JobResumeCuration.objects.create(
            user=self.user, job_title="Backend Engineer", job_description="jd",
            status="completed", tailored_resume_data=SAMPLE,
        )
        self.url = f"/api/resumes/job-curations/{self.curation.id}/tailored-resume/export/pdf/"

    def test_returns_a_pdf_attachment(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])

    def test_downloaded_pdf_is_text_extractable(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)
        text = _extract(io.BytesIO(response.content))

        self.assertIn("Jane Doe", text)
        self.assertIn("Kubernetes", text)

    def test_pdf_export_is_available_on_the_free_plan(self):
        # PDF download is advertised as a Free-tier feature; only DOCX is
        # Pro-gated. Gating this would contradict the pricing page.
        self.client.force_authenticate(user=self.user)

        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_another_users_curation_is_not_accessible(self):
        intruder = User.objects.create_user(
            name="Mallory", email="mallory@example.com", phone="5551238888",
            password="safe-password",
        )
        self.client.force_authenticate(user=intruder)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_404_when_no_tailored_resume_exists_yet(self):
        empty = JobResumeCuration.objects.create(
            user=self.user, job_title="Nothing Yet", job_description="jd",
            status="completed", tailored_resume_data={},
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            f"/api/resumes/job-curations/{empty.id}/tailored-resume/export/pdf/"
        )

        self.assertEqual(response.status_code, 404)
