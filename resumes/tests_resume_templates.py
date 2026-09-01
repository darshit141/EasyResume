"""Tests for the resume layout template system (resumes.resume_template),
its preference API, the Pro-gate on custom overrides, the per-version
snapshot, and the fact that every renderer honours a template without ever
dropping content.
"""

import io

from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader
from rest_framework.test import APIClient

from authentication.models import User
from billing.models import Subscription
from resumes.models import JobResumeCuration, JobResumeCurationVersion
from resumes.resume_template import (
    DEFAULT_TEMPLATE,
    resolve_template,
    template_ats_report,
    visible_sections,
)
from resumes.services.resume_template_service import ResumeTemplateService
from resumes.services.tailored_resume_export_service import TailoredResumeExportService
from resumes.services.tailored_resume_pdf_service import TailoredResumePdfService

SAMPLE = {
    "personal_information": {"name": "Jane Doe", "email": "jane@example.com", "phone": "555-0100"},
    "summary": "Backend engineer with 8 years in Python and Django.",
    "skills": ["Python", "Django", "PostgreSQL"],
    "experience": [{"header": "Senior Engineer, Acme (2020-2024)", "bullets": ["Cut p95 latency 40%."]}],
    "projects": [{"header": "OpenParser", "bullets": ["Wrote an MIT-licensed resume parser."]}],
    "education": [{"header": "BSc Computer Science, Manchester", "bullets": []}],
    "certifications": [{"header": "Certified Kubernetes Administrator", "bullets": []}],
}


def _pdf_text(buffer):
    buffer.seek(0)
    return "".join((page.extract_text() or "") for page in PdfReader(buffer).pages)


class ResolveTemplateTests(TestCase):
    def test_none_yields_the_default_unchanged(self):
        self.assertEqual(resolve_template(None, None), DEFAULT_TEMPLATE)

    def test_unknown_preset_falls_back_to_default_id(self):
        config = resolve_template("does_not_exist", None)
        self.assertEqual(config["template_id"], "ats_classic")

    def test_preset_partial_config_is_merged(self):
        config = resolve_template("modern", None)
        self.assertEqual(config["accent_color"], "#2C42B0")
        self.assertEqual(config["name_alignment"], "center")

    def test_hostile_overrides_are_clamped_not_trusted(self):
        config = resolve_template(
            "ats_classic",
            {
                "accent_color": "#000000",  # not in the palette
                "font_family": "comic-sans",
                "density": "enormous",
                "columns": 7,
                "heading_style": "sparkles",
            },
        )
        self.assertEqual(config["accent_color"], "#101828")
        self.assertEqual(config["font_family"], "sans")
        self.assertEqual(config["density"], "standard")
        self.assertEqual(config["columns"], 1)
        self.assertEqual(config["heading_style"], "caps")

    def test_section_order_override_never_drops_a_section(self):
        config = resolve_template("ats_classic", {"section_order": ["skills", "summary"]})
        # Listed ones come first, in the given order...
        self.assertEqual(config["section_order"][:2], ["skills", "summary"])
        # ...and every canonical section is still present.
        self.assertCountEqual(config["section_order"], DEFAULT_TEMPLATE["section_order"])

    def test_hidden_sections_removed_from_visible_but_kept_in_order(self):
        config = resolve_template("ats_classic", {"hidden_sections": ["projects"]})
        self.assertIn("projects", config["section_order"])
        self.assertNotIn("projects", visible_sections(config))

    def test_two_column_is_reported_as_an_ats_risk(self):
        report = template_ats_report(resolve_template("two_column", None))
        self.assertFalse(report["safe"])
        self.assertTrue(report["warnings"])


class RendererHonoursTemplateTests(TestCase):
    """Whatever the template says, no resume content may go missing."""

    def _assert_all_content_present(self, text):
        for needle in ["Jane Doe", "Python", "Senior Engineer", "OpenParser", "Manchester", "Kubernetes"]:
            self.assertIn(needle, text, f"{needle!r} missing from rendered output")

    def test_pdf_default_and_customised_both_keep_all_content(self):
        self._assert_all_content_present(_pdf_text(TailoredResumePdfService.build_pdf(SAMPLE)))
        custom = resolve_template("modern", {"section_order": ["skills", "summary", "experience"], "font_family": "serif"})
        self._assert_all_content_present(
            _pdf_text(TailoredResumePdfService.build_pdf(SAMPLE, template_config=custom))
        )

    def test_pdf_respects_hidden_section(self):
        config = resolve_template("ats_classic", {"hidden_sections": ["projects"]})
        text = _pdf_text(TailoredResumePdfService.build_pdf(SAMPLE, template_config=config))
        self.assertIn("Senior Engineer", text)
        self.assertNotIn("OpenParser", text)

    def test_docx_customised_keeps_all_content(self):
        config = resolve_template("compact", {"font_family": "serif"})
        buffer = TailoredResumeExportService.build_docx(SAMPLE, template_config=config)
        from docx import Document

        text = "\n".join(p.text for p in Document(buffer).paragraphs)
        self._assert_all_content_present(text)


