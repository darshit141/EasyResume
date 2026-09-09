"""Handing the resume file to the extension so it can attach it.

The failure that matters here is a silent one: the user ticks the box,
believes the resume went with the application, and it didn't. So every
case where we cannot supply the file has to come back as an explicit,
nameable reason rather than as nothing.
"""

import base64

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from resumes.models import Resume
from resumes.services.autofill_resume_file_service import (
    MAX_ATTACH_BYTES,
    AutofillResumeFileService,
    content_type_for,
)

User = get_user_model()


class AutofillResumeFileTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@example.com", password="pw", name="A")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("autofill-resume-file")

    def _resume(self, *, filename="cv.pdf", content=b"%PDF-1.4 hello", **kwargs):
        resume = Resume.objects.create(
            user=kwargs.pop("user", self.user),
            original_filename=filename,
            status="completed",
            **kwargs,
        )
        if content is not None:
            resume.file.save(filename, ContentFile(content), save=True)
        return resume

    def test_returns_the_file_the_extension_can_rebuild(self):
        self._resume(content=b"%PDF-1.4 hello")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["available"])
        self.assertEqual(response.data["filename"], "cv.pdf")
        self.assertEqual(response.data["content_type"], "application/pdf")
        self.assertEqual(base64.b64decode(response.data["data"]), b"%PDF-1.4 hello")

    def test_no_file_is_an_answer_not_an_error(self):
        """A resume built by the guided builder has no source file at all.
        That is ordinary, and the user attaching one themselves is the
        intended fallback."""
        Resume.objects.create(
            user=self.user,
            original_filename="Guided resume",
            status="completed",
            source=Resume.SOURCE_GUIDED,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["available"])
        self.assertEqual(response.data["reason"], "no_file")

    def test_prefers_the_newest_resume_that_actually_has_a_file(self):
        """A guided resume is often the newest and has no file. Returning
        nothing then would hide the PDF the user still has."""
        self._resume(filename="old.pdf")
        Resume.objects.create(
            user=self.user,
            original_filename="Guided resume",
            status="completed",
            source=Resume.SOURCE_GUIDED,
        )

        response = self.client.get(self.url)

        self.assertTrue(response.data["available"])
        self.assertEqual(response.data["filename"], "old.pdf")

    def test_never_returns_another_users_file(self):
        other = User.objects.create_user(email="b@example.com", password="pw", name="B")
        self._resume(user=other, filename="theirs.pdf")

        response = self.client.get(self.url)

        self.assertFalse(response.data["available"])

    def test_an_oversized_file_says_so_instead_of_being_truncated(self):
        resume = self._resume(filename="huge.pdf", content=b"x")
        resume.file.save("huge.pdf", ContentFile(b"x" * (MAX_ATTACH_BYTES + 1)), save=True)

        response = self.client.get(self.url)

        self.assertFalse(response.data["available"])
        self.assertEqual(response.data["reason"], "too_large")
        self.assertNotIn("data", response.data)

    def test_a_row_whose_blob_is_gone_degrades_instead_of_crashing(self):
        resume = self._resume()
        resume.file.storage.delete(resume.file.name)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["available"])
        self.assertEqual(response.data["reason"], "missing_from_storage")

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_handles_no_resume_at_all(self):
        self.assertFalse(AutofillResumeFileService.build(None)["available"])


class ContentTypeTests(APITestCase):
    def test_names_the_types_an_ats_actually_accepts(self):
        self.assertEqual(content_type_for("a.pdf"), "application/pdf")
        self.assertEqual(
            content_type_for("A CV.DOCX"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(content_type_for("a.doc"), "application/msword")

    def test_falls_back_rather_than_guessing_wrong(self):
        self.assertEqual(content_type_for("resume"), "application/octet-stream")
        self.assertEqual(content_type_for(""), "application/octet-stream")
