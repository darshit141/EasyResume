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
    serializer_class = AIJobSerializer
    lookup_url_kwarg = "job_id"

    def get_queryset(self):
        return AIJob.objects.filter(user=self.request.user)


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
        return AIJob.objects.filter(user=self.request.user)
