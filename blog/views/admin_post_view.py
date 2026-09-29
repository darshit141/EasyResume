"""Writing and publishing blog posts from the admin panel.

GET    blog/admin/posts/                every post, drafts and scheduled ones too
POST   blog/admin/posts/                a new post
GET    blog/admin/posts/<id>/           one post, with its body
PATCH  blog/admin/posts/<id>/           edit, publish, schedule or unpublish
DELETE blog/admin/posts/<id>/

GET    blog/admin/subscribers/          newsletter signups (?export=csv to download)
PATCH  blog/admin/subscribers/<id>/     {"is_active": bool}
"""

import csv

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import AdminAuditLog
from api.utils.audit import record_admin_action
from blog.models import NewsletterSubscriber, Post
from blog.serializers.admin_post_serializer import AdminPostListSerializer, AdminPostSerializer, post_state


def audit_post(request, post, before_state=None, created=False):
    """One audit row per save: publishing and unpublishing are called out,
    since they change what visitors see."""
    after = post_state(post)
    if created:
        action = AdminAuditLog.ACTION_BLOG_POST_CREATED
    elif before_state == "draft" and after != "draft":
        action = AdminAuditLog.ACTION_BLOG_POST_PUBLISHED
    elif before_state != "draft" and after == "draft":
        action = AdminAuditLog.ACTION_BLOG_POST_UNPUBLISHED
    else:
        action = AdminAuditLog.ACTION_BLOG_POST_UPDATED
    detail = {"slug": post.slug, "title": post.title, "state": after}
    if post.published_at:
        detail["published_at"] = post.published_at.isoformat()
    record_admin_action(request, action, f"blog_post:{post.pk}", detail=detail)


class AdminPostListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        posts = Post.objects.defer("cover_data").order_by("-updated_at")
        return Response(AdminPostListSerializer(posts, many=True).data)

    def post(self, request):
        serializer = AdminPostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = serializer.save()
        audit_post(request, post, created=True)
        return Response(AdminPostSerializer(post).data, status=status.HTTP_201_CREATED)


class AdminPostDetailAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get_post(self, pk):
        return Post.objects.defer("cover_data").filter(pk=pk).first()

    def not_found(self):
        return Response({"detail": "That post no longer exists."}, status=status.HTTP_404_NOT_FOUND)

    def get(self, request, pk):
        post = self.get_post(pk)
        if post is None:
            return self.not_found()
        return Response(AdminPostSerializer(post).data)

    def patch(self, request, pk):
        post = self.get_post(pk)
        if post is None:
            return self.not_found()
        before = post_state(post)
        serializer = AdminPostSerializer(post, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # "Publish now" from a draft goes live now if the post was only ever
        # scheduled; one that was live before keeps its original date. An
        # explicit date in the request always wins.
        data = serializer.validated_data
        if (
            data.get("status") == Post.STATUS_PUBLISHED
            and before == "draft"
            and "published_at" not in data
            and (post.published_at is None or post.published_at > timezone.now())
        ):
            data["published_at"] = timezone.now()
        for name, value in data.items():
            setattr(post, name, value)
        # Only the fields the editor owns (never the cover picture's, which
        # has its own card), so an editor tab left open can't undo it.
        post.save(update_fields=list(dict.fromkeys([*data, "published_at", "slug", "updated_at"])))
        audit_post(request, post, before_state=before)
        return Response(AdminPostSerializer(post).data)

    def delete(self, request, pk):
        post = self.get_post(pk)
        if post is None:
            return self.not_found()
        record_admin_action(
            request, AdminAuditLog.ACTION_BLOG_POST_DELETED, f"blog_post:{post.pk}",
            detail={"slug": post.slug, "title": post.title, "state": post_state(post)},
        )
        post.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def subscriber_row(sub):
    return {"id": sub.id, "email": sub.email, "is_active": sub.is_active, "source": sub.source,
            "created_at": sub.created_at}


class AdminSubscriberListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        subscribers = NewsletterSubscriber.objects.order_by("-created_at")
        if request.query_params.get("export") == "csv":
            active = subscribers.filter(is_active=True)
            record_admin_action(
                request, AdminAuditLog.ACTION_SUBSCRIBERS_EXPORTED, "newsletter",
                detail={"count": active.count()},
            )
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = 'attachment; filename="newsletter-subscribers.csv"'
            writer = csv.writer(response)
            writer.writerow(["email", "source", "subscribed_at"])
            for sub in active:
                writer.writerow([sub.email, sub.source, sub.created_at.date().isoformat()])
            return response
        return Response({
            "active": subscribers.filter(is_active=True).count(),
            "total": subscribers.count(),
            "subscribers": [subscriber_row(s) for s in subscribers[:500]],
        })


class AdminSubscriberDetailAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk):
        sub = NewsletterSubscriber.objects.filter(pk=pk).first()
        if sub is None:
            return Response({"detail": "No such subscriber."}, status=status.HTTP_404_NOT_FOUND)
        is_active = request.data.get("is_active")
        if not isinstance(is_active, bool):
            return Response({"detail": "is_active must be true or false."}, status=status.HTTP_400_BAD_REQUEST)
        sub.is_active = is_active
        sub.save(update_fields=["is_active"])
        record_admin_action(
            request, AdminAuditLog.ACTION_SUBSCRIBER_UPDATED, f"subscriber:{sub.pk}",
            detail={"is_active": is_active},
        )
        return Response(subscriber_row(sub))
