"""Tests for choosing *which* resume a curation runs against.

A user with several resumes has to be able to point a curation at one of
them deliberately. Two separate defects broke that end to end, and both are
pinned here because neither was visible from the backend alone:

1. The list endpoint answers ``{"resumes": [...], "limit": N}``. The curation
   page read the body as a bare array, which threw, and its error path left
   the picker permanently on "Loading..." - so there was no control to
   choose with at all.
2. ``ResumeListItemSerializer`` deliberately excludes ``parsed_data``, but the
   picker labelled options from it. Even with the list rendering, every
   option would read "Resume #<id>", so the resumes were indistinguishable.

The backend contract those two depended on is what these tests hold still.
"""

from resumes.models import JobResumeCuration
from resumes.services.job_gap_analysis_service import CandidateDataUnavailableError
from resumes.services.job_curation_service import JobCurationService
from resumes.services.job_gap_analysis_service import JobGapAnalysisService
from resumes.services.job_resume_curation_service import JobResumeCurationService
from resumes.tests_multi_resume import MultiResumeTestCase


class ResumeListResponseContractTests(MultiResumeTestCase):
    """The exact response shape and fields the picker is built on."""

    def test_the_body_is_an_object_with_a_resumes_key_not_a_bare_array(self):
        self.create_resume(filename="alpha.pdf")

        response = self.client.get("/api/resumes/list/")

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, dict)
        self.assertIn("resumes", response.data)
        self.assertIsInstance(response.data["resumes"], list)

    def test_every_item_carries_the_fields_the_picker_labels_with(self):
        # original_filename is what distinguishes one option from another;
        # without it the list is a row of interchangeable ids.
        self.create_resume(filename="backend-engineer.pdf")

        items = self.client.get("/api/resumes/list/").data["resumes"]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["original_filename"], "backend-engineer.pdf")
        self.assertIn("id", items[0])
        self.assertIn("status", items[0])
        self.assertIn("updated_at", items[0])

    def test_distinct_uploads_are_distinguishable_from_the_list_alone(self):
        self.create_resume(filename="alpha.pdf")
        self.create_resume(filename="beta.pdf")

        items = self.client.get("/api/resumes/list/").data["resumes"]

        labels = {item["original_filename"] for item in items}
        self.assertEqual(labels, {"alpha.pdf", "beta.pdf"})


class CurationHonoursTheChosenResumeTests(MultiResumeTestCase):
    """A submitted resume_id must decide the source, not upload recency."""

    def setUp(self):
        super().setUp()
        self.older = self.create_resume(filename="older.pdf")
        # Created second, so it is the one the "latest" fallback would pick.
        self.newer = self.create_resume(filename="newer.pdf")

    def test_create_pending_stores_the_requested_resume(self):
        curation = JobCurationService().create_pending(
            self.user, "Backend Engineer", "Python and Django.", resume_id=self.older.id
        )

        self.assertEqual(curation.resume_id, self.older.id)

    def test_the_older_resume_is_used_when_it_is_the_one_chosen(self):
        # The regression that matters: choosing anything other than the most
        # recent upload has to actually change the source data.
        self.older.parsed_data = {**self.older.parsed_data, "summary": "Chosen resume."}
        self.older.save(update_fields=["parsed_data"])

        data = JobResumeCurationService.build_candidate_source_data(
            self.user, resume_id=self.older.id
        )

        self.assertIn("Chosen resume.", str(data))

    def test_gap_analysis_reads_the_same_chosen_resume(self):
        # The match report and the tailored resume must not disagree about
        # which resume they are talking about.
        self.older.parsed_data = {**self.older.parsed_data, "summary": "Chosen resume."}
        self.older.save(update_fields=["parsed_data"])

        data = JobGapAnalysisService.build_candidate_data(self.user, resume_id=self.older.id)

        self.assertIn("Chosen resume.", str(data))

    def test_another_users_resume_id_is_refused_rather_than_substituted(self):
        # Note this raises instead of quietly falling back to one of the
        # user's own resumes. Failing loudly is the right call: silently
        # curating a different resume than the one asked for is the exact
        # class of bug this file exists to prevent.
        theirs = self.create_resume(user=self.other_user, filename="theirs.pdf")

        with self.assertRaises(CandidateDataUnavailableError):
            JobResumeCurationService.build_candidate_source_data(
                self.user, resume_id=theirs.id
            )

    def test_omitting_a_choice_falls_back_to_the_most_recent(self):
        # Documents the fallback the frontend bug was silently relying on:
        # it is a sane default, but it is not a substitute for choosing.
        self.newer.parsed_data = {**self.newer.parsed_data, "summary": "Most recent."}
        self.newer.save(update_fields=["parsed_data"])

        data = JobResumeCurationService.build_candidate_source_data(self.user)

        self.assertIn("Most recent.", str(data))


class CurationAPIPassesTheSelectionThroughTests(MultiResumeTestCase):
    """resume_id has to survive the serializer and reach the stored row."""

    def test_a_submitted_resume_id_is_persisted_on_the_curation(self):
        chosen = self.create_resume(filename="chosen.pdf")
        self.create_resume(filename="decoy.pdf")

        response = self.client.post(
            "/api/resumes/job-curations/analyze/",
            {
                "job_title": "Backend Engineer",
                "job_description": "We need Python and Django experience on our platform team.",
                "resume_id": chosen.id,
                "curate_resume": True,
            },
            format="json",
        )

        self.assertIn(response.status_code, (200, 201, 202), response.data)
        curation = JobResumeCuration.objects.filter(user=self.user).latest("id")
        self.assertEqual(curation.resume_id, chosen.id)
