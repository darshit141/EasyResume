import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image
from rest_framework.test import APIClient

from api.models import AdminAuditLog
from authentication.models import User
from blog.models import Post


def picture(fmt="PNG", size=(1600, 1000), name="cover.png"):
    out = io.BytesIO()
    Image.new("RGB", size, (20, 90, 160)).save(out, fmt)
    return SimpleUploadedFile(name, out.getvalue(), content_type=f"image/{fmt.lower()}")


def make_post(**overrides):
    data = {"title": "What an ATS actually does", "body": "word " * 300, "status": Post.STATUS_PUBLISHED}
    data.update(overrides)
    return Post.objects.create(**data)


class BlogCoverTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(email="covers-admin@example.test", password="x-Pass-12345", is_staff=True)
        self.user = User.objects.create_user(email="covers-user@example.test", password="x-Pass-12345")
        self.post = make_post(slug="what-an-ats-actually-does", tags="ats, resume tips")

    def as_admin(self):
        self.client.force_authenticate(self.admin)

    def public_cover(self, slug=None):
        return self.client.get(f"/api/blog/posts/{slug or self.post.slug}/").json()["cover"]

    def test_a_post_gets_a_picture_matched_to_its_topic(self):
        self.assertEqual(self.public_cover(), {"source": "auto", "builtin": "ats-overlay", "alt": ""})
        other = make_post(title="Landing interviews", tags="Cover Letters")
        self.assertEqual(self.public_cover(other.slug)["builtin"], "builder-split")
        plain = make_post(title="Anything else")
        self.assertEqual(self.public_cover(plain.slug)["builtin"], "match-bridge")

    def test_the_index_carries_each_cover(self):
        covers = [p["cover"] for p in self.client.get("/api/blog/posts/").json()]
        self.assertEqual(covers, [{"source": "auto", "builtin": "ats-overlay", "alt": ""}])

    def test_only_admins_can_change_covers(self):
        self.assertEqual(self.client.get("/api/blog/admin/covers/").status_code, 401)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/blog/admin/covers/").status_code, 403)
        response = self.client.patch(f"/api/blog/admin/covers/{self.post.slug}/", {"source": "hidden"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_admin_list_includes_drafts_and_their_automatic_picture(self):
        make_post(title="Unfinished", status=Post.STATUS_DRAFT)
        self.as_admin()
        rows = self.client.get("/api/blog/admin/covers/").json()
        self.assertEqual({r["status"] for r in rows}, {"draft", "published"})
        ats = next(r for r in rows if r["slot"] == self.post.slug)
        self.assertEqual(ats["source"], "default")
        self.assertEqual(ats["default"], "ats-overlay")
        self.assertIn("ats-overlay", [b["value"] for b in ats["builtins"]])

    def test_pick_another_picture_with_a_description(self):
        self.as_admin()
        response = self.client.patch(
            f"/api/blog/admin/covers/{self.post.slug}/",
            {"source": "builtin", "builtin": "track-board", "alt_text": "  A board of applications  "},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.public_cover(), {"source": "builtin", "builtin": "track-board", "alt": "A board of applications"})
        self.assertTrue(
            AdminAuditLog.objects.filter(target=f"blog_cover:{self.post.slug}", action="site_image_updated").exists()
        )

    def test_unknown_pictures_and_sources_are_refused(self):
        self.as_admin()
        url = f"/api/blog/admin/covers/{self.post.slug}/"
        self.assertEqual(self.client.patch(url, {"source": "builtin", "builtin": "nope"}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(url, {"source": "custom"}, format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/blog/admin/covers/missing/", {"source": "hidden"}, format="json").status_code, 404)

    def test_hide_the_cover(self):
        self.as_admin()
        self.client.patch(f"/api/blog/admin/covers/{self.post.slug}/", {"source": "hidden"}, format="json")
        self.assertEqual(self.public_cover(), {"source": "hidden"})

    def test_upload_is_stored_as_webp_and_served_publicly(self):
        self.as_admin()
        response = self.client.post(
            f"/api/blog/admin/covers/{self.post.slug}/upload/", {"file": picture(), "alt_text": "Our office"}
        )
        self.assertEqual(response.status_code, 201)
        cover = self.public_cover()
        self.assertEqual(cover["source"], "custom")
        self.assertEqual((cover["width"], cover["height"], cover["alt"]), (1600, 1000, "Our office"))
        self.client.force_authenticate(None)
        served = self.client.get("/api/" + cover["src"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/webp")
        self.assertIn("immutable", served["Cache-Control"])
        with Image.open(io.BytesIO(served.content)) as image:
            self.assertEqual(image.format, "WEBP")

    def test_a_drafts_upload_needs_its_version_token(self):
        draft = make_post(title="Coming soon", status=Post.STATUS_DRAFT)
        self.as_admin()
        row = self.client.post(f"/api/blog/admin/covers/{draft.slug}/upload/", {"file": picture()}).json()
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/" + row["current"]["src"]).status_code, 200)
        self.assertEqual(self.client.get(f"/api/blog/posts/{draft.slug}/cover/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/blog/posts/{draft.slug}/cover/?v=guess").status_code, 404)

    def test_bad_uploads_are_refused(self):
        self.as_admin()
        url = f"/api/blog/admin/covers/{self.post.slug}/upload/"
        gif = picture("GIF", name="x.gif")
        self.assertEqual(self.client.post(url, {"file": gif}).status_code, 400)
        junk = SimpleUploadedFile("x.png", b"not a picture", content_type="image/png")
        self.assertEqual(self.client.post(url, {"file": junk}).status_code, 400)
        self.assertEqual(self.client.post(url, {}).status_code, 400)
        self.assertEqual(self.public_cover()["source"], "auto")

    def test_choosing_a_picture_drops_the_upload(self):
        self.as_admin()
        self.client.post(f"/api/blog/admin/covers/{self.post.slug}/upload/", {"file": picture()})
        self.client.patch(
            f"/api/blog/admin/covers/{self.post.slug}/", {"source": "builtin", "builtin": "gap-columns"}, format="json"
        )
        self.post.refresh_from_db()
        self.assertIsNone(self.post.cover_data)
        self.assertEqual(self.client.get(f"/api/blog/posts/{self.post.slug}/cover/").status_code, 404)

    def test_reset_goes_back_to_automatic_and_leaves_the_post_alone(self):
        self.as_admin()
        before = Post.objects.get(pk=self.post.pk).updated_at
        self.client.post(f"/api/blog/admin/covers/{self.post.slug}/upload/", {"file": picture(), "alt_text": "x"})
        response = self.client.delete(f"/api/blog/admin/covers/{self.post.slug}/")
        self.assertEqual(response.json()["source"], "default")
        self.assertEqual(self.public_cover(), {"source": "auto", "builtin": "ats-overlay", "alt": ""})
        self.assertEqual(Post.objects.get(pk=self.post.pk).updated_at, before)
        self.assertTrue(AdminAuditLog.objects.filter(action="site_image_reset").exists())
