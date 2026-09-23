import gzip
import ipaddress
import json
import time
from unittest.mock import MagicMock, patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import JobImportAttempt
from resumes.services import job_url_fetch_service as fetch_module
from resumes.services.job_import_service import import_job_posting, stats_by_domain
from resumes.services.job_page_extractor import finalize_description
from resumes.services.job_url_fetch_service import (
    JobUrlBlockedError,
    JobUrlNotAllowedError,
    JobUrlNotFoundError,
    JobUrlTimeoutError,
    bot_walled_site,
    fetch_job_posting,
)

# Same structure as LinkedIn's public job-card fragment (class names and
# layout taken from a live response), with made-up content.
LINKEDIN_FRAGMENT = """
<section class="top-card-layout">
  <h2 class="top-card-layout__title font-sans text-lg topcard__title">Python Developer</h2>
  <h4 class="top-card-layout__second-subline">
    <div class="topcard__flavor-row">
      <span class="topcard__flavor">
        <a class="topcard__org-name-link topcard__flavor--black-link" href="#">Acme Systems</a>
      </span>
      <span class="topcard__flavor topcard__flavor--bullet">Bengaluru, Karnataka, India</span>
    </div>
    <div class="topcard__flavor-row">
      <span class="posted-time-ago__text topcard__flavor--metadata">4 days ago</span>
      <span class="num-applicants__figure topcard__flavor--metadata">Over 200 applicants</span>
    </div>
  </h4>
  <a class="face-pile">See who Acme Systems has hired for this role</a>
  <button>Apply</button>
  <div>Join or sign in to find your next job</div>
</section>
<section class="core-section-container my-3 description">
  <div class="description__text description__text--rich">
    <section class="show-more-less-html" data-max-lines="5">
      <div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5 relative">
        Key Responsibilities:<br><br>
        <ul>
          <li>Design, develop and maintain backend services in Python and Django.</li>
          <li>Build RESTful APIs, ensuring correctness, security and performance.</li>
          <li>Write clean, reusable, well-documented code and review others' code.</li>
        </ul>
        Requirements:<br>
        <ul><li>3+ years of Python experience.</li><li>Solid SQL and PostgreSQL skills.</li></ul>
      </div>
      <button class="show-more-less-html__button">Show more</button>
    </section>
  </div>
  <ul class="description__job-criteria-list">
    <li class="description__job-criteria-item">
      <h3 class="description__job-criteria-subheader">Seniority level</h3>
      <span class="description__job-criteria-text description__job-criteria-text--criteria">Mid-Senior level</span>
    </li>
    <li class="description__job-criteria-item">
      <h3 class="description__job-criteria-subheader">Employment type</h3>
      <span class="description__job-criteria-text description__job-criteria-text--criteria">Full-time</span>
    </li>
  </ul>
</section>
<section class="similar-jobs">Referrals increase your chances of interviewing at Acme Systems by 2x
See who you know</section>
"""

GREENHOUSE_API_JOB = json.dumps({
    "title": "Account Manager",
    "company_name": "Acme",
    "location": {"name": "Dublin, Ireland"},
    "content": "&lt;p&gt;You will own a book of enterprise accounts and grow them.&lt;/p&gt;"
               "&lt;ul&gt;&lt;li&gt;5+ years in account management&lt;/li&gt;&lt;/ul&gt;",
})

COMPANY_PAGE_WITH_GREENHOUSE = """
<html><head><title>Account Manager - Careers at Acme</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebPage","name":"Careers"}]}</script>
</head><body>
<nav>Home Careers Teams</nav>
<div id="grnhse_app"></div>
<script id="greenhouse-script-js" src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>
</body></html>
"""


def _page_router(pages):
    """A _read_page stand-in that answers by URL prefix."""
    def read(url, *args, **kwargs):
        for prefix, body in pages.items():
            if url.startswith(prefix):
                return body
        raise AssertionError(f"Unexpected fetch: {url}")
    return read


class _FetchTestCase(TestCase):
    def setUp(self):
        cache.clear()
        public = patch.object(fetch_module, "_is_public_address", return_value=True)
        public.start()
        self.addCleanup(public.stop)


