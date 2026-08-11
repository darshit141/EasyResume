from unittest.mock import patch
from urllib.error import URLError

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from authentication.models import User
from resumes.services.llm_service import (
    LLMEmptyResponseError,
    LLMInvalidResponseError,
    LLMModelUnavailableError,
    LLMService,
    LLMSchemaValidationError,
    LLMTimeoutError,
    OllamaLLMService,
)
from resumes.services.resume_parser_service import ResumeParserService


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


class ResumeUploadAPICompatibilityTests(TestCase):
    def test_upload_contract_is_unchanged_when_parser_is_mocked(self):
        user = User.objects.create_user(
            name="Jane Doe", email="jane@example.com", phone="1234567890",
            password="safe-password",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        upload = SimpleUploadedFile(
            "resume.pdf", b"placeholder", content_type="application/pdf",
        )
        parsed = ResumeParserServiceTests.valid_data

        with patch("resumes.services.resume_upload_service.TextExtractor.extract_text", return_value="resume"), patch(
            "resumes.services.resume_upload_service.ResumeParser.parse", return_value=parsed
        ):
            response = client.post("/api/resumes/upload/", {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            set(response.data),
            {"id", "file", "original_filename", "status", "ats_score", "created_at"},
        )
