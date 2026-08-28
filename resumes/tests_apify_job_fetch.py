"""Tests for the Apify rescue path on job-URL import.

The contract that matters most: this rescue must never make a broken import
*worse*. No token, a network failure, a bad response, or too little text all
have to fall back to exactly the JOB_URL_UNREADABLE behaviour that existed
before this rescue was added — never an unhandled exception, and never a
fabricated "successful" import with junk content.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from resumes.services.apify_job_fetch_service import fetch_via_apify
from resumes.services.job_url_fetch_service import JobUrlUnreadableError, fetch_job_posting

URL = "https://boards.greenhouse.io/acme/jobs/999"  # already allowlisted by default

# A JS-shell page: real sites like this return a near-empty document because
# the posting is rendered client-side after load.
JS_SHELL_PAGE = "<html><head><title>Loading…</title></head><body><div id=\"root\"></div></body></html>"

APIFY_ITEM = {
    "metadata": {"title": "Senior Backend Engineer", "url": URL},
    "text": "We need a senior backend engineer with Python, Django and PostgreSQL experience. " * 4,
}


class _Response:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else []

    def json(self):
        return self._json_body


class ApifyRescueUnitTests(TestCase):
    """fetch_via_apify() in isolation — its no-token-means-no-op contract,
    and that every failure mode returns None rather than raising."""

    @override_settings(APIFY_API_TOKEN="")
    def test_returns_none_without_a_configured_token(self):
        with patch("resumes.services.apify_job_fetch_service.requests.post") as post:
            result = fetch_via_apify(URL)

        self.assertIsNone(result)
        post.assert_not_called()

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_a_successful_call_returns_title_and_description(self):
        with patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            return_value=_Response(200, [APIFY_ITEM]),
        ):
            result = fetch_via_apify(URL)

        self.assertEqual(result["title"], "Senior Backend Engineer")
        self.assertIn("Python, Django and PostgreSQL", result["description"])

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_the_token_is_sent_and_the_correct_actor_is_called(self):
        with patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            return_value=_Response(200, [APIFY_ITEM]),
        ) as post:
            fetch_via_apify(URL)

        call_url = post.call_args.args[0]
        self.assertIn("apify~rag-web-browser", call_url)
        self.assertEqual(post.call_args.kwargs["params"]["token"], "apify_test_token")
        self.assertEqual(post.call_args.kwargs["json"]["query"], URL)

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_a_network_error_returns_none_rather_than_raising(self):
        import requests

        with patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            side_effect=requests.RequestException("connection reset"),
        ):
            result = fetch_via_apify(URL)

        self.assertIsNone(result)

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_an_error_response_returns_none(self):
        with patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            return_value=_Response(500, {"error": "actor failed"}),
        ):
            result = fetch_via_apify(URL)

        self.assertIsNone(result)

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_an_empty_dataset_returns_none(self):
        with patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            return_value=_Response(200, []),
        ):
            result = fetch_via_apify(URL)

        self.assertIsNone(result)

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_too_little_extracted_text_returns_none_rather_than_a_junk_import(self):
        # A login wall or another JS shell rendered by the headless browser
        # too - the rescue attempt itself can fail the same way the plain
        # fetch did, and that must not be presented as a successful import.
        with patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            return_value=_Response(200, [{"metadata": {"title": "x"}, "text": "Sign in to continue"}]),
        ):
            result = fetch_via_apify(URL)

        self.assertIsNone(result)

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_a_missing_title_does_not_fail_the_rescue(self):
        item = {"text": APIFY_ITEM["text"]}  # no "metadata" key at all
        with patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            return_value=_Response(200, [item]),
        ):
            result = fetch_via_apify(URL)

        self.assertEqual(result["title"], "")
        self.assertIn("Python", result["description"])


class ApifyRescueIntegrationTests(TestCase):
    """fetch_job_posting() end-to-end: the rescue is only reached after the
    primary fetch has already failed, and never changes behaviour when it
    isn't configured."""

    def fetch(self, page):
        # Same pattern JobUrlExtractionTests uses in tests_job_url_fetch.py:
        # skip real DNS/socket work, exercise everything downstream of it.
        return patch(
            "resumes.services.job_url_fetch_service._read_page", return_value=page
        ), patch(
            "resumes.services.job_url_fetch_service._is_public_address", return_value=True
        )

    @override_settings(APIFY_API_TOKEN="")
    def test_without_a_token_the_original_unreadable_error_is_unchanged(self):
        read_patch, addr_patch = self.fetch(JS_SHELL_PAGE)
        with read_patch, addr_patch, patch(
            "resumes.services.apify_job_fetch_service.requests.post"
        ) as post:
            with self.assertRaises(JobUrlUnreadableError):
                fetch_job_posting(URL)

        post.assert_not_called()

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_a_token_configured_rescues_a_posting_the_primary_fetch_could_not_read(self):
        read_patch, addr_patch = self.fetch(JS_SHELL_PAGE)
        with read_patch, addr_patch, patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            return_value=_Response(200, [APIFY_ITEM]),
        ):
            result = fetch_job_posting(URL)

        self.assertEqual(result["title"], "Senior Backend Engineer")
        self.assertIn("Python", result["description"])

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_the_rescue_failing_too_still_raises_the_original_error(self):
        read_patch, addr_patch = self.fetch(JS_SHELL_PAGE)
        with read_patch, addr_patch, patch(
            "resumes.services.apify_job_fetch_service.requests.post",
            return_value=_Response(200, []),
        ):
            with self.assertRaises(JobUrlUnreadableError):
                fetch_job_posting(URL)