class TemplatePreferenceApiTests(TestCase):
    def setUp(self):
        self.free = User.objects.create_user(email="free@example.com", name="Free", password="pw12345!")
        self.pro = User.objects.create_user(email="pro@example.com", name="Pro", password="pw12345!")
        Subscription.objects.create(user=self.pro, plan_id="pro", status="active")
        self.client = APIClient()

    def test_get_returns_default_for_a_new_user(self):
        self.client.force_authenticate(self.free)
        res = self.client.get("/api/resumes/template-preference/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["template_id"], "ats_classic")
        self.assertEqual(res.data["config"]["accent_color"], "#101828")

    def test_free_user_may_select_a_preset(self):
        self.client.force_authenticate(self.free)
        res = self.client.put(
            "/api/resumes/template-preference/", {"template_id": "compact"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.free.refresh_from_db()
        self.assertEqual(self.free.resume_template_id, "compact")

    def test_free_user_sending_overrides_is_blocked_with_402(self):
        self.client.force_authenticate(self.free)
        res = self.client.put(
            "/api/resumes/template-preference/",
            {"template_id": "modern", "overrides": {"accent_color": "#0F766E"}},
            format="json",
        )
        self.assertEqual(res.status_code, 402)
        self.free.refresh_from_db()
        # Nothing persisted on the blocked call.
        self.assertEqual(self.free.resume_template_id, "ats_classic")

    def test_pro_user_may_set_overrides(self):
        self.client.force_authenticate(self.pro)
        res = self.client.put(
            "/api/resumes/template-preference/",
            {"template_id": "modern", "overrides": {"accent_color": "#0F766E", "density": "compact"}},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["config"]["accent_color"], "#0F766E")
        self.assertEqual(res.data["config"]["density"], "compact")

    def test_invalid_override_value_is_a_400(self):
        self.client.force_authenticate(self.pro)
        res = self.client.put(
            "/api/resumes/template-preference/",
            {"template_id": "modern", "overrides": {"density": "gigantic"}},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_template_list_is_available_to_any_authenticated_user(self):
        self.client.force_authenticate(self.free)
        res = self.client.get("/api/resumes/templates/")
        self.assertEqual(res.status_code, 200)
        ids = {t["id"] for t in res.data}
        self.assertEqual(ids, {"ats_classic", "modern", "compact", "two_column"})


class VersionSnapshotTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="u@example.com", name="U", password="pw12345!")

    def test_config_for_version_prefers_the_snapshot_over_current_pref(self):
        curation = JobResumeCuration.objects.create(
            user=self.user, job_title="Dev", job_description="x", status="completed"
        )
        snapshot = resolve_template("compact", None)
        version = JobResumeCurationVersion.objects.create(
            curation=curation, version=1, tailored_resume_data=SAMPLE, template_config=snapshot
        )
        # User's live preference is something else entirely.
        self.user.resume_template_id = "modern"
        self.user.save(update_fields=["resume_template_id"])

        resolved = ResumeTemplateService.config_for_version(version, user=self.user)
        self.assertEqual(resolved["template_id"], "compact")

    def test_config_for_version_falls_back_to_user_pref_when_no_snapshot(self):
        self.user.resume_template_id = "modern"
        self.user.save(update_fields=["resume_template_id"])
        curation = JobResumeCuration.objects.create(
            user=self.user, job_title="Dev", job_description="x", status="completed"
        )
        version = JobResumeCurationVersion.objects.create(
            curation=curation, version=1, tailored_resume_data=SAMPLE, template_config={}
        )
        resolved = ResumeTemplateService.config_for_version(version, user=self.user)
        self.assertEqual(resolved["template_id"], "modern")
