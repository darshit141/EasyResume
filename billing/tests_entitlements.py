from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import User
from billing.entitlements import FREE_ENTITLEMENTS
from billing.models import AIUsageLog, Subscription
from billing.services.entitlements_service import (
    EntitlementsService,
    FeatureRequiresProError,
    PlanLimitReachedError,
)
from resumes.models import JobResumeCuration, JobResumeCurationVersion


def _make_user(email):
    return User.objects.create_user(
        name="Jane Doe", email=email, phone=f"9{abs(hash(email)) % 1000000000:09d}",
        password="safe-password",
    )


# Shaped to satisfy TailoredResumeDataSerializer, so tests that render a
# real response exercise the gate rather than dying in serialization.
_TAILORED_DATA = {
    "personal_information": {"name": "Jane Doe", "email": "jane@example.com", "phone": "1234567890"},
    "summary": "Backend engineer.",
    "skills": ["Python"],
    "experience": [],
    "projects": [],
    "education": [],
    "certifications": [],
}


# Shaped to satisfy GapAnalysisDataSerializer (see
# resumes.serializers.job_curation_serializer), so tests that render a real
# gap-analysis response exercise the actual serializer instead of a mock.
_GAP_ANALYSIS_DATA = {
    "overall_match_percentage": 80.0,
    "matched_requirements": [],
    "partially_matched_requirements": [],
    "missing_requirements": [],
    "not_verifiable_requirements": [],
    "strengths": [],
    "weaknesses_gaps": [],
    "important_missing_information": [],
    "recommendations_for_profile_improvement": [],
    "ats_keyword_coverage": {"covered_keywords": [], "missing_keywords": [], "coverage_percentage": 80.0},
    "interview_readiness_observations": [],
}


def _generate_versions(user, count):
    curation = JobResumeCuration.objects.create(
        user=user, job_title="Engineer", job_description="desc",
        tailored_resume_data=_TAILORED_DATA,
    )
    for i in range(count):
        JobResumeCurationVersion.objects.create(curation=curation, version=i + 1)
    return curation


