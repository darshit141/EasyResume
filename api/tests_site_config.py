"""The site's theme and pictures are chosen in the admin panel.

Theme settings go through SystemConfiguration (the admin Configuration PUT);
pictures through the site-image endpoints. Both are read by the public
site-config endpoint every page load, so changes need no redeploy.
"""

import io
import importlib

from django.apps import apps as django_apps
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from api.models import AdminAuditLog, SiteImage, SystemConfiguration
from api.site_images import SLOTS
from api.views.admin_views import DEFAULT_CONFIGS
from api.views.site_views import COLOR_MODES, THEMES
from authentication.models import User

URL = "/api/site/config/"


def picture(fmt="PNG", size=(64, 40), color=(200, 30, 60)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    buf.seek(0)
    buf.name = f"pic.{fmt.lower()}"
    return buf


def make_admin():
    return User.objects.create_user(
        email="admin-images@example.test", password="x-Strong-pass-1", is_staff=True, is_active=True
    )


@override_settings(SITE_THEME="indigo", SITE_DEFAULT_COLOR_MODE="system")
class PublicSiteConfigTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_public_defaults(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["theme"], {"preset": "indigo", "default_mode": "system"})
        self.assertEqual(response.json()["images"], {})
        self.assertEqual(response.json()["hero_visual"], "hero-full-loop")

    def test_theme_choice_applies_without_redeploy(self):
        for key, value in (("SITE_THEME", "emerald"), ("SITE_DEFAULT_COLOR_MODE", "dark")):
            SystemConfiguration.objects.create(key=key, value=value, value_type="string", category="appearance")
        self.assertEqual(self.client.get(URL).json()["theme"], {"preset": "emerald", "default_mode": "dark"})

    def test_unknown_theme_falls_back(self):
        SystemConfiguration.objects.create(key="SITE_THEME", value="neon", value_type="string", category="appearance")
        self.assertEqual(self.client.get(URL).json()["theme"]["preset"], "indigo")

    def test_admin_choices_match_what_the_endpoint_accepts(self):
        for key, allowed in (("SITE_THEME", THEMES), ("SITE_DEFAULT_COLOR_MODE", COLOR_MODES)):
            meta = next(d for d in DEFAULT_CONFIGS if d["key"] == key)
            self.assertEqual(tuple(c["value"] for c in meta["choices"]), allowed)

    def test_overrides_are_reported_per_slot(self):
        SiteImage.objects.create(slot="about_header", source="hidden")
        SiteImage.objects.create(slot="home_hero", source="builtin", builtin="hero-workspace")
        SiteImage.objects.create(slot="features_header", source="builtin", builtin="no-such-scene")
        body = self.client.get(URL).json()
        self.assertIsNone(body["images"]["about_header"])
        self.assertEqual(body["images"]["home_hero"], {"builtin": "hero-workspace"})
        # A name the site can't render leaves the default in place.
        self.assertNotIn("features_header", body["images"])
        self.assertEqual(body["hero_visual"], "hero-workspace")

    def test_hero_only_pictures_are_refused_elsewhere(self):
        SiteImage.objects.create(slot="about_header", source="builtin", builtin="example")
        self.assertNotIn("about_header", self.client.get(URL).json()["images"])


class AdminSiteImageTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin()
        self.client.force_authenticate(self.admin)

    def test_non_admins_are_refused(self):
        user = User.objects.create_user(email="plain@example.test", password="x-Strong-pass-1", is_active=True)
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(client.get("/api/admin/site-images/").status_code, 403)
        self.assertEqual(client.patch("/api/admin/site-images/home_hero/", {"source": "hidden"}, format="json").status_code, 403)
        self.assertEqual(
            client.post("/api/admin/site-images/home_hero/upload/", {"file": picture()}, format="multipart").status_code,
            403,
        )

    def test_list_covers_every_slot_with_its_default(self):
        body = self.client.get("/api/admin/site-images/").json()
        self.assertEqual([s["slot"] for s in body], list(SLOTS))
        hero = body[0]
        self.assertEqual(hero["source"], "default")
        self.assertEqual(hero["current"], {"builtin": "full-loop"})
        self.assertIn("example", [b["value"] for b in hero["builtins"]])
        about = next(s for s in body if s["slot"] == "about_header")
        self.assertNotIn("example", [b["value"] for b in about["builtins"]])

    def test_pick_hide_and_reset(self):
        url = "/api/admin/site-images/about_header/"
        response = self.client.patch(url, {"source": "builtin", "builtin": "quiet-studio", "alt_text": "A calm desk"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(URL).json()["images"]["about_header"], {"builtin": "quiet-studio", "alt": "A calm desk"})

        self.client.patch(url, {"source": "hidden"}, format="json")
        self.assertIsNone(self.client.get(URL).json()["images"]["about_header"])

        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertNotIn("about_header", self.client.get(URL).json()["images"])
        actions = list(AdminAuditLog.objects.values_list("action", flat=True))
        self.assertEqual(actions.count(AdminAuditLog.ACTION_SITE_IMAGE_UPDATED), 2)
        self.assertEqual(actions.count(AdminAuditLog.ACTION_SITE_IMAGE_RESET), 1)

    def test_rejects_unknown_slot_and_picture(self):
        self.assertEqual(self.client.patch("/api/admin/site-images/nope/", {"source": "hidden"}, format="json").status_code, 404)
        response = self.client.patch(
            "/api/admin/site-images/about_header/", {"source": "builtin", "builtin": "example"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.patch("/api/admin/site-images/about_header/", {"source": "custom"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_upload_is_reencoded_and_served(self):
        response = self.client.post(
            "/api/admin/site-images/home_cta/upload/",
            {"file": picture("JPEG", (3000, 1500)), "alt_text": "Our team"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        row = SiteImage.objects.get(slot="home_cta")
        self.assertEqual((row.width, row.height), (2400, 1200))
        self.assertEqual(bytes(row.image)[8:12], b"WEBP")

        override = APIClient().get(URL).json()["images"]["home_cta"]
        self.assertEqual((override["width"], override["height"], override["alt"]), (2400, 1200, "Our team"))
        self.assertTrue(override["src"].startswith("site/images/home_cta/?v="))

        served = APIClient().get("/api/" + override["src"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/webp")
        self.assertIn("immutable", served["Cache-Control"])
        self.assertEqual(served["Cross-Origin-Resource-Policy"], "cross-origin")

        # Picking a bundled picture again drops the upload.
        self.client.patch("/api/admin/site-images/home_cta/", {"source": "builtin", "builtin": "pipeline-wide"}, format="json")
        self.assertIsNone(SiteImage.objects.get(slot="home_cta").image)
        self.assertEqual(APIClient().get("/api/site/images/home_cta/").status_code, 404)

    def test_upload_refuses_non_pictures(self):
        fake = io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
        fake.name = "x.svg"
        response = self.client.post("/api/admin/site-images/home_cta/upload/", {"file": fake}, format="multipart")
        self.assertEqual(response.status_code, 400)
        gif = picture("GIF")
        response = self.client.post("/api/admin/site-images/home_cta/upload/", {"file": gif}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SiteImage.objects.filter(slot="home_cta").exists())


class ConfigChoiceValidationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin())

    def test_choice_keys_reject_values_off_the_list(self):
        response = self.client.put("/api/admin/config/", [{"key": "SITE_THEME", "value": "neon"}], format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.put("/api/admin/config/", [{"key": "SITE_THEME", "value": "rose"}], format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(APIClient().get(URL).json()["theme"]["preset"], "rose")


class HeroMigrationTests(TestCase):
    """0009 carries a pre-slot hero choice over to the new slot."""

    def run_forwards(self):
        module = importlib.import_module("api.migrations.0009_move_hero_visual_to_site_image")
        module.forwards(django_apps, None)

    def test_existing_choice_is_carried_over(self):
        SystemConfiguration.objects.create(key="HOMEPAGE_HERO_VISUAL", value="hero-evolution", value_type="string", category="site")
        self.run_forwards()
        self.assertEqual(SiteImage.objects.get(slot="home_hero").builtin, "hero-evolution")
        self.assertFalse(SystemConfiguration.objects.filter(key="HOMEPAGE_HERO_VISUAL").exists())

    def test_default_choice_needs_no_row(self):
        SystemConfiguration.objects.create(key="HOMEPAGE_HERO_VISUAL", value="hero-full-loop", value_type="string", category="site")
        self.run_forwards()
        self.assertFalse(SiteImage.objects.exists())
