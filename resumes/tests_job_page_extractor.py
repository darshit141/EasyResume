"""Job links must import the posting, not the website around it.

The pages below are modelled on real company career sites: the posting is a
fraction of the markup, surrounded by div-based menus, cookie banners,
share bars, "similar jobs" lists and footers that are not <nav> or <footer>
tags. Before job_page_extractor existed, all of it ended up in the
description and was sent for AI analysis.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from resumes.services.job_page_extractor import (
    JobPageNotAPostingError,
    clean_plain_text,
    extract_job_content,
)
from resumes.services.job_url_fetch_service import (
    JobUrlNotAPostingError,
    JobUrlUnreadableError,
    fetch_job_posting,
)

POSTING_BODY = """
<h2>About the role</h2>
<p>We're looking for a Senior Backend Engineer to design, build and operate the
APIs behind our payments platform, working closely with product and data teams.</p>
<h2>What you'll do</h2>
<ul>
  <li>Build and maintain Python and Django services that handle millions of requests.</li>
  <li>Own the reliability of PostgreSQL databases, including migrations and tuning.</li>
  <li>Mentor other engineers through code review and design discussions.</li>
</ul>
<h2>Requirements</h2>
<ul>
  <li>5+ years of experience building backend systems in Python.</li>
  <li>Strong knowledge of relational databases and REST API design.</li>
</ul>
<h2>Benefits</h2>
<p>Hybrid working from our Berlin office, 30 days of holiday and a learning budget.</p>
"""

SITE_CHROME_TOP = """
<div class="site-menu">
  <a href="/">Home</a> <a href="/about">About us</a> <a href="/products">Our products</a>
  <a href="/investors">Investor relations</a> <a href="/press">Press room</a>
</div>
<div id="cookie-consent" class="cookie-content">
  We use cookies to improve your experience. By continuing you accept our cookie policy.
  <a href="/privacy">Read more</a>
</div>
<div class="breadcrumbs"><a href="/careers">Careers</a> / Engineering</div>
"""

SITE_CHROME_BOTTOM = """
<div class="share-bar">Share this job on LinkedIn, Twitter or by email with a friend today.</div>
<div class="similar-jobs">
  <h3>Similar jobs</h3>
  <a href="/jobs/frontend-engineer">Frontend Engineer, Berlin</a>
  <a href="/jobs/data-engineer">Data Engineer, Remote</a>
  <a href="/jobs/sre">Site Reliability Engineer, Munich</a>
</div>
<div class="newsletter-signup">Subscribe to our newsletter for monthly company news and product updates.</div>
<div class="site-footer-links">
  Acme GmbH, Friedrichstrasse 1, Berlin. Imprint. Privacy. Terms. Copyright 2026 Acme. All rights reserved.
