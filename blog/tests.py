from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from blog.models import NewsletterSubscriber, Post


def _post(**overrides):
    data = {
        "title": "How ATS Scoring Actually Works",
        "excerpt": "A short summary.",
        "body": "word " * 400,
        "status": Post.STATUS_PUBLISHED,
    }
    data.update(overrides)
    return Post.objects.create(**data)


class PostModelTests(TestCase):
    def test_slug_is_generated_from_title(self):
        post = _post(title="Beat The Bots In 2026")
        self.assertEqual(post.slug, "beat-the-bots-in-2026")

    def test_duplicate_titles_get_distinct_slugs(self):
        first = _post(title="Resume Tips")
        second = _post(title="Resume Tips")
        self.assertEqual(first.slug, "resume-tips")
        self.assertEqual(second.slug, "resume-tips-2")

    def test_publishing_stamps_published_at(self):
        post = _post()
        self.assertIsNotNone(post.published_at)

    def test_draft_does_not_get_published_at(self):
        post = _post(status=Post.STATUS_DRAFT)
        self.assertIsNone(post.published_at)

    def test_reading_time_is_at_least_one_minute(self):
        post = _post(body="just a few words")
        self.assertEqual(post.reading_minutes, 1)

    def test_reading_time_scales_with_length(self):
        post = _post(body="word " * 400)
        self.assertEqual(post.reading_minutes, 2)

    def test_tag_list_splits_and_strips(self):
        post = _post(tags=" ats , resume tips ,")
        self.assertEqual(post.tag_list, ["ats", "resume tips"])

    def test_published_manager_excludes_drafts(self):
        _post(title="Live One")
        _post(title="Hidden One", status=Post.STATUS_DRAFT)
        self.assertEqual([p.title for p in Post.published.all()], ["Live One"])

    def test_published_manager_excludes_future_scheduled_posts(self):
        _post(title="Later", published_at=timezone.now() + timedelta(days=1))
        self.assertFalse(Post.published.exists())


class PostAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_list_is_public_and_returns_published_posts(self):
        _post(title="Public Post")
        response = self.client.get("/api/blog/posts/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "Public Post")

    def test_list_omits_body_to_keep_the_index_small(self):
        _post()
        response = self.client.get("/api/blog/posts/")
        self.assertNotIn("body", response.data[0])

    def test_list_can_filter_by_tag(self):
        _post(title="About ATS", tags="ats")
        _post(title="About Interviews", tags="interviews")

        response = self.client.get("/api/blog/posts/?tag=ats")

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "About ATS")

    def test_detail_returns_body_and_seo_fields(self):
        post = _post(title="Detail Post", excerpt="The summary.")
        response = self.client.get(f"/api/blog/posts/{post.slug}/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("body", response.data)
        self.assertEqual(response.data["seo_title"], "Detail Post")
        self.assertEqual(response.data["seo_description"], "The summary.")

    def test_explicit_seo_fields_win_over_fallbacks(self):
        post = _post(seo_title="Custom Title", seo_description="Custom description.")
        response = self.client.get(f"/api/blog/posts/{post.slug}/")

        self.assertEqual(response.data["seo_title"], "Custom Title")
        self.assertEqual(response.data["seo_description"], "Custom description.")

    def test_draft_post_is_not_reachable_by_slug(self):
        post = _post(status=Post.STATUS_DRAFT)
        response = self.client.get(f"/api/blog/posts/{post.slug}/")
        self.assertEqual(response.status_code, 404)


class NewsletterAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_valid_email_is_stored(self):
        response = self.client.post(
            "/api/blog/subscribe/", {"email": "reader@example.com", "source": "footer"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(NewsletterSubscriber.objects.filter(email="reader@example.com").exists())

    def test_email_is_normalised_to_lowercase(self):
        self.client.post("/api/blog/subscribe/", {"email": "Reader@Example.com"}, format="json")
        self.assertTrue(NewsletterSubscriber.objects.filter(email="reader@example.com").exists())

    def test_resubscribing_succeeds_without_leaking_membership(self):
        self.client.post("/api/blog/subscribe/", {"email": "reader@example.com"}, format="json")
        response = self.client.post("/api/blog/subscribe/", {"email": "reader@example.com"}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(NewsletterSubscriber.objects.filter(email="reader@example.com").count(), 1)

    def test_resubscribing_reactivates_an_unsubscribed_address(self):
        NewsletterSubscriber.objects.create(email="reader@example.com", is_active=False)
        self.client.post("/api/blog/subscribe/", {"email": "reader@example.com"}, format="json")

        self.assertTrue(NewsletterSubscriber.objects.get(email="reader@example.com").is_active)

    def test_invalid_email_is_rejected(self):
        response = self.client.post("/api/blog/subscribe/", {"email": "not-an-email"}, format="json")
        self.assertEqual(response.status_code, 400)


@override_settings(FRONTEND_URL="https://example.com")
class SitemapAndRobotsTests(TestCase):
    """These are served at the site root (not /api/) because that's the
    only place a search engine will look for them, and both are built from
    FRONTEND_URL rather than the API's own host — see blog/views/seo_view.py.

    FRONTEND_URL is pinned explicitly rather than relied on from whatever
    the environment's .env happens to set — the exact mistake this suite
    exists to avoid repeating (see the BYOK quota tests for the same fix
    for the same reason).
    """

    def setUp(self):
        self.client = APIClient()

    def test_sitemap_lists_the_static_marketing_pages(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        body = response.content.decode()
        for path in ("/", "/how-it-works", "/features", "/pricing", "/faq", "/about", "/contact"):
            self.assertIn(f"<loc>https://example.com{path}</loc>", body)

    def test_sitemap_leaves_out_signup(self):
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertNotIn("https://example.com/signup<", body)

    def test_sitemap_lists_the_blog_only_once_it_has_posts(self):
        """An empty blog listing is thin content, so it stays out of the sitemap."""
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertNotIn("<loc>https://example.com/blog</loc>", body)

        _post(status=Post.STATUS_PUBLISHED)
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("<loc>https://example.com/blog</loc>", body)

    def test_sitemap_includes_only_published_posts(self):
        published = _post(status=Post.STATUS_PUBLISHED)
        draft = _post(title="Not ready yet", status=Post.STATUS_DRAFT)
        future = _post(
            title="Scheduled",
            status=Post.STATUS_PUBLISHED,
            published_at=timezone.now() + timedelta(days=3),
        )

        body = self.client.get("/sitemap.xml").content.decode()

        self.assertIn(f"/blog/{published.slug}</loc>", body)
        self.assertNotIn(f"/blog/{draft.slug}</loc>", body)
        self.assertNotIn(f"/blog/{future.slug}</loc>", body)

    def test_sitemap_never_lists_app_or_auth_routes(self):
        """A crawler gains nothing from an authenticated app shell or an
        OTP/reset link, and listing them just narrates internal structure."""
        body = self.client.get("/sitemap.xml").content.decode()

        for path in ("/app", "/login", "/verify-otp", "/reset-password"):
            self.assertNotIn(f"<loc>https://example.com{path}<", body)

    def test_robots_txt_points_at_the_sitemap_on_the_frontend_origin(self):
        response = self.client.get("/robots.txt")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertIn("Sitemap: https://example.com/sitemap.xml", response.content.decode())

    def test_robots_txt_disallows_the_authenticated_app_shell(self):
        body = self.client.get("/robots.txt").content.decode()
        self.assertIn("Disallow: /app/", body)

    @override_settings(DEBUG=True)
    def test_a_debug_deploy_disallows_everything(self):
        """The safe default for a non-production environment is staying out
        of search results entirely, not trusting every deploy to remember
        DEBUG=False before it's reachable."""
        body = self.client.get("/robots.txt").content.decode()

        self.assertIn("Disallow: /\n", body)
        self.assertNotIn("Sitemap:", body)


class FrontendUrlCheckTests(TestCase):
    def run_check(self, url):
        from django.test import override_settings

        from blog.checks import frontend_url_is_public

        with override_settings(FRONTEND_URL=url):
            return [w.id for w in frontend_url_is_public(None)]

    def test_localhost_is_flagged(self):
        self.assertEqual(self.run_check("http://localhost:5173"), ["blog.W001"])

    def test_retired_domain_is_flagged(self):
        self.assertEqual(self.run_check("https://intelliresume.patrixel.com"), ["blog.W002"])

    def test_live_domain_passes(self):
        self.assertEqual(self.run_check("https://intelliresume.online"), [])
