from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from blog.models import Post
from blog.serializers.blog_serializer import (
    NewsletterSubscribeSerializer,
    PostDetailSerializer,
    PostListSerializer,
)


@extend_schema(
    tags=["Blog"],
    summary="List published blog posts",
    description=(
        "Public, unauthenticated. Returns only posts that are published and "
        "whose publish time has passed, newest first. Excludes post bodies — "
        "fetch a single post for its content. Optionally filter by tag."
    ),
    parameters=[
        OpenApiParameter(
            name="tag",
            description="Case-insensitive tag filter, e.g. `ats`.",
            required=False,
            type=str,
        ),
    ],
    responses={status.HTTP_200_OK: PostListSerializer(many=True)},
)
class PostListAPIView(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PostListSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = Post.published.all()
        tag = self.request.query_params.get("tag")
        if tag:
            # tags is a flat comma-separated string, so this is a substring
            # match; good enough for a small blog and avoids a join table.
            queryset = queryset.filter(tags__icontains=tag)
        return queryset


@extend_schema(
    tags=["Blog"],
    summary="Get a published blog post by slug",
    description=(
        "Public, unauthenticated. Returns the full Markdown body plus SEO "
        "title/description (which fall back to the post title and excerpt). "
        "Unpublished or future-dated posts return 404."
    ),
    responses={
        status.HTTP_200_OK: PostDetailSerializer,
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No published post with that slug."),
    },
)
class PostDetailAPIView(RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PostDetailSerializer
    lookup_field = "slug"

    # Deliberately a method, not a `queryset` class attribute. The published
    # manager filters on `published_at__lte=timezone.now()`, and a class
    # attribute would evaluate that `now()` once at import — freezing the
    # cutoff to server start time, so every post published afterwards would
    # 404 until the process restarted. DRF's `.all()` re-clone does not
    # re-run the manager, so only rebuilding it per request fixes this.
    def get_queryset(self):
        return Post.published.all()


@extend_schema(
    tags=["Blog"],
    summary="Subscribe to the newsletter",
    description=(
        "Public, unauthenticated. Stores the address locally. Subscribing an "
        "address that is already on the list succeeds rather than erroring, "
        "so the response never reveals who is subscribed."
    ),
    request=NewsletterSubscribeSerializer,
    responses={
        status.HTTP_201_CREATED: OpenApiResponse(description="Subscribed."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Invalid email address."),
    },
)
class NewsletterSubscribeAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = NewsletterSubscribeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()
        return Response(
            {"message": "You're on the list. Thanks for subscribing!"},
            status=status.HTTP_201_CREATED,
        )