class LinkedInImportTests(_FetchTestCase):
    def fetch(self):
        with patch.object(
            fetch_module, "_read_page",
            side_effect=_page_router({"https://www.linkedin.com/jobs-guest/": LINKEDIN_FRAGMENT}),
        ):
            return fetch_job_posting("https://www.linkedin.com/jobs/view/python-developer-at-acme-4468058393")

    def test_only_the_description_is_imported(self):
        description = self.fetch()["description"]

        self.assertTrue(description.startswith("Key Responsibilities:"))
        self.assertIn("- Build RESTful APIs, ensuring correctness, security and performance.", description)
        self.assertIn("3+ years of Python experience.", description)
        for chrome in (
            "applicants", "days ago", "Join or sign in", "See who", "Referrals increase",
            "Show more", "Apply", "Seniority level",
        ):
            self.assertNotIn(chrome, description)

    def test_card_details_become_separate_fields(self):
        result = self.fetch()

        self.assertEqual(result["title"], "Python Developer")
        self.assertEqual(result["company"], "Acme Systems")
        self.assertEqual(result["location"], "Bengaluru, Karnataka, India")
        self.assertEqual(result["employment_type"], "Full-time")
        self.assertEqual(result["seniority"], "Mid-Senior level")
        self.assertEqual(result["source"], "linkedin")

    def test_current_job_id_links_are_understood(self):
        with patch.object(
            fetch_module, "_read_page",
            side_effect=_page_router({"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4468058393": LINKEDIN_FRAGMENT}),
        ):
            result = fetch_job_posting("https://www.linkedin.com/jobs/search/?currentJobId=4468058393&keywords=python")
        self.assertEqual(result["title"], "Python Developer")


class CompanyCareerPageTests(_FetchTestCase):
    def test_greenhouse_embedded_on_a_company_site_is_read_through_its_api(self):
        with patch.object(fetch_module, "_read_page", side_effect=_page_router({
            "https://careers.acme.com/": COMPANY_PAGE_WITH_GREENHOUSE,
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs/8184174": GREENHOUSE_API_JOB,
        })):
            result = fetch_job_posting("https://careers.acme.com/positions/8184174?gh_jid=8184174")

        self.assertEqual(result["title"], "Account Manager")
        self.assertEqual(result["company"], "Acme")
        self.assertEqual(result["location"], "Dublin, Ireland")
        self.assertIn("own a book of enterprise accounts", result["description"])
        self.assertIn("- 5+ years in account management", result["description"])
        self.assertNotIn("Home Careers Teams", result["description"])
        self.assertEqual(result["source"], "greenhouse")

    def test_any_public_site_is_accepted_by_default(self):
        self.assertTrue(fetch_module.is_job_domain_allowed("careers.somecompany.example"))

    @override_settings(JOB_URL_ALLOW_ANY_PUBLIC_SITE=False, ALLOWED_JOB_URL_DOMAINS=["greenhouse.io"])
    def test_allowlist_mode_still_works(self):
        self.assertFalse(fetch_module.is_job_domain_allowed("careers.somecompany.example"))
        self.assertTrue(fetch_module.is_job_domain_allowed("boards.greenhouse.io"))


class StructuredDataFieldTests(_FetchTestCase):
    def test_json_ld_company_location_and_employment_type(self):
        page = """<html><head><script type="application/ld+json">
        {"@type":"JobPosting","title":"Data Engineer",
         "hiringOrganization":{"@type":"Organization","name":"Globex"},
         "employmentType":["FULL_TIME","CONTRACTOR"],
         "jobLocation":[{"@type":"Place","address":{"addressLocality":"Pune","addressRegion":"MH","addressCountry":{"name":"India"}}}],
         "description":"&lt;p&gt;Build data pipelines in Spark.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Airflow&lt;/li&gt;&lt;/ul&gt;"}
        </script></head><body></body></html>"""
        with patch.object(fetch_module, "_read_page", return_value=page):
            result = fetch_job_posting("https://jobs.globex.example/postings/77")

        self.assertEqual(result["company"], "Globex")
        self.assertEqual(result["location"], "Pune, MH, India")
        self.assertEqual(result["employment_type"], "Full-time, Contract")
        self.assertEqual(result["description"], "Build data pipelines in Spark.\n\n- Airflow")
        self.assertEqual(result["source"], "json_ld")

    def test_plain_text_description_keeps_its_line_breaks(self):
        page = """<html><head><script type="application/ld+json">
        {"@type":"JobPosting","title":"Nurse","description":"Night shifts.\\nRegistered nurse licence required."}
        </script></head></html>"""
        with patch.object(fetch_module, "_read_page", return_value=page):
            result = fetch_job_posting("https://jobs.hospital.example/1")
        self.assertEqual(result["description"], "Night shifts.\nRegistered nurse licence required.")


