"""Extraction-fidelity tests.

These matter more than they look: everything downstream (AI parsing, ATS
scoring, job matching) only ever sees TextExtractor's output. Text dropped
here doesn't produce a visibly broken result - it produces a confident,
plausible, wrong one, describing a resume that was never fully read.
"""

import io

from django.test import TestCase
from docx import Document

from resumes.utils.text_extractor import TextExtractor


def _docx(build):
    document = Document()
    build(document)
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    buffer.name = "resume.docx"
    return buffer


class DocxTableExtractionTests(TestCase):
    def test_table_cell_content_is_extracted(self):
        # Regression: walking only document.paragraphs dropped every table
        # cell. Resume templates use tables constantly, so a real resume
        # lost its email, phone, skills and achievements while the upload
        # still reported success.
        def build(document):
            document.add_paragraph("Jane Doe")
            table = document.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "Email: jane@example.com"
            table.cell(0, 1).text = "Phone: 555-0100"
            table.cell(1, 0).text = "Skills: Python, Django, PostgreSQL"
            table.cell(1, 1).text = "Reduced infrastructure cost 40%"

        text = TextExtractor.extract_text(_docx(build))

        self.assertIn("jane@example.com", text)
        self.assertIn("555-0100", text)
        self.assertIn("Python", text)
        self.assertIn("40%", text)

    def test_nested_tables_are_extracted(self):
        def build(document):
            outer = document.add_table(rows=1, cols=1)
            inner = outer.cell(0, 0).add_table(rows=1, cols=1)
            inner.cell(0, 0).text = "Certified Kubernetes Administrator"

        text = TextExtractor.extract_text(_docx(build))

        self.assertIn("Certified Kubernetes Administrator", text)

    def test_header_and_footer_text_is_extracted(self):
        # Contact details live in headers more often than expected, and an
        # ATS that misses them reads the candidate as having none.
        def build(document):
            document.add_paragraph("Experience")
            document.sections[0].header.paragraphs[0].text = "jane@portfolio.dev"
            document.sections[0].footer.paragraphs[0].text = "linkedin.com/in/janedoe"

        text = TextExtractor.extract_text(_docx(build))

        self.assertIn("jane@portfolio.dev", text)
        self.assertIn("linkedin.com/in/janedoe", text)

    def test_document_reading_order_is_preserved(self):
        # document.paragraphs and document.tables are separate flat lists,
        # so naive concatenation would move a mid-document table to the end,
        # detaching it from the section it belongs to.
        def build(document):
            document.add_paragraph("SECTION ONE")
            table = document.add_table(rows=1, cols=1)
            table.cell(0, 0).text = "MIDDLE TABLE"
            document.add_paragraph("SECTION TWO")

        text = TextExtractor.extract_text(_docx(build))

        self.assertLess(text.index("SECTION ONE"), text.index("MIDDLE TABLE"))
        self.assertLess(text.index("MIDDLE TABLE"), text.index("SECTION TWO"))

    def test_plain_paragraph_resume_still_works(self):
        def build(document):
            document.add_paragraph("Jane Doe")
            document.add_paragraph("Senior Engineer with 8 years experience")

        text = TextExtractor.extract_text(_docx(build))

        self.assertIn("Jane Doe", text)
        self.assertIn("Senior Engineer", text)

    def test_repeated_header_text_is_not_duplicated(self):
        # The same header repeats per section; without de-duplication it
        # would inflate keyword counts and skew any keyword-coverage score.
        def build(document):
            document.sections[0].header.paragraphs[0].text = "Jane Doe"
            document.add_paragraph("Experience")

        text = TextExtractor.extract_text(_docx(build))

        self.assertEqual(text.count("Jane Doe"), 1)

    def test_empty_cells_do_not_add_blank_lines(self):
        def build(document):
            table = document.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "Only content"

        text = TextExtractor.extract_text(_docx(build))

        self.assertEqual(text.strip(), "Only content")

    def test_unsupported_extension_is_rejected(self):
        buffer = io.BytesIO(b"not a resume")
        buffer.name = "resume.txt"

        with self.assertRaises(ValueError):
            TextExtractor.extract_text(buffer)


class ATSCoverageConsistencyTests(TestCase):
    """The coverage percentage must be derived from the keyword lists, not
    taken on trust from the model."""

    def test_percentage_is_recomputed_from_the_lists(self):
        from resumes.services.job_gap_analysis_service import JobGapAnalysisService

        # A model response that contradicts itself: 1 of 4 keywords covered,
        # but claiming 85%. This previously passed schema validation and was
        # shown to the user verbatim.
        result = {
            "ats_keyword_coverage": {
                "covered_keywords": ["Python"],
                "missing_keywords": ["AWS", "Docker", "Kubernetes"],
                "coverage_percentage": 85,
            }
        }

        JobGapAnalysisService._recompute_ats_coverage(result)

        self.assertEqual(result["ats_keyword_coverage"]["coverage_percentage"], 25.0)

    def test_full_coverage_is_one_hundred(self):
        from resumes.services.job_gap_analysis_service import JobGapAnalysisService

        result = {
            "ats_keyword_coverage": {
                "covered_keywords": ["Python", "AWS"],
                "missing_keywords": [],
                "coverage_percentage": 42,
            }
        }

        JobGapAnalysisService._recompute_ats_coverage(result)

        self.assertEqual(result["ats_keyword_coverage"]["coverage_percentage"], 100.0)

    def test_no_keywords_does_not_divide_by_zero(self):
        from resumes.services.job_gap_analysis_service import JobGapAnalysisService

        result = {
            "ats_keyword_coverage": {
                "covered_keywords": [],
                "missing_keywords": [],
                "coverage_percentage": 99,
            }
        }

        JobGapAnalysisService._recompute_ats_coverage(result)

        self.assertEqual(result["ats_keyword_coverage"]["coverage_percentage"], 0)

    def test_same_lists_always_produce_the_same_percentage(self):
        from resumes.services.job_gap_analysis_service import JobGapAnalysisService

        def score():
            result = {
                "ats_keyword_coverage": {
                    "covered_keywords": ["a", "b"],
                    "missing_keywords": ["c"],
                    "coverage_percentage": 0,
                }
            }
            JobGapAnalysisService._recompute_ats_coverage(result)
            return result["ats_keyword_coverage"]["coverage_percentage"]

        self.assertEqual(score(), score())
        self.assertEqual(score(), 66.67)
