from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiResponse, extend_schema

from ai_jobs.models import AIJob
from ai_jobs.serializers import AIJobSerializer
from ai_jobs.services import enqueue_ai_job
from resumes.serializers.job_curation_serializer import APIErrorSerializer
from resumes.serializers.resume_serializer import ResumeUploadSerializer
from resumes.services.llm_service import resolve_provider
from resumes.services.resume_upload_service import (
    ResumeLimitReachedError,
    ResumeTextExtractionError,
    ResumeUploadService,
)
from billing.services.entitlements_service import (
    EntitlementsService,
    PlanRestrictionError,
)
from billing.views.plan_limit_response import plan_restriction_response

@extend_schema(
    tags=["Resume"],
    summary="Upload Resume",
    description=(
        "Upload a PDF/DOC/DOCX resume (max 5MB). Extracts text (synchronous, "
        "fast, non-AI — a corrupt/unreadable file is rejected immediately), "
        "then parses it into structured data and computes an ATS score "
        "using the provider chosen via `ai_provider` (defaults to the "
        "server's `LLM_PROVIDER`): `ollama` | `groq` | `gemini` queue the "
        "parse on a background worker and return **202** immediately — poll "
        "`GET /api/ai-jobs/{id}/` until `status` is `completed`, then fetch "
        "`GET /api/resumes/{resume_id}/` for the parsed result; `standard` "
        "uses a deterministic, non-AI parser instead — near-instant, so it "
        "stays synchronous and returns **201** directly with no API key, "
        "resume text never leaving the server, but materially lower "
        "extraction accuracy.\n\n"
        "**Every upload creates a new, independent resume** — it never "
        "overwrites an existing one. A user may keep up to "
        "`settings.MAX_RESUMES_PER_USER` resumes at once; uploading past "
        "that limit fails with `code: RESUME_LIMIT_REACHED` until one is "
        "deleted via `DELETE /api/resumes/<id>/`."
    ),
    request=ResumeUploadSerializer,
    responses={
        status.HTTP_201_CREATED: OpenApiResponse(
            response=ResumeUploadSerializer,
            description="Returned only for `ai_provider=standard` — the non-AI parse is synchronous.",
        ),
        status.HTTP_202_ACCEPTED: OpenApiResponse(
            description="Returned for every real AI provider — queued for background parsing. Poll GET /api/ai-jobs/{id}/ for the result."
        ),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            response=APIErrorSerializer,
            description="Invalid file (wrong type, over 5MB, missing), or code: RESUME_LIMIT_REACHED.",
        ),
        status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(
            response=APIErrorSerializer,
            description="The file's text could not be extracted (corrupt or unsupported content). code: RESUME_TEXT_EXTRACTION_FAILED.",
        ),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(
            response=APIErrorSerializer,
            description="AI response was malformed. code: LLM_SCHEMA_VALIDATION_FAILED | LLM_INVALID_RESPONSE.",
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
            response=APIErrorSerializer,
            description="AI service unavailable or timed out. code: LLM_UNAVAILABLE | LLM_MODEL_UNAVAILABLE | LLM_TIMEOUT.",
        ),
    },
)
class ResumeUploadAPIView(APIView):
    """
    API to upload a resume.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = "ai_resume"

    def post(self, request):
        serializer = ResumeUploadSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            ResumeUploadService.check_can_upload(request.user)
        except ResumeLimitReachedError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        provider = serializer.validated_data.get("ai_provider")
        is_ai_call = provider != "standard"
        try:
            EntitlementsService.check_ai_provider_allowed(request.user, provider)
            if is_ai_call:
                EntitlementsService.check_can_use_ai(request.user)
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)

        if not is_ai_call:
            # The deterministic, non-AI parser is near-instant — no reason
            # to round-trip it through a queue, so it stays synchronous.
            try:
                resume = ResumeUploadService.upload_resume(
                    request.user,
                    serializer.validated_data,
                    provider=provider,
                )
            except ResumeTextExtractionError as exc:
                return Response(
                    {"detail": exc.message, "code": exc.code},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            return Response(
                ResumeUploadSerializer(resume).data,
                status=status.HTTP_201_CREATED,
            )

        # Real AI providers run on a background worker instead of blocking
        # the request. Text extraction stays synchronous (fast, non-AI) so
        # an unreadable file is still rejected immediately with 422; only
        # the AI parse itself is queued.
        try:
            resume = ResumeUploadService.create_pending(request.user, serializer.validated_data)
        except ResumeTextExtractionError as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        job = enqueue_ai_job(
            request.user,
            AIJob.JOB_TYPE_RESUME_PARSE,
            provider=resolve_provider(provider),
            resume=resume,
        )
        return Response(AIJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)