class BotWalledSiteTests(_FetchTestCase):
    def test_known_blocking_sites_fail_instantly_by_name(self):
        cases = {
            "https://www.naukri.com/job-listings-python-developer-240826007480": "Naukri",
            "https://in.indeed.com/viewjob?jk=35267e52b60933e9": "Indeed",
            "https://www.glassdoor.co.in/job-listing/x-JV_KO0,1.htm?jl=1": "Glassdoor",
        }
        for url, name in cases.items():
            with patch.object(fetch_module, "_read_page") as read:
                with self.assertRaises(JobUrlBlockedError) as raised:
                    fetch_job_posting(url)
            self.assertIn(name, raised.exception.message)
            read.assert_not_called()

    def test_suffix_wildcards_do_not_match_lookalike_domains(self):
        self.assertIsNone(bot_walled_site("notindeed.com"))
        self.assertIsNone(bot_walled_site("indeedjobs.example"))
        self.assertEqual(bot_walled_site("uk.indeed.com"), "Indeed")


class _FakeResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.released = False

    def stream(self, _amount, decode_content=None):
        yield self._body

    def release_conn(self):
        self.released = True


class NetworkSafetyTests(TestCase):
    """The connection must go to the address that was checked, never to a
    fresh DNS answer, and every redirect hop is checked again."""

    PUBLIC = ipaddress.ip_address("93.184.216.34")

    def _pools(self, *responses):
        pools = []
        queue = list(responses)

        def make_pool(host, **kwargs):
            pool = MagicMock()
            pool.host = host
            pool.kwargs = kwargs
            pool.urlopen.return_value = queue.pop(0)
            pools.append(pool)
            return pool
        return pools, make_pool

    def test_connects_to_the_validated_address_with_the_real_hostname(self):
        pools, make_pool = self._pools(
            _FakeResponse(200, {"Content-Type": "text/html; charset=utf-8"}, b"<p>ok</p>")
        )
        with patch.object(fetch_module, "_public_ips", return_value=[self.PUBLIC]), \
                patch.object(fetch_module, "HTTPSConnectionPool", side_effect=make_pool):
            body = fetch_module._read_page("https://jobs.example.com/posting/1?x=1")

        self.assertEqual(body, "<p>ok</p>")
        pool = pools[0]
        self.assertEqual(pool.host, "93.184.216.34")
        self.assertEqual(pool.kwargs["server_hostname"], "jobs.example.com")
        self.assertEqual(pool.kwargs["assert_hostname"], "jobs.example.com")
        _, path = pool.urlopen.call_args.args
        self.assertEqual(path, "/posting/1?x=1")
        self.assertEqual(pool.urlopen.call_args.kwargs["headers"]["Host"], "jobs.example.com")
        self.assertFalse(pool.urlopen.call_args.kwargs["redirect"])

    def test_pages_are_requested_as_html_only(self):
        # Workday answers a request that also accepts JSON with a small
        # client-side redirect instead of the posting.
        pools, make_pool = self._pools(_FakeResponse(200, {"Content-Type": "text/html"}, b"<p>ok</p>"))
        with patch.object(fetch_module, "_public_ips", return_value=[self.PUBLIC]),                 patch.object(fetch_module, "HTTPSConnectionPool", side_effect=make_pool):
            fetch_module._read_page("https://jobs.example.com/posting/1")
        self.assertEqual(
            pools[0].urlopen.call_args.kwargs["headers"]["Accept"], "text/html,application/xhtml+xml"
        )

    def test_gzip_bodies_are_decompressed(self):
        body = gzip.compress("<p>Café engineer</p>".encode("utf-8"))
        _, make_pool = self._pools(
            _FakeResponse(200, {"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"}, body)
        )
        with patch.object(fetch_module, "_public_ips", return_value=[self.PUBLIC]),                 patch.object(fetch_module, "HTTPSConnectionPool", side_effect=make_pool):
            self.assertEqual(fetch_module._read_page("https://jobs.example.com/p"), "<p>Café engineer</p>")

    def test_a_compressed_body_cannot_expand_past_the_size_cap(self):
        bomb = gzip.compress(b"a" * (fetch_module._MAX_BYTES * 4))
        self.assertEqual(len(fetch_module._decompress(bomb, "gzip")), fetch_module._MAX_BYTES)

    def test_redirect_to_a_private_address_is_refused(self):
        pools, make_pool = self._pools(
            _FakeResponse(302, {"Location": "http://metadata.internal/latest/"})
        )

        def ips(hostname):
            return [self.PUBLIC] if hostname == "jobs.example.com" else []

        with patch.object(fetch_module, "_public_ips", side_effect=ips), \
                patch.object(fetch_module, "HTTPSConnectionPool", side_effect=make_pool), \
                patch.object(fetch_module, "HTTPConnectionPool", side_effect=make_pool):
            with self.assertRaises(JobUrlNotAllowedError):
                fetch_module._read_page("https://jobs.example.com/posting/1")
        self.assertEqual(len(pools), 1, "the private redirect target must never be connected to")

    def test_status_codes_map_to_specific_errors(self):
        for status, error in ((403, JobUrlBlockedError), (429, JobUrlBlockedError), (404, JobUrlNotFoundError)):
            _, make_pool = self._pools(_FakeResponse(status, {"Content-Type": "text/html"}))
            with patch.object(fetch_module, "_public_ips", return_value=[self.PUBLIC]), \
                    patch.object(fetch_module, "HTTPSConnectionPool", side_effect=make_pool):
                with self.assertRaises(error):
                    fetch_module._read_page("https://jobs.example.com/posting/1")

    def test_an_exhausted_time_budget_stops_before_connecting(self):
        token = fetch_module._deadline.set(time.monotonic() - 1)
        try:
            with patch.object(fetch_module, "_public_ips", return_value=[self.PUBLIC]), \
                    patch.object(fetch_module, "HTTPSConnectionPool") as pool_class:
                with self.assertRaises(JobUrlTimeoutError):
                    fetch_module._read_page("https://jobs.example.com/posting/1")
            pool_class.assert_not_called()
        finally:
            fetch_module._deadline.reset(token)


