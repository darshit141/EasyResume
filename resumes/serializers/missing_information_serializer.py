from rest_framework import serializers

from resumes.serializers.job_curation_serializer import (
    ATSKeywordCoverageSerializer,
    ProfileRecommendationSerializer,
)
from resumes.services.llm_service import SUPPORTED_PROVIDERS


class MissingInformationQuerySerializer(serializers.Serializer):
    regenerate = serializers.BooleanField(
        default=False,
        required=False,
        help_text=(
            "When true, forces a fresh candidate-to-job comparison instead of "
            "reusing a cached one. Pass this after updating your profile so "
            "the review reflects the new information."
        ),
    )
    ai_provider = serializers.ChoiceField(
        choices=SUPPORTED_PROVIDERS,
        required=False,
        allow_null=True,
        help_text=(
            "Which AI provider to use for this comparison (ollama | groq | "
            "gemini). Defaults to the server's configured provider."
        ),
    )


class RequirementSummarySerializer(serializers.Serializer):
    requirement = serializers.CharField()
    category = serializers.CharField()
    match_status = serializers.ChoiceField(
        choices=["MATCHED", "PARTIALLY_MATCHED"],
    )
    reasoning = serializers.CharField()


class MissingRequirementSerializer(serializers.Serializer):
    requirement = serializers.CharField()
    category = serializers.CharField()
    profile_section = serializers.CharField(
        help_text="Profile section to review or update for this requirement.",
    )
    message = serializers.CharField(
        help_text="Neutral statement of fact: this requirement is not present in the profile.",
    )
    guidance = serializers.CharField(
        help_text=(
            "Non-presumptuous guidance: add it only if genuinely already "
            "possessed, otherwise treat it as a skill gap to acquire."
        ),
    )


class MissingInformationReviewSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    job_title = serializers.CharField()
    overall_match_percentage = serializers.FloatField(min_value=0, max_value=100)
    strong_matches = RequirementSummarySerializer(many=True)
    partial_matches = RequirementSummarySerializer(many=True)
    missing_requirements = MissingRequirementSerializer(many=True)
    missing_candidate_information = serializers.ListField(child=serializers.CharField())
    recommendations = ProfileRecommendationSerializer(many=True)
    ats_keyword_gaps = ATSKeywordCoverageSerializer()
    suggested_profile_sections = serializers.ListField(child=serializers.CharField())
