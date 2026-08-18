from django.urls import path

from blog.views import (
    NewsletterSubscribeAPIView,
    PostDetailAPIView,
    PostListAPIView,
)

urlpatterns = [
    path("posts/", PostListAPIView.as_view(), name="blog-post-list"),
    path("posts/<slug:slug>/", PostDetailAPIView.as_view(), name="blog-post-detail"),
    path("subscribe/", NewsletterSubscribeAPIView.as_view(), name="newsletter-subscribe"),
]
