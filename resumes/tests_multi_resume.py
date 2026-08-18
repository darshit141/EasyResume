from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import Resume, ResumeVersion

_PARSED_DATA = {
    "personal_information": {"name": "Jane Doe", "email": "jane@example.com", "phone": "1234567890"},
    "summary": "Backend engineer.",
    "skills": ["Python"],
    "experience": [],
    "education": [],
    "projects": [],
    "certifications": [],
    "achievements": [],
    "languages": [],
}


class MultiResumeTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane@example.com", phone="1234567890", password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe", email="john@example.com", phone="0987654321", password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def create_resume(self, user=None, filename="resume.pdf", **overrides):
        defaults = dict(
            user=user or self.user,
            file=SimpleUploadedFile(filename, b"resume content"),
            original_filename=filename,
            status="completed",
            parsed_data=_PARSED_DATA,
        )
        defaults.update(overrides)
        return Resume.objects.create(**defaults)

    def upload(self, filename="new-resume.pdf"):
        upload = SimpleUploadedFile(filename, b"placeholder content")
        # Must clear the service's _MIN_EXTRACTED_CHARS (50) floor, or every
        # upload is rejected as an unreadable file before reaching anything
        # this test suite actually means to exercise.
        extracted_text = "Backend engineer with five years of Python and Django experience."
        with patch(
            "resumes.services.resume_upload_service.TextExtractor.extract_text", return_value=extracted_text
        ), patch(
            "resumes.services.resume_upload_service.ResumeParser.parse", return_value=_PARSED_DATA
        ):
            return self.client.post("/api/resumes/upload/", {"file": upload}, format="multipart")


class UploadCreatesIndependentResumesTests(MultiResumeTestCase):
    def test_first_upload_creates_a_resume(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 1)

    def test_second_upload_creates_a_new_resume_instead_of_replacing(self):
        self.upload("resume-one.pdf")
        response = self.upload("resume-two.pdf")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 2)
        filenames = set(Resume.objects.filter(user=self.user).values_list("original_filename", flat=True))
        self.assertEqual(filenames, {"resume-one.pdf", "resume-two.pdf"})

    def test_each_upload_gets_its_own_version_one(self):
        self.upload("resume-one.pdf")
        self.upload("resume-two.pdf")

        for resume in Resume.objects.filter(user=self.user):
            self.assertEqual(resume.versions.count(), 1)
            self.assertEqual(resume.versions.first().version, 1)

    @override_settings(MAX_RESUMES_PER_USER=2)
    def test_upload_past_the_limit_is_rejected_before_any_ai_call(self):
        self.create_resume(filename="one.pdf")
        self.create_resume(filename="two.pdf")

        with patch("resumes.services.resume_upload_service.TextExtractor.extract_text") as mock_extract:
            response = self.client.post(
                "/api/resumes/upload/",
                {"file": SimpleUploadedFile("three.pdf", b"x")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "RESUME_LIMIT_REACHED")
        mock_extract.assert_not_called()
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 2)

    @override_settings(MAX_RESUMES_PER_USER=2)
    def test_deleting_one_frees_a_slot_for_a_new_upload(self):
        r1 = self.create_resume(filename="one.pdf")
        self.create_resume(filename="two.pdf")
        self.client.delete(f"/api/resumes/{r1.id}/")

        response = self.upload("three.pdf")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 2)

    def test_the_limit_is_per_user(self):
        with override_settings(MAX_RESUMES_PER_USER=1):
            self.create_resume(user=self.other_user, filename="not-mine.pdf")
            response = self.upload("mine.pdf")

        self.assertEqual(response.status_code, 201)


class ResumeListAPITests(MultiResumeTestCase):
    def test_lists_only_the_authenticated_users_resumes(self):
        self.create_resume(filename="mine-1.pdf")
        self.create_resume(filename="mine-2.pdf")
        self.create_resume(user=self.other_user, filename="not-mine.pdf")

        response = self.client.get("/api/resumes/list/")

        self.assertEqual(response.status_code, 200)
        filenames = {r["original_filename"] for r in response.data["resumes"]}
        self.assertEqual(filenames, {"mine-1.pdf", "mine-2.pdf"})

    def test_list_excludes_parsed_data(self):
        self.create_resume()
        response = self.client.get("/api/resumes/list/")
        self.assertNotIn("parsed_data", response.data["resumes"][0])

    def test_list_reports_the_configured_limit(self):
        with override_settings(MAX_RESUMES_PER_USER=3):
            response = self.client.get("/api/resumes/list/")
        self.assertEqual(response.data["limit"], 3)

    def test_requires_authentication(self):
        client = APIClient()
        response = client.get("/api/resumes/list/")
        self.assertEqual(response.status_code, 401)


