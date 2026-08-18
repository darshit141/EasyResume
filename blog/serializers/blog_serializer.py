from rest_framework import serializers

from blog.models import NewsletterSubscriber, Post


class PostListSerializer(serializers.ModelSerializer):
    """Index/card representation — deliberately excludes `body` so the list
    endpoint stays small no matter how long the posts are."""

    tags = serializers.ListField(source="tag_list", child=serializers.CharField(), read_only=True)
    reading_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = Post
        fields = [
            "slug",
            "title",
            "excerpt",
            "cover_image",
            "tags",
            "author_name",
            "published_at",
            "reading_minutes",
        ]


class PostDetailSerializer(serializers.ModelSerializer):
    tags = serializers.ListField(source="tag_list", child=serializers.CharField(), read_only=True)
    reading_minutes = serializers.IntegerField(read_only=True)
    seo_title = serializers.SerializerMethodField()
    seo_description = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "slug",
            "title",
            "excerpt",
            "body",
            "cover_image",
            "tags",
            "author_name",
            "published_at",
            "updated_at",
            "reading_minutes",
            "seo_title",
            "seo_description",
        ]

    # SEO fields fall back to the human-facing copy so a post is never
    # published with an empty <title> or meta description.
    def get_seo_title(self, post):
        return post.seo_title or post.title

    def get_seo_description(self, post):
        return post.seo_description or post.excerpt


class NewsletterSubscribeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    source = serializers.CharField(max_length=50, required=False, allow_blank=True)

    def create(self, validated_data):
        # Re-subscribing an existing address must not 400 — from the
        # visitor's point of view the action succeeded either way, and
        # reporting "already subscribed" would leak who is on the list.
        subscriber, _ = NewsletterSubscriber.objects.get_or_create(
            email=validated_data["email"].lower(),
            defaults={"source": validated_data.get("source", "")},
        )
        if not subscriber.is_active:
            subscriber.is_active = True
            subscriber.save(update_fields=["is_active"])
        return subscriber
