from resumes.prompts import (
    JOB_DESCRIPTION_ANALYSIS_SCHEMA,
    build_job_description_extraction_prompt,
)
from resumes.services.llm_service import (
    LLMSchemaValidationError,
    get_llm_service,
)


JOB_ANALYSIS_ARRAY_FIELDS = [
    "required_skills",
    "preferred_skills",
    "technologies",
    "frameworks",
    "databases",
    "cloud_technologies",
    "tools",
    "soft_skills",
    "responsibilities",
    "experience_requirements",
    "education_requirements",
    "certification_requirements",
    "important_keywords",
    "ats_relevant_terms",
    "other_explicit_requirements",
]


class JobCurationAIService:
    """
    Uses the configured LLM provider to extract strict job requirement data.
    """

    def __init__(self, llm_service=None, provider=None, user=None, model=None, fallback_provider=None):
        """``user`` is the account the work is being done for. It is used for
        one thing: if they have saved their own provider API key, the client
        built here uses it instead of the server's (see
        billing.services.user_ai_key_service). Left None — a management
        command, a health check — the server's own credentials are used, which
        is the behaviour this had before BYOK existed."""
        self.user = user
        self.llm_service = llm_service or get_llm_service(
            provider, user=user, model=model, fallback_provider=fallback_provider
        )

    def analyze_job_description(self, job_description):
        prompt = build_job_description_extraction_prompt(job_description)
        analysis = self.llm_service.generate_structured(
            prompt,
            JOB_DESCRIPTION_ANALYSIS_SCHEMA,
        )
        return self.validate_analysis(analysis)

    @staticmethod
    def validate_analysis(analysis):
        if not isinstance(analysis, dict):
            raise LLMSchemaValidationError()

        expected_fields = set(JOB_ANALYSIS_ARRAY_FIELDS + ["seniority_level"])
        if set(analysis) != expected_fields:
            raise LLMSchemaValidationError()

        for field in JOB_ANALYSIS_ARRAY_FIELDS:
            value = analysis.get(field)
            if not isinstance(value, list):
                raise LLMSchemaValidationError()
            for item in value:
                if not isinstance(item, str):
                    raise LLMSchemaValidationError()

        if not isinstance(analysis.get("seniority_level"), str):
            raise LLMSchemaValidationError()

        return {
            **{
                field: [
                    item.strip()
                    for item in analysis[field]
                    if item.strip()
                ]
                for field in JOB_ANALYSIS_ARRAY_FIELDS
            },
            "seniority_level": analysis["seniority_level"].strip(),
        }