class DescriptionCleanupTests(TestCase):
    def test_board_chrome_lines_are_dropped_but_sentences_are_kept(self):
        text = "\n".join([
            "Over 200 applicants",
            "Be among the first 25 applicants",
            "Posted 3 days ago",
            "Easy Apply",
            "Show more",
            "Apply before 30 June with a cover letter.",
            "You will save the team hours every week.",
        ])
        self.assertEqual(
            finalize_description(text),
            "Apply before 30 June with a cover letter.\nYou will save the team hours every week.",
        )


class ImportServiceTests(TestCase):
    URL = "https://jobs.lever.co/acme/1234abcd-0000?utm_source=newsletter"
    POSTING = {
        "title": "Engineer", "description": "Build things.", "company": "", "location": "",
        "employment_type": "", "seniority": "", "source": "lever",
    }

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            name="Jane", email="jane-import@example.com", phone="1234567890", password="safe-password",
        )

    def test_a_repeat_import_is_served_from_cache_and_both_are_recorded(self):
        with patch("resumes.services.job_import_service.fetch_job_posting", return_value=self.POSTING) as fetch:
            import_job_posting(self.URL, user=self.user)
            # Same posting, different tracking parameter: still one fetch.
            second = import_job_posting("https://jobs.lever.co/acme/1234abcd-0000?utm_source=x", user=self.user)

        fetch.assert_called_once()
        self.assertEqual(second["title"], "Engineer")
        sources = list(JobImportAttempt.objects.order_by("created_at").values_list("source", flat=True))
        self.assertEqual(sources, ["lever", "cache"])
        attempt = JobImportAttempt.objects.first()
        self.assertEqual(attempt.domain, "jobs.lever.co")
        self.assertNotIn("utm_source", attempt.url)

    def test_failures_are_recorded_with_their_code_and_not_cached(self):
        with patch(
            "resumes.services.job_import_service.fetch_job_posting", side_effect=JobUrlBlockedError(),
        ) as fetch:
            for _ in range(2):
                with self.assertRaises(JobUrlBlockedError):
                    import_job_posting(self.URL, user=self.user)

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(
            list(JobImportAttempt.objects.values_list("outcome", flat=True)),
            ["JOB_URL_BLOCKED", "JOB_URL_BLOCKED"],
        )

    def test_a_bookkeeping_failure_never_fails_the_import(self):
        with patch("resumes.services.job_import_service.fetch_job_posting", return_value=self.POSTING), \
                patch.object(JobImportAttempt.objects, "create", side_effect=RuntimeError("db down")):
            result = import_job_posting(self.URL, user=self.user)
        self.assertEqual(result["description"], "Build things.")

    def test_stats_by_domain(self):
        for outcome in ("SUCCESS", "SUCCESS", "JOB_URL_BLOCKED", "SUCCESS"):
            JobImportAttempt.objects.create(domain="naukri.com" if outcome != "SUCCESS" else "linkedin.com",
                                            url="x", outcome=outcome, source="linkedin", duration_ms=400)
        rows = {row["domain"]: row for row in stats_by_domain(30)}
        self.assertEqual(rows["linkedin.com"]["success_rate"], 1.0)
        self.assertEqual(rows["naukri.com"]["success_rate"], 0.0)
        self.assertEqual(rows["naukri.com"]["top_failure"], "JOB_URL_BLOCKED")


