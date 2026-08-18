from rest_framework import serializers

from resumes.models import JobResumeCurationVersion
from resumes.serializers.job_curation_serializer import TailoredResumeDataSerializer
from resumes.serializers.missing_information_serializer import (
    MissingInformationReviewSerializer,
)


class TailoredResumeVersionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    version = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class TailoredResumeVersionComparisonSerializer(serializers.Serializer):
    """A factual, deterministically computed diff against the immediately
    prior version — never AI-generated commentary. Only present when a
    prior version actually exists (never on version 1)."""

    previous_version = serializers.IntegerField()
    previous_match_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, allow_null=True
    )
    current_match_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, allow_null=True
    )
    match_percentage_delta = serializers.FloatField(allow_null=True)
    newly_matched_requirements = serializers.ListField(child=serializers.CharField())
    no_longer_matched_requirements = serializers.ListField(child=serializers.CharField())
    newly_emphasized_skills = serializers.ListField(child=serializers.CharField())


class JobResumeCurationRegenerationResponseSerializer(serializers.Serializer):
    """
    Bundles everything the frontend needs after a (re)generation call: the
    new tailored resume version, the tailored resume itself, a freshly
    consistent gap review (match score, gap summary, missing requirements)
    computed from the same underlying candidate-vs-job comparison, and a
    factual comparison against the previous version (if one exists).
    """

    id = serializers.IntegerField()
    job_title = serializers.CharField()
    status = serializers.CharField()
    tailored_resume_version = TailoredResumeVersionSerializer(
        allow_null=True,
        required=False,
    )
    tailored_resume_data = TailoredResumeDataSerializer()
    tailored_resume_error_code = serializers.CharField()
    gap_review = MissingInformationReviewSerializer()
    version_comparison = TailoredResumeVersionComparisonSerializer(
        allow_null=True,
        required=False,
    )
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class TailoredResumeVersionListSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobResumeCurationVersion
        fields = [
            "id",
            "version",
            "match_percentage",
            "created_at",
        ]


class TailoredResumeVersionDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobResumeCurationVersion
        fields = [
            "id",
            "version",
            "tailored_resume_data",
            "match_percentage",
            "gap_analysis_snapshot",
            "created_at",
        ]
