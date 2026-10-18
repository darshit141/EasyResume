import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import User
from resumes.services.llm_service import (
    DahlLLMService,
    FallbackLLMService,
    GeminiLLMService,
    GroqLLMService,
    KiraLLMService,
    LLMEmptyResponseError,
    LLMInvalidResponseError,
    LLMModelUnavailableError,
    LLMService,
    LLMServiceError,
    LLMSchemaValidationError,
    LLMTimeoutError,
    OllamaLLMService,
    SUPPORTED_PROVIDERS,
    _to_gemini_schema,
    get_llm_service,
)
from resumes.services.resume_parser_service import ResumeParserService
from resumes.services.standard_resume_parser_service import StandardResumeParserService
from resumes.tests_job_curation import poll_job
from resumes.utils.ats_scorer import ATSScorer


def _resume(**overrides):
    base = {
        "personal_information": {"name": "Jane Doe", "email": None, "phone": None},
        "name": "Jane Doe", "email": None, "phone": None,
        "summary": None, "skills": [], "experience": [], "education": [],
        "projects": [], "certifications": [], "achievements": [], "languages": [],
    }
    base.update(overrides)
    return base


class FakeLLMService(LLMService):
    def __init__(self, result):
        self.result = result

    def generate_structured(self, prompt, schema):
        return self.result


class ResumeParserServiceTests(SimpleTestCase):
    valid_data = {
        "personal_information": {
            "name": "Jane Doe", "email": "jane@example.com", "phone": None,
        },
        "name": "Jane Doe", "email": "jane@example.com", "phone": None,
        "summary": None, "skills": ["Python"],
        "experience": [
            {"header": "Developer - Acme (2020-2024)", "bullets": ["Built the API.", "Cut latency 40%."]},
        ],
        "education": [], "projects": [], "certifications": [],
        "achievements": [], "languages": [],
    }

    def test_parses_valid_structured_response_without_ollama(self):
        parsed = ResumeParserService(FakeLLMService(self.valid_data)).parse("resume")
        self.assertEqual(parsed, self.valid_data)

    def test_a_bare_string_entry_is_coerced_to_header_bullets(self):
        data = {**self.valid_data, "experience": ["Developer at Acme"]}
        parsed = ResumeParserService(FakeLLMService(data)).parse("resume")
        self.assertEqual(parsed["experience"], [{"header": "Developer at Acme", "bullets": []}])

    def test_every_bullet_survives_a_multi_bullet_entry(self):
        bullets = [f"Did important thing number {n}." for n in range(12)]
        data = {**self.valid_data, "experience": [{"header": "Engineer - BigCo", "bullets": bullets}]}
        parsed = ResumeParserService(FakeLLMService(data)).parse("resume")
        self.assertEqual(parsed["experience"][0]["bullets"], bullets)

    def test_rejects_schema_mismatch(self):
        invalid = {**self.valid_data, "skills": "Python"}
        with self.assertRaises(LLMSchemaValidationError):
            ResumeParserService(FakeLLMService(invalid)).parse("resume")

    def test_rejects_an_experience_entry_that_is_neither_string_nor_entry(self):
        invalid = {**self.valid_data, "experience": [123]}
        with self.assertRaises(LLMSchemaValidationError):
            ResumeParserService(FakeLLMService(invalid)).parse("resume")


