from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobResumeCuration, Resume
from resumes.services.llm_service import LLMModelUnavailableError, LLMService
from resumes.tests_job_curation import (
    VALID_JOB_ANALYSIS,
    VALID_REQUEST,
    gap_analysis,
    gap_item,
)


class QueuedLLMService(LLMService):
    """Fake LLM that returns queued responses in order, or raises a fixed error."""

    def __init__(self, results=None, exc=None):
        self.results = list(results or [])
        self.exc = exc
        self.call_count = 0

    def generate_structured(self, prompt, schema):
        self.call_count += 1
        if self.exc:
            raise self.exc
        if not self.results:
            raise AssertionError("No more queued LLM results were provided.")
        return self.results.pop(0)


CANDIDATE_PARSED_DATA = {
    "personal_information": {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "1234567890",
    },
    "summary": "Backend engineer.",
    "skills": ["Java", "Spring Boot", "Kafka", "Docker", "PostgreSQL"],
    "experience": [
        "Backend Engineer at Acme Corp (2019-2022) - built event-driven "
        "services with Java, Spring Boot, and Kafka.",
        "Software Engineer at Acme Corp - 3 years leading Java and Kafka projects.",
    ],
    "projects": [
        "Order Processing Pipeline - built with Kafka and PostgreSQL for Acme Corp in 2021.",
    ],
    "education": [
        "B.Sc. Computer Science, State University, 2015-2019.",
    ],
    "certifications": [
        "AWS Certified Developer - Associate, 2020.",
    ],
}


def valid_tailored_result(**overrides):
    result = {
        "summary": (
            "Backend engineer with hands-on Java, Spring Boot, and Kafka "
            "experience building reliable services."
        ),
        "skills": ["Java", "Spring Boot", "Kafka"],
        "experience": [
            {
                "source_index": 0,
                "tailored_text": (
                    "Backend Engineer at Acme Corp (2019-2022) - designed "
                    "event-driven services using Java, Spring Boot, and Kafka."
                ),
            }
        ],
        "projects": [
            {
                "source_index": 0,
                "tailored_text": (
                    "Order Processing Pipeline - built with Kafka and "
                    "PostgreSQL for Acme Corp in 2021."
                ),
            }
        ],
        "education": [
            {
                "source_index": 0,
                "tailored_text": "B.Sc. Computer Science, State University, 2015-2019.",
            }
        ],
        "certifications": [
            {
                "source_index": 0,
                "tailored_text": "AWS Certified Developer - Associate, 2020.",
            }
        ],
    }
    result.update(overrides)
    return result


class JobResumeCurationGenerateAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe",
            email="jane-curation@example.com",
            phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe",
            email="john-curation@example.com",
            phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.user)

    def create_resume(self, parsed_data=None, user=None):
        return Resume.objects.create(
            user=user or self.user,
            file=SimpleUploadedFile("resume.pdf", b"resume"),
            original_filename="resume.pdf",
            status="completed",
            parsed_data=parsed_data if parsed_data is not None else CANDIDATE_PARSED_DATA,
        )

    def create_curation(self, gap_items=None, user=None, analysis_data=None):
        return JobResumeCuration.objects.create(
            user=user or self.user,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=analysis_data or VALID_JOB_ANALYSIS,
            gap_analysis_data=gap_analysis(gap_items or [
                gap_item("Java", "required_skill", "MATCHED", ["Java"]),
            ]),
        )

    def url(self, curation):
        return f"/api/resumes/job-curations/{curation.id}/tailored-resume/"

    def generate(self, curation, payload=None, llm_service=None):
        self.authenticate()
        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=llm_service,
        ):
            return self.client.post(self.url(curation), payload or {}, format="json")

    def test_successful_tailored_resume_generation(self):
        self.create_resume()
        curation = self.create_curation()
        fake_llm = QueuedLLMService(results=[valid_tailored_result()])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 200)
        data = response.data["tailored_resume_data"]
        self.assertEqual(data["skills"], ["Java", "Spring Boot", "Kafka"])
        self.assertEqual(
            data["personal_information"]["email"],
            "jane@example.com",
        )
        self.assertEqual(data["experience"][0]["source_index"], 0)
        self.assertIn("tailored_text", data["experience"][0])
        curation.refresh_from_db()
        self.assertTrue(curation.tailored_resume_data)
        self.assertEqual(curation.tailored_resume_error_code, "")

    def test_strong_match_candidate_emphasizes_matched_skills(self):
        self.create_resume()
        curation = self.create_curation(gap_items=[
            gap_item("Java", "required_skill", "MATCHED", ["Java"]),
            gap_item("Spring Boot", "required_skill", "MATCHED", ["Spring Boot"]),
            gap_item("Kafka", "required_skill", "MATCHED", ["Kafka"]),
        ])
        fake_llm = QueuedLLMService(results=[valid_tailored_result()])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data["tailored_resume_data"]["skills"]),
            {"Java", "Spring Boot", "Kafka"},
        )

    def test_candidate_with_missing_requirements_does_not_fabricate(self):
        self.create_resume()
        curation = self.create_curation(gap_items=[
            gap_item("Java", "required_skill", "MATCHED", ["Java"]),
            gap_item("AWS", "cloud_technology", "MISSING", []),
        ])
        fake_llm = QueuedLLMService(results=[valid_tailored_result()])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("AWS", response.data["tailored_resume_data"]["skills"])

    def test_ai_inventing_a_skill_is_rejected(self):
        self.create_resume()
        curation = self.create_curation()
        bad_result = valid_tailored_result(skills=["Java", "AWS"])
        fake_llm = QueuedLLMService(results=[bad_result, bad_result])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "RESUME_CURATION_GROUNDING_FAILED")
        self.assertEqual(fake_llm.call_count, 2)
        curation.refresh_from_db()
        self.assertEqual(curation.tailored_resume_data, {})
        self.assertEqual(
            curation.tailored_resume_error_code,
            "RESUME_CURATION_GROUNDING_FAILED",
        )

    def test_ai_inventing_experience_is_rejected(self):
        self.create_resume()
        curation = self.create_curation()
        bad_result = valid_tailored_result(experience=[
            {
                "source_index": 99,
                "tailored_text": "Principal Engineer at a company not in candidate data.",
            }
        ])
        fake_llm = QueuedLLMService(results=[bad_result, bad_result])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "RESUME_CURATION_GROUNDING_FAILED")
        curation.refresh_from_db()
        self.assertEqual(curation.tailored_resume_data, {})

    def test_ai_inventing_a_project_is_rejected(self):
        self.create_resume()
        curation = self.create_curation()
        bad_result = valid_tailored_result(projects=[
            {
                "source_index": 0,
                "tailored_text": (
                    "Order Processing Pipeline - built with Kubernetes and "
                    "Kafka for Acme Corp in 2021."
                ),
            }
        ])
        fake_llm = QueuedLLMService(results=[bad_result, bad_result])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "RESUME_CURATION_GROUNDING_FAILED")

    def test_ai_changing_dates_is_rejected(self):
        self.create_resume()
        curation = self.create_curation()
        bad_result = valid_tailored_result(experience=[
            {
                "source_index": 0,
                "tailored_text": (
                    "Backend Engineer at Acme Corp (2020-2023) - designed "
                    "event-driven services using Java, Spring Boot, and Kafka."
                ),
            }
        ])
        fake_llm = QueuedLLMService(results=[bad_result, bad_result])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "RESUME_CURATION_GROUNDING_FAILED")

    def test_ai_changing_years_of_experience_is_rejected(self):
        self.create_resume()
        curation = self.create_curation()
        bad_result = valid_tailored_result(experience=[
            {
                "source_index": 1,
                "tailored_text": (
                    "Software Engineer at Acme Corp - 8 years leading Java "
                    "and Kafka projects."
                ),
            }
        ])
        fake_llm = QueuedLLMService(results=[bad_result, bad_result])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "RESUME_CURATION_GROUNDING_FAILED")

    def test_ai_changing_employer_is_rejected(self):
        self.create_resume()
        curation = self.create_curation()
        bad_result = valid_tailored_result(experience=[
            {
                "source_index": 0,
                "tailored_text": (
                    "Backend Engineer at Globex Corp (2019-2022) - designed "
                    "event-driven services using Java, Spring Boot, and Kafka."
                ),
            }
        ])
        fake_llm = QueuedLLMService(results=[bad_result, bad_result])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "RESUME_CURATION_GROUNDING_FAILED")

    def test_retry_recovers_after_stricter_prompt(self):
        self.create_resume()
        curation = self.create_curation()
        bad_result = valid_tailored_result(skills=["Java", "AWS"])
        good_result = valid_tailored_result()
        fake_llm = QueuedLLMService(results=[bad_result, good_result])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_llm.call_count, 2)
        self.assertEqual(
            response.data["tailored_resume_data"]["skills"],
            ["Java", "Spring Boot", "Kafka"],
        )

    def test_ai_malformed_structure_is_rejected(self):
        self.create_resume()
        curation = self.create_curation()
        malformed = valid_tailored_result()
        malformed["skills"] = "Java"
        fake_llm = QueuedLLMService(results=[malformed])

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "LLM_SCHEMA_VALIDATION_FAILED")
        self.assertEqual(fake_llm.call_count, 1)

    def test_ai_unavailable_returns_service_error(self):
        self.create_resume()
        curation = self.create_curation()
        fake_llm = QueuedLLMService(exc=LLMModelUnavailableError())

        response = self.generate(curation, llm_service=fake_llm)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "LLM_MODEL_UNAVAILABLE")
        curation.refresh_from_db()
        self.assertEqual(curation.tailored_resume_error_code, "LLM_MODEL_UNAVAILABLE")

    def test_authentication_is_required(self):
        self.create_resume()
        curation = self.create_curation()

        response = self.client.post(self.url(curation), {}, format="json")

        self.assertEqual(response.status_code, 401)

    def test_cross_user_access_is_denied(self):
        self.create_resume()
        curation = self.create_curation()
        self.client.force_authenticate(user=self.other_user)

        response = self.client.post(self.url(curation), {}, format="json")

        self.assertEqual(response.status_code, 404)

    def test_regeneration_uses_cache_by_default_and_forces_refresh_when_requested(self):
        self.create_resume()
        curation = self.create_curation()
        first_llm = QueuedLLMService(results=[valid_tailored_result()])
        first_response = self.generate(curation, llm_service=first_llm)
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_llm.call_count, 1)

        cached_llm = QueuedLLMService(results=[])
        cached_response = self.generate(curation, llm_service=cached_llm)
        self.assertEqual(cached_response.status_code, 200)
        self.assertEqual(cached_llm.call_count, 0)
        self.assertEqual(
            cached_response.data["tailored_resume_data"]["skills"],
            first_response.data["tailored_resume_data"]["skills"],
        )

        regenerated_result = valid_tailored_result(
            skills=["Java", "Kafka"],
        )
        regenerate_llm = QueuedLLMService(results=[regenerated_result])
        fresh_gap_llm = QueuedLLMService(results=[
            gap_analysis([gap_item("Python", "required_skill", "MISSING", [])])
        ])
        self.authenticate()
        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=regenerate_llm,
        ), patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=fresh_gap_llm,
        ):
            regenerate_response = self.client.post(
                self.url(curation), {"regenerate": True}, format="json"
            )
        self.assertEqual(regenerate_response.status_code, 200)
        self.assertEqual(regenerate_llm.call_count, 1)
        self.assertEqual(fresh_gap_llm.call_count, 1)
        self.assertEqual(
            regenerate_response.data["tailored_resume_data"]["skills"],
            ["Java", "Kafka"],
        )
