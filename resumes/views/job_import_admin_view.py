from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.models import JobImportAttempt
from resumes.services.job_import_service import stats_by_domain


class AdminJobImportStatsAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        tags=["Admin"],
        summary="Job-link import success rate per site",
        parameters=[
            OpenApiParameter("days", int, description="Look-back window, 1-365. Default 30."),
        ],
    )
    def get(self, request):
        try:
            days = min(max(int(request.query_params.get("days", 30)), 1), 365)
        except ValueError:
            days = 30

        latest_canary = (
            JobImportAttempt.objects.filter(trigger=JobImportAttempt.TRIGGER_CANARY)
            .order_by("-created_at")
            .values("created_at")
            .first()
        )
        canary = []
        if latest_canary:
            # Everything from the most recent daily run.
            canary = list(
                JobImportAttempt.objects.filter(
                    trigger=JobImportAttempt.TRIGGER_CANARY,
                    created_at__gte=latest_canary["created_at"].replace(minute=0, second=0, microsecond=0),
                )
                .order_by("domain")
                .values("domain", "outcome", "source", "duration_ms", "created_at")
            )

        return Response({
            "days": days,
            "domains": stats_by_domain(days),
            "last_daily_check": canary,
        })
