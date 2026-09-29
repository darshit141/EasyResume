from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import AdminAuditLog
from authentication.models import User
from blog.models import NewsletterSubscriber, Post

POSTS = "/api/blog/admin/posts/"


class AdminPostEditorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(email="editor-admin@example.test", password="x-Pass-12345", is_staff=True)
        self.user = User.objects.create_user(email="editor-user@example.test", password="x-Pass-12345")
        self.client.force_authenticate(self.admin)

    def create(self, **data):
        body = {"title": "Five resume myths", "body": "Myth one.\n\n## Why", "tags": ["resume tips", "ats"]}
        body.update(data)
        return self.client.post(POSTS, body, format="json")

    def public_slugs(self):
        return [p["slug"] for p in self.client.get("/api/blog/posts/").json()]

    def test_only_admins(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(POSTS).status_code, 401)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(POSTS).status_code, 403)
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(self.client.get("/api/blog/admin/subscribers/").status_code, 403)

    def test_a_new_post_starts_as_a_hidden_draft(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        post = response.json()
        self.assertEqual((post["slug"], post["state"], post["tags"]), ("five-resume-myths", "draft", ["resume tips", "ats"]))
        self.assertEqual(Post.objects.get().tags, "resume tips, ats")
        self.assertNotIn("five-resume-myths", self.public_slugs())
        self.assertTrue(AdminAuditLog.objects.filter(action="blog_post_created").exists())

    def test_publish_now_then_unpublish(self):
        pk = self.create().json()["id"]
        response = self.client.patch(f"{POSTS}{pk}/", {"status": "published"}, format="json")
        self.assertEqual(response.json()["state"], "published")
        self.assertIn("five-resume-myths", self.public_slugs())
        self.assertTrue(AdminAuditLog.objects.filter(action="blog_post_published").exists())
        first_date = response.json()["published_at"]

        self.client.patch(f"{POSTS}{pk}/", {"status": "draft"}, format="json")
        self.assertNotIn("five-resume-myths", self.public_slugs())
        self.assertTrue(AdminAuditLog.objects.filter(action="blog_post_unpublished").exists())
        # Back live after a fix: keeps the date it first went out.
        again = self.client.patch(f"{POSTS}{pk}/", {"status": "published"}, format="json").json()
        self.assertEqual(again["published_at"], first_date)

    def test_schedule_for_later(self):
        when = (timezone.now() + timedelta(days=2)).replace(microsecond=0)
        response = self.create(status="published", published_at=when.isoformat())
        self.assertEqual(response.json()["state"], "scheduled")
        self.assertNotIn("five-resume-myths", self.public_slugs())
        # Publishing now from a draft that was only scheduled goes live now.
        pk = response.json()["id"]
        self.client.patch(f"{POSTS}{pk}/", {"status": "draft"}, format="json")
        now_live = self.client.patch(f"{POSTS}{pk}/", {"status": "published"}, format="json").json()
        self.assertEqual(now_live["state"], "published")

    def test_address_is_cleaned_and_must_be_unique(self):
        self.create()
        taken = self.create(title="Another", slug="Five Resume Myths")
        self.assertEqual(taken.status_code, 400)
        self.assertEqual(taken.json()["slug"], ["Another post already uses this address."])
        ok = self.create(title="Another", slug="My New Address!")
        self.assertEqual(ok.json()["slug"], "my-new-address")
        # Clearing the address derives it from the title again.
        edited = self.client.patch(f"{POSTS}{ok.json()['id']}/", {"slug": ""}, format="json").json()
        self.assertEqual(edited["slug"], "another")

    def test_needs_a_title_and_text(self):
        self.assertEqual(self.create(title="  ").json()["title"], ["Give the post a title."])
        self.assertEqual(self.create(body="   ").json()["body"], ["Write the post before saving it."])

    def test_tags_are_tidied(self):
        post = self.create(tags=" ATS , ats, cover letters,, ").json()
        self.assertEqual(post["tags"], ["ATS", "cover letters"])

    def test_editing_leaves_the_cover_alone(self):
        pk = self.create().json()["id"]
        slug = Post.objects.get(pk=pk).slug
        self.client.patch(f"/api/blog/admin/covers/{slug}/", {"source": "builtin", "builtin": "track-board"}, format="json")
        self.client.patch(f"{POSTS}{pk}/", {"excerpt": "Short summary."}, format="json")
        post = Post.objects.get(pk=pk)
        self.assertEqual((post.cover_source, post.cover_builtin, post.excerpt), ("builtin", "track-board", "Short summary."))

    def test_list_and_delete(self):
        pk = self.create().json()["id"]
        rows = self.client.get(POSTS).json()
        self.assertEqual([r["id"] for r in rows], [pk])
        self.assertNotIn("body", rows[0])
        self.assertEqual(self.client.delete(f"{POSTS}{pk}/").status_code, 204)
        self.assertFalse(Post.objects.exists())
        self.assertTrue(AdminAuditLog.objects.filter(action="blog_post_deleted", target=f"blog_post:{pk}").exists())
        self.assertEqual(self.client.delete(f"{POSTS}{pk}/").status_code, 404)


class AdminSubscriberTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(email="subs-admin@example.test", password="x-Pass-12345", is_staff=True)
        )
        self.on = NewsletterSubscriber.objects.create(email="reader@example.test", source="footer")
        self.off = NewsletterSubscriber.objects.create(email="gone@example.test", is_active=False)

    def test_list_and_unsubscribe(self):
        data = self.client.get("/api/blog/admin/subscribers/").json()
        self.assertEqual((data["active"], data["total"]), (1, 2))
        row = self.client.patch(f"/api/blog/admin/subscribers/{self.on.pk}/", {"is_active": False}, format="json")
        self.assertFalse(row.json()["is_active"])
        self.assertEqual(
            self.client.patch(f"/api/blog/admin/subscribers/{self.on.pk}/", {"is_active": "no"}, format="json").status_code,
            400,
        )

    def test_export_has_only_active_subscribers(self):
        response = self.client.get("/api/blog/admin/subscribers/?export=csv")
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        text = response.content.decode()
        self.assertIn("reader@example.test", text)
        self.assertNotIn("gone@example.test", text)
        self.assertTrue(AdminAuditLog.objects.filter(action="subscribers_exported").exists())
