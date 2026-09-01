from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.services.entitlements_service import PlanRestrictionError
from billing.views.plan_limit_response import plan_restriction_response
from resumes.resume_template import preset_list
from resumes.serializers.resume_template_serializer import (
    ResumeTemplatePreferenceSerializer,
    ResumeTemplatePresetSerializer,
)
from resumes.services.resume_template_service import ResumeTemplateService


@extend_schema(
    tags=["Job Resume Curation"],
    summary="List the built-in resume layout templates",
    description=(
        "The presets a user can choose from for their tailored resume "
        "layout, each with its fully-resolved config and a plain-language "
        "ATS-safety report. Free plans may select any preset; adjusting the "
        "individual layout options on top of one is a Pro feature (see "
        "PUT /api/resumes/template-preference/)."
    ),
    responses={status.HTTP_200_OK: ResumeTemplatePresetSerializer(many=True)},
)
class ResumeTemplateListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(ResumeTemplatePresetSerializer(preset_list(), many=True).data)


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Get or set the user's default resume layout template",
    description=(
        "GET returns the user's current template choice, its resolved "
        "config and an ATS-safety report.\n\n"
        "PUT sets it: `template_id` (one of the presets) is required; "
        "`overrides` is an optional object of individual layout options "
        "(`heading_style`, `accent_color`, `font_family`, `name_alignment`, "
        "`density`, `columns`, `section_order`, `hidden_sections`). Sending "
        "a non-empty `overrides` requires the Pro plan; a Free user picks a "
        "preset only.\n\n"
        "Changing this preference never alters a tailored resume that was "
        "already generated — each version snapshots the template it was "
        "built with."
    ),
    request=ResumeTemplatePreferenceSerializer,
    responses={
        status.HTTP_200_OK: ResumeTemplatePreferenceSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            description="Unknown preset id, or an override failed validation."
        ),
        status.HTTP_402_PAYMENT_REQUIRED: OpenApiResponse(
            description="A Free user sent layout overrides (Pro-only)."
        ),
    },
)
class ResumeTemplatePreferenceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            ResumeTemplatePreferenceSerializer(
                ResumeTemplateService.get_preference(request.user)
            ).data
        )

    def put(self, request):
        serializer = ResumeTemplatePreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        overrides = serializer.validated_data.get("overrides") or {}
        try:
            preference = ResumeTemplateService.set_preference(
                request.user,
                serializer.validated_data["template_id"],
                overrides,
            )
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)
        except ValueError as exc:
            return Response(
                {"detail": str(exc), "code": "INVALID_TEMPLATE"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(ResumeTemplatePreferenceSerializer(preference).data)