class ATSScorerTests(SimpleTestCase):
    def test_empty_resume_scores_zero(self):
        self.assertEqual(ATSScorer.calculate_score(_resume()), 0)

    def test_fully_filled_resume_scores_near_max(self):
        resume = _resume(
            email="jane@example.com",
            phone="1234567890",
            summary="Backend engineer with 5 years building distributed systems at scale.",
            skills=["Python", "Django", "PostgreSQL", "Docker", "Kafka", "AWS", "Redis", "CI/CD"],
            experience=[
                "Led migration of the payments service, reducing latency by 35% for 2M+ daily users.",
                "Built a real-time analytics pipeline processing 10x more events than the legacy system.",
            ],
            education=["B.Sc. Computer Science, State University."],
            projects=["Open-source CLI tool with 500+ GitHub stars."],
            certifications=["AWS Certified Solutions Architect."],
            achievements=["Won internal hackathon, 2023."],
        )
        self.assertEqual(ATSScorer.calculate_score(resume), 100)

    def test_score_never_exceeds_max(self):
        # Even a resume engineered to hit every bonus can't cross 100.
        resume = _resume(
            email="a@example.com", phone="123",
            summary="x" * 200,
            skills=["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"],
            experience=["Increased revenue by 50% and led a team of 10x growth, 3x faster delivery."] * 5,
            education=["Degree"], projects=["Project"],
            certifications=["Cert"], achievements=["Award"],
        )
        self.assertLessEqual(ATSScorer.calculate_score(resume), ATSScorer.MAX_SCORE)

    def test_contact_info_is_scored_per_field_not_all_or_nothing(self):
        email_only = ATSScorer.calculate_score(_resume(email="jane@example.com"))
        phone_only = ATSScorer.calculate_score(_resume(phone="1234567890"))
        both = ATSScorer.calculate_score(_resume(email="jane@example.com", phone="1234567890"))
        self.assertEqual(email_only, 5)
        self.assertEqual(phone_only, 5)
        self.assertEqual(both, 10)

    def test_short_summary_scores_less_than_substantive_one(self):
        short = ATSScorer.calculate_score(_resume(summary="N/A"))
        substantive = ATSScorer.calculate_score(
            _resume(summary="Backend engineer with five years of experience shipping production systems.")
        )
        self.assertLess(short, substantive)

    def test_skills_score_scales_with_breadth_up_to_a_cap(self):
        few = ATSScorer.calculate_score(_resume(skills=["Python"]))
        many = ATSScorer.calculate_score(_resume(skills=["Python", "Django", "SQL", "AWS", "Docker", "Kafka", "Redis", "Go"]))
        way_too_many = ATSScorer.calculate_score(
            _resume(skills=["Python", "Django", "SQL", "AWS", "Docker", "Kafka", "Redis", "Go", "Rust", "C++"])
        )
        self.assertLess(few, many)
        self.assertEqual(many, way_too_many)  # capped at 8 skills' worth of credit

    def test_quantified_experience_scores_higher_than_generic_experience(self):
        generic = ATSScorer.calculate_score(_resume(experience=["Responsible for backend systems."]))
        quantified = ATSScorer.calculate_score(
            _resume(experience=["Reduced API latency by 35% for 2M+ daily active users."])
        )
        self.assertLess(generic, quantified)

    def test_action_verb_opener_scores_higher_than_passive_phrasing(self):
        passive = ATSScorer.calculate_score(_resume(experience=["Was responsible for the backend team."]))
        active = ATSScorer.calculate_score(_resume(experience=["Led the backend team of six engineers."]))
        self.assertLess(passive, active)

    def test_year_ranges_alone_do_not_count_as_quantified_metrics(self):
        # A plain date range shouldn't be mistaken for an impact metric.
        with_dates_only = ATSScorer.calculate_score(_resume(experience=["Engineer at Acme Corp (2019-2022)."]))
        with_real_metric = ATSScorer.calculate_score(
            _resume(experience=["Engineered a system serving 100,000+ requests per second."])
        )
        self.assertLess(with_dates_only, with_real_metric)

    def test_completeness_bonus_requires_all_four_core_sections(self):
        # Adding education contributes both its own 10 points *and* unlocks
        # the 10-point all-four-core-sections bonus — so the delta is 20,
        # not just education's own share.
        three_sections = ATSScorer.calculate_score(_resume(
            summary="A real professional summary that is long enough to count.",
            skills=["Python"],
            experience=["Built things."],
        ))
        four_sections = ATSScorer.calculate_score(_resume(
            summary="A real professional summary that is long enough to count.",
            skills=["Python"],
            experience=["Built things."],
            education=["Degree"],
        ))
        self.assertEqual(four_sections - three_sections, 20)

    def test_score_is_deterministic_for_the_same_input(self):
        resume = _resume(email="jane@example.com", skills=["Python", "Django"])
        first = ATSScorer.calculate_score(resume)
        second = ATSScorer.calculate_score(resume)
        self.assertEqual(first, second)


class StandardResumeParserServiceTests(SimpleTestCase):
    RESUME_TEXT = """Jane Doe
jane@example.com | +1 555-123-4567

SUMMARY
Backend engineer focused on distributed systems.

SKILLS
Skills: Python, Django, PostgreSQL
Soft Skills: Communication, Leadership
Hobbies: Chess, Hiking

WORK EXPERIENCE
Backend Engineer at Acme Corp (2019-2024)
Built the payments platform.

EDUCATION
BS Computer Science, State University (2019)
"""

    def test_extracts_contact_and_name(self):
        result = StandardResumeParserService().parse(self.RESUME_TEXT)
        self.assertEqual(result["email"], "jane@example.com")
        self.assertEqual(result["phone"], "+1 555-123-4567")
        self.assertEqual(result["name"], "Jane Doe")

    def test_excludes_soft_skills_and_hobbies_from_skills(self):
        result = StandardResumeParserService().parse(self.RESUME_TEXT)
        self.assertEqual(result["skills"], ["Python", "Django", "PostgreSQL"])

    def test_output_passes_llm_schema_validation(self):
        result = StandardResumeParserService().parse(self.RESUME_TEXT)
        validated = ResumeParserService._validate(result)
        self.assertEqual(validated["skills"], ["Python", "Django", "PostgreSQL"])
        exp_headers = [e["header"] for e in validated["experience"]]
        self.assertIn("Backend Engineer at Acme Corp (2019-2024)", exp_headers)
        edu_headers = [e["header"] for e in validated["education"]]
        self.assertIn("BS Computer Science, State University (2019)", edu_headers)

    def test_bulleted_lines_group_under_the_preceding_role(self):
        text = (
            "WORK EXPERIENCE\n"
            "Senior Engineer at Globex (2021-Present)\n"
            "- Led the platform team.\n"
            "- Cut build times by 60%.\n"
            "- Mentored four engineers.\n"
        )
        result = StandardResumeParserService().parse(text)
        self.assertEqual(len(result["experience"]), 1)
        entry = result["experience"][0]
        self.assertEqual(entry["header"], "Senior Engineer at Globex (2021-Present)")
        self.assertEqual(len(entry["bullets"]), 3)
        self.assertIn("Cut build times by 60%.", entry["bullets"])