class EntitlementResolutionTests(TestCase):
    def setUp(self):
        self.user = _make_user("resolve@example.com")

    def test_user_with_no_subscription_row_is_free(self):
        self.assertEqual(EntitlementsService.for_user(self.user)["plan_id"], "free")

    def test_active_pro_subscription_is_pro(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")
        self.assertEqual(EntitlementsService.for_user(self.user)["plan_id"], "pro")

    def test_canceled_pro_subscription_falls_back_to_free(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="canceled")
        self.assertEqual(EntitlementsService.for_user(self.user)["plan_id"], "free")

    def test_past_due_subscription_falls_back_to_free(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="past_due")
        self.assertEqual(EntitlementsService.for_user(self.user)["plan_id"], "free")

    def test_unknown_plan_id_falls_back_to_free(self):
        # A typo or a plan we removed must never grant paid access.
        Subscription.objects.create(user=self.user, plan_id="enterprise", status="active")
        self.assertEqual(EntitlementsService.for_user(self.user)["plan_id"], "free")


class TailoredResumeQuotaTests(TestCase):
    def setUp(self):
        self.user = _make_user("quota@example.com")

    def test_free_user_under_quota_passes(self):
        _generate_versions(self.user, 1)
        EntitlementsService.check_can_generate_tailored_resume(self.user)  # no raise

    def test_free_user_at_quota_is_blocked(self):
        _generate_versions(self.user, 2)
        with self.assertRaises(PlanLimitReachedError):
            EntitlementsService.check_can_generate_tailored_resume(self.user)

    def test_pro_user_is_never_blocked(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")
        _generate_versions(self.user, 50)
        EntitlementsService.check_can_generate_tailored_resume(self.user)  # no raise

    def test_quota_counts_versions_not_curations(self):
        # Regenerating costs a model call each time, so each version counts.
        _generate_versions(self.user, 2)
        self.assertEqual(EntitlementsService.tailored_resumes_used_this_month(self.user), 2)

    def test_another_users_generations_do_not_count(self):
        other = _make_user("other-quota@example.com")
        _generate_versions(other, 5)
        self.assertEqual(EntitlementsService.tailored_resumes_used_this_month(self.user), 0)

    def test_last_months_generations_do_not_count(self):
        curation = _generate_versions(self.user, 2)
        last_month = timezone.now().replace(day=1) - timezone.timedelta(days=5)
        JobResumeCurationVersion.objects.filter(curation=curation).update(created_at=last_month)

        self.assertEqual(EntitlementsService.tailored_resumes_used_this_month(self.user), 0)
        EntitlementsService.check_can_generate_tailored_resume(self.user)  # no raise

    def test_limit_error_reports_usage_and_reset_date(self):
        _generate_versions(self.user, 2)
        with self.assertRaises(PlanLimitReachedError) as ctx:
            EntitlementsService.check_can_generate_tailored_resume(self.user)

        payload = ctx.exception.to_payload()
        self.assertEqual(payload["code"], "PLAN_LIMIT_REACHED")
        self.assertEqual(payload["used"], 2)
        self.assertEqual(payload["limit"], 2)
        self.assertIn("resets_at", payload)


class ProviderGateTests(TestCase):
    def setUp(self):
        self.user = _make_user("provider@example.com")

    def test_free_user_may_use_every_provider(self):
        # Every provider is available to every plan — only the total number
        # of AI calls per month (a separate gate) differs by plan.
        for provider in ("ollama", "standard", "groq", "gemini"):
            EntitlementsService.check_ai_provider_allowed(self.user, provider)

    def test_omitting_provider_is_always_allowed(self):
        # No provider means "server default" — never a paid choice.
        EntitlementsService.check_ai_provider_allowed(self.user, None)
        EntitlementsService.check_ai_provider_allowed(self.user, "")

    def test_pro_user_may_use_every_provider(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")
        for provider in ("ollama", "standard", "groq", "gemini"):
            EntitlementsService.check_ai_provider_allowed(self.user, provider)


class FeatureGateTests(TestCase):
    def setUp(self):
        self.user = _make_user("feature@example.com")

    def test_free_user_cannot_export_docx(self):
        with self.assertRaises(FeatureRequiresProError):
            EntitlementsService.check_feature(self.user, "docx_export")

    def test_pro_user_can_export_docx(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")
        EntitlementsService.check_feature(self.user, "docx_export")

    def test_upgrade_message_names_the_feature(self):
        with self.assertRaises(FeatureRequiresProError) as ctx:
            EntitlementsService.check_feature(self.user, "docx_export")
        self.assertIn("Word (.docx) export", str(ctx.exception))


class EnforcementEndpointTests(TestCase):
    """The gates must hold at the HTTP boundary, not just in the service."""

    def setUp(self):
        self.user = _make_user("endpoint@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_entitlements_endpoint_reports_free_defaults(self):
        response = self.client.get("/api/billing/entitlements/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["plan_id"], "free")
        self.assertEqual(response.data["tailored_resumes_limit"], 2)
        self.assertFalse(response.data["docx_export"])
        self.assertTrue(response.data["shows_ads"])

    def test_entitlements_endpoint_requires_auth(self):
        self.assertEqual(APIClient().get("/api/billing/entitlements/").status_code, 401)

    def test_docx_export_returns_402_for_free_user(self):
        curation = _generate_versions(self.user, 1)
        response = self.client.get(
            f"/api/resumes/job-curations/{curation.id}/tailored-resume/export/docx/"
        )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "UPGRADE_REQUIRED")

    def test_version_history_returns_402_for_free_user(self):
        curation = _generate_versions(self.user, 1)
        response = self.client.get(
            f"/api/resumes/job-curations/{curation.id}/tailored-resume/versions/"
        )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "UPGRADE_REQUIRED")

    def test_paid_provider_on_upload_returns_402_before_any_ai_call(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("resume.pdf", b"placeholder content")
        with patch("resumes.services.resume_upload_service.ResumeParser.parse") as mock_parse:
            response = self.client.post(
                "/api/resumes/upload/", {"file": upload, "ai_provider": "groq"}, format="multipart"
            )

        self.assertEqual(response.status_code, 402)
        mock_parse.assert_not_called()

    def test_over_quota_generation_returns_402_before_any_ai_call(self):
        curation = _generate_versions(self.user, 2)
        with patch(
            "resumes.views.resume_curation_view.JobResumeCurationService"
        ) as mock_service:
            response = self.client.post(
                f"/api/resumes/job-curations/{curation.id}/tailored-resume/",
                {"regenerate": True},
                format="json",
            )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "PLAN_LIMIT_REACHED")
        mock_service.assert_not_called()

    def test_reading_a_cached_resume_does_not_consume_quota(self):
        # Already at the limit, but this call creates no new version, so the
        # quota gate must let it through.
        curation = _generate_versions(self.user, 2)
        version = JobResumeCurationVersion.objects.filter(curation=curation).first()

        # Asserting on the gate, not on the full response render: reaching the
        # generation service at all proves the quota check let this through,
        # and stops this test from breaking whenever the response shape changes.
        with patch(
            "resumes.views.resume_curation_view.JobResumeCurationService"
        ) as mock_service, patch(
            "resumes.views.resume_curation_view.MissingInformationReviewService"
        ), patch(
            "resumes.views.resume_curation_view.TailoredResumeVersionComparisonService"
        ), patch(
            "resumes.views.resume_curation_view.JobResumeCurationRegenerationResponseSerializer"
        ) as mock_response_serializer:
            mock_service.return_value.generate.return_value = (curation, version)
            # A bare MagicMock's `.data` is itself an unbounded, endlessly
            # attribute-chainable mock, which DRF's JSON renderer cannot
            # serialize (it recurses into MemoryError trying). Giving it a
            # plain dict here keeps the response actually renderable while
            # still not requiring the other mocked services' realistic shapes.
            mock_response_serializer.return_value.data = {"id": curation.id}
            response = self.client.post(
                f"/api/resumes/job-curations/{curation.id}/tailored-resume/",
                {"regenerate": False},
                format="json",
            )

        self.assertNotEqual(response.status_code, 402)
        mock_service.return_value.generate.assert_called_once()

    def test_paid_provider_on_job_analysis_returns_402_before_any_ai_call(self):
        with patch("resumes.views.job_curation_view.JobCurationService") as mock_service:
            response = self.client.post(
                "/api/resumes/job-curations/analyze/",
                {"job_title": "Engineer", "job_description": "We are hiring a backend engineer with strong Python experience.", "curate_resume": True, "ai_provider": "gemini"},
                format="json",
            )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "UPGRADE_REQUIRED")
        mock_service.assert_not_called()

    def test_paid_provider_on_gap_analysis_returns_402_before_any_ai_call(self):
        curation = _generate_versions(self.user, 0)
        with patch("resumes.views.job_curation_view.JobGapAnalysisService") as mock_service:
            response = self.client.get(
                f"/api/resumes/job-curations/{curation.id}/gap-analysis/?ai_provider=groq"
            )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "UPGRADE_REQUIRED")
        mock_service.assert_not_called()

    def test_paid_provider_on_missing_information_returns_402_before_any_ai_call(self):
        curation = _generate_versions(self.user, 0)
        with patch("resumes.views.missing_information_view.MissingInformationReviewService") as mock_service:
            response = self.client.get(
                f"/api/resumes/job-curations/{curation.id}/missing-information/?ai_provider=gemini"
            )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "UPGRADE_REQUIRED")
        mock_service.assert_not_called()

    def test_free_user_may_use_local_provider_at_every_ai_provider_gate(self):
        # "ollama" (a free-tier provider) must never itself be blocked — only
        # the choice of a paid provider should trip these gates.
        with patch("resumes.views.job_curation_view.JobCurationService") as mock_service:
            mock_service.return_value.create_analysis.return_value = _generate_versions(self.user, 0)
            response = self.client.post(
                "/api/resumes/job-curations/analyze/",
                {"job_title": "Engineer", "job_description": "We are hiring a backend engineer with strong Python experience.", "curate_resume": True, "ai_provider": "ollama"},
                format="json",
            )

        self.assertNotEqual(response.status_code, 402)
        mock_service.return_value.create_analysis.assert_called_once()

    def test_pro_user_passes_every_gate(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")
        curation = _generate_versions(self.user, 10)

        versions = self.client.get(
            f"/api/resumes/job-curations/{curation.id}/tailored-resume/versions/"
        )
        self.assertEqual(versions.status_code, 200)

        entitlements = self.client.get("/api/billing/entitlements/")
        self.assertIsNone(entitlements.data["tailored_resumes_limit"])
        self.assertTrue(entitlements.data["docx_export"])
        self.assertFalse(entitlements.data["shows_ads"])


class AIUsageQuotaTests(TestCase):
    """The blanket cost-control quota, separate from the narrower
    tailored-resumes-per-month feature limit — covers every AI-backed
    action combined, tracked via a real AIUsageLog row per call."""

    def setUp(self):
        self.user = _make_user("aiusage@example.com")

    def test_recording_usage_creates_a_log_row(self):
        EntitlementsService.record_ai_usage(self.user, AIUsageLog.ACTION_JOB_ANALYSIS, "gemini")

        log = AIUsageLog.objects.get(user=self.user)
        self.assertEqual(log.action, AIUsageLog.ACTION_JOB_ANALYSIS)
        self.assertEqual(log.provider, "gemini")

    def test_ai_calls_used_this_month_counts_every_action_type(self):
        EntitlementsService.record_ai_usage(self.user, AIUsageLog.ACTION_JOB_ANALYSIS, "gemini")
        EntitlementsService.record_ai_usage(self.user, AIUsageLog.ACTION_GAP_ANALYSIS, "groq")
        EntitlementsService.record_ai_usage(self.user, AIUsageLog.ACTION_RESUME_PARSE, "ollama")

        self.assertEqual(EntitlementsService.ai_calls_used_this_month(self.user), 3)

    def test_check_can_use_ai_passes_under_limit(self):
        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 5}):
            for _ in range(4):
                EntitlementsService.record_ai_usage(self.user, AIUsageLog.ACTION_JOB_ANALYSIS, "gemini")
            EntitlementsService.check_can_use_ai(self.user)  # must not raise

    def test_check_can_use_ai_blocks_once_the_limit_is_reached(self):
        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 2}):
            for _ in range(2):
                EntitlementsService.record_ai_usage(self.user, AIUsageLog.ACTION_JOB_ANALYSIS, "gemini")

            with self.assertRaises(PlanLimitReachedError) as ctx:
                EntitlementsService.check_can_use_ai(self.user)
        self.assertEqual(ctx.exception.code, "PLAN_LIMIT_REACHED")

    def test_pro_user_has_unlimited_ai_calls(self):
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")
        for _ in range(50):
            EntitlementsService.record_ai_usage(self.user, AIUsageLog.ACTION_JOB_ANALYSIS, "gemini")

        EntitlementsService.check_can_use_ai(self.user)  # must not raise

    def test_usage_summary_reports_ai_call_counters(self):
        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 10}):
            EntitlementsService.record_ai_usage(self.user, AIUsageLog.ACTION_JOB_ANALYSIS, "gemini")
            summary = EntitlementsService.usage_summary(self.user)

        self.assertEqual(summary["ai_calls_used"], 1)
        self.assertEqual(summary["ai_calls_limit"], 10)
        self.assertEqual(summary["ai_calls_remaining"], 9)


