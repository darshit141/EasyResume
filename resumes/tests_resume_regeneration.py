from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobResumeCuration, JobResumeCurationVersion, Resume
from resumes.services.llm_service import LLMModelUnavailableError
from resumes.tests_job_curation import VALID_REQUEST, gap_analysis, gap_item, job_analysis_for
from resumes.tests_resume_curation import QueuedLLMService, valid_tailored_result


def tailored(summary, **overrides):
    result = {
        "summary": summary,
        "skills": [],
        "experience": [],
        "projects": [],
        "education": [],
        "certifications": [],
    }
    result.update(overrides)
    return result


class JobResumeCurationRegenerationAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe",
            email="jane-regen@example.com",
            phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe",
            email="john-regen@example.com",
            phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()
        from billing.models import Subscription
        Subscription.objects.create(user=self.user, plan_id="pro", status="active")
        Subscription.objects.create(user=self.other_user, plan_id="pro", status="active")

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.user)

    def create_resume(self, parsed_data, user=None):
        return Resume.objects.create(
            user=user or self.user,
            file=SimpleUploadedFile("resume.pdf", b"resume"),
            original_filename="resume.pdf",
            status="completed",
            parsed_data=parsed_data,
        )

    def create_curation(self, analysis_data, gap_items, user=None):
        return JobResumeCuration.objects.create(
            user=user or self.user,
            job_title="Senior Java Backend Engineer",
            job_description=VALID_REQUEST["job_description"],
            status="completed",
            analysis_data=analysis_data,
            gap_analysis_data=gap_analysis(gap_items),
        )

    def tailored_url(self, curation):
        return f"/api/resumes/job-curations/{curation.id}/tailored-resume/"

    def versions_url(self, curation):
        return f"/api/resumes/job-curations/{curation.id}/tailored-resume/versions/"

    def version_detail_url(self, curation, version):
        return (
            f"/api/resumes/job-curations/{curation.id}/tailored-resume/"
            f"versions/{version.id}/"
        )

    def edit_url(self):
        return "/api/resumes/edit/"

    def generate(self, curation, tailored_result, payload=None):
        self.authenticate()
        fake_llm = QueuedLLMService(results=[tailored_result])
        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=fake_llm,
        ):
            response = self.client.post(
                self.tailored_url(curation), payload or {}, format="json"
            )
        return response, fake_llm

    def regenerate(self, curation, tailored_result, gap_result):
        self.authenticate()
        tailored_llm = QueuedLLMService(results=[tailored_result])
        gap_llm = QueuedLLMService(results=[gap_result])
        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=tailored_llm,
        ), patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=gap_llm,
        ):
            response = self.client.post(
                self.tailored_url(curation), {"regenerate": True}, format="json"
            )
        return response, tailored_llm, gap_llm

    def test_regeneration_with_unchanged_profile_creates_a_consistent_new_version(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )

        first_response, _ = self.generate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
        )
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.data["tailored_resume_version"]["version"], 1)

        second_response, _, _ = self.regenerate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
            gap_analysis([gap_item("Java", "required_skill", "MATCHED", ["Java"])]),
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.data["tailored_resume_version"]["version"], 2)
        self.assertEqual(second_response.data["tailored_resume_data"]["skills"], ["Java"])
        self.assertEqual(
            JobResumeCurationVersion.objects.filter(curation=curation).count(), 2
        )

    def test_regeneration_after_adding_legitimate_information(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(cloud_technologies=["AWS"]),
            gap_items=[gap_item("AWS", "cloud_technology", "MISSING", [])],
        )

        first_response, _ = self.generate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
        )
        self.assertNotIn("AWS", first_response.data["tailored_resume_data"]["skills"])

        edit_response = self.client.put(
            self.edit_url(),
            {"parsed_data": {"skills": ["Java", "AWS"]}},
            format="json",
        )
        self.assertEqual(edit_response.status_code, 200)

        second_response, _, _ = self.regenerate(
            curation,
            tailored(summary="Backend engineer skilled in Java and AWS.", skills=["Java", "AWS"]),
            gap_analysis([gap_item("AWS", "cloud_technology", "MATCHED", ["AWS"])]),
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertIn("AWS", second_response.data["tailored_resume_data"]["skills"])

    def test_previously_missing_skill_becomes_matched_only_after_profile_update(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(cloud_technologies=["AWS"]),
            gap_items=[gap_item("AWS", "cloud_technology", "MISSING", [])],
        )
        first_response, _ = self.generate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
        )
        self.assertEqual(
            first_response.data["gap_review"]["missing_requirements"][0]["requirement"],
            "AWS",
        )

        # Attempting to regenerate again WITHOUT updating the profile: AWS must
        # remain a skill gap, never silently promoted to matched.
        stale_response, _, _ = self.regenerate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
            gap_analysis([gap_item("AWS", "cloud_technology", "MISSING", [])]),
        )
        self.assertEqual(
            stale_response.data["gap_review"]["missing_requirements"][0]["requirement"],
            "AWS",
        )

        edit_response = self.client.put(
            self.edit_url(),
            {"parsed_data": {"skills": ["Java", "AWS"]}},
            format="json",
        )
        self.assertEqual(edit_response.status_code, 200)

        updated_response, _, _ = self.regenerate(
            curation,
            tailored(summary="Backend engineer skilled in Java and AWS.", skills=["Java", "AWS"]),
            gap_analysis([gap_item("AWS", "cloud_technology", "MATCHED", ["AWS"])]),
        )

        self.assertEqual(updated_response.data["gap_review"]["missing_requirements"], [])
        self.assertEqual(
            updated_response.data["gap_review"]["strong_matches"][0]["requirement"],
            "AWS",
        )

    def test_regeneration_after_modifying_existing_information(self):
        self.create_resume({
            "skills": ["Java"],
            "experience": [
                "Backend Engineer at Acme Corp - 3 years leading Java projects.",
            ],
        })
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )

        first_response, _ = self.generate(
            curation,
            tailored(
                summary="Backend engineer skilled in Java.",
                skills=["Java"],
                experience=[{
                    "source_index": 0,
                    "tailored_text": (
                        "Backend Engineer at Acme Corp - 3 years leading Java projects."
                    ),
                }],
            ),
        )
        self.assertIn(
            "3 years",
            first_response.data["tailored_resume_data"]["experience"][0]["tailored_text"],
        )

        edit_response = self.client.put(
            self.edit_url(),
            {
                "parsed_data": {
                    "skills": ["Java"],
                    "experience": [
                        "Backend Engineer at Acme Corp - 5 years leading Java projects.",
                    ],
                }
            },
            format="json",
        )
        self.assertEqual(edit_response.status_code, 200)

        second_response, _, _ = self.regenerate(
            curation,
            tailored(
                summary="Backend engineer skilled in Java.",
                skills=["Java"],
                experience=[{
                    "source_index": 0,
                    "tailored_text": (
                        "Backend Engineer at Acme Corp - 5 years leading Java projects."
                    ),
                }],
            ),
            gap_analysis([gap_item("Java", "required_skill", "MATCHED", ["Java"])]),
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertIn(
            "5 years",
            second_response.data["tailored_resume_data"]["experience"][0]["tailored_text"],
        )

    def test_ai_cannot_directly_modify_candidate_profile(self):
        resume = self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )

        response, _ = self.generate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
        )
        self.assertEqual(response.status_code, 200)

        resume.refresh_from_db()
        self.assertEqual(resume.parsed_data, {"skills": ["Java"]})

    def test_previous_version_remains_intact_after_regeneration(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )

        first_response, _ = self.generate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
        )
        first_version_id = first_response.data["tailored_resume_version"]["id"]

        second_response, _, _ = self.regenerate(
            curation,
            tailored(summary="Backend engineer skilled only in Java.", skills=["Java"]),
            gap_analysis([gap_item("Java", "required_skill", "MATCHED", ["Java"])]),
        )
        second_version_id = second_response.data["tailored_resume_version"]["id"]

        self.assertNotEqual(first_version_id, second_version_id)
        first_version = JobResumeCurationVersion.objects.get(id=first_version_id)
        self.assertEqual(
            first_version.tailored_resume_data["summary"],
            "Backend engineer skilled in Java.",
        )
        self.assertEqual(
            JobResumeCurationVersion.objects.filter(curation=curation).count(), 2
        )

    def test_ai_failure_does_not_corrupt_previous_resume(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )

        first_response, _ = self.generate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
        )
        self.assertEqual(first_response.status_code, 200)

        self.authenticate()
        failing_llm = QueuedLLMService(exc=LLMModelUnavailableError())
        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=failing_llm,
        ), patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=failing_llm,
        ):
            failed_response = self.client.post(
                self.tailored_url(curation), {"regenerate": True}, format="json"
            )

        self.assertEqual(failed_response.status_code, 503)
        curation.refresh_from_db()
        self.assertEqual(curation.tailored_resume_data["skills"], ["Java"])
        self.assertEqual(
            JobResumeCurationVersion.objects.filter(curation=curation).count(), 1
        )

    def test_unauthenticated_regeneration_is_rejected(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )

        response = self.client.post(self.tailored_url(curation), {}, format="json")

        self.assertEqual(response.status_code, 401)

    def test_cross_user_regeneration_is_rejected(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )
        self.client.force_authenticate(user=self.other_user)

        response = self.client.post(self.tailored_url(curation), {}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            JobResumeCurationVersion.objects.filter(curation=curation).count(), 0
        )

    def test_version_history_is_scoped_to_owner(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )
        response, _ = self.generate(
            curation,
            tailored(summary="Backend engineer skilled in Java.", skills=["Java"]),
        )
        version_id = response.data["tailored_resume_version"]["id"]
        version = JobResumeCurationVersion.objects.get(id=version_id)

        list_response = self.client.get(self.versions_url(curation))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)

        detail_response = self.client.get(self.version_detail_url(curation, version))
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["tailored_resume_data"]["skills"], ["Java"])

        self.client.force_authenticate(user=self.other_user)
        cross_user_list_response = self.client.get(self.versions_url(curation))
        self.assertEqual(len(cross_user_list_response.data), 0)

        cross_user_detail_response = self.client.get(
            self.version_detail_url(curation, version)
        )
        self.assertEqual(cross_user_detail_response.status_code, 404)