class ResumeDetailAPITests(MultiResumeTestCase):
    def test_get_returns_the_requested_resume(self):
        resume = self.create_resume(filename="target.pdf")

        response = self.client.get(f"/api/resumes/{resume.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["original_filename"], "target.pdf")

    def test_get_another_users_resume_returns_404(self):
        resume = self.create_resume(user=self.other_user)
        response = self.client.get(f"/api/resumes/{resume.id}/")
        self.assertEqual(response.status_code, 404)

    def test_put_edits_only_the_targeted_resume(self):
        resume_a = self.create_resume(filename="a.pdf")
        resume_b = self.create_resume(filename="b.pdf")

        new_data = {**_PARSED_DATA, "summary": "Updated summary for A only."}
        response = self.client.put(f"/api/resumes/{resume_a.id}/", {"parsed_data": new_data}, format="json")

        self.assertEqual(response.status_code, 200)
        resume_a.refresh_from_db()
        resume_b.refresh_from_db()
        self.assertEqual(resume_a.parsed_data["summary"], "Updated summary for A only.")
        self.assertEqual(resume_b.parsed_data["summary"], _PARSED_DATA["summary"])

    def test_put_creates_a_new_version_scoped_to_that_resume(self):
        resume_a = self.create_resume(filename="a.pdf")
        ResumeVersion.objects.create(resume=resume_a, version=1, parsed_data=_PARSED_DATA)
        resume_b = self.create_resume(filename="b.pdf")
        ResumeVersion.objects.create(resume=resume_b, version=1, parsed_data=_PARSED_DATA)

        self.client.put(
            f"/api/resumes/{resume_a.id}/",
            {"parsed_data": {**_PARSED_DATA, "summary": "v2"}},
            format="json",
        )

        self.assertEqual(resume_a.versions.count(), 2)
        self.assertEqual(resume_b.versions.count(), 1)

    def test_put_on_another_users_resume_returns_404(self):
        resume = self.create_resume(user=self.other_user)
        response = self.client.put(f"/api/resumes/{resume.id}/", {"parsed_data": _PARSED_DATA}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_delete_removes_only_the_targeted_resume(self):
        resume_a = self.create_resume(filename="a.pdf")
        resume_b = self.create_resume(filename="b.pdf")

        response = self.client.delete(f"/api/resumes/{resume_a.id}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Resume.objects.filter(id=resume_a.id).exists())
        self.assertTrue(Resume.objects.filter(id=resume_b.id).exists())

    def test_delete_cascades_to_its_versions(self):
        resume = self.create_resume()
        ResumeVersion.objects.create(resume=resume, version=1, parsed_data=_PARSED_DATA)
        ResumeVersion.objects.create(resume=resume, version=2, parsed_data=_PARSED_DATA)

        self.client.delete(f"/api/resumes/{resume.id}/")

        self.assertEqual(ResumeVersion.objects.filter(resume_id=resume.id).count(), 0)

    def test_delete_on_another_users_resume_returns_404_and_does_not_delete_it(self):
        resume = self.create_resume(user=self.other_user)
        response = self.client.delete(f"/api/resumes/{resume.id}/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Resume.objects.filter(id=resume.id).exists())


class ResumeVersionsPerResumeTests(MultiResumeTestCase):
    def test_version_history_is_scoped_to_the_requested_resume(self):
        resume_a = self.create_resume(filename="a.pdf")
        ResumeVersion.objects.create(resume=resume_a, version=1, parsed_data=_PARSED_DATA)
        ResumeVersion.objects.create(resume=resume_a, version=2, parsed_data=_PARSED_DATA)
        resume_b = self.create_resume(filename="b.pdf")
        ResumeVersion.objects.create(resume=resume_b, version=1, parsed_data=_PARSED_DATA)

        response = self.client.get(f"/api/resumes/versions/?resume_id={resume_a.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_omitting_resume_id_defaults_to_most_recently_uploaded(self):
        self.create_resume(filename="older.pdf")
        newer = self.create_resume(filename="newer.pdf")
        ResumeVersion.objects.create(resume=newer, version=1, parsed_data=_PARSED_DATA)

        response = self.client.get("/api/resumes/versions/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)


class BackwardCompatSingularEndpointsTests(MultiResumeTestCase):
    """`/view/` and `/edit/` predate multi-resume support and keep acting on
    the most recently uploaded resume, for callers that only ever needed
    "does this user have a usable resume"."""

    def test_view_without_id_returns_the_most_recently_uploaded_resume(self):
        self.create_resume(filename="older.pdf")
        self.create_resume(filename="newer.pdf")

        response = self.client.get("/api/resumes/view/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["original_filename"], "newer.pdf")

    def test_edit_without_id_only_touches_the_most_recently_uploaded_resume(self):
        older = self.create_resume(filename="older.pdf")
        newer = self.create_resume(filename="newer.pdf")

        self.client.put(
            "/api/resumes/edit/",
            {"parsed_data": {**_PARSED_DATA, "summary": "edited"}},
            format="json",
        )

        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.parsed_data["summary"], _PARSED_DATA["summary"])
        self.assertEqual(newer.parsed_data["summary"], "edited")
