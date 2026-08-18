from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobResumeCuration
from resumes.services.job_url_fetch_service import (
    JobUrlNotAllowedError,
    JobUrlUnreadableError,
    fetch_job_posting,
)
from resumes.tests_job_curation import (
    VALID_JOB_ANALYSIS,
    VALID_REQUEST,
    FakeJobAnalysisLLMService,
)

JOB_URL = "https://boards.greenhouse.io/acme/jobs/42"

JSON_LD_PAGE = """
<html><head>
<title>Acme — Senior Backend Engineer</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting",
 "title":"Senior Backend Engineer",
 "description":"<p>We need a backend engineer with <b>Python</b>, Django and PostgreSQL experience. You will build and operate REST APIs at scale.</p>"}
</script>
</head><body><nav>Ignore this navigation</nav></body></html>
"""

PLAIN_PAGE = "<html><head><title>Senior Backend Engineer at Acme</title></head><body>" \
    "<script>var tracking = 1;</script><nav>Home Jobs Login</nav>" \
    f"<div>{'We need a senior backend engineer with Python and Django. ' * 12}</div>" \
    "<footer>Cookie banner</footer></body></html>"


class JobUrlSecurityTests(TestCase):
    """SSRF guards. These are the reason this feature can safely exist."""

    def test_non_http_scheme_is_rejected(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x/1"):
            with self.assertRaises(JobUrlNotAllowedError):
                fetch_job_posting(url)

    def test_domain_outside_the_allowlist_is_rejected(self):
        with self.assertRaises(JobUrlNotAllowedError):
            fetch_job_posting("https://evil.example/jobs/1")

    def test_localhost_is_rejected(self):
        with override_settings(ALLOWED_JOB_URL_DOMAINS=[]):
            for url in ("http://localhost:8000/admin/", "http://127.0.0.1/x"):
                with self.assertRaises(JobUrlNotAllowedError):
                    fetch_job_posting(url)

    def test_private_network_addresses_are_rejected(self):
        with override_settings(ALLOWED_JOB_URL_DOMAINS=[]):
            for url in ("http://10.0.0.5/x", "http://192.168.1.1/x", "http://172.16.0.1/x"):
                with self.assertRaises(JobUrlNotAllowedError):
                    fetch_job_posting(url)

    def test_cloud_metadata_endpoint_is_rejected(self):
        # 169.254.169.254 is the classic SSRF target for stealing cloud
        # instance credentials.
        with override_settings(ALLOWED_JOB_URL_DOMAINS=[]):
            with self.assertRaises(JobUrlNotAllowedError):
                fetch_job_posting("http://169.254.169.254/latest/meta-data/")

    def test_nat64_wrapped_public_address_is_allowed(self):
        # 64:ff9b::/96 carries an IPv4 address inside an IPv6 one. DNS64/NAT64
        # networks return these for ordinary public hosts; Python reports the
        # raw form as `is_reserved`, which previously rejected every site on
        # such a network. 64:ff9b::12ac:4e22 is public IPv4 18.172.78.34.
        with patch(
            "resumes.services.job_url_fetch_service.socket.getaddrinfo",
            return_value=[(23, 1, 6, "", ("64:ff9b::12ac:4e22", 443, 0, 0))],
        ), patch(
            "resumes.services.job_url_fetch_service._read_page", return_value=JSON_LD_PAGE
        ):
            result = fetch_job_posting("https://boards.greenhouse.io/acme/jobs/1")
        self.assertEqual(result["title"], "Senior Backend Engineer")

    def test_nat64_wrapped_private_address_is_still_rejected(self):
        # Unwrapping must not become a bypass: 64:ff9b::a00:1 is 10.0.0.1.
        with patch(
            "resumes.services.job_url_fetch_service.socket.getaddrinfo",
            return_value=[(23, 1, 6, "", ("64:ff9b::a00:1", 443, 0, 0))],
        ):
            with self.assertRaises(JobUrlNotAllowedError):
                fetch_job_posting("https://boards.greenhouse.io/acme/jobs/1")

    def test_ipv4_mapped_private_address_is_rejected(self):
        # ::ffff:127.0.0.1 is loopback wearing an IPv6 costume.
        with patch(
            "resumes.services.job_url_fetch_service.socket.getaddrinfo",
            return_value=[(23, 1, 6, "", ("::ffff:127.0.0.1", 443, 0, 0))],
        ):
            with self.assertRaises(JobUrlNotAllowedError):
                fetch_job_posting("https://boards.greenhouse.io/acme/jobs/1")

    def test_allowed_domain_resolving_to_a_private_ip_is_rejected(self):
        # Defends against DNS rebinding: the domain passes the allowlist but
        # points somewhere internal.
        with patch(
            "resumes.services.job_url_fetch_service.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
        ):
            with self.assertRaises(JobUrlNotAllowedError):
                fetch_job_posting(JOB_URL)


class JobUrlExtractionTests(TestCase):
    def fetch(self, page):
        with patch(
            "resumes.services.job_url_fetch_service._read_page", return_value=page
        ), patch(
            "resumes.services.job_url_fetch_service._is_public_address", return_value=True
        ):
            return fetch_job_posting(JOB_URL)

    def test_json_ld_posting_is_preferred(self):
        result = self.fetch(JSON_LD_PAGE)

        self.assertEqual(result["title"], "Senior Backend Engineer")
        self.assertIn("Python", result["description"])
        # HTML inside the JSON-LD description is stripped to plain text.
        self.assertNotIn("<b>", result["description"])

    def test_json_ld_extraction_skips_page_chrome(self):
        result = self.fetch(JSON_LD_PAGE)
        self.assertNotIn("Ignore this navigation", result["description"])

    def test_falls_back_to_page_text_without_json_ld(self):
        result = self.fetch(PLAIN_PAGE)

        self.assertIn("backend engineer", result["description"].lower())
        self.assertEqual(result["title"], "Senior Backend Engineer at Acme")

    def test_fallback_drops_scripts_and_navigation(self):
        result = self.fetch(PLAIN_PAGE)

        self.assertNotIn("var tracking", result["description"])
        self.assertNotIn("Home Jobs Login", result["description"])
        self.assertNotIn("Cookie banner", result["description"])

    def test_page_with_too_little_text_is_unreadable(self):
        # What a login wall or bot-block page looks like.
        with self.assertRaises(JobUrlUnreadableError):
            self.fetch("<html><body>Please sign in to continue.</body></html>")

    def test_json_ld_nested_in_a_graph_is_found(self):
        page = """
        <html><head><script type="application/ld+json">
        {"@context":"https://schema.org","@graph":[
          {"@type":"Organization","name":"Acme"},
          {"@type":"JobPosting","title":"Platform Engineer",
           "description":"Operate Kubernetes clusters and build internal developer tooling for a large engineering organisation."}
        ]}
        </script></head><body></body></html>
        """
        result = self.fetch(page)
        self.assertEqual(result["title"], "Platform Engineer")


class JobUrlPreviewAPITests(TestCase):
    """Import is not curation: this endpoint reads a posting so the user can
    check it, and must never create anything or spend a credit."""

    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-preview@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = "/api/resumes/job-curations/import-url/"

    def test_returns_extracted_title_and_description(self):
        with patch(
            "resumes.views.job_curation_view.fetch_job_posting",
            return_value={"title": "Senior Backend Engineer", "description": "Python and Django."},
        ):
            response = self.client.post(self.url, {"job_url": JOB_URL}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["job_title"], "Senior Backend Engineer")
        self.assertEqual(response.data["job_description"], "Python and Django.")

    def test_creates_nothing_and_spends_no_credit(self):
        from billing.models import AIUsageLog

        with patch(
            "resumes.views.job_curation_view.fetch_job_posting",
            return_value={"title": "Engineer", "description": "Text."},
        ):
            self.client.post(self.url, {"job_url": JOB_URL}, format="json")

        self.assertEqual(JobResumeCuration.objects.count(), 0)
        self.assertEqual(AIUsageLog.objects.filter(user=self.user).count(), 0)

    def test_blocked_site_reports_a_distinct_code(self):
        from resumes.services.job_url_fetch_service import JobUrlBlockedError

        with patch(
            "resumes.views.job_curation_view.fetch_job_posting",
            side_effect=JobUrlBlockedError(),
        ):
            response = self.client.post(self.url, {"job_url": JOB_URL}, format="json")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["code"], "JOB_URL_BLOCKED")

    def test_javascript_rendered_page_reports_unreadable(self):
        with patch(
            "resumes.views.job_curation_view.fetch_job_posting",
            side_effect=JobUrlUnreadableError(),
        ):
            response = self.client.post(self.url, {"job_url": JOB_URL}, format="json")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["code"], "JOB_URL_UNREADABLE")

    def test_unsupported_domain_is_rejected_before_any_fetch(self):
        with patch("resumes.views.job_curation_view.fetch_job_posting") as mock_fetch:
            response = self.client.post(
                self.url, {"job_url": "https://evil.example/x"}, format="json"
            )

        self.assertEqual(response.status_code, 400)
        mock_fetch.assert_not_called()

    def test_requires_authentication(self):
        response = APIClient().post(self.url, {"job_url": JOB_URL}, format="json")
        self.assertEqual(response.status_code, 401)


class JobCurationFromUrlAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-url@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = "/api/resumes/job-curations/analyze/"

    def post(self, payload, imported=None, fetch_error=None):
        target = "resumes.views.job_curation_view.fetch_job_posting"
        patcher = (
            patch(target, side_effect=fetch_error)
            if fetch_error
            else patch(target, return_value=imported or {
                "title": "Senior Backend Engineer",
                "description": "We need a backend engineer with Python and Django experience.",
            })
        )
        with patcher as mock_fetch, patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=FakeJobAnalysisLLMService(VALID_JOB_ANALYSIS),
        ):
            response = self.client.post(self.url, payload, format="json")
        return response, mock_fetch

    def test_url_alone_is_accepted_and_imported(self):
        response, mock_fetch = self.post({"curate_resume": True, "job_url": JOB_URL})

        self.assertEqual(response.status_code, 201)
        mock_fetch.assert_called_once_with(JOB_URL)
        curation = JobResumeCuration.objects.get()
        self.assertEqual(curation.job_title, "Senior Backend Engineer")
        self.assertIn("Python", curation.job_description)
        self.assertEqual(curation.job_url, JOB_URL)

    def test_pasted_description_is_not_overwritten_by_the_link(self):
        # The user has seen the posting; their text wins and no fetch happens.
        response, mock_fetch = self.post({**VALID_REQUEST, "job_url": JOB_URL})

        self.assertEqual(response.status_code, 201)
        mock_fetch.assert_not_called()
        self.assertEqual(JobResumeCuration.objects.get().job_title, VALID_REQUEST["job_title"])

    def test_user_supplied_title_wins_over_the_imported_one(self):
        response, _ = self.post({
            "curate_resume": True, "job_url": JOB_URL, "job_title": "My Own Title",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(JobResumeCuration.objects.get().job_title, "My Own Title")

    def test_neither_url_nor_description_is_rejected(self):
        response = self.client.post(
            self.url, {"curate_resume": True, "job_title": "Engineer"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_description", response.data)

    def test_description_without_title_is_rejected(self):
        response = self.client.post(
            self.url,
            {"curate_resume": True, "job_description": VALID_REQUEST["job_description"]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_title", response.data)

    def test_blocked_job_board_returns_a_clear_actionable_error(self):
        response, _ = self.post(
            {"curate_resume": True, "job_url": JOB_URL},
            fetch_error=JobUrlUnreadableError(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["code"], "JOB_URL_UNREADABLE")
        self.assertIn("Paste the job description", response.data["detail"])
        self.assertEqual(JobResumeCuration.objects.count(), 0)

    def test_import_without_a_usable_title_asks_for_one(self):
        response, _ = self.post(
            {"curate_resume": True, "job_url": JOB_URL},
            imported={"title": "", "description": "A long enough job description to pass validation."},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_title", response.data)
