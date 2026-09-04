"""AI drafting and source grounding for outbound application emails.

This is the highest-stakes generated text in the product. A tailored resume
or cover letter is reviewed by the candidate inside our app before it goes
anywhere; this email is addressed to a real employer and signed with the
candidate's real name. A fabricated year of experience here is not a bad
paragraph — it is a lie told on someone's behalf, to someone who may hire
them.

So the same token grounding the tailored resume uses applies to the whole
body, against the candidate's verified data plus the job posting (quoting
the employer's own words back at them is legitimate; inventing a candidate
fact is not).

The one exemption is email boilerplate — "Dear hiring team", "Kind regards",
"Please find my resume attached". Those are courtesy formulae, not claims
about anybody, and grounding them would reject the most common opening line
in existence for containing words absent from a resume. Crucially the
exemption never covers a token containing a digit, so an invented metric
("5 years", "40% growth") is still caught.
"""

import json

from resumes.prompts import APPLICATION_EMAIL_SCHEMA, build_application_email_prompt
from resumes.services.llm_service import (
    LLMSchemaValidationError,
    LLMServiceError,
    get_llm_service,
)
from resumes.services.resume_curation_ai_service import ResumeCurationAIService


def _attachments_note(include_resume, include_cover_letter):
    """Describe what will really be sent, so the email can't promise a
    document that isn't there."""
    items = []
    if include_resume:
        items.append("the candidate's resume (PDF attachment)")
    if include_cover_letter:
        items.append("a full cover letter appended below the message")
    if not items:
        return ""
    return "This email will include " + " and ".join(items) + "."


class ApplicationEmailGroundingError(LLMServiceError):
    code = "APPLICATION_EMAIL_GROUNDING_FAILED"
    message = "The drafted application email made claims that could not be verified against your resume."

    def __init__(self, violations=None):
        self.violations = list(violations or [])
        super().__init__(self.message)


