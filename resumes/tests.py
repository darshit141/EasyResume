import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from authentication.models import User
from resumes.services.llm_service import (
    GeminiLLMService,
    GroqLLMService,
    LLMEmptyResponseError,
    LLMInvalidResponseError,
    LLMModelUnavailableError,
    LLMService,
    LLMSchemaValidationError,
    LLMTimeoutError,
    OllamaLLMService,
    _to_gemini_schema,
)
from resumes.services.resume_parser_service import ResumeParserService
from resumes.services.standard_resume_parser_service import StandardResumeParserService
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
        "summary": None, "skills": ["Python"], "experience": ["Developer"],
        "education": [], "projects": [], "certifications": [],
        "achievements": [], "languages": [],
    }

    def test_parses_valid_structured_response_without_ollama(self):
        parsed = ResumeParserService(FakeLLMService(self.valid_data)).parse("resume")
        self.assertEqual(parsed, self.valid_data)

    def test_rejects_schema_mismatch(self):
        invalid = {**self.valid_data, "skills": "Python"}
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
        self.assertIn("Backend Engineer at Acme Corp (2019-2024)", validated["experience"])
        self.assertIn("BS Computer Science, State University (2019)", validated["education"])


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


class GeminiLLMServiceTests(SimpleTestCase):
    def make_service(self, api_key="test-key", model="gemini-2.0-flash"):
        return GeminiLLMService(
            api_key=api_key,
            model=model,
            timeout=5,
            max_retries=0,
            temperature=0,
            base_url="https://generativelanguage.googleapis.com/v1beta",
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

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            set(response.data),
            {"id", "file", "original_filename", "status", "ats_score", "created_at"},
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

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "LLM_TIMEOUT")

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
        self.assertEqual(first_response.status_code, 201)

        second_upload = SimpleUploadedFile(
            "resume2.pdf", b"placeholder2", content_type="application/pdf",
        )
        with patch("resumes.services.resume_upload_service.TextExtractor.extract_text", return_value=_SAMPLE_RESUME_TEXT), patch(
            "resumes.services.resume_upload_service.ResumeParser.parse", side_effect=LLMTimeoutError()
        ):
            second_response = client.post("/api/resumes/upload/", {"file": second_upload}, format="multipart")
        self.assertEqual(second_response.status_code, 503)

        view_response = client.get("/api/resumes/view/")
        self.assertEqual(view_response.status_code, 200)
        self.assertEqual(view_response.data["status"], "completed")
        self.assertEqual(view_response.data["parsed_data"], good_parsed)


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
