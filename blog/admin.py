from django.contrib import admin

from blog.models import NewsletterSubscriber, Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "published_at", "author_name", "updated_at")
    list_filter = ("status", "published_at")
    search_fields = ("title", "excerpt", "body", "tags")
    # Slug is derived from the title on save, but pre-filling it in the form
    # lets an author see and adjust the URL before publishing.
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("title", "slug", "status", "published_at", "author_name")}),
        ("Content", {"fields": ("excerpt", "body", "cover_image", "tags")}),
        (
            "SEO",
            {
                "fields": ("seo_title", "seo_description"),
                "description": "Optional. Falls back to the post title and excerpt when left blank.",
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "is_active", "source", "created_at")
    list_filter = ("is_active", "source")
    search_fields = ("email",)
    readonly_fields = ("created_at",)
