import math
import re

from django.db import models
from django.utils import timezone
from django.utils.text import slugify

# Rough but stable reading-speed constant. Used for the "5 min read" label —
# a display hint, never anything the product depends on being exact.
_WORDS_PER_MINUTE = 200


class PublishedPostManager(models.Manager):
    """Only posts that are published AND whose publish time has arrived.

    Scheduling a post for the future must not leak it early, so the time
    check belongs here rather than in each view.
    """

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(status=Post.STATUS_PUBLISHED, published_at__lte=timezone.now())
        )


class Post(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    excerpt = models.TextField(
        max_length=400,
        blank=True,
        help_text="Short summary shown on the blog index and in search results.",
    )
    body = models.TextField(help_text="Post content in Markdown.")
    cover_image = models.ImageField(upload_to="blog/", blank=True, null=True)
    tags = models.CharField(
        max_length=200,
        blank=True,
        help_text="Comma-separated, e.g. \"ats, resume tips\".",
    )
    author_name = models.CharField(max_length=100, default="IntelliResume")

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    published_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Set automatically when first published. May be set to a future time to schedule.",
    )

    seo_title = models.CharField(
        max_length=70, blank=True, help_text="Overrides the page title tag. Falls back to the post title."
    )
    seo_description = models.CharField(
        max_length=160, blank=True, help_text="Meta description. Falls back to the excerpt."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()
    published = PublishedPostManager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [models.Index(fields=["status", "published_at"])]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        # Stamp the publish time the first time a post actually goes live, so
        # ordering and scheduling work without the author having to set it.
        if self.status == self.STATUS_PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        base = slugify(self.title)[:200] or "post"
        slug = base
        suffix = 2
        while Post.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug

    @property
    def tag_list(self):
        return [tag.strip() for tag in self.tags.split(",") if tag.strip()]

    @property
    def reading_minutes(self):
        words = len(re.findall(r"\w+", self.body))
        return max(1, math.ceil(words / _WORDS_PER_MINUTE))


class NewsletterSubscriber(models.Model):
    """Replaces the footer's previously non-functional signup form.

    Storing subscribers locally means the signup is honest the moment it
    ships; exporting them into a real email provider is a later step that
    doesn't block collecting interest now.
    """

    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(
        max_length=50, blank=True, help_text="Where the signup came from, e.g. \"footer\" or \"blog\"."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email
