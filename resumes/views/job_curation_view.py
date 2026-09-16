from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from django.http import Http404
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.models import JobResumeCuration
from resumes.serializers.job_curation_serializer import (
    APIErrorSerializer,
    JobCurationRequestSerializer,
    JobCurationSkippedSerializer,
    JobGapAnalysisResponseSerializer,
    JobResumeCurationSerializer,
    JobUrlPreviewRequestSerializer,
    JobUrlPreviewResponseSerializer,
)
from resumes.services.job_gap_analysis_service import (
    JobGapAnalysisService,
    JobRequirementsUnavailableError,
)
from resumes.services.application_tracking_service import ApplicationTrackingService
from resumes.services.job_curation_service import (
    JobCurationDatabaseError,
    JobCurationService,
)
from resumes.services.job_url_fetch_service import JobUrlFetchError, fetch_job_posting
from resumes.services.llm_service import SUPPORTED_PROVIDERS, LLMServiceError, resolve_provider
from ai_jobs.models import AIJob
from ai_jobs.serializers import AIJobSerializer
from ai_jobs.services import enqueue_ai_job
from billing.models import AIUsageLog
from billing.services.entitlements_service import (
    EntitlementsService,
    PlanRestrictionError,
)
from billing.views.plan_limit_response import plan_restriction_response
from resumes.views.error_responses import llm_error_response
from resumes.services.llm_service import requested_provider


JOB_ANALYSIS_EXAMPLE = {
    "required_skills": ["Python", "REST APIs"],
    "preferred_skills": ["Django REST Framework"],
    "technologies": ["Python"],
    "frameworks": ["Django"],
    "databases": ["PostgreSQL"],
    "cloud_technologies": ["AWS"],
    "tools": ["Docker", "Git"],
    "soft_skills": ["Communication"],
    "responsibilities": ["Build backend services"],
    "experience_requirements": ["5+ years backend engineering"],
    "education_requirements": ["Bachelor's degree or equivalent experience"],
    "certification_requirements": [],
    "important_keywords": ["backend", "API", "scalable"],
    "ats_relevant_terms": ["Python", "Django", "PostgreSQL"],
    "seniority_level": "Senior",
    "other_explicit_requirements": ["On-call participation"],
}

CURATION_ID_PARAMETER = OpenApiParameter(
    name="curation_id",
    type=int,
    location=OpenApiParameter.PATH,
    required=True,
    description=(
        "The job curation session ID returned by "
        "POST /api/resumes/job-curations/analyze/. The frontend must "
        "persist this ID; every subsequent step of the workflow (gap "
        "analysis, missing-information review, tailored resume generation, "
        "version history) is addressed by it."
    ),
)

AI_PROVIDER_PARAMETER = OpenApiParameter(
    name="ai_provider",
    type=str,
    location=OpenApiParameter.QUERY,
    required=False,
    enum=list(SUPPORTED_PROVIDERS),
    description=(
        "Which AI provider to use for this comparison. Defaults to the "
        "server's configured provider."
    ),
)

LLM_ERROR_EXAMPLE = OpenApiExample(
    "AI service unavailable",
    value={
        "detail": "The configured AI model is unavailable.",
        "code": "LLM_MODEL_UNAVAILABLE",
    },
    response_only=True,
    status_codes=["503"],
)

LLM_INVALID_RESPONSE_EXAMPLE = OpenApiExample(
    "AI returned an unusable response",
    value={
        "detail": "AI service returned data in an unexpected format.",
        "code": "LLM_SCHEMA_VALIDATION_FAILED",
    },
    response_only=True,
    status_codes=["502"],
)


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Step 1 — Submit job title/description and analyze it",
    description=(
        "**Workflow step 1-2.** This single endpoint both creates the job "
        "curation session AND runs the AI job-description analysis in one "
        "synchronous call — there is no separate \"create session\" step "
        "and no polling. Requires a valid JWT bearer token.\n\n"
        "Behavior depends on `curate_resume`:\n"
        "- `curate_resume: true` — validates the job title/description, "
        "creates a user-owned `JobResumeCuration` row, calls the "
        "configured AI provider (`ai_provider`, defaulting to the "
        "server's `LLM_PROVIDER`) to extract structured job requirements, "
        "validates the AI response against a strict schema, and persists "
        "it. Returns **201 Created** with the session `id`. **The frontend "
        "must store this `id`** — it is required for every later call "
        "(gap analysis, missing-information review, tailored resume "
        "generation, version history).\n"
        "- `curate_resume: false` — skips the AI call and does not create "
        "a session. Returns **200 OK** with no `id`. Use this when the "
        "user only wants a plain job posting saved without job-specific "
        "tailoring.\n\n"
        "**Validation rules:** `job_title` must be 2-200 characters after "
        "trimming. `job_description` must be 30-20000 characters after "
        "trimming. `curate_resume` is a required boolean.\n\n"
        "**Synchronous call, not a background job:** this request blocks "
        "until the LLM responds (bounded by the backend's configured "
        "`LLM_TIMEOUT`, default 120s, with up to `LLM_MAX_RETRIES` retries "
        "on transient failures). Set the client-side HTTP timeout above "
        "that, and show a loading state rather than polling."
    ),
    request=JobCurationRequestSerializer,
    responses={
        status.HTTP_201_CREATED: JobResumeCurationSerializer,
        status.HTTP_200_OK: JobCurationSkippedSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            description=(
                "Validation error: job_title/job_description missing or "
                "out of length range, or curate_resume missing/not boolean. "
                "Response body is a field-name-keyed error map, e.g. "
                '{"job_title": ["This field is required."]}.'
            )
        ),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(
            response=APIErrorSerializer,
            description=(
                "The AI response was empty, malformed, or failed schema "
                "validation. code: LLM_EMPTY_RESPONSE | "
                "LLM_INVALID_RESPONSE | LLM_SCHEMA_VALIDATION_FAILED."
            ),
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
            response=APIErrorSerializer,
            description=(
                "The AI service could not be reached in time. code: "
                "LLM_UNAVAILABLE | LLM_MODEL_UNAVAILABLE | LLM_TIMEOUT."
            ),
        ),
        status.HTTP_500_INTERNAL_SERVER_ERROR: OpenApiResponse(
            response=APIErrorSerializer,
            description="Database error while persisting the session. code: DATABASE_ERROR.",
        ),
    },
    examples=[
        OpenApiExample(
            "Analyze JD request",
            value={
                "job_title": "Senior Backend Engineer",
                "job_description": "We need a Senior Backend Engineer with Python, Django, PostgreSQL, Docker, and AWS experience...",
                "curate_resume": True,
            },
            request_only=True,
        ),
        OpenApiExample(
            "Structured analysis response",
            value={
                "id": 1,
                "job_title": "Senior Backend Engineer",
                "curate_resume": True,
                "status": "completed",
                "analysis_data": JOB_ANALYSIS_EXAMPLE,
                "error_code": "",
                "created_at": "2026-08-11T10:00:00Z",
                "updated_at": "2026-08-11T10:00:03Z",
            },
            response_only=True,
            status_codes=["201"],
        ),
        OpenApiExample(
            "Skipped curation response",
            value={
                "curate_resume": False,
                "message": "Job-specific resume curation was not requested.",
            },
            response_only=True,
            status_codes=["200"],
        ),
        LLM_ERROR_EXAMPLE,
    ],
)
class JobUrlPreviewAPIView(APIView):
    """Fetches a posting and returns what it found, without committing to
    anything.

    Deliberately separate from the analyze endpoint: importing is not
    curating. This makes no AI call, consumes no quota, and writes nothing to
    the database — so the user can see exactly what was extracted, correct it
    if the extraction was imperfect, and only then decide to spend a credit.
    """

    permission_classes = [IsAuthenticated]
    # Rate-limited because it makes an outbound request to a user-supplied
    # URL, which must not be abusable as a general-purpose fetch proxy.
    throttle_scope = "ai_resume"

    @extend_schema(
        tags=["Job Resume Curation"],
        summary="Import a job posting from a link (no AI, no quota)",
        description=(
            "Fetches the linked job posting and returns the title and "
            "description it could extract, for the user to review and edit "
            "before deciding to run any analysis. Creates nothing and spends "
            "no AI credit.\n\n"
            "Fails with a specific code when the posting can't be read: "
            "`JOB_URL_BLOCKED` (the site refuses automated access), "
            "`JOB_URL_NOT_FOUND` (dead link), or `JOB_URL_UNREADABLE` (the "
            "page renders its content with JavaScript). In every case the "
            "user can still paste the description manually."
        ),
        request=JobUrlPreviewRequestSerializer,
        responses={
            status.HTTP_200_OK: JobUrlPreviewResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Invalid or unsupported URL."),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(
                response=APIErrorSerializer,
                description="code: JOB_URL_BLOCKED | JOB_URL_NOT_FOUND | JOB_URL_UNREADABLE | JOB_URL_FETCH_FAILED.",
            ),
        },
    )
    def post(self, request):
        serializer = JobUrlPreviewRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            imported = fetch_job_posting(serializer.validated_data["job_url"])
        except JobUrlFetchError as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            JobUrlPreviewResponseSerializer({
                "job_title": imported["title"],
                "job_description": imported["description"],
            }).data
        )


class JobCurationCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_resume"

    def post(self, request):
        # validate_resume_id checks the resume belongs to the caller, so it
        # needs the request. Without this context every explicit resume
        # choice was rejected with "Authentication required."
        serializer = JobCurationRequestSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        provider = requested_provider(data.get("ai_provider"))
        # Tailoring is the user's choice. Declining it still saves the job -
        # it just doesn't spend an AI credit - so the quota gates apply only
        # to the path that actually calls a model.
        wants_analysis = data["curate_resume"]

        if wants_analysis:
            try:
                EntitlementsService.check_ai_provider_allowed(request.user, provider)
                EntitlementsService.check_can_use_ai(request.user)
            except PlanRestrictionError as exc:
                return plan_restriction_response(exc)

        job_title = data.get("job_title", "")
        job_description = data.get("job_description", "")
        job_url = data.get("job_url", "")

        # Import from the link only when the description wasn't pasted.
        # Anything the user typed always wins over the imported values —
        # they've seen the posting and we haven't.
        if job_url and not job_description:
            try:
                imported = fetch_job_posting(job_url)
            except JobUrlFetchError as exc:
                return Response(
                    {"detail": exc.message, "code": exc.code},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            job_description = imported["description"]
            job_title = job_title or imported["title"]

        if not job_title:
            return Response(
                {
                    "job_title": [
                        "We couldn't determine the job title from that link. "
                        "Please enter it manually."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not wants_analysis:
            curation, _ = ApplicationTrackingService.create_saved_job(
                request.user,
                job_title=job_title,
                job_description=job_description,
                job_url=job_url,
                resume_id=data.get("resume_id"),
            )
            return Response(
                JobResumeCurationSerializer(curation).data,
                status=status.HTTP_201_CREATED,
            )

        try:
            curation = JobCurationService(provider=provider).create_pending(
                user=request.user,
                job_title=job_title,
                job_description=job_description,
                job_url=job_url,
                resume_id=data.get("resume_id"),
            )
        except JobCurationDatabaseError as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # The AI call itself now runs on a background worker (see
        # ai_jobs.tasks._run_job_analysis) instead of blocking this request
        # — a slow/unavailable provider can no longer tie up a Gunicorn
        # worker. AIUsageLog is recorded by the worker only on success, same
        # "only successful calls count against quota" rule as before.
        job = enqueue_ai_job(
            request.user,
            AIJob.JOB_TYPE_JOB_ANALYSIS,
            provider=resolve_provider(provider),
            curation=curation,
        )
        return Response(
            AIJobSerializer(job).data,
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Get job curation history (list all sessions)",
    description=(
        "**Workflow step 11 — job curation history.** Returns every job "
        "curation session owned by the authenticated user, most recent "
        "first, including each session's `id`, `status`, and structured "
        "`analysis_data`. Use this to render a \"my job applications / "
        "tailoring sessions\" list screen. Requires a valid JWT bearer "
        "token. Synchronous, no query or path parameters, no request body."
    ),
    responses={
        status.HTTP_200_OK: JobResumeCurationSerializer(many=True),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
    },
)
class JobCurationListAPIView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = JobResumeCurationSerializer

    def get_queryset(self):
        return JobResumeCuration.objects.filter(user=self.request.user)


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Step 2 (fetch) — Get job analysis / curation result",
    description=(
        "**Workflow steps 3 and 5 — get job analysis / get curation "
        "result.** Returns one job curation session by ID: its status "
        "(`processing` | `completed` | `failed`), the structured job "
        "analysis (`analysis_data`), and `error_code` if analysis failed. "
        "Use this to re-display a session (e.g. after navigating back to "
        "it) without re-running AI analysis — it never calls the LLM. "
        "Requires a valid JWT bearer token. Only the owning user's session "
        "is visible; requests for another user's `curation_id`, or an ID "
        "that does not exist, both return 404 (no information is leaked "
        "about whether the ID belongs to someone else)."
    ),
    parameters=[CURATION_ID_PARAMETER],
    responses={
        status.HTTP_200_OK: JobResumeCurationSerializer,
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(
            description="No curation with this ID exists for the authenticated user."
        ),
    },
)
class JobCurationDetailAPIView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = JobResumeCurationSerializer
    lookup_url_kwarg = "curation_id"

    def get_queryset(self):
        return JobResumeCuration.objects.filter(user=self.request.user)


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Step 3 — Get candidate-vs-job match / gap analysis",
    description=(
        "**Workflow step 3-4.** Compares the authenticated user's latest "
        "completed resume data against the structured job requirements for "
        "this curation and returns a full gap analysis: overall match "
        "percentage, matched/partially-matched/missing/not-verifiable "
        "requirements (each with evidence, confidence, reasoning, and a "
        "recommendation), strengths, weaknesses, ATS keyword coverage, and "
        "interview-readiness notes. Requires a valid JWT bearer token. "
        "Only the owning user's session is visible (404 otherwise).\n\n"
        "**Caching:** the first call computes and persists the result; "
        "subsequent calls return the same cached result instantly without "
        "calling the LLM again, until it is explicitly refreshed (see the "
        "tailored-resume endpoint's `regenerate` flag, which also "
        "refreshes this data). There is no separate manual-refresh query "
        "parameter on this endpoint itself.\n\n"
        "**Synchronous call:** on a cache miss this blocks on the LLM "
        "(same timeout/retry behavior as job analysis)."
    ),
    parameters=[CURATION_ID_PARAMETER, AI_PROVIDER_PARAMETER],
    responses={
        status.HTTP_200_OK: JobGapAnalysisResponseSerializer,
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(
            description="No curation with this ID exists for the authenticated user."
        ),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(
            response=APIErrorSerializer,
            description="AI response was malformed. code: LLM_SCHEMA_VALIDATION_FAILED | LLM_INVALID_RESPONSE.",
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
            response=APIErrorSerializer,
            description=(
                "AI unavailable, or no completed resume / structured job "
                "requirements exist yet. code: LLM_UNAVAILABLE | "
                "LLM_MODEL_UNAVAILABLE | LLM_TIMEOUT | "
                "CANDIDATE_DATA_UNAVAILABLE | JOB_REQUIREMENTS_UNAVAILABLE."
            ),
        ),
    },
    examples=[
        OpenApiExample(
            "Gap analysis response",
            value={
                "id": 1,
                "job_title": "Senior Backend Engineer",
                "status": "completed",
                "gap_analysis_data": {
                    "overall_match_percentage": 50,
                    "matched_requirements": [
                        {
                            "requirement": "Python",
                            "category": "required_skill",
                            "match_status": "MATCHED",
                            "candidate_evidence": ["Python"],
                            "confidence": 1,
                            "reasoning": "Python is listed in candidate skills.",
                            "recommendation": "Keep Python prominent in the profile.",
                        }
                    ],
                    "partially_matched_requirements": [],
                    "missing_requirements": [],
                    "not_verifiable_requirements": [],
                    "strengths": ["Backend API experience"],
                    "weaknesses_gaps": ["AWS is not present in the verified resume data."],
                    "important_missing_information": ["Cloud experience details"],
                    "recommendations_for_profile_improvement": [
                        {
                            "recommendation": "Add AWS only if you genuinely have AWS experience.",
                            "recommendation_type": "MISSING_PROFILE_INFORMATION",
                        }
                    ],
                    "ats_keyword_coverage": {
                        "covered_keywords": ["Python"],
                        "missing_keywords": ["AWS"],
                        "coverage_percentage": 50,
                    },
                    "interview_readiness_observations": [
                        "Prepare examples around verified backend work."
                    ],
                },
                "gap_analysis_error_code": "",
                "created_at": "2026-08-11T10:00:00Z",
                "updated_at": "2026-08-11T10:00:03Z",
            },
            response_only=True,
            status_codes=["200"],
        ),
        LLM_INVALID_RESPONSE_EXAMPLE,
    ],
)
class JobGapAnalysisAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, curation_id):
        provider = request.query_params.get("ai_provider") or None
        if provider is not None and provider not in SUPPORTED_PROVIDERS:
            return Response(
                {"ai_provider": [f"Must be one of {', '.join(SUPPORTED_PROVIDERS)}."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        provider = requested_provider(provider)

        # This view never passes regenerate=True, so the service either
        # returns cached gap_analysis_data untouched (no AI call at all) or
        # computes it for the first time (one AI call). Checked up front so
        # the quota gate — and later, usage recording — only ever applies to
        # the branch that actually spends an AI call.
        already_has_gap_analysis = JobResumeCuration.objects.filter(
            id=curation_id, user=request.user
        ).exclude(gap_analysis_data={}).exists()

        try:
            EntitlementsService.check_ai_provider_allowed(request.user, provider)
            if not already_has_gap_analysis:
                EntitlementsService.check_can_use_ai(request.user)
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)

        if not already_has_gap_analysis:
            curation = JobResumeCuration.objects.filter(id=curation_id, user=request.user).first()
            if curation is None:
                raise Http404("Job curation analysis not found.")
            if curation.status != "completed" or not curation.analysis_data:
                return llm_error_response(JobRequirementsUnavailableError())

            job = enqueue_ai_job(
                request.user,
                AIJob.JOB_TYPE_GAP_ANALYSIS,
                provider=resolve_provider(provider),
                curation=curation,
            )
            return Response(AIJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

        try:
            curation = JobGapAnalysisService(provider=provider).get_or_create_gap_analysis(
                request.user,
                curation_id,
            )
        except JobResumeCuration.DoesNotExist as exc:
            raise Http404("Job curation analysis not found.") from exc
        except LLMServiceError as exc:
            return llm_error_response(exc)

        return Response(JobGapAnalysisResponseSerializer(curation).data)