class JobApplicationAIService:
    MAX_ATTEMPTS = 2

    # Courtesy and structural vocabulary an application email cannot be
    # written without. Extends the cover letter's salutation allowlist with
    # sign-off and "here is my application" phrasing.
    BOILERPLATE_ALLOWLIST = frozenset({
        # Salutations
        "dear", "hello", "greetings", "hi",
        "hiring", "manager", "team", "recruiter", "recruiting", "recruitment",
        "talent", "acquisition", "committee", "department", "sir", "madam",
        "to", "whom", "it", "may", "concern",
        # Sign-offs
        "regards", "kind", "best", "warm", "sincerely", "yours", "faithfully",
        "truly", "thanks", "thank", "you",
        # Application vocabulary
        "application", "applying", "apply", "position", "role", "vacancy",
        "opening", "opportunity", "candidate", "attached", "attachment",
        "enclosed", "resume", "cv", "cover", "letter", "please", "find",
        "looking", "forward", "hearing", "interest", "interested", "available",
        "happy", "discuss", "further", "detail", "details", "questions",
        "i", "my", "am", "the", "a", "an", "and", "for", "with", "this", "that",
    })

    # Ordinary English words that are capitalised only because they start a
    # sentence. The grounding heuristic treats any capitalised word as a
    # candidate proper noun, so "As a backend engineer..." gets flagged for
    # "As" — a grammatical artefact, not a claim about anybody.
    #
    # This does not weaken the check that matters. What grounding actually
    # protects against is invented proper nouns ("Globex") and invented
    # numbers ("12 years"), and no word below is either. Lower-cased claims
    # were never caught by this heuristic in the first place.
    SENTENCE_OPENERS = frozenset({
        "as", "at", "in", "on", "of", "by", "from", "during", "while", "after",
        "before", "since", "having", "over", "under", "across", "throughout",
        "alongside", "beyond", "within", "these", "those", "they", "their",
        "there", "its", "we", "our", "you", "your", "if", "when", "where",
        "what", "which", "who", "whom", "why", "how", "both", "each", "either",
        "neither", "not", "all", "any", "some", "many", "more", "most", "less",
        "other", "another", "such", "than", "then", "though", "although",
        "because", "but", "or", "so", "yet", "also", "however", "moreover",
        "therefore", "thus", "here", "now", "once", "only", "own", "same",
        "very", "just", "even", "still", "again", "further", "additionally",
        "furthermore", "currently", "previously", "recently", "throughout",
        "beyond", "between", "through", "along", "about", "above", "below",
        "it", "he", "she", "his", "her", "them", "was", "were", "been", "being",
        "have", "has", "had", "do", "does", "did", "can", "could", "would",
        "should", "will", "shall", "may", "might", "must", "is", "are", "be",
    })

    def __init__(self, llm_service=None, provider=None):
        self.llm_service = llm_service or get_llm_service(provider)

    def draft_email(
        self,
        candidate_source_data,
        job_title,
        job_description,
        job_requirements,
        company_name="",
        recipient_name="",
        include_resume=True,
        include_cover_letter=False,
    ):
        """Return `{"subject": str, "body": str}`, grounded or raising."""
        candidate_name = (
            (candidate_source_data.get("personal_information") or {}).get("name") or ""
        ).strip()
        attachments_note = _attachments_note(include_resume, include_cover_letter)

        retry_feedback = None
        last_error = None
        for _ in range(self.MAX_ATTEMPTS):
            prompt = build_application_email_prompt(
                job_title=job_title,
                company_name=company_name,
                recipient_name=recipient_name,
                candidate_name=candidate_name,
                candidate_evidence=json.dumps(candidate_source_data, sort_keys=True),
                job_requirements_text=json.dumps(job_requirements or {}, sort_keys=True),
                attachments_note=attachments_note,
                retry_feedback=retry_feedback,
            )
            raw = self.llm_service.generate_structured(prompt, APPLICATION_EMAIL_SCHEMA)
            try:
                return self.validate_and_ground(
                    raw,
                    candidate_source_data=candidate_source_data,
                    job_title=job_title,
                    job_description=job_description,
                    job_requirements=job_requirements,
                    company_name=company_name,
                    recipient_name=recipient_name,
                )
            except ApplicationEmailGroundingError as exc:
                last_error = exc
                retry_feedback = exc.violations

        raise last_error

    @classmethod
    def validate_and_ground(
        cls,
        raw,
        candidate_source_data,
        job_title,
        job_description,
        job_requirements,
        company_name="",
        recipient_name="",
    ):
        if not isinstance(raw, dict) or set(raw) != {"subject", "body"}:
            raise LLMSchemaValidationError()
        if not isinstance(raw["subject"], str) or not isinstance(raw["body"], str):
            raise LLMSchemaValidationError()

        subject = raw["subject"].strip()
        body = raw["body"].strip()
        if not subject or not body:
            raise LLMSchemaValidationError()

        grounding_text = cls._grounding_text_blob(
            candidate_source_data,
            job_title,
            job_description,
            job_requirements,
            company_name,
            recipient_name,
        )

        violations = [
            violation
            for violation in (
                ResumeCurationAIService._grounding_violations("subject", subject, grounding_text)
                + ResumeCurationAIService._grounding_violations("body", body, grounding_text)
            )
            if not cls._is_boilerplate(violation)
        ]
        if violations:
            raise ApplicationEmailGroundingError(violations=violations)

        return {"subject": subject, "body": body}

    @classmethod
    def _is_boilerplate(cls, violation):
        """Whether a violation is standard email courtesy vocabulary.

        A token containing a digit is never boilerplate, so invented metrics
        and durations stay caught no matter how they are phrased.
        """
        token = cls._violation_token(violation)
        if not token or any(char.isdigit() for char in token):
            return False
        lowered = token.lower()
        return lowered in cls.BOILERPLATE_ALLOWLIST or lowered in cls.SENTENCE_OPENERS

    @staticmethod
    def _violation_token(violation):
        # Violations are formatted as: "body: 'Token' is not present ..."
        start = violation.find("'")
        end = violation.find("'", start + 1)
        if start == -1 or end == -1:
            return ""
        return violation[start + 1:end].strip()

    @classmethod
    def _grounding_text_blob(
        cls,
        candidate_source_data,
        job_title,
        job_description,
        job_requirements,
        company_name="",
        recipient_name="",
    ):
        candidate_blob = ResumeCurationAIService._candidate_text_blob(candidate_source_data)
        # The tailored resume never restates the candidate's own name, so
        # `_candidate_text_blob` leaves personal_information out. An email
        # must sign off with that name, so add it back here — it is the
        # user's own verified contact data, not something the model invented.
        personal = candidate_source_data.get("personal_information") or {}
        job_blob = ResumeCurationAIService._normalise_text(
            " ".join([
                " ".join(str(value) for value in personal.values() if value),
                str(job_title or ""),
                str(job_description or ""),
                str(company_name or ""),
                # The addressee the user themselves typed in is a fact they
                # supplied, so the model may use it.
                str(recipient_name or ""),
                json.dumps(job_requirements or {}, sort_keys=True),
            ])
        )
        return f"{candidate_blob} {job_blob}"
