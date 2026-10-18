"""AI generation and source grounding for job-specific cover letters.

Same guarantee as the tailored resume: the model may only reword and select
from data the candidate actually supplied. Anything that cannot be traced
back to a source is rejected rather than silently accepted.

One deliberate difference from the resume grounder: a cover letter is
*expected* to name the role and quote the employer's own stated needs back at
them. So the ground-truth blob here is the candidate's data **plus the job
posting text**. Referencing the posting is legitimate; inventing a candidate
fact is not.
"""

import json

from resumes.prompts import COVER_LETTER_SCHEMA, build_cover_letter_prompt
from resumes.services.llm_service import (
    LLMSchemaValidationError,
    LLMServiceError,
    get_llm_service,
)
from resumes.services.resume_curation_ai_service import ResumeCurationAIService


class CoverLetterGroundingError(LLMServiceError):
    code = "COVER_LETTER_GROUNDING_FAILED"
    message = "Generated cover letter content could not be verified against candidate data."

    def __init__(self, violations=None):
        self.violations = list(violations or [])
        super().__init__(self.message)


class CoverLetterAIService:
    MAX_ATTEMPTS = 2
    MAX_BODY_PARAGRAPHS = 4

    # Standard salutation vocabulary. These are courtesy formulae, not claims
    # about anybody, so grounding them would reject the single most common
    # opening line in existence ("Dear Hiring Manager") for containing words
    # absent from the resume. Everything *outside* this list is still
    # grounded, so an invented addressee ("Dear Ms. Sharma", when no such
    # name appears in the posting) is still caught.
    SALUTATION_ALLOWLIST = frozenset({
        "dear", "hello", "greetings", "hi",
        "hiring", "manager", "team", "recruiter", "recruiting", "recruitment",
        "talent", "acquisition", "committee", "department", "sir", "madam",
        "to", "whom", "it", "may", "concern",
    })

    def __init__(self, llm_service=None, provider=None, user=None):
        """``user`` is the account the work is being done for. It is used for
        one thing: if they have saved their own provider API key, the client
        built here uses it instead of the server's (see
        billing.services.user_ai_key_service). Left None — a management
        command, a health check — the server's own credentials are used, which
        is the behaviour this had before BYOK existed."""
        self.user = user
        self.llm_service = llm_service or get_llm_service(provider, user=user)

    def generate_cover_letter(
        self,
        candidate_source_data,
        job_title,
        job_description,
        job_requirements,
        candidate_match_summary,
    ):
        retry_feedback = None
        last_error = None

        for _ in range(self.MAX_ATTEMPTS):
            prompt = build_cover_letter_prompt(
                candidate_source_data=json.dumps(candidate_source_data, sort_keys=True),
                job_title=job_title,
                job_description=job_description,
                job_requirements=json.dumps(job_requirements, sort_keys=True),
                candidate_match_summary=json.dumps(candidate_match_summary, sort_keys=True),
                retry_feedback=retry_feedback,
            )
            raw = self.llm_service.generate_structured(prompt, COVER_LETTER_SCHEMA)
            try:
                return self.validate_and_ground(
                    raw,
                    candidate_source_data,
                    job_title=job_title,
                    job_description=job_description,
                    job_requirements=job_requirements,
                )
            except CoverLetterGroundingError as exc:
                last_error = exc
                retry_feedback = exc.violations

        raise last_error

    @classmethod
    def validate_and_ground(
        cls, raw, candidate_source_data, job_title, job_description, job_requirements
    ):
        cls._validate_shape(raw)

        grounding_text = cls._grounding_text_blob(
            candidate_source_data, job_title, job_description, job_requirements
        )

        greeting = raw["greeting"].strip()
        opening = raw["opening_paragraph"].strip()
        closing = raw["closing_paragraph"].strip()
        body = [p.strip() for p in raw["body_paragraphs"] if p.strip()]

        violations = []
        violations += cls._greeting_violations(greeting, grounding_text)
        violations += cls._violations("opening_paragraph", opening, grounding_text)
        for index, paragraph in enumerate(body):
            violations += cls._violations(f"body_paragraphs[{index}]", paragraph, grounding_text)
        violations += cls._violations("closing_paragraph", closing, grounding_text)

        if violations:
            raise CoverLetterGroundingError(violations=violations)

        return {
            "greeting": greeting,
            "opening_paragraph": opening,
            "body_paragraphs": body,
            "closing_paragraph": closing,
            # Taken from verified data, never from the model — see the note on
            # COVER_LETTER_SCHEMA.
            "signature_name": (candidate_source_data.get("personal_information") or {}).get("name")
            or "",
        }

    @classmethod
    def _validate_shape(cls, raw):
        if not isinstance(raw, dict) or set(raw) != set(COVER_LETTER_SCHEMA["required"]):
            raise LLMSchemaValidationError()
        for field in ("greeting", "opening_paragraph", "closing_paragraph"):
            if not isinstance(raw[field], str):
                raise LLMSchemaValidationError()
        paragraphs = raw["body_paragraphs"]
        if not isinstance(paragraphs, list) or len(paragraphs) > cls.MAX_BODY_PARAGRAPHS:
            raise LLMSchemaValidationError()
        if not all(isinstance(p, str) for p in paragraphs):
            raise LLMSchemaValidationError()

    @classmethod
    def _violations(cls, field, text, grounding_text):
        """Reuses the tailored resume's token grounding so both features apply
        one identical definition of "is this fact actually supported?"."""
        return ResumeCurationAIService._grounding_violations(field, text, grounding_text)

    @classmethod
    def _greeting_violations(cls, greeting, grounding_text):
        """Grounds the greeting with standard salutation words exempted — see
        SALUTATION_ALLOWLIST."""
        return [
            violation
            for violation in cls._violations("greeting", greeting, grounding_text)
            if not cls._is_allowed_salutation_violation(violation)
        ]

    @classmethod
    def _is_allowed_salutation_violation(cls, violation):
        # Violations are formatted as: "greeting: 'Token' is not present ..."
        start = violation.find("'")
        end = violation.find("'", start + 1)
        if start == -1 or end == -1:
            return False
        return violation[start + 1:end].strip().lower() in cls.SALUTATION_ALLOWLIST

    @classmethod
    def _grounding_text_blob(
        cls, candidate_source_data, job_title, job_description, job_requirements
    ):
        candidate_blob = ResumeCurationAIService._candidate_text_blob(candidate_source_data)
        job_blob = ResumeCurationAIService._normalise_text(
            " ".join([
                str(job_title or ""),
                str(job_description or ""),
                json.dumps(job_requirements or {}, ensure_ascii=False, sort_keys=True),
            ])
        )
        return f"{candidate_blob} {job_blob}"