class OllamaLLMServiceTests(SimpleTestCase):
    def test_model_availability_succeeds_when_the_configured_model_is_listed(self):
        service = OllamaLLMService("http://localhost:11434", "test-model", 1, 0, 0)
        response = type(
            "Response",
            (),
            {
                "read": lambda self: b'{"models": [{"name": "test-model"}]}',
                "__enter__": lambda self: self,
                "__exit__": lambda *args: None,
            },
        )()
        with patch("resumes.services.llm_service.urlopen", return_value=response):
            self.assertTrue(service.check_model_available())

    def test_model_availability_rejects_missing_model(self):
        service = OllamaLLMService("http://localhost:11434", "test-model", 1, 0, 0)
        response = type(
            "Response",
            (),
            {
                "read": lambda self: b'{"models": [{"name": "other-model"}]}',
                "__enter__": lambda self: self,
                "__exit__": lambda *args: None,
            },
        )()
        with patch("resumes.services.llm_service.urlopen", return_value=response):
            with self.assertRaises(LLMModelUnavailableError):
                service.check_model_available()

    def test_missing_model_configuration_is_an_application_error(self):
        service = OllamaLLMService("http://localhost:11434", "", 1, 0, 0)
        with self.assertRaises(LLMModelUnavailableError):
            service.generate_structured("prompt", {})

    def test_empty_response_is_rejected(self):
        service = OllamaLLMService("http://localhost:11434", "test", 1, 0, 0)
        response = type("Response", (), {"read": lambda self: b'{\"response\": \"\"}', "__enter__": lambda self: self, "__exit__": lambda *args: None})()
        with patch("resumes.services.llm_service.urlopen", return_value=response):
            with self.assertRaises(LLMEmptyResponseError):
                service.generate_structured("prompt", {})

    def test_invalid_json_is_rejected(self):
        service = OllamaLLMService("http://localhost:11434", "test", 1, 0, 0)
        response = type("Response", (), {"read": lambda self: b'{\"response\": \"not json\"}', "__enter__": lambda self: self, "__exit__": lambda *args: None})()
        with patch("resumes.services.llm_service.urlopen", return_value=response):
            with self.assertRaises(LLMInvalidResponseError):
                service.generate_structured("prompt", {})

    def test_timeout_is_converted_to_application_error(self):
        service = OllamaLLMService("http://localhost:11434", "test", 1, 0, 0)
        with patch("resumes.services.llm_service.urlopen", side_effect=TimeoutError):
            with self.assertRaises(LLMTimeoutError):
                service.generate_structured("prompt", {})

    def test_unavailable_ollama_is_converted_to_application_error(self):
        service = OllamaLLMService("http://localhost:11434", "test", 1, 0, 0)
        with patch("resumes.services.llm_service.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(Exception, "AI service is unavailable"):
                service.generate_structured("prompt", {})


def _fake_response(body_bytes):
    return type(
        "Response",
        (),
        {
            "read": lambda self: body_bytes,
            "__enter__": lambda self: self,
            "__exit__": lambda *args: None,
        },
    )()


class GroqLLMServiceTests(SimpleTestCase):
    def make_service(self, api_key="test-key", model="llama-3.3-70b-versatile"):
        return GroqLLMService(
            api_key=api_key,
            model=model,
            timeout=5,
            max_retries=0,
            temperature=0,
            base_url="https://api.groq.com/openai/v1",
        )

    def test_missing_api_key_is_rejected_without_a_network_call(self):
        service = self.make_service(api_key="")
        with patch("resumes.services.llm_service.urlopen") as mock_urlopen:
            with self.assertRaises(LLMModelUnavailableError):
                service.generate_structured("prompt", {})
            mock_urlopen.assert_not_called()

    def test_missing_model_is_rejected_without_a_network_call(self):
        service = self.make_service(model="")
        with patch("resumes.services.llm_service.urlopen") as mock_urlopen:
            with self.assertRaises(LLMModelUnavailableError):
                service.generate_structured("prompt", {})
            mock_urlopen.assert_not_called()

    def test_successful_completion_returns_parsed_json(self):
        service = self.make_service()
        body = json.dumps({
            "choices": [{"message": {"content": json.dumps({"skills": ["Python"]})}}]
        }).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            result = service.generate_structured("prompt", {"type": "object"})
        self.assertEqual(result, {"skills": ["Python"]})

    def test_request_includes_bearer_auth_and_json_mode(self):
        service = self.make_service()
        body = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode("utf-8")
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _fake_response(body)

        with patch("resumes.services.llm_service.urlopen", side_effect=fake_urlopen):
            service.generate_structured("prompt", {"type": "object"})

        request = captured["request"]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIn("JSON schema", payload["messages"][0]["content"])

    def test_empty_choices_raises_empty_response_error(self):
        service = self.make_service()
        body = json.dumps({"choices": []}).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            with self.assertRaises(LLMEmptyResponseError):
                service.generate_structured("prompt", {})

    def test_non_json_content_raises_invalid_response_error(self):
        service = self.make_service()
        body = json.dumps({"choices": [{"message": {"content": "not json"}}]}).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            with self.assertRaises(LLMInvalidResponseError):
                service.generate_structured("prompt", {})

    def test_401_is_converted_to_model_unavailable(self):
        service = self.make_service()
        error = HTTPError("url", 401, "Unauthorized", {}, None)
        with patch("resumes.services.llm_service.urlopen", side_effect=error):
            with self.assertRaises(LLMModelUnavailableError):
                service.generate_structured("prompt", {})

    def test_timeout_is_converted_to_application_error(self):
        service = self.make_service()
        with patch("resumes.services.llm_service.urlopen", side_effect=TimeoutError):
            with self.assertRaises(LLMTimeoutError):
                service.generate_structured("prompt", {})

    def test_model_availability_succeeds_when_model_is_listed(self):
        service = self.make_service()
        body = json.dumps({"data": [{"id": "llama-3.3-70b-versatile"}]}).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            self.assertTrue(service.check_model_available())

    def test_model_availability_rejects_missing_model(self):
        service = self.make_service()
        body = json.dumps({"data": [{"id": "other-model"}]}).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            with self.assertRaises(LLMModelUnavailableError):
                service.check_model_available()


class KiraLLMServiceTests(SimpleTestCase):
    """Kira (kiraai.vn) speaks the same OpenAI-compatible wire format as
    Groq, so KiraLLMService is a thin subclass. These tests confirm the
    inherited behaviour still holds when constructed as Kira and that the
    class stays wired into provider resolution."""

    def make_service(self, api_key="test-key", model="kira-3.5-flash"):
        return KiraLLMService(
            api_key=api_key,
            model=model,
            timeout=5,
            max_retries=0,
            temperature=0,
            base_url="https://kiraai.vn/api/v1",
        )

    def test_is_a_groq_client_subclass(self):
        self.assertTrue(issubclass(KiraLLMService, GroqLLMService))

    def test_successful_completion_returns_parsed_json(self):
        service = self.make_service()
        body = json.dumps({
            "choices": [{"message": {"content": json.dumps({"skills": ["Python"]})}}]
        }).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            result = service.generate_structured("prompt", {"type": "object"})
        self.assertEqual(result, {"skills": ["Python"]})

    def test_request_hits_configured_base_url_with_bearer_auth(self):
        service = self.make_service()
        body = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode("utf-8")
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _fake_response(body)

        with patch("resumes.services.llm_service.urlopen", side_effect=fake_urlopen):
            service.generate_structured("prompt", {"type": "object"})

        request = captured["request"]
        self.assertEqual(request.full_url, "https://kiraai.vn/api/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "kira-3.5-flash")
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_missing_api_key_is_rejected_without_a_network_call(self):
        service = self.make_service(api_key="")
        with patch("resumes.services.llm_service.urlopen") as mock_urlopen:
            with self.assertRaises(LLMModelUnavailableError):
                service.generate_structured("prompt", {})
            mock_urlopen.assert_not_called()

    def test_timeout_is_converted_to_application_error(self):
        service = self.make_service()
        with patch("resumes.services.llm_service.urlopen", side_effect=TimeoutError):
            with self.assertRaises(LLMTimeoutError):
                service.generate_structured("prompt", {})

    def test_model_availability_succeeds_when_model_is_listed(self):
        service = self.make_service()
        body = json.dumps({"data": [{"id": "kira-3.5-flash"}]}).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            self.assertTrue(service.check_model_available())

    def test_kira_is_a_supported_provider(self):
        self.assertIn("kira", SUPPORTED_PROVIDERS)

    @override_settings(
        LLM_PROVIDER="kira",
        LLM_FALLBACK_PROVIDER="",
        KIRA_API_KEY="k",
        KIRA_MODEL="kira-3.5-flash",
        KIRA_BASE_URL="https://kiraai.vn/api/v1",
    )
    def test_get_llm_service_returns_kira_client_when_configured_as_default(self):
        self.assertIsInstance(get_llm_service(), KiraLLMService)

    @override_settings(
        LLM_PROVIDER="groq",
        LLM_FALLBACK_PROVIDER="kira",
        KIRA_API_KEY="k",
        KIRA_MODEL="kira-3.5-flash",
        KIRA_BASE_URL="https://kiraai.vn/api/v1",
    )
    def test_kira_can_be_the_configured_fallback_provider(self):
        service = get_llm_service()
        self.assertIsInstance(service, FallbackLLMService)
        self.assertEqual(service.fallback_provider_name, "kira")
        self.assertIsInstance(service.fallback, KiraLLMService)


class DahlLLMServiceTests(SimpleTestCase):
    """Dahl Global (inference.dahl.global) tests covering multi-model failover,
    endpoint formatting, auth error handling, and provider resolution."""

    def make_service(
        self,
        api_key="test-dahl-key",
        model="MiniMaxAI/MiniMax-M2.7",
        fallback_models=("zai-org/GLM-5.3-Flash", "deepseek-ai/DeepSeek-V4-Flash-0731"),
    ):
        return DahlLLMService(
            api_key=api_key,
            model=model,
            timeout=5,
            max_retries=0,
            temperature=0,
            base_url="https://inference.dahl.global/v1",
            fallback_models=fallback_models,
        )

    def test_dahl_is_a_supported_provider(self):
        self.assertIn("dahl", SUPPORTED_PROVIDERS)

    def test_candidate_models_includes_primary_and_fallbacks(self):
        service = self.make_service()
        self.assertEqual(
            service.candidate_models(),
            ["MiniMaxAI/MiniMax-M2.7", "zai-org/GLM-5.3-Flash", "deepseek-ai/DeepSeek-V4-Flash-0731"],
        )

    def test_successful_completion_returns_parsed_json(self):
        service = self.make_service()
        body = json.dumps({
            "choices": [{"message": {"content": json.dumps({"skills": ["Django", "Python"]})}}]
        }).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            result = service.generate_structured("prompt", {"type": "object"})
        self.assertEqual(result, {"skills": ["Django", "Python"]})
        self.assertEqual(service.last_model_used, "MiniMaxAI/MiniMax-M2.7")

    def test_request_hits_configured_base_url_with_bearer_auth(self):
        service = self.make_service()
        body = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode("utf-8")
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _fake_response(body)

        with patch("resumes.services.llm_service.urlopen", side_effect=fake_urlopen):
            service.generate_structured("prompt", {"type": "object"})

        request = captured["request"]
        self.assertEqual(request.full_url, "https://inference.dahl.global/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-dahl-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "MiniMaxAI/MiniMax-M2.7")
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_model_fallback_when_primary_model_fails(self):
        service = self.make_service()
        primary_error = HTTPError(
            "https://inference.dahl.global/v1/chat/completions",
            503, "Service Unavailable", {}, None
        )
        fallback_body = json.dumps({
            "choices": [{"message": {"content": json.dumps({"ok": True})}}]
        }).encode("utf-8")

        calls = []
        def fake_urlopen(request, timeout=None):
            calls.append(json.loads(request.data.decode("utf-8"))["model"])
            if len(calls) == 1:
                raise primary_error
            return _fake_response(fallback_body)

        with patch("resumes.services.llm_service.urlopen", side_effect=fake_urlopen):
            result = service.generate_structured("prompt", {"type": "object"})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, ["MiniMaxAI/MiniMax-M2.7", "zai-org/GLM-5.3-Flash"])
        self.assertEqual(service.last_model_used, "zai-org/GLM-5.3-Flash")

    def test_missing_api_key_raises_auth_error(self):
        from resumes.services.llm_service import LLMAuthError
        service = self.make_service(api_key="")
        with self.assertRaises(LLMAuthError):
            service.generate_structured("prompt", {})

    def test_probe_model_success(self):
        service = self.make_service()
        body = json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            ok, detail = service.probe_model("MiniMaxAI/MiniMax-M2.7")
        self.assertTrue(ok)
        self.assertIn("answered in", detail)

    @override_settings(
        LLM_PROVIDER="dahl",
        LLM_FALLBACK_PROVIDER="",
        DAHL_API_KEY="token",
        DAHL_MODEL="MiniMaxAI/MiniMax-M2.7",
        DAHL_BASE_URL="https://inference.dahl.global/v1",
    )
    def test_get_llm_service_returns_dahl_client_when_configured(self):
        self.assertIsInstance(get_llm_service(), DahlLLMService)


class GeminiLLMServiceTests(SimpleTestCase):
    def make_service(self, api_key="test-key", model="gemini-3.8-flash", fallback_models=()):
        return GeminiLLMService(
            api_key=api_key,
            model=model,
            timeout=5,
            max_retries=0,
            temperature=0,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            fallback_models=fallback_models,
            # These tests script urlopen one call at a time; model discovery
            # would consume the first scripted response. Discovery has its
            # own tests in tests_gemini_resilience.py.
            discover_models=False,
        )

    def test_missing_api_key_is_rejected_without_a_network_call(self):
        service = self.make_service(api_key="")
        with patch("resumes.services.llm_service.urlopen") as mock_urlopen:
            with self.assertRaises(LLMModelUnavailableError):
                service.generate_structured("prompt", {})
            mock_urlopen.assert_not_called()

    def test_successful_completion_returns_parsed_json(self):
        service = self.make_service()
        body = json.dumps({
            "candidates": [{"content": {"parts": [{"text": json.dumps({"skills": ["Java"]})}]}}]
        }).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            result = service.generate_structured("prompt", {"type": "object"})
        self.assertEqual(result, {"skills": ["Java"]})

    def test_request_includes_api_key_header_and_response_schema(self):
        service = self.make_service()
        body = json.dumps({
            "candidates": [{"content": {"parts": [{"text": "{}"}]}}]
        }).encode("utf-8")
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _fake_response(body)

        schema = {"type": "object", "properties": {"skills": {"type": "array", "items": {"type": "string"}}}}
        with patch("resumes.services.llm_service.urlopen", side_effect=fake_urlopen):
            service.generate_structured("prompt", schema)

        request = captured["request"]
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(payload["generationConfig"]["responseSchema"]["type"], "OBJECT")

    def test_empty_candidates_raises_empty_response_error(self):
        service = self.make_service()
        body = json.dumps({"candidates": []}).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            with self.assertRaises(LLMEmptyResponseError):
                service.generate_structured("prompt", {})

    def test_non_json_text_raises_invalid_response_error(self):
        service = self.make_service()
        body = json.dumps({
            "candidates": [{"content": {"parts": [{"text": "not json"}]}}]
        }).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            with self.assertRaises(LLMInvalidResponseError):
                service.generate_structured("prompt", {})

    def test_403_is_converted_to_model_unavailable(self):
        service = self.make_service()
        error = HTTPError("url", 403, "Forbidden", {}, None)
        with patch("resumes.services.llm_service.urlopen", side_effect=error):
            with self.assertRaises(LLMModelUnavailableError):
                service.generate_structured("prompt", {})

    def test_timeout_is_converted_to_application_error(self):
        service = self.make_service()
        with patch("resumes.services.llm_service.urlopen", side_effect=TimeoutError):
            with self.assertRaises(LLMTimeoutError):
                service.generate_structured("prompt", {})

    def test_model_availability_succeeds(self):
        service = self.make_service()
        body = b"{}"
        with patch("resumes.services.llm_service.urlopen", return_value=_fake_response(body)):
            self.assertTrue(service.check_model_available())

    def test_404_retired_model_fails_over_to_next_candidate_and_succeeds(self):
        service = self.make_service(model="gemini-retired-model", fallback_models=["gemini-3.8-flash"])
        error_404 = HTTPError("url", 404, "Not Found", {}, None)
        success_body = json.dumps({
            "candidates": [{"content": {"parts": [{"text": json.dumps({"skills": ["Python"]})}]}}]
        }).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", side_effect=[error_404, _fake_response(success_body)]):
            result = service.generate_structured("prompt", {"type": "object"})
        self.assertEqual(result, {"skills": ["Python"]})

    def test_503_busy_model_fails_over_to_next_candidate_and_succeeds(self):
        service = self.make_service(model="gemini-3.7-flash", fallback_models=["gemini-3.8-flash"])
        error_503 = HTTPError("url", 503, "High Demand", {}, None)
        success_body = json.dumps({
            "candidates": [{"content": {"parts": [{"text": json.dumps({"skills": ["Django"]})}]}}]
        }).encode("utf-8")
        with patch("resumes.services.llm_service.urlopen", side_effect=[error_503, _fake_response(success_body)]):
            result = service.generate_structured("prompt", {"type": "object"})
        self.assertEqual(result, {"skills": ["Django"]})


class GeminiSchemaConversionTests(SimpleTestCase):
    def test_nullable_string_type_array_becomes_nullable_flag(self):
        result = _to_gemini_schema({"type": ["string", "null"]})
        self.assertEqual(result, {"type": "STRING", "nullable": True})

    def test_plain_type_is_uppercased(self):
        result = _to_gemini_schema({"type": "array", "items": {"type": "string"}})
        self.assertEqual(result, {"type": "ARRAY", "items": {"type": "STRING"}})

    def test_nested_object_properties_are_converted_recursively(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "skills": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["skills"],
        }
        result = _to_gemini_schema(schema)
        self.assertEqual(result["type"], "OBJECT")
        self.assertEqual(result["properties"]["name"], {"type": "STRING", "nullable": True})
        self.assertEqual(result["properties"]["skills"], {"type": "ARRAY", "items": {"type": "STRING"}})
        self.assertEqual(result["required"], ["skills"])

    def test_enum_is_preserved(self):
        result = _to_gemini_schema({"type": "string", "enum": ["MATCHED", "MISSING"]})
        self.assertEqual(result["enum"], ["MATCHED", "MISSING"])


class _StubLLMService(LLMService):
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = 0

    def generate_structured(self, prompt, schema):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result

    def check_model_available(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return True


class FallbackLLMServiceTests(SimpleTestCase):
    def test_falls_back_on_primary_timeout(self):
        primary = _StubLLMService(exc=LLMTimeoutError())
        fallback = _StubLLMService(result={"ok": True})
        service = FallbackLLMService(primary, fallback, "gemini")

        result = service.generate_structured("prompt", {})

        self.assertEqual(result, {"ok": True})
        self.assertTrue(service.used_fallback)
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)

    def test_falls_back_on_model_unavailable(self):
        primary = _StubLLMService(exc=LLMModelUnavailableError())
        fallback = _StubLLMService(result={"ok": True})
        service = FallbackLLMService(primary, fallback, "gemini")

        self.assertEqual(service.generate_structured("prompt", {}), {"ok": True})

    def test_falls_back_on_generic_connection_failure(self):
        primary = _StubLLMService(exc=LLMServiceError())
        fallback = _StubLLMService(result={"ok": True})
        service = FallbackLLMService(primary, fallback, "gemini")

        self.assertEqual(service.generate_structured("prompt", {}), {"ok": True})

    def test_does_not_fall_back_on_schema_validation_failure(self):
        # The provider responded; the *content* was bad — that's the
        # caller's own grounding/retry logic to handle, not this fallback.
        primary = _StubLLMService(exc=LLMSchemaValidationError())
        fallback = _StubLLMService(result={"ok": True})
        service = FallbackLLMService(primary, fallback, "gemini")

        with self.assertRaises(LLMSchemaValidationError):
            service.generate_structured("prompt", {})
        self.assertEqual(fallback.calls, 0)

    def test_does_not_fall_back_when_primary_succeeds(self):
        primary = _StubLLMService(result={"primary": True})
        fallback = _StubLLMService(result={"fallback": True})
        service = FallbackLLMService(primary, fallback, "gemini")

        self.assertEqual(service.generate_structured("prompt", {}), {"primary": True})
        self.assertFalse(service.used_fallback)
        self.assertEqual(fallback.calls, 0)

    def test_fallback_error_propagates_if_fallback_also_fails(self):
        primary = _StubLLMService(exc=LLMTimeoutError())
        fallback = _StubLLMService(exc=LLMModelUnavailableError())
        service = FallbackLLMService(primary, fallback, "gemini")

        with self.assertRaises(LLMModelUnavailableError):
            service.generate_structured("prompt", {})


class GetLLMServiceFallbackWiringTests(SimpleTestCase):
    @override_settings(LLM_PROVIDER="groq", LLM_FALLBACK_PROVIDER="gemini")
    def test_default_provider_is_wrapped_with_configured_fallback(self):
        service = get_llm_service()
        self.assertIsInstance(service, FallbackLLMService)
        self.assertEqual(service.fallback_provider_name, "gemini")
        self.assertIsInstance(service.primary, GroqLLMService)
        self.assertIsInstance(service.fallback, GeminiLLMService)

    @override_settings(LLM_PROVIDER="groq", LLM_FALLBACK_PROVIDER="")
    def test_no_wrapping_when_fallback_not_configured(self):
        service = get_llm_service()
        self.assertIsInstance(service, GroqLLMService)

    @override_settings(LLM_PROVIDER="groq", LLM_FALLBACK_PROVIDER="groq")
    def test_no_wrapping_when_fallback_equals_the_primary(self):
        service = get_llm_service()
        self.assertIsInstance(service, GroqLLMService)

    @override_settings(LLM_PROVIDER="groq", LLM_FALLBACK_PROVIDER="gemini")
    def test_explicitly_requested_provider_is_never_wrapped(self):
        # A user's saved provider preference must never be silently
        # swapped out — that would bypass their plan's allowed-provider
        # gate and surprise them with a different provider's output.
        service = get_llm_service(provider="ollama")
        self.assertIsInstance(service, OllamaLLMService)


# Upload now rejects files that yield almost no extractable text (the
# signature of a scanned/image PDF), so these stubs must return something of
# realistic length — a six-character stub would be rejected before ever
# reaching the parser these tests are actually exercising.
_SAMPLE_RESUME_TEXT = (
    "Jane Doe\njane@example.com | 555-0100\n\n"
    "SUMMARY\nBackend engineer with six years building payment systems.\n\n"
    "EXPERIENCE\nSoftware Engineer, Acme Corp (2020-2024)\n"
    "Reduced checkout latency by 35% across the settlement pipeline.\n\n"
    "EDUCATION\nBSc Computer Science, State University\n"
)


class ResumeUploadAPICompatibilityTests(TestCase):
    def _client(self):
        user = User.objects.create_user(
            name="Jane Doe", email="jane@example.com", phone="1234567890",
            password="safe-password",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        return client, user

    def test_upload_contract_is_unchanged_when_parser_is_mocked(self):
        client, _ = self._client()
        upload = SimpleUploadedFile(
            "resume.pdf", b"placeholder", content_type="application/pdf",
        )
        parsed = ResumeParserServiceTests.valid_data

        with patch("resumes.services.resume_upload_service.TextExtractor.extract_text", return_value=_SAMPLE_RESUME_TEXT), patch(
            "resumes.services.resume_upload_service.ResumeParser.parse", return_value=parsed
        ):
            response = client.post("/api/resumes/upload/", {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(client, response)
        self.assertEqual(job_status.data["status"], "completed")
        resume_id = job_status.data["result"]["resume_id"]
        detail_response = client.get(f"/api/resumes/{resume_id}/")
        self.assertEqual(
            set(detail_response.data),
            {"id", "original_filename", "status", "ats_score", "parsed_data", "created_at", "updated_at"},
        )

    def test_llm_timeout_returns_503_not_a_false_success(self):
        client, _ = self._client()
        upload = SimpleUploadedFile(
            "resume.pdf", b"placeholder", content_type="application/pdf",
        )
        with patch("resumes.services.resume_upload_service.TextExtractor.extract_text", return_value=_SAMPLE_RESUME_TEXT), patch(
            "resumes.services.resume_upload_service.ResumeParser.parse", side_effect=LLMTimeoutError()
        ):
            response = client.post("/api/resumes/upload/", {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(client, response)
        self.assertEqual(job_status.data["status"], "failed")
        self.assertEqual(job_status.data["error_code"], "LLM_TIMEOUT")

    def test_failed_reupload_leaves_existing_resume_untouched(self):
        client, user = self._client()
        good_parsed = ResumeParserServiceTests.valid_data
        first_upload = SimpleUploadedFile(
            "resume.pdf", b"placeholder", content_type="application/pdf",
        )
        with patch("resumes.services.resume_upload_service.TextExtractor.extract_text", return_value=_SAMPLE_RESUME_TEXT), patch(
            "resumes.services.resume_upload_service.ResumeParser.parse", return_value=good_parsed
        ):
            first_response = client.post("/api/resumes/upload/", {"file": first_upload}, format="multipart")
        self.assertEqual(first_response.status_code, 202)
        first_job_status = poll_job(client, first_response)
        self.assertEqual(first_job_status.data["status"], "completed")

        second_upload = SimpleUploadedFile(
            "resume2.pdf", b"placeholder2", content_type="application/pdf",
        )
        with patch("resumes.services.resume_upload_service.TextExtractor.extract_text", return_value=_SAMPLE_RESUME_TEXT), patch(
            "resumes.services.resume_upload_service.ResumeParser.parse", side_effect=LLMTimeoutError()
        ):
            second_response = client.post("/api/resumes/upload/", {"file": second_upload}, format="multipart")
        self.assertEqual(second_response.status_code, 202)
        second_job_status = poll_job(client, second_response)
        self.assertEqual(second_job_status.data["status"], "failed")

        # The failed second upload's own Resume row is marked "failed" (never
        # left stuck "processing"), and — the actual point of this test — the
        # first, successfully parsed resume is completely untouched by it.
        second_resume_id = second_job_status.data["resume_id"]
        second_detail_response = client.get(f"/api/resumes/{second_resume_id}/")
        self.assertEqual(second_detail_response.data["status"], "failed")

        first_resume_id = first_job_status.data["result"]["resume_id"]
        first_detail_response = client.get(f"/api/resumes/{first_resume_id}/")
        self.assertEqual(first_detail_response.status_code, 200)
        self.assertEqual(first_detail_response.data["status"], "completed")
        self.assertEqual(first_detail_response.data["parsed_data"], good_parsed)


class ResumeUploadValidationMessageTests(TestCase):
    """Every rejection must tell the user what to do about it.

    These assert on the message text, not just the status code, because the
    whole point of this validation is that the user is told what is wrong —
    a correct 400 carrying an unhelpful message is still a bug.
    """

    def setUp(self):
        user = User.objects.create_user(
            name="Jane Doe", email="jane-validation@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)

    def _upload(self, name, content=b"placeholder content", **extra):
        upload = SimpleUploadedFile(name, content)
        return self.client.post("/api/resumes/upload/", {"file": upload, **extra}, format="multipart")

    def test_legacy_doc_explains_how_to_convert(self):
        response = self._upload("resume.doc")

        self.assertEqual(response.status_code, 400)
        message = response.data["file"][0]
        self.assertIn(".docx", message)
        self.assertIn("Save As", message)

    def test_unsupported_type_names_the_accepted_formats(self):
        response = self._upload("resume.txt")

        self.assertEqual(response.status_code, 400)
        self.assertIn("PDF or DOCX", response.data["file"][0])

    def test_empty_file_is_rejected_before_any_parsing(self):
        response = self._upload("resume.pdf", content=b"")

        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.data["file"][0].lower())

    def test_oversized_file_reports_its_actual_size(self):
        response = self._upload("resume.pdf", content=b"x" * (6 * 1024 * 1024))

        self.assertEqual(response.status_code, 400)
        message = response.data["file"][0]
        self.assertIn("6.0 MB", message)
        self.assertIn("5 MB", message)

    def test_scanned_pdf_yielding_no_text_explains_the_real_cause(self):
        # A scanned PDF raises nothing — it just extracts to almost nothing.
        with patch(
            "resumes.services.resume_upload_service.TextExtractor.extract_text",
            return_value="   \n  ",
        ):
            response = self._upload("scan.pdf")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["code"], "RESUME_TEXT_EXTRACTION_FAILED")
        self.assertIn("scanned", response.data["detail"].lower())

    def test_no_ai_call_is_made_when_text_extraction_yields_nothing(self):
        with patch(
            "resumes.services.resume_upload_service.TextExtractor.extract_text", return_value=""
        ), patch("resumes.services.resume_upload_service.ResumeParser.parse") as mock_parse:
            self._upload("scan.pdf")

        mock_parse.assert_not_called()


class APIKeyPoolTests(SimpleTestCase):
    def test_parse_keys_various_delimiters(self):
        from resumes.services.llm_service import _parse_api_keys, APIKeyPool

        raw = "key1, key2\nkey3; key4,key1"
        parsed = _parse_api_keys(raw)
        self.assertEqual(parsed, ["key1", "key2", "key3", "key4"])

    def test_cooldown_and_active_keys_rotation(self):
        from resumes.services.llm_service import APIKeyPool
        from django.core.cache import cache

        pool = APIKeyPool("token_a, token_b, token_c", provider_name="test_provider")
        self.assertEqual(pool.active_keys(), ["token_a", "token_b", "token_c"])
        self.assertEqual(pool.primary_key, "token_a")

        # Mark token_a exhausted
        pool.mark_exhausted("token_a", cooldown=60)
        self.assertTrue(pool.is_on_cooldown("token_a"))
        self.assertEqual(pool.active_keys(), ["token_b", "token_c"])
        self.assertEqual(pool.primary_key, "token_b")

        # Clear cooldown
        pool.mark_healthy("token_a")
        self.assertFalse(pool.is_on_cooldown("token_a"))
        self.assertEqual(pool.active_keys(), ["token_a", "token_b", "token_c"])


class MultiTokenFailoverTests(SimpleTestCase):
    def test_gemini_rotates_tokens_on_429(self):
        from urllib.error import HTTPError
        from resumes.services.llm_service import GeminiLLMService

        service = GeminiLLMService(
            api_keys="key_one, key_two",
            model="gemini-3.8-flash",
            fallback_models=[],
            discover_models=False,
        )

        attempts = []
        def mock_post_generate(model, prompt, schema, thinking, max_output_tokens=None, api_key=None):
            attempts.append(api_key)
            if api_key == "key_one":
                raise HTTPError("url", 429, "Resource exhausted", {"status": "RESOURCE_EXHAUSTED"}, None)
            return {"candidates": [{"content": {"parts": [{"text": '{"result": "ok"}'}]}}]}

        with patch.object(service, "_post_generate", side_effect=mock_post_generate):
            res = service.generate_structured("prompt", {})
            self.assertEqual(res, {"result": "ok"})
            self.assertEqual(attempts, ["key_one", "key_two"])
            self.assertTrue(service.key_pool.is_on_cooldown("key_one"))

    def test_gemini_all_tokens_and_models_exhausted_raises_high_demand(self):
        from urllib.error import HTTPError
        from resumes.services.llm_service import GeminiLLMService, LLMHighDemandError

        service = GeminiLLMService(
            api_keys="key_a, key_b",
            model="gemini-3.8-flash",
            fallback_models=["gemini-3.7-flash"],
            discover_models=False,
        )

        def mock_post_generate(model, prompt, schema, thinking, max_output_tokens=None, api_key=None):
            raise HTTPError("url", 429, "Resource exhausted", {"status": "RESOURCE_EXHAUSTED"}, None)

        with patch.object(service, "_post_generate", side_effect=mock_post_generate):
            with self.assertRaises(LLMHighDemandError) as ctx:
                service.generate_structured("prompt", {})
            self.assertEqual(ctx.exception.code, "AI_HIGH_DEMAND_CAPACITY")
            self.assertIn("Due to high demand", str(ctx.exception))

    def test_dahl_rotates_tokens_on_429(self):
        from urllib.error import HTTPError
        from resumes.services.llm_service import DahlLLMService

        service = DahlLLMService(
            api_keys="dahl_1, dahl_2",
            model="MiniMaxAI/MiniMax-M2.7",
            fallback_models=[],
        )

        attempts = []
        def mock_urlopen(request, timeout):
            import io
            headers = dict(request.header_items())
            auth = headers.get("Authorization", "")
            key = auth.replace("Bearer ", "").strip()
            attempts.append(key)
            if key == "dahl_1":
                fp = io.BytesIO(b'{"error": {"code": 429, "message": "rate limit"}}')
                raise HTTPError("url", 429, "Rate limit", {}, fp)
            fp = io.BytesIO(b'{"choices": [{"message": {"content": "{\\"status\\": \\"success\\"}"}}]}')
            return fp

        with patch("resumes.services.llm_service.urlopen", side_effect=mock_urlopen):
            res = service.generate_structured("test", {})
            self.assertEqual(res, {"status": "success"})
            self.assertEqual(attempts, ["dahl_1", "dahl_2"])


class AdminTestAIEndpointTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            name="Admin Staff", email="staff-ai-test@example.com", phone="9998887771",
            password="safe-password", is_staff=True,
        )
        self.normal_user = User.objects.create_user(
            name="Normal User", email="normal-user@example.com", phone="9998887772",
            password="safe-password", is_staff=False,
        )
        self.client = APIClient()

    def test_forbidden_for_non_staff(self):
        self.client.force_authenticate(user=self.normal_user)
        response = self.client.post("/api/admin/config/test-ai/", {"provider": "gemini"})
        self.assertEqual(response.status_code, 403)

    def test_staff_can_test_gemini(self):
        self.client.force_authenticate(user=self.staff_user)
        with patch("resumes.services.llm_service.GeminiLLMService.probe_model", return_value=(True, "answered in 0.2s")):
            response = self.client.post("/api/admin/config/test-ai/", {
                "provider": "gemini",
                "api_key": "test_token_1, test_token_2",
                "model": "gemini-3.8-flash",
            })
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data["ok"])
            self.assertEqual(response.data["provider"], "gemini")
            self.assertEqual(response.data["token_count"], 2)
            self.assertEqual(response.data["active_tokens"], 2)
            self.assertIn("responded successfully", response.data["message"])
