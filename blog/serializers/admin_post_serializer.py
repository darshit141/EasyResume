from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers

from blog.covers import cover_payload
from blog.models import Post


def post_state(post):
    """draft, scheduled (published but its time hasn't come) or published."""
    if post.status != Post.STATUS_PUBLISHED:
        return "draft"
    if post.published_at and post.published_at > timezone.now():
        return "scheduled"
    return "published"


class AdminPostListSerializer(serializers.ModelSerializer):
    """A row in the admin panel's post list: everything but the body."""

    tags = serializers.ListField(source="tag_list", child=serializers.CharField(), read_only=True)
    reading_minutes = serializers.IntegerField(read_only=True)
    state = serializers.SerializerMethodField()
    cover = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id", "title", "slug", "excerpt", "tags", "author_name", "status", "state",
            "published_at", "created_at", "updated_at", "reading_minutes", "cover",
        ]

    def get_state(self, post):
        return post_state(post)

    def get_cover(self, post):
        return cover_payload(post)


class AdminPostSerializer(AdminPostListSerializer):
    """Read and write one post from the admin panel's editor.

    Tags go in as a list (or a comma-separated string) and are stored
    comma-separated, as the public site reads them. The address (slug) may
    be left blank to derive it from the title.
    """

    tags = serializers.JSONField(required=False)
    slug = serializers.CharField(max_length=220, required=False, allow_blank=True)

    class Meta(AdminPostListSerializer.Meta):
        fields = AdminPostListSerializer.Meta.fields + ["body", "seo_title", "seo_description"]
        read_only_fields = ["created_at", "updated_at"]
        extra_kwargs = {
            "title": {"error_messages": {"blank": "Give the post a title.", "required": "Give the post a title."}},
            "body": {"error_messages": {"blank": "Write the post before saving it.", "required": "Write the post before saving it."}},
        }

    def to_representation(self, post):
        data = super().to_representation(post)
        data["tags"] = post.tag_list
        return data

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Give the post a title.")
        return value

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Write the post before saving it.")
        return value

    def validate_slug(self, value):
        slug = slugify(value)[:220]
        if value.strip() and not slug:
            raise serializers.ValidationError("Use letters, numbers and dashes in the address.")
        if slug and Post.objects.filter(slug=slug).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError("Another post already uses this address.")
        return slug

    def validate_tags(self, value):
        if value in (None, ""):
            return ""
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            raise serializers.ValidationError("Tags must be a list of words.")
        tags, seen = [], set()
        for tag in (t.strip().replace(",", " ") for t in value):
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                tags.append(tag[:40])
        joined = ", ".join(tags)
        if len(joined) > 200:
            raise serializers.ValidationError("Too many tags. Keep it to a handful.")
        return joined

    def validate_author_name(self, value):
        return value.strip() or "IntelliResume"

    def validate(self, attrs):
        status_value = attrs.get("status", getattr(self.instance, "status", Post.STATUS_DRAFT))
        body = attrs.get("body", getattr(self.instance, "body", ""))
        if status_value == Post.STATUS_PUBLISHED and not (body or "").strip():
            raise serializers.ValidationError({"body": "A post needs some text before it can go live."})
        return attrs

    def create(self, validated_data):
        return Post.objects.create(**validated_data)
