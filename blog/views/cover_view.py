"""Blog cover pictures.

Public:
GET    blog/posts/<slug>/cover/?v=<version>   the uploaded cover's bytes

Admin (the admin panel's Appearance tab):
GET    blog/admin/covers/                   every post, what its cover shows now
PATCH  blog/admin/covers/<slug>/            {"source": "auto"|"builtin"|"hidden", "builtin", "alt_text"}
POST   blog/admin/covers/<slug>/upload/     multipart "file" (+ optional "alt_text")
DELETE blog/admin/covers/<slug>/            back to the automatic picture
"""

import secrets

from django.http import Http404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import AdminAuditLog
from api.utils.audit import record_admin_action
from api.utils.pictures import clean_alt, picture_response, to_webp
from blog.covers import COVER_CHOICES, COVER_LABELS, automatic_cover, cover_payload
from blog.models import Post

COVER_FIELDS = [
    "cover_source", "cover_builtin", "cover_data", "cover_width", "cover_height",
    "cover_version", "cover_alt", "cover_updated_at",
]


class PostCoverFileAPIView(APIView):
    """The bytes of a post's uploaded cover.

    A published post's cover is public, like the post. A draft's is served
    only to someone holding its current version token, which is random and
    known only to the admin panel, so an unpublished post's picture can be
    previewed without leaking. The version changes on every upload, so the
    response can be cached for good.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request, slug):
        post = Post.objects.filter(slug=slug, cover_source=Post.COVER_CUSTOM).first()
        if post is None or not post.cover_data:
            raise Http404
        is_public = Post.published.filter(pk=post.pk).exists()
        if not is_public and request.query_params.get("v") != post.cover_version:
            raise Http404
        return picture_response(post.cover_data)


def admin_payload(post):
    current = cover_payload(post)
    source = post.cover_source or "default"
    auto = automatic_cover(post)
    return {
        # Shaped like a site image slot so the admin panel can use one card.
        "slot": post.slug,
        "label": post.title,
        "page": "Blog",
        "status": post.status,
        "published_at": post.published_at,
        "default": auto,
        "default_label": f"matched to the topic: {COVER_LABELS[auto]}",
        "builtins": [{"value": name, "label": COVER_LABELS[name]} for name in COVER_CHOICES],
        "source": source,
        "builtin": post.cover_builtin if source == Post.COVER_BUILTIN else "",
        "alt_text": post.cover_alt,
        "current": None if current["source"] == "hidden" else current,
        "updated_at": post.cover_updated_at,
    }


def get_post(slug):
    """(post or None, error response or None)."""
    post = Post.objects.filter(slug=slug).first()
    if post is None:
        return None, Response({"detail": "No post with that address."}, status=status.HTTP_404_NOT_FOUND)
    return post, None


def save_cover(request, post, detail, action=AdminAuditLog.ACTION_SITE_IMAGE_UPDATED):
    reset = action == AdminAuditLog.ACTION_SITE_IMAGE_RESET
    post.cover_updated_at = None if reset else timezone.now()
    # Only the cover fields: saving the whole post would bump updated_at
    # and could race an edit made to the post's text at the same time.
    post.save(update_fields=COVER_FIELDS)
    record_admin_action(request, action, f"blog_cover:{post.slug}", detail=detail)


class AdminBlogCoverListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        posts = Post.objects.defer("cover_data", "body").order_by("-published_at", "-created_at")
        return Response([admin_payload(post) for post in posts])


class AdminBlogCoverDetailAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]
    parser_classes = [JSONParser]

    def patch(self, request, slug):
        post, error = get_post(slug)
        if error:
            return error

        source = request.data.get("source")
        if source not in ("auto", Post.COVER_BUILTIN, Post.COVER_HIDDEN, None):
            return Response(
                {"detail": "source must be auto, builtin or hidden. Upload a file to use your own picture."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if source is not None:
            if source == Post.COVER_BUILTIN:
                builtin = request.data.get("builtin")
                if builtin not in COVER_LABELS:
                    return Response(
                        {"detail": "Pick one of the listed pictures."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                post.cover_builtin = builtin
            else:
                post.cover_builtin = ""
            # Any choice here replaces an upload.
            post.cover_data = None
            post.cover_width = post.cover_height = None
            post.cover_version = ""
            post.cover_source = Post.COVER_AUTO if source == "auto" else source
        if "alt_text" in request.data:
            post.cover_alt = clean_alt(request.data.get("alt_text"))
        save_cover(request, post, {"source": post.cover_source or "auto", "builtin": post.cover_builtin})
        return Response(admin_payload(post))

    def delete(self, request, slug):
        post, error = get_post(slug)
        if error:
            return error
        post.cover_source = Post.COVER_AUTO
        post.cover_builtin = ""
        post.cover_data = None
        post.cover_width = post.cover_height = None
        post.cover_version = ""
        post.cover_alt = ""
        save_cover(request, post, {}, action=AdminAuditLog.ACTION_SITE_IMAGE_RESET)
        return Response(admin_payload(post))


class AdminBlogCoverUploadAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, slug):
        post, error = get_post(slug)
        if error:
            return error
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "Attach a picture to upload."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            data, width, height = to_webp(upload)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        post.cover_source = Post.COVER_CUSTOM
        post.cover_builtin = ""
        post.cover_data = data
        post.cover_width, post.cover_height = width, height
        post.cover_version = secrets.token_hex(6)
        if "alt_text" in request.data:
            post.cover_alt = clean_alt(request.data.get("alt_text"))
        save_cover(
            request, post, {"source": "custom", "width": width, "height": height, "bytes": len(data)},
        )
        return Response(admin_payload(post), status=status.HTTP_201_CREATED)
