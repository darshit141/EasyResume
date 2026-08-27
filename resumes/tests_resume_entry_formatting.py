"""Tests for the header/bullets layout split on resume entries.

Two things are being protected here:

1. That a role renders the way a resume actually reads - an identifying line
   above its achievements - rather than one run-on paragraph.
2. That the split cannot become a hole in the no-fabrication guarantee.
   header and bullets are new text reaching the final document, so they get
   the same grounding check tailored_text does; without that, the layout
   fields would be an unguarded channel for inventing detail.
"""

import io

from django.test import TestCase
from docx import Document
from pypdf import PdfReader

from resumes.services.resume_curation_ai_service import (
    ResumeCurationAIService,
    ResumeCurationGroundingError,
)
from resumes.services.tailored_resume_export_service import TailoredResumeExportService
from resumes.services.tailored_resume_pdf_service import TailoredResumePdfService

STRUCTURED = {
    "personal_information": {"name": "Darshit Patel"},
    "experience": [
        {
            "source_index": 0,
            "tailored_text": "flat form kept for older clients",
            "header": "Software Engineer - Keyanna Technology | Mar 2025 - Present",
            "bullets": [
                "Develop backend functionality for enterprise GIS platforms.",
                "Built telecom modules using Java 17 and Kafka.",
            ],
        }
    ],
}

LEGACY = {
    "personal_information": {"name": "Legacy User"},
    "experience": [
        {
            "source_index": 0,
            "tailored_text": "Software Engineer at OldCo | 2020-2022: shipped things.",
        }
    ],
}


def _pdf_text(data):
    buffer = TailoredResumePdfService.build_pdf(data)
    buffer.seek(0)
    reader = PdfReader(buffer)
    return "".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data):
    buffer = TailoredResumeExportService.build_docx(data)
    buffer.seek(0)
    document = Document(buffer)
    return "\n".join(p.text for p in document.paragraphs)


class PdfEntryFormattingTests(TestCase):
    def test_header_and_each_bullet_appear_separately(self):
        text = _pdf_text(STRUCTURED)

        self.assertIn("Software Engineer - Keyanna Technology", text)
        self.assertIn("Develop backend functionality", text)
        self.assertIn("Built telecom modules", text)

    def test_header_is_on_its_own_line_not_merged_into_a_bullet(self):
        # The original bug: everything collapsed into one paragraph.
        text = _pdf_text(STRUCTURED)

        header_line = "Software Engineer - Keyanna Technology | Mar 2025 - Present"
        self.assertIn(header_line, text)
        self.assertNotIn(f"{header_line} Develop backend", text)

    def test_legacy_entries_without_the_split_still_render(self):
        # Curations generated before this field existed must not render blank.
        text = _pdf_text(LEGACY)

        self.assertIn("Software Engineer at OldCo", text)

    def test_structured_entries_remain_text_extractable(self):
        # The ATS-readability guarantee has to survive the layout change.
        text = _pdf_text(STRUCTURED)

        self.assertTrue(text.strip())
        self.assertIn("Java 17", text)


class DocxEntryFormattingTests(TestCase):
    def test_header_and_bullets_both_render(self):
        text = _docx_text(STRUCTURED)

        self.assertIn("Software Engineer - Keyanna Technology", text)
        self.assertIn("Develop backend functionality", text)
        self.assertIn("Built telecom modules", text)

    def test_legacy_entries_still_render(self):
        text = _docx_text(LEGACY)

        self.assertIn("Software Engineer at OldCo", text)


class EntryFormattingGroundingTests(TestCase):
    """header and bullets must not be a way around the no-fabrication rule."""

    SOURCE = {
        "skills": ["Java", "Kafka"],
        "experience": ["Backend Engineer at Acme Corp 2019-2022 using Java and Kafka."],
        "projects": [],
        "education": [],
        "certifications": [],
    }

    def _raw(self, **item_overrides):
        item = {
            "source_index": 0,
            "tailored_text": "Backend Engineer at Acme Corp 2019-2022 using Java and Kafka.",
        }
        item.update(item_overrides)
        return {
            "summary": "Backend Engineer at Acme Corp using Java and Kafka.",
            "skills": ["Java", "Kafka"],
            "experience": [item],
            "projects": [],
            "education": [],
            "certifications": [],
        }

    def test_grounded_header_and_bullets_are_accepted(self):
        result = ResumeCurationAIService.validate_and_ground(
            self._raw(
                header="Backend Engineer at Acme Corp 2019-2022",
                bullets=["Built services using Java and Kafka."],
            ),
            self.SOURCE,
        )

        entry = result["experience"][0]
        self.assertEqual(entry["header"], "Backend Engineer at Acme Corp 2019-2022")
        self.assertEqual(len(entry["bullets"]), 1)

    def test_an_invented_employer_in_the_header_is_rejected(self):
        with self.assertRaises(ResumeCurationGroundingError):
            ResumeCurationAIService.validate_and_ground(
                self._raw(
                    header="Principal Engineer at Google 2019-2022",
                    bullets=["Built services using Java and Kafka."],
                ),
                self.SOURCE,
            )

    def test_an_invented_technology_in_a_bullet_is_rejected(self):
        with self.assertRaises(ResumeCurationGroundingError):
            ResumeCurationAIService.validate_and_ground(
                self._raw(
                    header="Backend Engineer at Acme Corp 2019-2022",
                    bullets=["Deployed everything on Kubernetes and Terraform."],
                ),
                self.SOURCE,
            )

    def test_a_missing_split_falls_back_instead_of_failing_the_generation(self):
        # Rejecting a fully grounded resume purely over layout would be the
        # wrong trade - a plainer resume beats no resume.
        result = ResumeCurationAIService.validate_and_ground(self._raw(), self.SOURCE)

        entry = result["experience"][0]
        self.assertEqual(entry["header"], entry["tailored_text"])
        self.assertEqual(entry["bullets"], [])
