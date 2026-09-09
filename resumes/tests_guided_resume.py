from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import GuidedResumeSession, Resume, ResumeVersion
from resumes.services.guided_resume_ai_service import GuidedResumeAIService
from resumes.services.guided_resume_service import GuidedResumeService


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def generate_structured(self, prompt, schema):
        return self.payload


class GuidedResumeServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="fresher@example.com", name="Fresher", password="Pw123456!")

    def test_get_or_start_is_idempotent(self):
        a = GuidedResumeService.get_or_start(self.user)
        b = GuidedResumeService.get_or_start(self.user)
        self.assertEqual(a.id, b.id)

    def test_answers_merge_and_pull_out_role_and_situation(self):
        s = GuidedResumeService.get_or_start(self.user)
        s = GuidedResumeService.save_answers(s, {"target_role": "Data Analyst", "situation": "student"})
        s = GuidedResumeService.save_answers(s, {"full_name": "Riya Patel"})
        self.assertEqual(s.target_role, "Data Analyst")
        self.assertEqual(s.situation, "student")
        self.assertEqual(s.answers["full_name"], "Riya Patel")

    def test_education_assembles_without_ai(self):
        s = GuidedResumeService.get_or_start(self.user)
        s = GuidedResumeService.save_answers(s, {
            "education": [{
                "qualification": "B.Sc. Computer Science",
                "institution": "State University",
                "dates": "2021 - 2025",
                "status": "in_progress",
                "highlights": "GPA 3.8\nData Structures teaching assistant",
            }],
        })
        edu = GuidedResumeService.assemble(s)["education"]
        self.assertEqual(len(edu), 1)
        self.assertIn("B.Sc. Computer Science", edu[0]["header"])
        self.assertIn("(in progress)", edu[0]["header"])
        self.assertEqual(len(edu[0]["bullets"]), 2)

    def test_draft_section_drops_ungrounded_bullets(self):
        answers_text = "Role: Support Intern\nOrganisation: Acme\nDay to day: answered customer emails and updated a tracking sheet"
        fake = _FakeLLM({"entries": [{
            "header": "Support Intern - Acme",
            "bullets": [
                "Answered customer emails and maintained the tracking sheet",   # grounded
                "Led a team of 12 engineers on a Kubernetes migration",         # invented -> dropped
            ],
        }]})
        svc = GuidedResumeAIService(llm_service=fake)
        entries = svc.draft_section("experience", "Support", answers_text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["bullets"]), 1)
        self.assertIn("tracking sheet", entries[0]["bullets"][0])

    def test_polish_returns_original_when_the_rewrite_invents_a_fact(self):
        fake = _FakeLLM({"text": "Reduced churn by 40% using Kubernetes."})
        svc = GuidedResumeAIService(llm_service=fake)
        out = svc.polish("helped reduce customer churn", "stronger")
        self.assertFalse(out["changed"])
        self.assertEqual(out["suggestion"], "helped reduce customer churn")

    def test_polish_accepts_a_clean_grammar_fix(self):
        fake = _FakeLLM({"text": "Answered customer emails and updated the tracking sheet."})
        svc = GuidedResumeAIService(llm_service=fake)
        out = svc.polish("answered customer emails and updated the tracking sheet", "grammar")
        self.assertTrue(out["changed"])

    def test_finalize_creates_a_real_guided_resume(self):
        s = GuidedResumeService.get_or_start(self.user)
        GuidedResumeService.save_answers(s, {
            "full_name": "Riya Patel", "email": "riya@example.com",
            "skills": ["Python", "SQL", "Excel"],
            "education": [{"qualification": "B.Sc. IT", "institution": "State University"}],
        })
        GuidedResumeService.accept_section(s, "summary", {"summary": "Recent IT graduate focused on data analysis."})
        GuidedResumeService.accept_section(s, "experience", {"entries": [
            {"header": "Data Intern - Acme", "bullets": ["Built weekly sales dashboards in Excel."]},
        ]})
        s.refresh_from_db()

        resume = GuidedResumeService().finalize(s)
        s.refresh_from_db()

        self.assertEqual(resume.source, Resume.SOURCE_GUIDED)
        self.assertEqual(resume.status, "completed")
        self.assertFalse(resume.file)
        self.assertEqual(resume.parsed_data["personal_information"]["name"], "Riya Patel")
        self.assertEqual(resume.parsed_data["skills"], ["Python", "SQL", "Excel"])
        self.assertEqual(resume.parsed_data["experience"][0]["header"], "Data Intern - Acme")
        self.assertEqual(resume.parsed_data["experience"][0]["bullets"], ["Built weekly sales dashboards in Excel."])
        self.assertIsNotNone(resume.ats_score)
        self.assertEqual(ResumeVersion.objects.filter(resume=resume).count(), 1)
        self.assertEqual(s.status, GuidedResumeSession.STATUS_FINALIZED)
        self.assertEqual(s.resume_id, resume.id)


class GuidedResumeAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="api@example.com", name="Api", password="Pw123456!")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_get_starts_a_session_and_returns_the_question_bank(self):
        res = self.client.get("/api/resumes/guided/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "in_progress")
        section_ids = [s["id"] for s in res.data["sections"]]
        self.assertIn("experience", section_ids)
        self.assertIn("skills", section_ids)
        self.assertTrue(res.data["situations"])

    def test_answers_then_finalize_flow(self):
        self.client.post("/api/resumes/guided/answers/", {
            "answers": {"full_name": "Sam Lee", "email": "sam@example.com", "skills": ["Java"]}
        }, format="json")
        self.client.post("/api/resumes/guided/accept/", {
            "section": "experience",
            "entries": [{"header": "Volunteer - Local NGO", "bullets": ["Organised weekend food drives."]}],
        }, format="json")
        res = self.client.post("/api/resumes/guided/finalize/", {}, format="json")
        self.assertEqual(res.status_code, 201)
        resume = Resume.objects.get(id=res.data["resume_id"])
        self.assertEqual(resume.source, Resume.SOURCE_GUIDED)
        self.assertEqual(resume.parsed_data["experience"][0]["bullets"], ["Organised weekend food drives."])

    @patch("resumes.views.guided_resume_view.GuidedResumeService.draft_section")
    def test_draft_endpoint_returns_a_suggestion_without_committing(self, mock_draft):
        mock_draft.return_value = {"entries": [{"header": "Intern - Acme", "bullets": ["Did things."]}]}
        self.client.post("/api/resumes/guided/answers/", {
            "answers": {"experience": [{"title": "Intern", "org": "Acme", "day_to_day": "did things"}]}
        }, format="json")
        res = self.client.post("/api/resumes/guided/draft/", {"section": "experience"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["entries"][0]["header"], "Intern - Acme")
        # not committed
        session = GuidedResumeSession.objects.get(user=self.user)
        self.assertNotIn("experience", session.draft_data)


class PolishRephrasingTests(TestCase):
    """Polish is rephrasing, not generation, and must be judged that way.

    Grounding a rewrite the way a *generated* bullet is grounded treats
    every capitalised word as a claim. That rejected ordinary two-sentence
    rewrites because the second sentence's first word is capitalised, which
    is grammar, not a fact. These pin the line between "said it better" and
    "said something new".
    """

    def facts(self, original, suggestion):
        from resumes.services.guided_resume_ai_service import GuidedResumeAIService

        return GuidedResumeAIService._new_facts(suggestion, original)

    # -- a rephrase may say anything in ordinary words -------------------

    def test_multi_sentence_rewrite_is_not_a_fabrication(self):
        """The regression that made this feature feel broken: 'Improved'
        only starts sentence two."""
        self.assertEqual(
            self.facts(
                "did customer support and solved issues",
                "Resolved customer issues efficiently. Improved satisfaction across accounts.",
            ),
            [],
        )

    def test_professional_vocabulary_upgrade_is_allowed(self):
        self.assertEqual(
            self.facts(
                "i talked to clients about their problems",
                "Liaised with clients to understand and resolve their concerns",
            ),
            [],
        )

    def test_a_proper_noun_already_in_the_original_may_be_capitalised(self):
        """The author wrote 'react'; writing it properly as 'React' is
        formatting, not invention."""
        self.assertEqual(
            self.facts("worked on frontend using react", "Developed interfaces using React"),
            [],
        )

    # -- but it may not assert anything new ------------------------------

    def test_an_invented_metric_is_blocked(self):
        self.assertIn(
            "40%",
            self.facts("managed social media pages", "Grew following by 40% in 6 months"),
        )

    def test_an_invented_employer_is_blocked(self):
        self.assertIn(
            "Deloitte",
            self.facts("worked as an intern", "Interned at Deloitte on the audit team"),
        )

    def test_an_invented_tool_is_blocked(self):
        self.assertIn(
            "Selenium",
            self.facts("helped with testing", "Automated regression testing using Selenium"),
        )

    def test_an_invented_acronym_is_blocked(self):
        self.assertIn("SQL", self.facts("did some data work", "Analysed datasets in SQL"))

    def test_an_invented_team_size_is_blocked(self):
        self.assertIn("12", self.facts("supported the team", "Supported a team of 12 engineers"))

    # -- the operation instructions must ask for a real rewrite ----------

    def test_operations_do_not_tell_the_model_to_leave_the_text_alone(self):
        """The old wording ("Add nothing that isn't already stated") made
        the model return the original with the punctuation tidied, which
        reads to a user as the AI doing nothing."""
        from resumes.services.guided_resume_ai_service import GuidedResumeAIService

        combined = " ".join(GuidedResumeAIService.OPERATIONS.values()).lower()
        self.assertIn("rewrite", combined)
        self.assertNotIn("invent nothing", combined)
