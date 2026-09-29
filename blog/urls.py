from django.urls import path

from blog.views import (
    NewsletterSubscribeAPIView,
    PostDetailAPIView,
    PostListAPIView,
)
from blog.views.cover_view import (
    AdminBlogCoverDetailAPIView,
    AdminBlogCoverListAPIView,
    AdminBlogCoverUploadAPIView,
    PostCoverFileAPIView,
)

urlpatterns = [
    path("posts/", PostListAPIView.as_view(), name="blog-post-list"),
    path("posts/<slug:slug>/", PostDetailAPIView.as_view(), name="blog-post-detail"),
    path("posts/<slug:slug>/cover/", PostCoverFileAPIView.as_view(), name="blog-post-cover"),
    path("admin/covers/", AdminBlogCoverListAPIView.as_view(), name="admin-blog-covers"),
    path("admin/covers/<slug:slug>/", AdminBlogCoverDetailAPIView.as_view(), name="admin-blog-cover"),
    path("admin/covers/<slug:slug>/upload/", AdminBlogCoverUploadAPIView.as_view(), name="admin-blog-cover-upload"),
    path("subscribe/", NewsletterSubscribeAPIView.as_view(), name="newsletter-subscribe"),
]
