"""The admin switch that decides whether users may pick the AI provider.

Hiding the picker is not enough on its own: an old browser tab, a cached page
or a hand-written request can still send `ai_provider`. With the switch off
the server must ignore it, and every request must use the configured default
- which is also what keeps the configured fallback provider in play.
"""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.models import SystemConfiguration
from authentication.models import User
from billing.entitlements import FREE_ENTITLEMENTS
from billing.services.entitlements_service import EntitlementsService
from resumes.services.llm_service import LLMService, requested_provider, resolve_provider
from resumes.tests_job_curation import VALID_JOB_ANALYSIS

RESUME_TEXT = (
    "Jane Doe. Backend engineer with eight years of Python, Django and "
    "PostgreSQL experience across payments and infrastructure teams."
)


class _FakeJobAnalysisLLM(LLMService):
    def generate_structured(self, prompt, schema):
        return VALID_JOB_ANALYSIS


def _make_user(email):
    return User.objects.create_user(
        name="Jane Doe", email=email, phone=f"9{abs(hash(email)) % 1000000000:09d}", password="safe-password"
    )


class RequestedProviderTests(TestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()

    @override_settings(AI_PROVIDER_SELECTION_ENABLED=False)
    def test_switched_off_a_requested_provider_is_ignored(self):
        self.assertIsNone(requested_provider("groq"))

    @override_settings(AI_PROVIDER_SELECTION_ENABLED=True)
    def test_switched_on_the_requested_provider_is_used(self):
        self.assertEqual(requested_provider("groq"), "groq")

    @override_settings(AI_PROVIDER_SELECTION_ENABLED=True)
    def test_asking_for_nothing_always_means_the_default(self):
        self.assertIsNone(requested_provider(""))
        self.assertIsNone(requested_provider(None))

    @override_settings(AI_PROVIDER_SELECTION_ENABLED=False)
    def test_the_admin_panel_wins_over_the_environment(self):
        SystemConfiguration.objects.create(
            key="AI_PROVIDER_SELECTION_ENABLED", value="true", value_type="boolean", category="llm"
        )
        self.assertEqual(requested_provider("groq"), "groq")


class PickerVisibilityTests(TestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()
        self.user = _make_user("picker@example.com")

    def test_the_app_is_told_whether_to_show_the_picker(self):
        with override_settings(AI_PROVIDER_SELECTION_ENABLED=False):
            self.assertFalse(EntitlementsService.usage_summary(self.user)["ai_provider_selection_enabled"])
        with override_settings(AI_PROVIDER_SELECTION_ENABLED=True):
            self.assertTrue(EntitlementsService.usage_summary(self.user)["ai_provider_selection_enabled"])


@override_settings(LLM_FALLBACK_PROVIDER="")
class ServerEnforcementTests(TestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()
        self.user = _make_user("enforce@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _analyze(self, provider):
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=_FakeJobAnalysisLLM(),
        ), patch("resumes.views.job_curation_view.resolve_provider", wraps=resolve_provider) as spy:
            response = self.client.post(
                "/api/resumes/job-curations/analyze/",
                {
                    "job_title": "Engineer",
                    "job_description": "We are hiring a backend engineer with strong Python experience.",
                    "curate_resume": True,
                    "ai_provider": provider,
                },
                format="json",
            )
        self.assertEqual(response.status_code, 202)
        return spy

    @override_settings(AI_PROVIDER_SELECTION_ENABLED=False)
    def test_switched_off_a_provider_sent_anyway_is_not_used(self):
        spy = self._analyze("gemini")
        spy.assert_called_once_with(None)

    @override_settings(AI_PROVIDER_SELECTION_ENABLED=True)
    def test_switched_on_the_chosen_provider_is_used(self):
        spy = self._analyze("gemini")
        spy.assert_called_once_with("gemini")

    @override_settings(AI_PROVIDER_SELECTION_ENABLED=False)
    def test_switched_off_the_non_ai_parser_cannot_be_chosen_to_skip_the_quota(self):
        """"standard" is also a user choice. With choice off it is ignored, so
        the upload is an ordinary AI parse and the AI quota applies."""
        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 0}), patch(
            "resumes.services.resume_upload_service.TextExtractor.extract_text", return_value=RESUME_TEXT
        ), patch("resumes.services.resume_upload_service.ResumeParser.parse", return_value={}) as parse:
            response = self.client.post(
                "/api/resumes/upload/",
                {"file": SimpleUploadedFile("resume.pdf", b"placeholder"), "ai_provider": "standard"},
                format="multipart",
            )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "PLAN_LIMIT_REACHED")
        parse.assert_not_called()