</div>
"""

CHROME_PHRASES = [
    "Investor relations",
    "We use cookies",
    "Share this job",
    "Similar jobs",
    "Data Engineer, Remote",
    "Subscribe to our newsletter",
    "All rights reserved",
    "Careers / Engineering",
]


def page(body, title="Senior Backend Engineer | Acme Careers"):
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


class ExtractionTests(SimpleTestCase):
    def assert_only_the_posting(self, result):
        description = result["description"]
        for phrase in ("payments platform", "PostgreSQL databases", "5+ years of experience", "learning budget"):
            self.assertIn(phrase, description)
        for phrase in CHROME_PHRASES:
            self.assertNotIn(phrase, description)

    def test_a_named_description_container_is_used(self):
        markup = page(
            SITE_CHROME_TOP
            + '<div class="job-header"><h1>Senior Backend Engineer</h1><span>Berlin</span></div>'
            + f'<div class="job-description">{POSTING_BODY}</div>'
            + SITE_CHROME_BOTTOM
        )

        result = extract_job_content(markup)

        self.assert_only_the_posting(result)
        self.assertEqual(result["title"], "Senior Backend Engineer")

    def test_an_unmarked_page_is_found_by_its_content(self):
        """No helpful class names at all — the posting is found by what it
        reads like: paragraphs and lists, few links, job wording."""
        markup = page(
            SITE_CHROME_TOP
            + '<div class="wrapper"><div class="col-8">'
            + POSTING_BODY
            + '</div><div class="col-4"><a href="/jobs/a">Frontend Engineer, Berlin</a>'
            '<a href="/jobs/b">Data Engineer, Remote</a></div></div>'
            + SITE_CHROME_BOTTOM
        )

        result = extract_job_content(markup)

        self.assert_only_the_posting(result)

    def test_sections_split_across_siblings_are_kept_together(self):
        markup = page(
            '<main>'
            '<section class="intro"><p>We\'re looking for a Senior Backend Engineer to join our '
            'payments platform team, building reliable, well-tested services for our customers.</p></section>'
            '<section class="reqs"><h2>Requirements</h2><ul>'
            '<li>5+ years of experience with Python, Django and PostgreSQL databases.</li>'
            '<li>Experience operating services in production, including on-call rotations.</li></ul></section>'
            '<section class="perks"><h2>Benefits</h2><p>Hybrid work, 30 days of holiday, '
            'and a yearly learning budget for courses, books and conferences.</p></section>'
            '</main>'
            + SITE_CHROME_BOTTOM
        )

        result = extract_job_content(markup)

        for phrase in ("payments platform", "5+ years of experience", "learning budget"):
            self.assertIn(phrase, result["description"])
        self.assertNotIn("Similar jobs", result["description"])

    def test_structure_is_kept_as_readable_text(self):
        markup = page(f'<div class="job-description">{POSTING_BODY}</div>')

        description = extract_job_content(markup)["description"]

        self.assertIn("What you'll do\n", description)
        self.assertIn("- Build and maintain Python and Django services", description)
        self.assertNotIn("<li>", description)
        self.assertNotIn("\n\n\n", description)

    def test_hidden_and_dialog_content_is_ignored(self):
        markup = page(
            f'<div class="job-description">{POSTING_BODY}</div>'
            '<div hidden>Hidden promotional text that nobody can see on the page at all.</div>'
            '<div role="dialog">Sign up for job alerts and never miss an opening again!</div>'
            '<div style="display: none">Secret tracking copy for experiments and analytics.</div>'
        )

        description = extract_job_content(markup)["description"]

        self.assertNotIn("Hidden promotional", description)
        self.assertNotIn("job alerts", description)
        self.assertNotIn("Secret tracking", description)

    def test_ui_lines_and_repeats_are_removed(self):
        markup = page(
            '<div class="job-description"><p>Apply now</p>'
            + POSTING_BODY
            + '<p>Apply now</p><p>Save job</p></div>'
        )

        description = extract_job_content(markup)["description"]

        self.assertNotIn("Apply now", description)
        self.assertNotIn("Save job", description)

    def test_a_non_english_posting_is_accepted(self):
        german = (
            '<div class="stellenbeschreibung"><h2>Deine Aufgaben</h2><ul>'
            '<li>Du entwickelst und betreibst unsere Python-Services in der Cloud.</li>'
            '<li>Du arbeitest eng mit Produkt- und Datenteams zusammen.</li></ul>'
            '<h2>Dein Profil</h2><ul><li>Mehrjährige Erfahrung in der Backend-Entwicklung.</li>'
            '<li>Sehr gute Kenntnisse in Django und PostgreSQL.</li></ul>'
            '<h2>Wir bieten</h2><p>Hybrides Arbeiten, 30 Urlaubstage und ein Weiterbildungsbudget.</p></div>'
        )

        result = extract_job_content(page(german, title="Backend-Entwickler (m/w/d) – Acme"))

        self.assertIn("Weiterbildungsbudget", result["description"])
        self.assertEqual(result["title"], "Backend-Entwickler (m/w/d)")

    def test_a_company_homepage_is_not_a_posting(self):
        homepage = page(
            SITE_CHROME_TOP
            + '<div class="hero"><p>Acme makes accounting simple for small businesses across Europe, '
            'with invoicing, bookkeeping and reporting in one friendly app that saves hours every week.</p>'
            '<p>Trusted by more than 40,000 companies, from corner shops to fast-growing startups, '
            'in twelve countries and nine languages.</p>'
            '<p>Start a free trial today and see why our customers rate us five stars for support.</p></div>'
            + SITE_CHROME_BOTTOM,
            title="Acme – Accounting for small businesses",
        )

        with self.assertRaises(JobPageNotAPostingError):
            extract_job_content(homepage)

    def test_a_careers_page_listing_openings_is_not_a_posting(self):
        links = "".join(
            f'<li><a href="/jobs/{4100 + n}/senior-role-{n}">Opening number {n}, Berlin or Remote</a></li>' for n in range(12)
        )
        listing = page(
            f'<div class="openings"><h1>Join our team</h1><ul>{links}</ul>'
            '<p>We are hiring engineers, designers and managers across all our offices.</p></div>',
            title="Careers at Acme",
        )

        with self.assertRaises(JobPageNotAPostingError):
            extract_job_content(listing)

    def test_a_posting_with_a_similar_jobs_list_is_still_a_posting(self):
        """Many job links on the page must not be mistaken for a listing
        when the page also has a real, substantial posting."""
        links = "".join(f'<a href="/jobs/{5000 + n}">Other job {n}</a>' for n in range(10))
        markup = page(f'<div class="job-description">{POSTING_BODY}</div><div class="more">{links}</div>')

        result = extract_job_content(markup)

        self.assertIn("payments platform", result["description"])
        self.assertNotIn("Other job", result["description"])

    def test_a_shell_page_returns_none(self):
        self.assertIsNone(extract_job_content(page('<div id="root"></div>')))

    def test_a_shell_page_with_job_menu_links_is_still_just_unreadable(self):
        """A client-rendered posting often ships only its site menu. Menu
        links like /jobs/search are not postings, so this must stay
        "unreadable" and reach the browser rescue, not be called a listing."""
        menu = "".join(
            f'<a href="/jobs/{slug}">{slug}</a>'
            for slug in ("search", "saved", "alerts", "teams", "locations", "students", "benefits", "faq")
        )
        self.assertIsNone(extract_job_content(page(f'<div class="top">{menu}</div><div id="root"></div>')))

    def test_a_page_of_posting_links_with_no_posting_text_is_a_listing(self):
        links = "".join(f'<li><a href="/jobs/{8100 + n}/">Role {n}</a></li>' for n in range(10))

        with self.assertRaises(JobPageNotAPostingError):
            extract_job_content(page(f'<ol class="list menu">{links}</ol>', title="Jobs"))

    def test_malformed_markup_does_not_break_extraction(self):
        broken = page(f'<div class="job-description"><p>Unclosed paragraph <b>bold{POSTING_BODY}</div></span></p>')

        result = extract_job_content(broken)

        self.assertIn("payments platform", result["description"])

    def test_a_huge_page_is_capped(self):
        body = "<p>" + ("Experience with Python and Django is required for this engineer role. " * 60) + "</p>"
        markup = page('<div class="job-description">' + body * 40 + "</div>")

        self.assertLessEqual(len(extract_job_content(markup)["description"]), 20000)

    def test_plain_text_cleanup_drops_ui_lines(self):
        cleaned = clean_plain_text("Apply now\nSenior Engineer\nApply now\nWe need Python experience.\nShare")

        self.assertEqual(cleaned, "Senior Engineer\nWe need Python experience.")


URL = "https://boards.greenhouse.io/acme/jobs/42"


class FetchIntegrationTests(TestCase):
    """The same behaviour through fetch_job_posting, which is what the import
    endpoint calls."""

    def fetch(self, markup):
        with patch("resumes.services.job_url_fetch_service._read_page", return_value=markup), \
                patch("resumes.services.job_url_fetch_service._is_public_address", return_value=True), \
                patch("resumes.services.job_url_fetch_service._try_adapters", return_value=None):
            return fetch_job_posting(URL)

    def test_a_company_career_page_imports_only_the_posting(self):
        markup = page(SITE_CHROME_TOP + f'<div class="job-description">{POSTING_BODY}</div>' + SITE_CHROME_BOTTOM)

        result = self.fetch(markup)

        for phrase in CHROME_PHRASES:
            self.assertNotIn(phrase, result["description"])
        self.assertEqual(result["title"], "Senior Backend Engineer")

    def test_a_homepage_link_gets_a_clear_error(self):
        homepage = page(
            '<div class="hero"><p>Acme makes accounting simple for small businesses across Europe, '
            'with invoicing, bookkeeping and reporting in one friendly app that saves hours.</p>'
            '<p>Trusted by more than 40,000 companies in twelve countries and nine languages.</p></div>',
            title="Acme",
        )

        with self.assertRaises(JobUrlNotAPostingError) as ctx:
            self.fetch(homepage)

        self.assertEqual(ctx.exception.code, "JOB_URL_NOT_A_POSTING")

    def test_the_full_body_beats_a_meta_summary(self):
        summary = "Senior Backend Engineer at Acme. " * 8
        markup = (
            f'<html><head><meta property="og:description" content="{summary}">'
            "<title>Senior Backend Engineer</title></head>"
            f'<body><div class="job-description">{POSTING_BODY}</div></body></html>'
        )

        result = self.fetch(markup)

        self.assertIn("learning budget", result["description"])

    def test_a_site_wide_meta_description_is_not_accepted_as_a_posting(self):
        site_blurb = "Acme makes accounting software that small businesses across Europe love to use every day. " * 3
        markup = f'<html><head><meta name="description" content="{site_blurb}"></head><body><div id="app"></div></body></html>'

        with self.assertRaises(JobUrlUnreadableError):
            self.fetch(markup)

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_the_browser_rescue_also_imports_only_the_posting(self):
        rendered = page(SITE_CHROME_TOP + f'<div class="job-description">{POSTING_BODY}</div>' + SITE_CHROME_BOTTOM)
        item = {"metadata": {"title": "Senior Backend Engineer"}, "html": rendered, "text": "everything " * 100}

        class _Response:
            status_code = 200

            def json(self):
                return [item]

        with patch("resumes.services.apify_job_fetch_service.requests.post", return_value=_Response()) as post:
            result = self.fetch(page('<div id="root"></div>'))

        self.assertEqual(post.call_args.kwargs["json"]["outputFormats"], ["html", "text"])
        self.assertIn("payments platform", result["description"])
        for phrase in CHROME_PHRASES:
            self.assertNotIn(phrase, result["description"])

    @override_settings(APIFY_API_TOKEN="apify_test_token")
    def test_a_rendered_homepage_is_reported_as_not_a_posting(self):
        rendered = page(
            '<div class="hero"><p>Acme makes accounting simple for small businesses across Europe, '
            'with invoicing, bookkeeping and reporting in one friendly app that saves hours.</p>'
            '<p>Trusted by more than 40,000 companies in twelve countries and nine languages.</p></div>'
        )

        class _Response:
            status_code = 200

            def json(self):
                return [{"metadata": {"title": "Acme"}, "html": rendered, "text": ""}]

        with patch("resumes.services.apify_job_fetch_service.requests.post", return_value=_Response()):
            with self.assertRaises(JobUrlNotAPostingError):
                self.fetch(page('<div id="root"></div>'))
