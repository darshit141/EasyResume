"""AI-generation and source-data grounding for job-tailored resumes.

The AI is only ever allowed to reorder, reword, and select from verified
candidate data. Every generated fact is checked against the candidate's
source data before it can be persisted or returned; anything that cannot be
traced back to a source entry is rejected rather than silently accepted.
"""

import json
import re

from resumes.prompts import TAILORED_RESUME_SCHEMA, build_resume_curation_prompt
from resumes.services.llm_service import (
    LLMSchemaValidationError,
    LLMServiceError,
    get_llm_service,
)


class ResumeCurationGroundingError(LLMServiceError):
    code = "RESUME_CURATION_GROUNDING_FAILED"
    message = "Generated resume content could not be verified against candidate data."

    def __init__(self, violations=None):
        self.violations = list(violations or [])
        super().__init__(self.message)


class ResumeCurationAIService:
    """
    Uses the configured LLM provider to produce a job-tailored resume, then
    validates the result strictly against the candidate's own source data.
    """

    ITEM_FIELDS = ("experience", "projects", "education", "certifications")
    MAX_ATTEMPTS = 2
    _TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*|\d+(?:\.\d+)?%?")

    def __init__(self, llm_service=None):
        self.llm_service = llm_service or get_llm_service()

    def generate_tailored_resume(
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
            prompt = build_resume_curation_prompt(
                candidate_source_data=json.dumps(candidate_source_data, sort_keys=True),
                job_title=job_title,
                job_description=job_description,
                job_requirements=json.dumps(job_requirements, sort_keys=True),
                candidate_match_summary=json.dumps(candidate_match_summary, sort_keys=True),
                retry_feedback=retry_feedback,
            )
            raw = self.llm_service.generate_structured(prompt, TAILORED_RESUME_SCHEMA)
            try:
                return self.validate_and_ground(raw, candidate_source_data)
            except ResumeCurationGroundingError as exc:
                last_error = exc
                retry_feedback = exc.violations

        raise last_error

    @classmethod
    def validate_and_ground(cls, raw, candidate_source_data):
        cls._validate_shape(raw)

        candidate_text = cls._candidate_text_blob(candidate_source_data)
        candidate_skill_terms = {
            cls._normalise_text(skill)
            for skill in candidate_source_data.get("skills", [])
        }

        violations = []

        summary = raw["summary"].strip()
        violations += cls._grounding_violations("summary", summary, candidate_text)

        skills = []
        for skill in raw["skills"]:
            skill = skill.strip()
            if not skill:
                continue
            if cls._normalise_text(skill) not in candidate_skill_terms:
                violations.append(
                    f"skills: '{skill}' is not present in verified candidate data."
                )
                continue
            skills.append(skill)

        result_items = {}
        for field in cls.ITEM_FIELDS:
            source_list = candidate_source_data.get(field, [])
            validated_entries = []
            for item in raw[field]:
                index = item["source_index"]
                text = item["tailored_text"].strip()
                if index < 0 or index >= len(source_list):
                    violations.append(
                        f"{field}: source_index {index} does not reference verified "
                        "candidate data."
                    )
                    continue
                violations += cls._grounding_violations(field, text, candidate_text)
                validated_entries.append({
                    "source_index": index,
                    "source_text": source_list[index],
                    "tailored_text": text,
                })
            result_items[field] = validated_entries

        if violations:
            raise ResumeCurationGroundingError(violations=violations)

        return {
            "summary": summary,
            "skills": skills,
            **result_items,
        }

    @classmethod
    def _validate_shape(cls, raw):
        if not isinstance(raw, dict) or set(raw) != set(TAILORED_RESUME_SCHEMA["required"]):
            raise LLMSchemaValidationError()
        if not isinstance(raw["summary"], str):
            raise LLMSchemaValidationError()
        if not isinstance(raw["skills"], list) or not all(
            isinstance(skill, str) for skill in raw["skills"]
        ):
            raise LLMSchemaValidationError()
        for field in cls.ITEM_FIELDS:
            if not isinstance(raw[field], list):
                raise LLMSchemaValidationError()
            for item in raw[field]:
                if not isinstance(item, dict) or set(item) != {"source_index", "tailored_text"}:
                    raise LLMSchemaValidationError()
                index = item["source_index"]
                if not isinstance(index, int) or isinstance(index, bool):
                    raise LLMSchemaValidationError()
                if not isinstance(item["tailored_text"], str):
                    raise LLMSchemaValidationError()

    @classmethod
    def _significant_tokens(cls, text):
        """Tokens that look like facts (numbers, dates, proper nouns) and must be grounded.

        The leading token of each text is skipped since it is almost always a
        freely-reworded action verb, not a fact.
        """
        raw_tokens = cls._TOKEN_RE.findall(text)
        significant = []
        for index, raw_token in enumerate(raw_tokens):
            if index == 0:
                continue
            # A trailing period is almost always sentence punctuation, not
            # part of the fact itself (unlike an internal period, as in
            # "Node.js" or "3.5"), so it is stripped before comparison.
            token = raw_token.rstrip(".")
            if not token:
                continue
            has_digit = any(character.isdigit() for character in token)
            is_capitalized = token[0].isupper()
            if has_digit or (is_capitalized and len(token) >= 2):
                significant.append(token)
        return significant

    @classmethod
    def _grounding_violations(cls, field, text, candidate_text):
        violations = []
        for token in cls._significant_tokens(text):
            normalised = cls._normalise_text(token)
            if normalised and not cls._is_grounded(normalised, candidate_text):
                violations.append(
                    f"{field}: '{token}' is not present in verified candidate data."
                )
        return violations

    @staticmethod
    def _is_grounded(normalised, candidate_text):
        """Boundary-aware search: a fact must appear as a whole token, not merely
        as a substring of an unrelated number or word (e.g. '8' inside a phone
        number), while still tolerating adjacent punctuation like a sentence-
        ending period."""
        pattern = r"(?<![a-z0-9])" + re.escape(normalised) + r"(?![a-z0-9])"
        return re.search(pattern, candidate_text) is not None

    @classmethod
    def _candidate_text_blob(cls, candidate_source_data):
        groundable = {
            key: value
            for key, value in candidate_source_data.items()
            if key not in {"resume_id", "personal_information"}
        }
        return cls._normalise_text(json.dumps(groundable, sort_keys=True))

    @staticmethod
    def _normalise_text(value):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.+#]+", " ", str(value).lower())).strip()
