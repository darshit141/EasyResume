from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiResponse, extend_schema

from resumes.serializers.job_curation_serializer import APIErrorSerializer
from resumes.serializers.resume_serializer import ResumeUploadSerializer
from resumes.services.llm_service import LLMServiceError, resolve_provider
from resumes.services.resume_upload_service import (
    ResumeLimitReachedError,
    ResumeTextExtractionError,
    ResumeUploadService,
)
from billing.models import AIUsageLog
from billing.services.entitlements_service import (
    EntitlementsService,
    PlanRestrictionError,
)
from billing.views.plan_limit_response import plan_restriction_response
from resumes.views.error_responses import llm_error_response

@extend_schema(
    tags=["Resume"],
    summary="Upload Resume",
    description=(
        "Upload a PDF/DOC/DOCX resume (max 5MB). Extracts text, then parses "
        "it into structured data and computes an ATS score using the "
        "provider chosen via `ai_provider` (defaults to the server's "
        "`LLM_PROVIDER`): `ollama` | `groq` | `gemini` call an AI model and "
        "block synchronously until parsing completes (same LLM "
        "timeout/retry behavior as job curation endpoints); `standard` uses "
        "a deterministic, non-AI parser instead — near-instant, no API key, "
        "resume text never leaves the server, but materially lower "
        "extraction accuracy.\n\n"
        "**Every upload creates a new, independent resume** — it never "
        "overwrites an existing one. A user may keep up to "
        "`settings.MAX_RESUMES_PER_USER` resumes at once; uploading past "
        "that limit fails with `code: RESUME_LIMIT_REACHED` until one is "
        "deleted via `DELETE /api/resumes/<id>/`."
    ),
    request=ResumeUploadSerializer,
    responses={
        status.HTTP_201_CREATED: ResumeUploadSerializer,
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
        except LLMServiceError as exc:
            return llm_error_response(exc)

        if is_ai_call:
            EntitlementsService.record_ai_usage(
                request.user, AIUsageLog.ACTION_RESUME_PARSE, resolve_provider(provider)
            )

        return Response(
            ResumeUploadSerializer(resume).data,
            status=status.HTTP_201_CREATED,
        )