class AIUsageEndpointGatingTests(TestCase):
    """Confirms the quota is actually wired into the endpoints, not just the
    service layer — and that the non-AI `standard` resume parser is fully
    exempt, since it never calls a provider at all."""

    def setUp(self):
        self.user = _make_user("aiusage-endpoint@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_standard_resume_parse_bypasses_the_ai_quota_entirely(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 0}):
            upload = SimpleUploadedFile("resume.pdf", b"placeholder content")
            with patch(
                "resumes.services.resume_upload_service.TextExtractor.extract_text", return_value="resume"
            ), patch("resumes.services.resume_upload_service.ResumeParser.parse", return_value={}):
                response = self.client.post(
                    "/api/resumes/upload/",
                    {"file": upload, "ai_provider": "standard"},
                    format="multipart",
                )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(AIUsageLog.objects.filter(user=self.user).exists())

    def test_job_analysis_is_blocked_once_ai_quota_is_exhausted(self):
        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 0}):
            with patch("resumes.views.job_curation_view.JobCurationService") as mock_service:
                response = self.client.post(
                    "/api/resumes/job-curations/analyze/",
                    {"job_title": "Engineer", "job_description": "We are hiring a backend engineer with strong Python experience.", "curate_resume": True},
                    format="json",
                )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "PLAN_LIMIT_REACHED")
        mock_service.assert_not_called()

    def test_job_analysis_records_usage_on_success(self):
        curation = _generate_versions(self.user, 0)
        with patch("resumes.views.job_curation_view.JobCurationService") as mock_service:
            mock_service.return_value.create_analysis.return_value = curation
            response = self.client.post(
                "/api/resumes/job-curations/analyze/",
                {"job_title": "Engineer", "job_description": "We are hiring a backend engineer with strong Python experience.", "curate_resume": True},
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            AIUsageLog.objects.filter(user=self.user, action=AIUsageLog.ACTION_JOB_ANALYSIS).count(), 1
        )

    def test_cached_gap_analysis_fetch_never_consumes_ai_quota(self):
        curation = _generate_versions(self.user, 0)
        curation.status = "completed"
        curation.analysis_data = {"required_skills": []}
        curation.gap_analysis_data = _GAP_ANALYSIS_DATA
        curation.save()

        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 0}):
            with patch("resumes.views.job_curation_view.JobGapAnalysisService") as mock_service:
                mock_service.return_value.get_or_create_gap_analysis.return_value = curation
                response = self.client.get(f"/api/resumes/job-curations/{curation.id}/gap-analysis/")

        self.assertEqual(response.status_code, 200)
        mock_service.return_value.get_or_create_gap_analysis.assert_called_once()
