from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from ai_jobs.models import AIJob
from ai_jobs.serializers import AIJobSerializer


JOB_ID_PARAMETER = OpenApiParameter(
    name="job_id",
    type=int,
    location=OpenApiParameter.PATH,
    required=True,
    description="The AI job ID returned when the background operation was queued.",
)


@extend_schema(
    tags=["AI Jobs"],
    summary="Poll an AI job's status",
    description=(
        "Every AI-backed operation (job analysis, gap analysis, tailored "
        "resume generation, cover letter generation, resume parsing) runs "
        "on a background worker rather than inside the request that "
        "queued it. That request returns 202 with this job's `id`; the "
        "frontend polls this endpoint until `status` is `completed` or "
        "`failed`, then fetches the real output from the resource named "
        "in `result` (e.g. `GET /api/resumes/job-curations/{curation_id}/`"
        "). Only the owning user's job is visible — any other job id "
        "returns 404."
    ),
    parameters=[JOB_ID_PARAMETER],
    responses={
        200: AIJobSerializer,
        401: OpenApiResponse(description="Authentication required."),
        404: OpenApiResponse(description="No job with this ID exists for the authenticated user."),
    },
)
class AIJobStatusAPIView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_job_poll"
    serializer_class = AIJobSerializer
    lookup_url_kwarg = "job_id"

    def get_queryset(self):
        return AIJob.objects.filter(user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        """High-concurrency Redis cache lookup.

        When dozens of users poll every 2 seconds, serving status from Redis
        memory bypasses PostgreSQL SELECT queries entirely, keeping database
        connections free and latency sub-millisecond.
        """
        from django.core.cache import cache
        from rest_framework.response import Response

        job_id = self.kwargs.get(self.lookup_url_kwarg)
        cache_key = f"ai_job_poll:{job_id}"
        cached = cache.get(cache_key)

        # Enforce multi-tenant security: cached job must belong to requesting user
        if cached and isinstance(cached, dict) and cached.get("user_id") == request.user.id:
            return Response(cached.get("data"))

        # Fallback to database query on cache miss
        response = super().retrieve(request, *args, **kwargs)

        # Populate cache on read
        try:
            status = response.data.get("status")
            is_terminal = status in AIJob.TERMINAL_STATUSES
            timeout = 300 if is_terminal else 60
            cache.set(cache_key, {"user_id": request.user.id, "data": response.data}, timeout=timeout)
        except Exception:
            pass

        return response


@extend_schema(
    tags=["AI Jobs"],
    summary="List recent AI jobs for the authenticated user",
    description=(
        "Most-recent-first history of every AI job the authenticated user "
        "has queued, for a lightweight \"AI activity\" view or for "
        "recovering a job id after a page reload."
    ),
    responses={200: AIJobSerializer(many=True), 401: OpenApiResponse(description="Authentication required.")},
)
class AIJobListAPIView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AIJobSerializer

    def get_queryset(self):
        qs = AIJob.objects.filter(user=self.request.user)
        curation_id = self.request.query_params.get("curation_id")
        if curation_id:
            qs = qs.filter(curation_id=curation_id)
        job_type = self.request.query_params.get("job_type")
        if job_type:
            qs = qs.filter(job_type=job_type)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs
