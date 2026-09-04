from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.services.entitlements_service import EntitlementsService, PlanRestrictionError
from billing.views.plan_limit_response import plan_restriction_response
from resumes.serializers.guided_resume_serializer import (
    GuidedAcceptRequestSerializer,
    GuidedAnswersRequestSerializer,
    GuidedPolishRequestSerializer,
    GuidedSectionRequestSerializer,
    GuidedSessionSerializer,
)
from resumes.services.guided_resume_ai_service import GuidedResumeAIService
from resumes.services.guided_resume_service import GuidedResumeService
from resumes.services.llm_service import LLMServiceError


def _session_response(session):
    return Response(GuidedSessionSerializer(session).data)


@extend_schema(tags=["Guided Resume Builder"])
class GuidedResumeSessionAPIView(APIView):
    """The current in-progress guided-builder session for the user, with the
    question bank. GET starts one if none exists; DELETE abandons it."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return _session_response(GuidedResumeService.get_or_start(request.user))

    def delete(self, request):
        return _session_response(GuidedResumeService.reset(request.user))


@extend_schema(tags=["Guided Resume Builder"], request=GuidedAnswersRequestSerializer)
class GuidedResumeAnswersAPIView(APIView):
    """Merge a patch of interview answers into the session."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = GuidedAnswersRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = GuidedResumeService.get_or_start(request.user)
        session = GuidedResumeService.save_answers(session, serializer.validated_data["answers"])
        return _session_response(session)


@extend_schema(tags=["Guided Resume Builder"], request=GuidedSectionRequestSerializer)
class GuidedResumeDraftAPIView(APIView):
    """Draft one section (experience / projects / education / summary) from
    its answers. Returns the suggestion for review — nothing is committed
    until POST /accept/."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_resume"

    def post(self, request):
        serializer = GuidedSectionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        section = serializer.validated_data["section"]

        try:
            EntitlementsService.check_can_use_ai(request.user)
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)

        session = GuidedResumeService.get_or_start(request.user)
        try:
            if section == "education":
                # Education needs no model — assemble it deterministically
                # and hand it back in the same shape as a drafted section.
                data = {"entries": GuidedResumeService.assemble(session)["education"]}
            else:
                data = GuidedResumeService().draft_section(session, section)
        except LLMServiceError as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(data)


@extend_schema(tags=["Guided Resume Builder"], request=GuidedAcceptRequestSerializer)
class GuidedResumeAcceptAPIView(APIView):
    """Commit reviewed/edited section content into the draft."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = GuidedAcceptRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        session = GuidedResumeService.get_or_start(request.user)
        session = GuidedResumeService.accept_section(
            session,
            data["section"],
            {"entries": data.get("entries", []), "summary": data.get("summary", "")},
        )
        return _session_response(session)


@extend_schema(tags=["Guided Resume Builder"], request=GuidedPolishRequestSerializer)
class GuidedResumePolishAPIView(APIView):
    """Improve one line of text — fix grammar, tighten, standardise
    terminology — without adding a fact that isn't in the input. Powers the
    'improve this' action next to every editable field."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_resume"

    def post(self, request):
        serializer = GuidedPolishRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            EntitlementsService.check_can_use_ai(request.user)
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)

        session = GuidedResumeService.get_or_start(request.user)
        try:
            result = GuidedResumeAIService().polish(
                serializer.validated_data["text"],
                serializer.validated_data["operation"],
                target_role=session.target_role or None,
            )
        except LLMServiceError as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(result)


@extend_schema(tags=["Guided Resume Builder"])
class GuidedResumeFinalizeAPIView(APIView):
    """Turn the session into a real Resume (source="guided") and return its id."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        session = GuidedResumeService.get_or_start(request.user)
        resume = GuidedResumeService().finalize(session)
        return Response({"resume_id": resume.id}, status=status.HTTP_201_CREATED)