class AdminJobImportStatsAPITests(TestCase):
    url = "/api/resumes/admin/job-imports/stats/"

    def test_admin_only(self):
        user = User.objects.create_user(name="U", email="u-stats@example.com", phone="1111111111", password="x-pass-123")
        client = APIClient()
        client.force_authenticate(user=user)
        self.assertEqual(client.get(self.url).status_code, 403)

    def test_returns_per_domain_rates_and_the_last_daily_check(self):
        admin = User.objects.create_superuser(email="a-stats@example.com", name="A", phone="2222222222", password="x-pass-123")
        JobImportAttempt.objects.create(domain="linkedin.com", url="x", outcome="SUCCESS", source="linkedin")
        JobImportAttempt.objects.create(
            domain="lever.co", url="y", outcome="SUCCESS", source="lever", trigger=JobImportAttempt.TRIGGER_CANARY,
        )
        client = APIClient()
        client.force_authenticate(user=admin)

        response = client.get(self.url, {"days": 7})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["domain"] for row in response.data["domains"]], ["linkedin.com"])
        self.assertEqual([row["domain"] for row in response.data["last_daily_check"]], ["lever.co"])


@override_settings(OPS_ALERT_EMAIL="ops@example.com")
class DailyCheckTests(TestCase):
    GOOD = {"title": "Engineer", "description": "x" * 400}

    def run_with(self, import_side_effect):
        from resumes.services import job_import_canary

        checks = (("Lever", lambda: "https://jobs.lever.co/a/1"), ("Workday", lambda: "https://wd.example/j/2"))
        with patch.object(job_import_canary, "CHECKS", checks), \
                patch.object(job_import_canary, "import_job_posting", side_effect=import_side_effect):
            return job_import_canary.run_daily_check()

    def test_all_working_sends_no_alert(self):
        result = self.run_with(lambda url, **kwargs: self.GOOD)
        self.assertEqual(result["failed"], [])
        self.assertEqual(len(mail.outbox), 0)

    def test_a_broken_platform_sends_an_alert(self):
        def side_effect(url, **kwargs):
            if "wd.example" in url:
                raise JobUrlBlockedError()
            return self.GOOD

        result = self.run_with(side_effect)

        self.assertEqual(result["failed"], ["Workday"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ops@example.com"])
        self.assertIn("Workday: Import failed: JOB_URL_BLOCKED", mail.outbox[0].body)
        self.assertIn("Working: Lever", mail.outbox[0].body)

    def test_a_suspiciously_short_import_counts_as_broken(self):
        result = self.run_with(lambda url, **kwargs: {"title": "Engineer", "description": "Apply now"})
        self.assertEqual(result["failed"], ["Lever", "Workday"])
