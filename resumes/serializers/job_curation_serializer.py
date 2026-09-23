from urllib.parse import urlparse

from django.conf import settings
from rest_framework import serializers
from api.utils.config import get_config_value

from resumes.models import JobResumeCuration
from resumes.services.job_curation_ai_service import JOB_ANALYSIS_ARRAY_FIELDS
from resumes.services.llm_service import SUPPORTED_PROVIDERS


MAX_JOB_DESCRIPTION_LENGTH = 20000
MIN_JOB_DESCRIPTION_LENGTH = 30


class JobCurationRequestSerializer(serializers.Serializer):
    """Accepts *either* a job URL to import from, *or* a pasted title and
    description. See `validate` for the exact rule."""

    job_title = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=200,
        trim_whitespace=True,
        help_text="Job title the resume should be tailored toward. Optional when job_url is supplied.",
    )
    job_description = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=MAX_JOB_DESCRIPTION_LENGTH,
        trim_whitespace=True,
        help_text="Full job description text to analyze. Optional when job_url is supplied.",
    )
    curate_resume = serializers.BooleanField(
        help_text="When true, create a job curation analysis session.",
    )
    job_url = serializers.URLField(
        required=False,
        allow_blank=True,
        max_length=1000,
        help_text=(
            "Link to the original job posting. When job_description is not "
            "supplied, the server fetches this page and extracts the posting "
            "from it. Must be on a supported job site "
            "(settings.ALLOWED_JOB_URL_DOMAINS); other domains are rejected. "
            "Many large boards block automated access, in which case the "
            "request fails with JOB_URL_UNREADABLE and the description must "
            "be pasted instead."
        ),
    )
    ai_provider = serializers.ChoiceField(
        choices=SUPPORTED_PROVIDERS,
        required=False,
        allow_null=True,
        help_text=(
            "Which AI provider to use for this analysis (ollama | groq | "
            "gemini). Defaults to the server's configured provider."
        ),
    )
    resume_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="The ID of the specific resume to curate against. Optional; defaults to the user's latest completed resume.",
    )

    def validate_resume_id(self, value):
        if value:
            from resumes.models import Resume
            request = self.context.get("request")
            if not request or not request.user:
                raise serializers.ValidationError("Authentication required.")
            if not Resume.objects.filter(id=value, user=request.user, status="completed").exists():
                raise serializers.ValidationError("Resume not found or not completed.")
        return value

    def validate_job_title(self, value):
        # Blank is allowed here; `validate` decides whether it was required,
        # since that depends on whether a URL was supplied.
        value = value.strip()
        if value and len(value) < 2:
            raise serializers.ValidationError(
                "Job title must be at least 2 characters long."
            )
        return value

    def validate_job_description(self, value):
        value = value.strip()
        if value and len(value) < MIN_JOB_DESCRIPTION_LENGTH:
            raise serializers.ValidationError(
                "Job description must be at least 30 characters long."
            )
        return value

    def validate(self, attrs):
        """Either a URL to import from, or a pasted title + description.

        Reported against `job_description` rather than as a non-field error
        so the message lands on the field the user is most likely looking at.
        """
        has_url = bool(attrs.get("job_url", "").strip())
        has_description = bool(attrs.get("job_description", "").strip())
        has_title = bool(attrs.get("job_title", "").strip())

        if not has_url and not has_description:
            raise serializers.ValidationError({
                "job_description": [
                    "Paste the job description, or provide a link to the posting."
                ]
            })

        # Without a URL to import from, the title can't be recovered later.
        if not has_url and not has_title:
            raise serializers.ValidationError({
                "job_title": ["Enter the job title, or provide a link to the posting."]
            })

        return attrs

    def validate_job_url(self, value):
        value = value.strip()
        if not value:
            return value

        from resumes.services.job_url_fetch_service import is_job_domain_allowed

        if not is_job_domain_allowed(urlparse(value).hostname):
            allowed_domains = get_config_value("ALLOWED_JOB_URL_DOMAINS", settings.ALLOWED_JOB_URL_DOMAINS)
            raise serializers.ValidationError(
                "That link isn't from a supported job site. Supported: "
                + ", ".join(sorted(allowed_domains))
                + "."
            )
        return value


class JobUrlPreviewRequestSerializer(serializers.Serializer):
    job_url = serializers.URLField(max_length=1000)

    def validate_job_url(self, value):
        return JobCurationRequestSerializer().validate_job_url(value)


class JobUrlPreviewResponseSerializer(serializers.Serializer):
    job_title = serializers.CharField(allow_blank=True)
    job_description = serializers.CharField()
    company = serializers.CharField(allow_blank=True, required=False)
    location = serializers.CharField(allow_blank=True, required=False)
    employment_type = serializers.CharField(allow_blank=True, required=False)
    seniority = serializers.CharField(allow_blank=True, required=False)
    # Which extraction strategy produced it (linkedin, json_ld, cache, ...).
    source = serializers.CharField(allow_blank=True, required=False)


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
    analysis_data = serializers.SerializerMethodField()
    # Booleans, not the payloads themselves: the client needs to know which
    # AI work is already done (so it can show it without re-running anything,
    # and can ask before spending a credit on work that isn't), but shipping
    # the full gap/resume/letter blobs in every list row would be wasteful.
    has_gap_analysis = serializers.SerializerMethodField()
    has_tailored_resume = serializers.SerializerMethodField()
    has_cover_letter = serializers.SerializerMethodField()

    def get_analysis_data(self, curation):
        if curation.status == "completed" and curation.analysis_data:
            return JobAnalysisSerializer(curation.analysis_data).data
        return {}

    class Meta:
        model = JobResumeCuration
        fields = [
            "id",
            "resume",
            "job_title",
            "job_url",
            "curate_resume",
            "status",
            "analysis_data",
            "error_code",
            "application_status",
            "applied_at",
            # Provenance: lets the tracker say why a job the user never
            # typed in is in their list, and distinguish an application we
            # watched being submitted from one they told us about.
            "source",
            "autofilled_at",
            "submit_detected_at",
            "has_gap_analysis",
            "has_tailored_resume",
            "has_cover_letter",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_has_gap_analysis(self, curation):
        return bool(curation.gap_analysis_data)

    def get_has_tailored_resume(self, curation):
        return bool(curation.tailored_resume_data)

    def get_has_cover_letter(self, curation):
        return bool(curation.cover_letter_data)


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
    ai_provider = serializers.ChoiceField(
        choices=SUPPORTED_PROVIDERS,
        required=False,
        allow_null=True,
        help_text=(
            "Which AI provider to use for this generation (ollama | groq | "
            "gemini). Defaults to the server's configured provider."
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
    header = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=(
            "The entry's identifying line only - role/title, employer and dates - "
            "so it can be rendered as a heading above its bullets. Absent on "
            "curations generated before this field existed; clients should fall "
            "back to tailored_text."
        ),
    )
    bullets = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            "The entry's individual achievements, one per bullet. A formatting "
            "split of the same grounded content as tailored_text - each bullet is "
            "grounding-checked identically, so this introduces no new facts."
        ),
    )


class TailoredResumeDataSerializer(serializers.Serializer):
    personal_information = PersonalInformationSerializer()
    summary = serializers.CharField(allow_blank=True)
    skills = serializers.ListField(child=serializers.CharField())
    experience = TailoredResumeItemSerializer(many=True)
    projects = TailoredResumeItemSerializer(many=True)
    education = TailoredResumeItemSerializer(many=True)
    certifications = TailoredResumeItemSerializer(many=True)

