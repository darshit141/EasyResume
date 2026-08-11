from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobResumeCuration, Resume, ResumeVersion
from resumes.tests_job_curation import (
    VALID_REQUEST,
    gap_analysis,
    gap_item,
    job_analysis_for,
)
from resumes.tests_resume_curation import (
    CANDIDATE_PARSED_DATA,
    QueuedLLMService,
    valid_tailored_result,
)


class MissingInformationReviewAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe",
            email="jane-missing@example.com",
            phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe",
            email="john-missing@example.com",
            phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()

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

    def missing_info_url(self, curation):
        return f"/api/resumes/job-curations/{curation.id}/missing-information/"

    def edit_url(self):
        return "/api/resumes/edit/"

    def tailored_resume_url(self, curation):
        return f"/api/resumes/job-curations/{curation.id}/tailored-resume/"

    def test_missing_information_returned_correctly(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(
                required_skills=["Java"],
                cloud_technologies=["AWS"],
            ),
            gap_items=[
                gap_item("Java", "required_skill", "MATCHED", ["Java"]),
                gap_item("AWS", "cloud_technology", "MISSING", []),
            ],
        )
        self.authenticate()

        response = self.client.get(self.missing_info_url(curation))

        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data["strong_matches"][0]["requirement"], "Java")
        missing = data["missing_requirements"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["requirement"], "AWS")
        self.assertEqual(missing[0]["profile_section"], "skills")
        self.assertEqual(
            missing[0]["message"],
            "AWS is listed as a job requirement but is not present in your current profile.",
        )
        self.assertNotIn("Add AWS to your resume", missing[0]["guidance"])
        self.assertIn("If you genuinely have AWS", missing[0]["guidance"])
        self.assertIn("current skill gap", missing[0]["guidance"])
        self.assertEqual(data["suggested_profile_sections"], ["skills"])

    def test_existing_profile_information_preserved(self):
        original_data = {"skills": ["Java"], "experience": ["Backend Engineer"]}
        resume = self.create_resume(original_data)
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )
        self.authenticate()

        response = self.client.get(self.missing_info_url(curation))
        resume.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(resume.parsed_data, original_data)

    def test_user_can_update_legitimate_missing_information(self):
        self.create_resume({"skills": ["Java"]})
        self.authenticate()
        updated_data = {"skills": ["Java", "AWS"], "summary": "Backend engineer."}

        response = self.client.put(
            self.edit_url(),
            {"parsed_data": updated_data},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("regenerate your resume", response.data["message"])
        resume = Resume.objects.filter(user=self.user).order_by("-id").first()
        self.assertEqual(resume.parsed_data, updated_data)
        self.assertEqual(ResumeVersion.objects.filter(resume=resume).count(), 1)

    def test_unauthenticated_profile_update_is_rejected(self):
        self.create_resume({"skills": ["Java"]})

        response = self.client.put(
            self.edit_url(),
            {"parsed_data": {"skills": ["Java", "AWS"]}},
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_cross_user_updates_are_isolated(self):
        self.create_resume({"skills": ["Java"]}, user=self.user)
        self.create_resume({"skills": ["Python"]}, user=self.other_user)
        self.authenticate(self.other_user)

        response = self.client.put(
            self.edit_url(),
            {"parsed_data": {"skills": ["Python", "Django"]}},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        my_resume = Resume.objects.filter(user=self.user).order_by("-id").first()
        other_resume = Resume.objects.filter(user=self.other_user).order_by("-id").first()
        self.assertEqual(my_resume.parsed_data, {"skills": ["Java"]})
        self.assertEqual(other_resume.parsed_data, {"skills": ["Python", "Django"]})

    def test_missing_information_review_is_read_only_for_llm_when_cached(self):
        self.create_resume(CANDIDATE_PARSED_DATA)
        curation = self.create_curation(
            analysis_data=job_analysis_for(cloud_technologies=["AWS"]),
            gap_items=[gap_item("AWS", "cloud_technology", "MISSING", [])],
        )
        self.authenticate()
        fake_llm = QueuedLLMService(results=[])

        with patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=fake_llm,
        ):
            response = self.client.get(self.missing_info_url(curation))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_llm.call_count, 0)

    def test_regeneration_reflects_updated_profile(self):
        self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(cloud_technologies=["AWS"]),
            gap_items=[gap_item("AWS", "cloud_technology", "MISSING", [])],
        )
        self.authenticate()

        stale_response = self.client.get(self.missing_info_url(curation))
        self.assertEqual(len(stale_response.data["missing_requirements"]), 1)

        edit_response = self.client.put(
            self.edit_url(),
            {"parsed_data": {"skills": ["Java", "AWS"]}},
            format="json",
        )
        self.assertEqual(edit_response.status_code, 200)

        fresh_gap_llm = QueuedLLMService(results=[
            gap_analysis([gap_item("AWS", "cloud_technology", "MATCHED", ["AWS"])])
        ])
        with patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=fresh_gap_llm,
        ):
            refreshed_response = self.client.get(
                f"{self.missing_info_url(curation)}?regenerate=true"
            )

        self.assertEqual(refreshed_response.status_code, 200)
        self.assertEqual(refreshed_response.data["missing_requirements"], [])
        self.assertEqual(
            refreshed_response.data["strong_matches"][0]["requirement"], "AWS"
        )

        tailored_llm = QueuedLLMService(results=[
            valid_tailored_result(
                summary="Backend engineer skilled in Java and AWS.",
                skills=["AWS"],
                experience=[],
                projects=[],
                education=[],
                certifications=[],
            )
        ])
        second_gap_llm = QueuedLLMService(results=[
            gap_analysis([gap_item("AWS", "cloud_technology", "MATCHED", ["AWS"])])
        ])
        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=tailored_llm,
        ), patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=second_gap_llm,
        ):
            tailored_response = self.client.post(
                self.tailored_resume_url(curation),
                {"regenerate": True},
                format="json",
            )

        self.assertEqual(tailored_response.status_code, 200)
        self.assertEqual(
            tailored_response.data["tailored_resume_data"]["skills"], ["AWS"]
        )

    def test_no_ai_generated_information_is_written_into_profile(self):
        resume = self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )
        self.authenticate()
        fake_llm = QueuedLLMService(results=[
            valid_tailored_result(
                summary="Backend engineer skilled in Java.",
                skills=["Java"],
                experience=[],
                projects=[],
                education=[],
                certifications=[],
            )
        ])

        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=fake_llm,
        ):
            response = self.client.post(
                self.tailored_resume_url(curation), {}, format="json"
            )

        self.assertEqual(response.status_code, 200)
        resume.refresh_from_db()
        self.assertEqual(resume.parsed_data, {"skills": ["Java"]})

    def test_fabricated_tailored_resume_content_is_not_persisted_anywhere(self):
        resume = self.create_resume({"skills": ["Java"]})
        curation = self.create_curation(
            analysis_data=job_analysis_for(required_skills=["Java"]),
            gap_items=[gap_item("Java", "required_skill", "MATCHED", ["Java"])],
        )
        self.authenticate()
        bad_result = valid_tailored_result(
            summary="Backend engineer skilled in Java.",
            skills=["Java", "AWS"],
            experience=[],
            projects=[],
            education=[],
            certifications=[],
        )
        fake_llm = QueuedLLMService(results=[bad_result, bad_result])

        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=fake_llm,
        ):
            response = self.client.post(
                self.tailored_resume_url(curation), {}, format="json"
            )

        self.assertEqual(response.status_code, 502)
        resume.refresh_from_db()
        curation.refresh_from_db()
        self.assertEqual(resume.parsed_data, {"skills": ["Java"]})
        self.assertEqual(curation.tailored_resume_data, {})
