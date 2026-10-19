from unittest.mock import MagicMock, patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from authentication.models import User
from resumes.models import JobResumeCuration, Resume
from resumes.prompts import (
    TAILOR_MODE_SYSTEM_BASELINE,
    TAILOR_MODE_REPHRASE_ONLY,
    TAILOR_MODE_FULL_TAILORED,
)
from resumes.services.job_resume_curation_service import JobResumeCurationService


class TailoringModesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Alice Candidate",
            email="alice@example.com",
            phone="1234567890",
            password="safe-password",
        )
        self.resume = Resume.objects.create(
            user=self.user,
            file=SimpleUploadedFile("alice_resume.pdf", b"fake binary content"),
            original_filename="alice_resume.pdf",
            status="completed",
            parsed_data={
                "personal_information": {
                    "name": "Alice Candidate",
                    "email": "alice@example.com",
                    "phone": "1234567890",
                },
                "summary": "Full-stack engineer with 4 years building web services.",
                "skills": ["JavaScript", "Python", "Django", "HTML"],
                "experience": [
                    {
                        "header": "Software Developer - Acme Corp | Jan 2021 - Present",
                        "bullets": [
                            "Built high-throughput REST APIs using Python and Django.",
                            "Optimized database queries and improved response times by 30%.",
                        ],
                    }
                ],
                "projects": [
                    {
                        "header": "Inventory Tracker | 2022",
                        "bullets": ["Developed full-stack tracking app with React and Django."],
                    }
                ],
                "education": [
                    {
                        "header": "B.S. in Computer Science - State University | 2017 - 2021",
                        "bullets": [],
                    }
                ],
                "certifications": [],
                "achievements": [],
                "languages": ["English"],
            },
        )
        self.curation = JobResumeCuration.objects.create(
            user=self.user,
            resume=self.resume,
            job_title="Senior Python Backend Engineer",
            job_description="Seeking a Senior Python Backend Engineer with strong Django, PostgreSQL, and AWS expertise.",
            status="completed",
            analysis_data={
                "required_skills": ["Python", "Django", "PostgreSQL"],
                "preferred_skills": ["AWS", "Docker"],
                "technologies": ["Python", "Django"],
                "frameworks": ["Django"],
                "databases": ["PostgreSQL"],
                "cloud_technologies": ["AWS"],
                "tools": ["Git"],
                "soft_skills": ["Communication"],
                "responsibilities": ["Design microservices"],
                "experience_requirements": ["3+ years"],
                "education_requirements": ["B.S. in CS"],
                "certification_requirements": [],
                "important_keywords": ["REST", "API"],
                "ats_relevant_terms": ["Python", "Django"],
                "seniority_level": "Senior",
                "other_explicit_requirements": [],
            },
            gap_analysis_data={
                "overall_match_percentage": 75.0,
                "matched_requirements": [
                    {
                        "requirement": "Python",
                        "category": "required_skill",
                        "match_status": "MATCHED",
                        "candidate_evidence": ["Python in skills"],
                        "confidence": 1.0,
                        "reasoning": "Explicitly matched.",
                        "recommendation": "",
                    },
                    {
                        "requirement": "Django",
                        "category": "framework",
                        "match_status": "MATCHED",
                        "candidate_evidence": ["Django in skills"],
                        "confidence": 1.0,
                        "reasoning": "Explicitly matched.",
                        "recommendation": "",
                    },
                ],
                "partially_matched_requirements": [],
                "missing_requirements": [
                    {
                        "requirement": "PostgreSQL",
                        "category": "database",
                        "match_status": "MISSING",
                        "candidate_evidence": [],
                        "confidence": 0.0,
                        "reasoning": "Not found in profile.",
                        "recommendation": "Add PostgreSQL if experienced.",
                    },
                    {
                        "requirement": "AWS",
                        "category": "cloud_technology",
                        "match_status": "MISSING",
                        "candidate_evidence": [],
                        "confidence": 0.0,
                        "reasoning": "Not found in profile.",
                        "recommendation": "Learn AWS fundamentals.",
                    },
                ],
            },
        )

    def test_system_baseline_mode_builds_deterministic_ats_resume_without_llm(self):
        service = JobResumeCurationService(user=self.user)
        service.ai_service = MagicMock()
        service.gap_analysis_service = MagicMock()
        service.gap_analysis_service.get_or_create_gap_analysis.return_value = self.curation

        curation, version = service.generate(
            user=self.user,
            curation_id=self.curation.id,
            regenerate=True,
            mode=TAILOR_MODE_SYSTEM_BASELINE,
            add_missing_skills=False,
        )

        service.ai_service.generate_tailored_resume.assert_not_called()
        service.ai_service.rephrase_resume.assert_not_called()

        self.assertIsNotNone(version)
        self.assertEqual(curation.tailored_resume_data.get("tailoring_mode"), "system_baseline")

        # Matching skills (Python, Django) should be prioritized at the front of the list
        skills = curation.tailored_resume_data["skills"]
        self.assertIn("Python", skills[:2])
        self.assertIn("Django", skills[:2])

        # ATS section structure preserved
        self.assertEqual(len(curation.tailored_resume_data["experience"]), 1)
        exp_0 = curation.tailored_resume_data["experience"][0]
        self.assertEqual(exp_0["source_index"], 0)
        self.assertIn("Software Developer", exp_0["header"])
        self.assertEqual(len(exp_0["bullets"]), 2)

    def test_system_baseline_mode_appends_allowed_missing_skills_when_opted_in(self):
        service = JobResumeCurationService(user=self.user)
        service.ai_service = MagicMock()
        service.gap_analysis_service = MagicMock()
        service.gap_analysis_service.get_or_create_gap_analysis.return_value = self.curation

        curation, version = service.generate(
            user=self.user,
            curation_id=self.curation.id,
            regenerate=True,
            mode=TAILOR_MODE_SYSTEM_BASELINE,
            add_missing_skills=True,
        )

        skills = curation.tailored_resume_data["skills"]
        # Approved missing skills PostgreSQL and AWS should now be included
        self.assertIn("PostgreSQL", skills)
        self.assertIn("AWS", skills)

    def test_rephrase_only_mode_delegates_to_rephrase_resume(self):
        service = JobResumeCurationService(user=self.user)
        service.ai_service = MagicMock()
        service.gap_analysis_service = MagicMock()
        service.gap_analysis_service.get_or_create_gap_analysis.return_value = self.curation
        service.ai_service.rephrase_resume.return_value = {
            "summary": "Polished summary for full-stack engineer.",
            "skills": ["Python", "Django", "JavaScript"],
            "experience": [
                {
                    "source_index": 0,
                    "source_text": "Built REST APIs.",
                    "tailored_text": "Architected high-throughput REST APIs.",
                    "header": "Software Developer - Acme Corp | Jan 2021 - Present",
                    "bullets": ["Architected high-throughput REST APIs."],
                }
            ],
            "projects": [],
            "education": [],
            "certifications": [],
        }

        curation, version = service.generate(
            user=self.user,
            curation_id=self.curation.id,
            regenerate=True,
            mode=TAILOR_MODE_REPHRASE_ONLY,
        )

        service.ai_service.rephrase_resume.assert_called_once()
        self.assertEqual(curation.tailored_resume_data.get("tailoring_mode"), "rephrase_only")
        self.assertEqual(curation.tailored_resume_data["summary"], "Polished summary for full-stack engineer.")

    def test_full_tailored_mode_delegates_to_generate_tailored_resume(self):
        service = JobResumeCurationService(user=self.user)
        service.ai_service = MagicMock()
        service.gap_analysis_service = MagicMock()
        service.gap_analysis_service.get_or_create_gap_analysis.return_value = self.curation
        service.ai_service.generate_tailored_resume.return_value = {
            "summary": "Tailored Senior Python Backend Engineer summary.",
            "skills": ["Python", "Django", "PostgreSQL"],
            "experience": [
                {
                    "source_index": 0,
                    "source_text": "Built REST APIs.",
                    "tailored_text": "Engineered scalable REST microservices.",
                    "header": "Software Developer - Acme Corp | Jan 2021 - Present",
                    "bullets": ["Engineered scalable REST microservices."],
                }
            ],
            "projects": [],
            "education": [],
            "certifications": [],
        }

        curation, version = service.generate(
            user=self.user,
            curation_id=self.curation.id,
            regenerate=True,
            mode=TAILOR_MODE_FULL_TAILORED,
        )

        service.ai_service.generate_tailored_resume.assert_called_once()
        self.assertEqual(curation.tailored_resume_data.get("tailoring_mode"), "full_tailored")
