from django.test import SimpleTestCase

from resumes.resume_content import (
    entry_text,
    flatten_parsed_data_for_grounding,
    normalize_entry,
    normalize_parsed_data,
)
from resumes.utils.ats_scorer import ATSScorer


class NormalizeEntryTests(SimpleTestCase):
    def test_a_plain_string_becomes_a_headered_entry_with_no_bullets(self):
        self.assertEqual(
            normalize_entry("Backend Engineer at Acme (2019-2022)"),
            {"header": "Backend Engineer at Acme (2019-2022)", "bullets": []},
        )

    def test_a_full_entry_keeps_every_bullet(self):
        bullets = [f"Achievement {n}" for n in range(15)]
        out = normalize_entry({"header": "Engineer - BigCo", "bullets": bullets})
        self.assertEqual(out["header"], "Engineer - BigCo")
        self.assertEqual(out["bullets"], bullets)

    def test_a_bodyonly_entry_promotes_its_first_bullet_to_the_header(self):
        out = normalize_entry({"header": "", "bullets": ["Did a thing", "Did another"]})
        self.assertEqual(out["header"], "Did a thing")
        self.assertEqual(out["bullets"], ["Did another"])

    def test_empty_entries_are_dropped(self):
        self.assertIsNone(normalize_entry(""))
        self.assertIsNone(normalize_entry({"header": "  ", "bullets": ["", "   "]}))

    def test_bullets_and_header_are_trimmed_and_coerced_to_strings(self):
        out = normalize_entry({"header": "  Role  ", "bullets": ["  a  ", 42, "b"]})
        self.assertEqual(out["header"], "Role")
        self.assertEqual(out["bullets"], ["a", "b"])


class NormalizeParsedDataTests(SimpleTestCase):
    def test_legacy_flat_experience_is_upgraded_without_losing_text(self):
        legacy = {
            "summary": "x",
            "skills": ["Python"],
            "experience": ["Engineer at Acme - built the API"],
            "projects": [],
            "education": [],
            "certifications": [],
            "achievements": ["Won an award"],
            "languages": [],
        }
        out = normalize_parsed_data(legacy)
        self.assertEqual(
            out["experience"],
            [{"header": "Engineer at Acme - built the API", "bullets": []}],
        )
        self.assertEqual(out["skills"], ["Python"])
        self.assertEqual(out["achievements"], ["Won an award"])

    def test_already_normalized_data_is_unchanged(self):
        data = {
            "experience": [{"header": "Engineer - BigCo", "bullets": ["Shipped things"]}],
            "skills": ["Go"],
        }
        self.assertEqual(normalize_parsed_data(data)["experience"], data["experience"])


class EntryTextAndFlattenTests(SimpleTestCase):
    def test_entry_text_includes_the_header_and_every_bullet(self):
        text = entry_text({"header": "Engineer - BigCo (2021)", "bullets": ["Led the team", "Cut costs 30%"]})
        self.assertIn("Engineer - BigCo (2021)", text)
        self.assertIn("Led the team", text)
        self.assertIn("Cut costs 30%", text)

    def test_flatten_for_grounding_turns_entry_sections_into_text_blocks(self):
        data = {
            "experience": [{"header": "Engineer - BigCo", "bullets": ["Built X", "Improved Y by 40%"]}],
            "skills": ["Rust"],
        }
        flat = flatten_parsed_data_for_grounding(data)
        self.assertEqual(len(flat["experience"]), 1)
        self.assertIsInstance(flat["experience"][0], str)
        self.assertIn("Improved Y by 40%", flat["experience"][0])
        self.assertEqual(flat["skills"], ["Rust"])


class ATSScorerStructuredEntryTests(SimpleTestCase):
    def _resume(self, experience):
        return {
            "email": "j@example.com", "phone": "1234567890",
            "summary": "A backend engineer with real, substantive experience to describe.",
            "skills": ["Python", "Django", "Postgres", "Kafka", "AWS", "Docker", "CI", "Redis"],
            "experience": experience, "education": ["BSc"], "projects": [], "certifications": [],
            "achievements": [], "languages": [],
        }

    def test_scoring_reads_individual_bullets_not_a_single_blob(self):
        strong = self._resume([
            {"header": "Senior Engineer - Northwind (2021-present)", "bullets": [
                "Reduced checkout latency by 38% across the settlement pipeline.",
                "Led a team of 4 engineers delivering the payments rewrite.",
                "Cut infra spend 25% by right-sizing the fleet.",
            ]},
        ])
        weak = self._resume([
            {"header": "Engineer", "bullets": ["Worked on various backend things and helped the team."]},
        ])
        self.assertGreater(
            ATSScorer.calculate_score(strong), ATSScorer.calculate_score(weak)
        )

    def test_a_legacy_string_entry_still_scores(self):
        score = ATSScorer.calculate_score(
            self._resume(["Led a team of 6 and improved throughput by 50%."])
        )
        self.assertGreater(score, 0)
