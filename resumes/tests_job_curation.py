from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobResumeCuration, Resume
from resumes.services.llm_service import (
    LLMModelUnavailableError,
    LLMService,
)


class FakeJobAnalysisLLMService(LLMService):
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc

    def generate_structured(self, prompt, schema):
        if self.exc:
            raise self.exc
        return self.result


class FakeGapAnalysisLLMService(FakeJobAnalysisLLMService):
    pass


VALID_JOB_ANALYSIS = {
    "required_skills": ["Python", "REST APIs"],
    "preferred_skills": ["Django REST Framework"],
    "technologies": ["Python"],
    "frameworks": ["Django"],
    "databases": ["PostgreSQL"],
    "cloud_technologies": ["AWS"],
    "tools": ["Docker", "Git"],
    "soft_skills": ["Communication"],
    "responsibilities": ["Build backend services"],
    "experience_requirements": ["5+ years backend engineering"],
    "education_requirements": ["Bachelor's degree or equivalent experience"],
    "certification_requirements": [],
    "important_keywords": ["backend", "API", "scalable"],
    "ats_relevant_terms": ["Python", "Django", "PostgreSQL"],
    "seniority_level": "Senior",
    "other_explicit_requirements": ["On-call participation"],
}


VALID_REQUEST = {
    "job_title": "Senior Backend Engineer",
    "job_description": (
        "We need a Senior Backend Engineer with Python, Django, PostgreSQL, "
        "Docker, AWS, REST API design, and strong communication skills."
    ),
    "curate_resume": True,
}


def gap_item(requirement, category, status, evidence=None):
    return {
        "requirement": requirement,
        "category": category,
        "match_status": status,
        "candidate_evidence": evidence or [],
        "confidence": 1,
        "reasoning": "Test reasoning.",
        "recommendation": "Test recommendation.",
    }


def gap_analysis(items):
    return {
        "overall_match_percentage": 0,
        "matched_requirements": [
            item for item in items if item["match_status"] == "MATCHED"
        ],
        "partially_matched_requirements": [
            item for item in items if item["match_status"] == "PARTIALLY_MATCHED"
        ],
        "missing_requirements": [
            item for item in items if item["match_status"] == "MISSING"
        ],
        "not_verifiable_requirements": [
            item for item in items if item["match_status"] == "NOT_VERIFIABLE"
        ],
        "strengths": ["Verified backend skills"],
        "weaknesses_gaps": ["Some requirements are missing."],
        "important_missing_information": [
            "You can update your profile with missing information you genuinely have."
        ],
        "recommendations_for_profile_improvement": [
            {
                "recommendation": "Add missing profile details only when they are genuine.",
                "recommendation_type": "MISSING_PROFILE_INFORMATION",
            },
            {
                "recommendation": "Acquire missing skills before claiming them.",
                "recommendation_type": "SKILL_TO_ACQUIRE",
            },
        ],
        "ats_keyword_coverage": {
            "covered_keywords": [],
            "missing_keywords": [],
            "coverage_percentage": 0,
        },
        "interview_readiness_observations": [
            "Prepare examples only from verified experience."
        ],
    }


def poll_job(client, response):
    """Every AI-backed endpoint in this file now queues a background job and
    returns 202 instead of blocking. CELERY_TASK_ALWAYS_EAGER (set under
    `manage.py test`) makes that task run synchronously as part of the
    `client.post`/`client.get` call above, so by the time this function
    runs the job is already in its terminal state — no real polling loop
    needed in tests."""
    assert response.status_code == 202, response.data
    job_id = response.data["id"]
    return client.get(f"/api/ai-jobs/{job_id}/")


def job_analysis_for(**overrides):
    data = {
        "required_skills": [],
        "preferred_skills": [],
        "technologies": [],
        "frameworks": [],
        "databases": [],
        "cloud_technologies": [],
        "tools": [],
        "soft_skills": [],
        "responsibilities": [],
        "experience_requirements": [],
        "education_requirements": [],
        "certification_requirements": [],
        "important_keywords": [],
        "ats_relevant_terms": [],
        "seniority_level": "",
        "other_explicit_requirements": [],
    }
    data.update(overrides)
    return data


class JobCurationAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe",
            email="jane@example.com",
            phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe",
            email="john@example.com",
            phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()
        self.url = "/api/resumes/job-curations/analyze/"

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.user)

    def test_successful_structured_analysis(self):
        self.authenticate()
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(VALID_JOB_ANALYSIS),
        ):
            response = self.client.post(self.url, VALID_REQUEST, format="json")

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "completed")
        curation_id = job_status.data["result"]["curation_id"]

        curation_response = self.client.get(f"/api/resumes/job-curations/{curation_id}/")
        self.assertEqual(curation_response.data["status"], "completed")
        self.assertEqual(curation_response.data["analysis_data"], VALID_JOB_ANALYSIS)
        self.assertEqual(JobResumeCuration.objects.count(), 1)
        self.assertEqual(JobResumeCuration.objects.get().user, self.user)

    def test_missing_job_title_is_rejected(self):
        self.authenticate()
        payload = {**VALID_REQUEST}
        payload.pop("job_title")

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_title", response.data)

    def test_missing_job_description_is_rejected(self):
        self.authenticate()
        payload = {**VALID_REQUEST}
        payload.pop("job_description")

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_description", response.data)

    def test_curate_resume_false_skips_ai_and_persistence(self):
        self.authenticate()
        payload = {**VALID_REQUEST, "curate_resume": False}
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service"
        ) as get_llm_service:
            response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["curate_resume"], False)
        self.assertEqual(JobResumeCuration.objects.count(), 0)
        get_llm_service.assert_not_called()

    def test_ollama_unavailable_returns_service_error(self):
        self.authenticate()
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(
                exc=LLMModelUnavailableError()
            ),
        ):
            response = self.client.post(self.url, VALID_REQUEST, format="json")

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "failed")
        self.assertEqual(job_status.data["error_code"], "LLM_MODEL_UNAVAILABLE")
        self.assertEqual(JobResumeCuration.objects.get().status, "failed")

    def test_invalid_ai_response_is_rejected(self):
        self.authenticate()
        invalid_analysis = {**VALID_JOB_ANALYSIS}
        invalid_analysis["required_skills"] = "Python"
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(invalid_analysis),
        ):
            response = self.client.post(self.url, VALID_REQUEST, format="json")

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "failed")
        self.assertEqual(job_status.data["error_code"], "LLM_SCHEMA_VALIDATION_FAILED")
        self.assertEqual(JobResumeCuration.objects.get().status, "failed")

    def test_authentication_is_required(self):
        response = self.client.post(self.url, VALID_REQUEST, format="json")

        self.assertEqual(response.status_code, 401)

    def test_list_is_authenticated_and_scoped_to_user(self):
        JobResumeCuration.objects.create(
            user=self.user,
            job_title="Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=VALID_JOB_ANALYSIS,
        )
        JobResumeCuration.objects.create(
            user=self.other_user,
            job_title="Frontend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=VALID_JOB_ANALYSIS,
        )
        self.authenticate()

        response = self.client.get("/api/resumes/job-curations/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["job_title"], "Backend Engineer")

    def test_cross_user_detail_access_is_prevented(self):
        curation = JobResumeCuration.objects.create(
            user=self.user,
            job_title="Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=VALID_JOB_ANALYSIS,
        )
        self.authenticate(self.other_user)

        response = self.client.get(f"/api/resumes/job-curations/{curation.id}/")

        self.assertEqual(response.status_code, 404)

    def test_job_url_is_saved_and_returned(self):
        self.authenticate()
        payload = {**VALID_REQUEST, "job_url": "https://www.linkedin.com/jobs/view/backend-engineer"}
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(VALID_JOB_ANALYSIS),
        ):
            response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 202)
        poll_job(self.client, response)
        self.assertEqual(
            JobResumeCuration.objects.get().job_url, "https://www.linkedin.com/jobs/view/backend-engineer"
        )

    def test_job_url_is_optional(self):
        self.authenticate()
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(VALID_JOB_ANALYSIS),
        ):
            response = self.client.post(self.url, VALID_REQUEST, format="json")

        self.assertEqual(response.status_code, 202)
        poll_job(self.client, response)
        self.assertEqual(JobResumeCuration.objects.get().job_url, "")

    def test_malformed_job_url_is_rejected(self):
        self.authenticate()
        payload = {**VALID_REQUEST, "job_url": "not a url"}

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_url", response.data)

    def test_job_url_is_never_fetched_by_the_server(self):
        # The whole point of storing it as a plain reference: no outbound
        # request is ever made to the URL itself.
        self.authenticate()
        payload = {**VALID_REQUEST, "job_url": "https://www.linkedin.com/jobs/view/backend-engineer"}
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(VALID_JOB_ANALYSIS),
        ), patch("urllib.request.urlopen") as mock_urlopen:
            response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 202)
        poll_job(self.client, response)
        mock_urlopen.assert_not_called()

    def test_url_on_an_unsupported_domain_is_rejected(self):
        self.authenticate()
        payload = {**VALID_REQUEST, "job_url": "https://random-scam-site.example/jobs/1"}

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_url", response.data)
        self.assertEqual(JobResumeCuration.objects.count(), 0)

    def test_url_on_a_subdomain_of_an_allowed_domain_is_accepted(self):
        self.authenticate()
        payload = {**VALID_REQUEST, "job_url": "https://boards.greenhouse.io/acme/jobs/42"}
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(VALID_JOB_ANALYSIS),
        ):
            response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 202)
        poll_job(self.client, response)
        self.assertEqual(
            JobResumeCuration.objects.get().job_url, "https://boards.greenhouse.io/acme/jobs/42"
        )

    def test_domain_match_is_case_insensitive(self):
        self.authenticate()
        payload = {**VALID_REQUEST, "job_url": "https://WWW.LINKEDIN.COM/jobs/view/1"}
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(VALID_JOB_ANALYSIS),
        ):
            response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 202)
        poll_job(self.client, response)

    def test_a_lookalike_domain_is_not_accepted_as_a_suffix_match(self):
        # "notlinkedin.com" must not pass just because it ends in a
        # substring that looks like an allowed domain.
        self.authenticate()
        payload = {**VALID_REQUEST, "job_url": "https://notlinkedin.com/jobs/1"}

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)

    @override_settings(ALLOWED_JOB_URL_DOMAINS=[])
    def test_empty_configured_list_disables_the_restriction(self):
        self.authenticate()
        payload = {**VALID_REQUEST, "job_url": "https://random-scam-site.example/jobs/1"}
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(VALID_JOB_ANALYSIS),
        ):
            response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 202)
        poll_job(self.client, response)


class JobGapAnalysisAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe",
            email="jane-gap@example.com",
            phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe",
            email="john-gap@example.com",
            phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.user)

    def create_resume(self, parsed_data):
        return Resume.objects.create(
            user=self.user,
            file=SimpleUploadedFile("resume.pdf", b"resume"),
            original_filename="resume.pdf",
            status="completed",
            parsed_data=parsed_data,
        )

    def create_curation(self, analysis_data):
        return JobResumeCuration.objects.create(
            user=self.user,
            job_title="Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=analysis_data,
        )

    def gap_url(self, curation):
        return f"/api/resumes/job-curations/{curation.id}/gap-analysis/"

    def run_gap(self, curation, ai_result):
        """Queues the (cold-path) gap analysis job and returns the raw 202/
        error response, without following up — use this when the test wants
        to inspect the queued/failed job itself."""
        self.authenticate()
        with patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=FakeGapAnalysisLLMService(ai_result),
        ):
            return self.client.get(self.gap_url(curation))

    def run_gap_and_fetch(self, curation, ai_result):
        """Queues the job, waits for it (eager mode makes this synchronous
        under test), then re-fetches the same endpoint — which now finds
        the result cached and returns it directly (200) — exactly what the
        frontend does after polling to completion."""
        queued = self.run_gap(curation, ai_result)
        poll_job(self.client, queued)
        return self.client.get(self.gap_url(curation))

    def test_exact_skill_match(self):
        self.create_resume({"skills": ["Python"]})
        curation = self.create_curation(job_analysis_for(required_skills=["Python"]))
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([gap_item("Python", "required_skill", "MATCHED", ["Python"])]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["gap_analysis_data"]["matched_requirements"][0]["requirement"],
            "Python",
        )

    def test_case_insensitive_skill_match(self):
        self.create_resume({"skills": ["python"]})
        curation = self.create_curation(job_analysis_for(required_skills=["Python"]))
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([gap_item("Python", "required_skill", "MATCHED", ["python"])]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["gap_analysis_data"]["matched_requirements"][0]["match_status"],
            "MATCHED",
        )

    def test_partial_experience_match_is_enforced(self):
        self.create_resume({"experience": ["Backend Engineer - 3 years"]})
        curation = self.create_curation(
            job_analysis_for(experience_requirements=["5 years backend experience"])
        )
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([
                gap_item(
                    "5 years backend experience",
                    "experience_requirement",
                    "MATCHED",
                    ["Backend Engineer - 3 years"],
                )
            ]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["gap_analysis_data"]["partially_matched_requirements"][0]["match_status"],
            "PARTIALLY_MATCHED",
        )

    def test_missing_skill_is_not_inferred_from_other_tools(self):
        self.create_resume({"skills": ["Java", "Spring Boot", "Kafka", "Docker"]})
        curation = self.create_curation(job_analysis_for(cloud_technologies=["AWS"]))
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([gap_item("AWS", "cloud_technology", "MATCHED", ["Docker"])]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["gap_analysis_data"]["missing_requirements"][0]["requirement"],
            "AWS",
        )

    def test_missing_experience_is_reported(self):
        self.create_resume({"skills": ["Python"], "experience": ["Backend Engineer"]})
        curation = self.create_curation(
            job_analysis_for(experience_requirements=["5 years backend experience"])
        )
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([
                gap_item(
                    "5 years backend experience",
                    "experience_requirement",
                    "MATCHED",
                    ["Backend Engineer"],
                )
            ]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["gap_analysis_data"]["missing_requirements"][0]["category"],
            "experience_requirement",
        )

    def test_missing_certification_is_reported(self):
        self.create_resume({"certifications": []})
        curation = self.create_curation(
            job_analysis_for(certification_requirements=["AWS Certified Developer"])
        )
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([
                gap_item(
                    "AWS Certified Developer",
                    "certification_requirement",
                    "MATCHED",
                    ["AWS"],
                )
            ]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["gap_analysis_data"]["missing_requirements"][0]["requirement"],
            "AWS Certified Developer",
        )

    def test_missing_education_is_reported(self):
        self.create_resume({"education": []})
        curation = self.create_curation(
            job_analysis_for(education_requirements=["Bachelor's degree"])
        )
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([
                gap_item(
                    "Bachelor's degree",
                    "education_requirement",
                    "MATCHED",
                    ["Bachelor"],
                )
            ]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["gap_analysis_data"]["missing_requirements"][0]["category"],
            "education_requirement",
        )

    def test_candidate_with_no_matching_skills(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(job_analysis_for(required_skills=["Python"]))
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([gap_item("Python", "required_skill", "MATCHED", ["Java"])]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(response.data["gap_analysis_data"]["matched_requirements"]),
            0,
        )

    def test_candidate_with_all_matching_skills(self):
        self.create_resume({"skills": ["Python", "Django", "AWS"]})
        curation = self.create_curation(
            job_analysis_for(required_skills=["Python", "Django", "AWS"])
        )
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([
                gap_item("Python", "required_skill", "MATCHED", ["Python"]),
                gap_item("Django", "required_skill", "MATCHED", ["Django"]),
                gap_item("AWS", "required_skill", "MATCHED", ["AWS"]),
            ]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(response.data["gap_analysis_data"]["matched_requirements"]),
            3,
        )
        self.assertEqual(
            response.data["gap_analysis_data"]["overall_match_percentage"],
            100,
        )

    def test_candidate_data_cannot_be_modified_by_ai(self):
        original_data = {"skills": ["Docker"]}
        resume = self.create_resume(original_data)
        curation = self.create_curation(job_analysis_for(cloud_technologies=["AWS"]))
        response = self.run_gap_and_fetch(
            curation,
            gap_analysis([gap_item("AWS", "cloud_technology", "MATCHED", ["AWS"])]),
        )
        resume.refresh_from_db()
        curation.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(resume.parsed_data, original_data)
        self.assertEqual(curation.candidate_data_snapshot["parsed_data"], original_data)
        self.assertEqual(
            response.data["gap_analysis_data"]["missing_requirements"][0]["requirement"],
            "AWS",
        )

    def test_cross_user_gap_analysis_access_is_denied(self):
        self.create_resume({"skills": ["Python"]})
        curation = self.create_curation(job_analysis_for(required_skills=["Python"]))
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(self.gap_url(curation))

        self.assertEqual(response.status_code, 404)

    def test_ai_malformed_gap_response_is_rejected(self):
        self.create_resume({"skills": ["Python"]})
        curation = self.create_curation(job_analysis_for(required_skills=["Python"]))
        response = self.run_gap(curation, {"invalid": []})

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "failed")
        self.assertEqual(job_status.data["error_code"], "LLM_SCHEMA_VALIDATION_FAILED")

    def test_ollama_unavailable_during_gap_analysis(self):
        self.create_resume({"skills": ["Python"]})
        curation = self.create_curation(job_analysis_for(required_skills=["Python"]))
        self.authenticate()
        with patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=FakeGapAnalysisLLMService(
                exc=LLMModelUnavailableError()
            ),
        ):
            response = self.client.get(self.gap_url(curation))

        self.assertEqual(response.status_code, 202)
        job_status = poll_job(self.client, response)
        self.assertEqual(job_status.data["status"], "failed")
        self.assertEqual(job_status.data["error_code"], "LLM_MODEL_UNAVAILABLE")
