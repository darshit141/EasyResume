from rest_framework import serializers

from resumes.models import JobResumeCuration
from resumes.services.job_curation_ai_service import JOB_ANALYSIS_ARRAY_FIELDS


MAX_JOB_DESCRIPTION_LENGTH = 20000
MIN_JOB_DESCRIPTION_LENGTH = 30


class JobCurationRequestSerializer(serializers.Serializer):
    job_title = serializers.CharField(
        max_length=200,
        trim_whitespace=True,
        help_text="Job title the resume should be tailored toward.",
    )
    job_description = serializers.CharField(
        max_length=MAX_JOB_DESCRIPTION_LENGTH,
        trim_whitespace=True,
        help_text="Full job description text to analyze.",
    )
    curate_resume = serializers.BooleanField(
        help_text="When true, create a job curation analysis session.",
    )

    def validate_job_title(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError(
                "Job title must be at least 2 characters long."
            )
        return value.strip()

    def validate_job_description(self, value):
        value = value.strip()
        if len(value) < MIN_JOB_DESCRIPTION_LENGTH:
            raise serializers.ValidationError(
                "Job description must be at least 30 characters long."
            )
        return value


class JobAnalysisSerializer(serializers.Serializer):
    required_skills = serializers.ListField(child=serializers.CharField())
    preferred_skills = serializers.ListField(child=serializers.CharField())
    technologies = serializers.ListField(child=serializers.CharField())
    frameworks = serializers.ListField(child=serializers.CharField())
    databases = serializers.ListField(child=serializers.CharField())
    cloud_technologies = serializers.ListField(child=serializers.CharField())
    tools = serializers.ListField(child=serializers.CharField())
    soft_skills = serializers.ListField(child=serializers.CharField())
    responsibilities = serializers.ListField(child=serializers.CharField())
    experience_requirements = serializers.ListField(child=serializers.CharField())
    education_requirements = serializers.ListField(child=serializers.CharField())
    certification_requirements = serializers.ListField(child=serializers.CharField())
    important_keywords = serializers.ListField(child=serializers.CharField())
    ats_relevant_terms = serializers.ListField(child=serializers.CharField())
    seniority_level = serializers.CharField(allow_blank=True)
    other_explicit_requirements = serializers.ListField(
        child=serializers.CharField()
    )

    def validate(self, attrs):
        if set(attrs) != set(JOB_ANALYSIS_ARRAY_FIELDS + ["seniority_level"]):
            raise serializers.ValidationError("Invalid job analysis structure.")
        return attrs


class JobResumeCurationSerializer(serializers.ModelSerializer):
    analysis_data = JobAnalysisSerializer(read_only=True)

    class Meta:
        model = JobResumeCuration
        fields = [
            "id",
            "job_title",
            "curate_resume",
            "status",
            "analysis_data",
            "error_code",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class JobCurationSkippedSerializer(serializers.Serializer):
    curate_resume = serializers.BooleanField()
    message = serializers.CharField()


class APIErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code = serializers.CharField()


class GapRequirementSerializer(serializers.Serializer):
    requirement = serializers.CharField()
    category = serializers.CharField()
    match_status = serializers.ChoiceField(
        choices=[
            "MATCHED",
            "PARTIALLY_MATCHED",
            "MISSING",
            "NOT_VERIFIABLE",
        ]
    )
    candidate_evidence = serializers.ListField(child=serializers.CharField())
    confidence = serializers.FloatField(min_value=0, max_value=1)
    reasoning = serializers.CharField()
    recommendation = serializers.CharField()


class ProfileRecommendationSerializer(serializers.Serializer):
    recommendation = serializers.CharField()
    recommendation_type = serializers.ChoiceField(
        choices=["MISSING_PROFILE_INFORMATION", "SKILL_TO_ACQUIRE"]
    )


class ATSKeywordCoverageSerializer(serializers.Serializer):
    covered_keywords = serializers.ListField(child=serializers.CharField())
    missing_keywords = serializers.ListField(child=serializers.CharField())
    coverage_percentage = serializers.FloatField(min_value=0, max_value=100)


class GapAnalysisDataSerializer(serializers.Serializer):
    overall_match_percentage = serializers.FloatField(min_value=0, max_value=100)
    matched_requirements = GapRequirementSerializer(many=True)
    partially_matched_requirements = GapRequirementSerializer(many=True)
    missing_requirements = GapRequirementSerializer(many=True)
    not_verifiable_requirements = GapRequirementSerializer(many=True)
    strengths = serializers.ListField(child=serializers.CharField())
    weaknesses_gaps = serializers.ListField(child=serializers.CharField())
    important_missing_information = serializers.ListField(child=serializers.CharField())
    recommendations_for_profile_improvement = ProfileRecommendationSerializer(many=True)
    ats_keyword_coverage = ATSKeywordCoverageSerializer()
    interview_readiness_observations = serializers.ListField(
        child=serializers.CharField()
    )


class JobGapAnalysisResponseSerializer(serializers.ModelSerializer):
    gap_analysis_data = GapAnalysisDataSerializer(read_only=True)

    class Meta:
        model = JobResumeCuration
        fields = [
            "id",
            "job_title",
            "status",
            "gap_analysis_data",
            "gap_analysis_error_code",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ResumeCurationGenerateRequestSerializer(serializers.Serializer):
    regenerate = serializers.BooleanField(
        default=False,
        required=False,
        help_text=(
            "When true, forces a fresh AI generation and replaces any previously "
            "generated tailored resume. When false, a cached tailored resume is "
            "returned if one already exists."
        ),
    )


class PersonalInformationSerializer(serializers.Serializer):
    name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    email = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)


class TailoredResumeItemSerializer(serializers.Serializer):
    source_index = serializers.IntegerField(
        help_text="Index into the candidate's verified source data list this entry was grounded in.",
    )
    source_text = serializers.CharField(
        help_text="The original, unmodified candidate source entry.",
    )
    tailored_text = serializers.CharField(
        help_text="The AI-tailored wording, validated to introduce no new facts.",
    )


class TailoredResumeDataSerializer(serializers.Serializer):
    personal_information = PersonalInformationSerializer()
    summary = serializers.CharField(allow_blank=True)
    skills = serializers.ListField(child=serializers.CharField())
    experience = TailoredResumeItemSerializer(many=True)
    projects = TailoredResumeItemSerializer(many=True)
    education = TailoredResumeItemSerializer(many=True)
    certifications = TailoredResumeItemSerializer(many=True)

