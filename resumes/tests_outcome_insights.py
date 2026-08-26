"""Tests for outcome insights.

The most important behaviour under test is the refusal: this feature must
say nothing at all until the data can support a claim. A tool whose whole
pitch is "we tell you what actually worked" loses that position the moment
it reports a finding drawn from six applications.
"""

from django.test import TestCase

from authentication.models import User
from resumes.models import JobResumeCuration
from resumes.services.outcome_insights_service import (
    MIN_PER_GROUP,
    MIN_RESOLVED_OUTCOMES,
    outcome_insights,
)


def _user():
    return User.objects.create_user(
        name="Jane Doe", email="insights@example.com", phone="5557770000",
        password="safe-password",
    )


def _curation(user, status, match=None, coverage=None, tailored=True):
    gap = {}
    if match is not None:
        gap["overall_match_percentage"] = match
    if coverage is not None:
        gap["ats_keyword_coverage"] = {
            "covered_keywords": [],
            "missing_keywords": [],
            "coverage_percentage": coverage,
        }
    return JobResumeCuration.objects.create(
        user=user,
        job_title="Engineer",
        job_description="jd",
        status="completed",
        application_status=status,
        gap_analysis_data=gap,
        tailored_resume_data={"summary": "x"} if tailored else {},
    )


class InsufficientDataTests(TestCase):
    def setUp(self):
        self.user = _user()

    def test_reports_nothing_with_no_outcomes_at_all(self):
        result = outcome_insights()

        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["outcomes_still_needed"], MIN_RESOLVED_OUTCOMES)

    def test_reports_nothing_just_below_the_threshold(self):
        for _ in range(MIN_RESOLVED_OUTCOMES - 1):
            _curation(self.user, JobResumeCuration.APPLICATION_STATUS_REJECTED, match=50)

        result = outcome_insights()

        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["findings"], [])

    def test_refuses_when_one_group_is_too_small_even_if_total_is_large(self):
        # 40 rejections and 2 interviews cannot support a claim about what
        # interviews have in common, however large the total looks.
        for _ in range(40):
            _curation(self.user, JobResumeCuration.APPLICATION_STATUS_REJECTED, match=50)
        for _ in range(MIN_PER_GROUP - 3):
            _curation(self.user, JobResumeCuration.APPLICATION_STATUS_INTERVIEWING, match=90)

        result = outcome_insights()

        self.assertEqual(result["status"], "insufficient_data")

    def test_applications_still_awaiting_an_outcome_do_not_count(self):
        for _ in range(50):
            _curation(self.user, JobResumeCuration.APPLICATION_STATUS_APPLIED, match=70)

        result = outcome_insights()

        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["resolved_outcomes"], 0)


class SufficientDataTests(TestCase):
    def setUp(self):
        self.user = _user()
        # 20 interviewed at high match, 20 rejected at low match.
        for _ in range(20):
            _curation(
                self.user, JobResumeCuration.APPLICATION_STATUS_INTERVIEWING,
                match=80, coverage=75,
            )
        for _ in range(20):
            _curation(
                self.user, JobResumeCuration.APPLICATION_STATUS_REJECTED,
                match=45, coverage=40,
            )

    def test_reports_once_there_is_enough_data(self):
        result = outcome_insights()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolved_outcomes"], 40)
        self.assertEqual(result["interviewed"], 20)
        self.assertEqual(result["rejected"], 20)

    def test_detects_a_real_difference_in_match_percentage(self):
        result = outcome_insights()

        match = next(f for f in result["findings"] if f["signal"] == "Job match percentage")
        self.assertEqual(match["interviewed_average"], 80.0)
        self.assertEqual(match["rejected_average"], 45.0)
        self.assertEqual(match["difference"], 35.0)
        self.assertTrue(match["is_meaningful"])

    def test_every_finding_carries_its_sample_sizes(self):
        # A difference without its sample size is not a finding, it is a
        # claim the reader cannot evaluate.
        result = outcome_insights()

        for finding in result["findings"]:
            self.assertIn("sample_interviewed", finding)
            self.assertIn("sample_rejected", finding)
            self.assertGreaterEqual(finding["sample_interviewed"], MIN_PER_GROUP)

    def test_offers_count_as_success_alongside_interviews(self):
        _curation(self.user, JobResumeCuration.APPLICATION_STATUS_OFFER, match=95)

        result = outcome_insights()

        self.assertEqual(result["interviewed"], 21)

    def test_note_states_these_are_correlations_not_causes(self):
        result = outcome_insights()

        self.assertIn("correlation", result["note"].lower())


class NoDifferenceTests(TestCase):
    """A finding of 'this signal doesn't predict anything' is a real result
    and must be reported as such, not quietly hidden."""

    def setUp(self):
        self.user = _user()
        for _ in range(20):
            _curation(self.user, JobResumeCuration.APPLICATION_STATUS_INTERVIEWING, match=60)
        for _ in range(20):
            _curation(self.user, JobResumeCuration.APPLICATION_STATUS_REJECTED, match=59)

    def test_a_negligible_difference_is_reported_as_not_meaningful(self):
        result = outcome_insights()

        match = next(f for f in result["findings"] if f["signal"] == "Job match percentage")
        self.assertEqual(match["difference"], 1.0)
        self.assertFalse(match["is_meaningful"])
