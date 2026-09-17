from blog.views.blog_view import (
    NewsletterSubscribeAPIView,
    PostDetailAPIView,
    PostListAPIView,
)
from blog.views.seo_view import RobotsTxtView, SitemapXMLView

__all__ = [
    "NewsletterSubscribeAPIView",
    "PostDetailAPIView",
    "PostListAPIView",
    "RobotsTxtView",
    "SitemapXMLView",
]
