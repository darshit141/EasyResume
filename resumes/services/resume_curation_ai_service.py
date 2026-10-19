"""AI-generation and source-data grounding for job-tailored resumes.

The AI is only ever allowed to reorder, reword, and select from verified
candidate data. Every generated fact is checked against the candidate's
source data before it can be persisted or returned; anything that cannot be
traced back to a source entry is rejected rather than silently accepted.
"""

import json
import logging
import re

from resumes.prompts import TAILORED_RESUME_SCHEMA, build_resume_curation_prompt
from resumes.services.llm_service import (
    LLMSchemaValidationError,
    LLMServiceError,
    get_llm_service,
)

logger = logging.getLogger(__name__)


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
    # A number keeps an immediately trailing magnitude / percent / multiplier
    # or duration/rate suffix as part of the same token — "1.2k", "50%", "2x", "30s" — so a
    # metric written the same way in the candidate's resume and in the
    # generated text compares equal.
    _TOKEN_RE = re.compile(
        r"[A-Za-z][A-Za-z0-9+#.]*|\d+(?:\.\d+)?(?:[kKmMbBxX%]|s|ms|sec|secs|seconds|min|mins|hr|hrs|h|d|days|yr|yrs|y|\+)?"
    )
    _NUMERIC_TOKEN_RE = re.compile(
        r"^(\d+(?:\.\d+)?)(?:[kmbx%]|s|ms|sec|secs|seconds|min|mins|hr|hrs|h|d|days|yr|yrs|y|\+)?$",
        re.IGNORECASE,
    )
    _SENTENCE_BREAKS = frozenset(".!?;:\n\r\t\u2022-\u2013\u2014|/(")
    _COMMON_RESUME_VOCABULARY = frozenset({
        "background", "experience", "experienced", "skills", "skilled", "summary", "overview",
        "professional", "proven", "track", "record", "results", "strong", "extensive", "deep",
        "solid", "hands", "demonstrated", "dedicated", "proficient", "successful", "expertise",
        "accomplished", "core", "key", "primary", "focus", "focused", "high", "quality",
        "solutions", "systems", "development", "architecture", "engineering", "engineer",
        "developer", "role", "roles", "position", "positions", "team", "teams", "work",
        "project", "projects", "environment", "environments", "industry", "industries",
        "practices", "responsibilities", "achievements", "contributions", "collaborated",
        "led", "built", "managed", "designed", "implemented", "delivered", "maintained",
        "resolved", "improved", "optimized", "developed", "created", "spearheaded",
        "orchestrated", "engineered", "architected", "streamlined", "automated", "accelerated",
        "facilitated", "executed", "mentored", "partnered", "supervised", "administered",
        "revamped", "championed", "directed", "pioneered", "formulated", "standardized",
        "consolidated", "maximized", "minimized", "modernized", "secured", "strengthened",
        "transformed", "validated", "audited", "authored", "conducted", "deployed",
        "established", "expanded", "identified", "initiated", "instituted", "launched",
        "operated", "organized", "oversaw", "performed", "planned", "prepared", "produced",
        "promoted", "provided", "reduced", "reorganized", "restructured", "simplified",
        "trained", "upgraded", "utilized", "leveraged", "integrated", "configured",
        "analyzed", "tested", "reviewed", "documented", "monitored",
        "year", "years", "month", "months", "day", "days", "time", "present", "current",
        # English grammatical sentence openers
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
        "furthermore", "currently", "previously", "recently",
    })

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

    def generate_tailored_resume(
        self,
        candidate_source_data,
        job_title,
        job_description,
        job_requirements,
        candidate_match_summary,
        allowed_missing_skills=None,
    ):
        retry_feedback = None
        last_error = None

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            prompt = build_resume_curation_prompt(
                candidate_source_data=json.dumps(
                    candidate_source_data, ensure_ascii=False, sort_keys=True
                ),
                job_title=job_title,
                job_description=job_description,
                job_requirements=json.dumps(job_requirements, sort_keys=True),
                candidate_match_summary=json.dumps(candidate_match_summary, sort_keys=True),
                retry_feedback=retry_feedback,
                allowed_missing_skills=allowed_missing_skills,
            )
            raw = self.llm_service.generate_structured(prompt, TAILORED_RESUME_SCHEMA)
            try:
                return self.validate_and_ground(raw, candidate_source_data, allowed_missing_skills=allowed_missing_skills)
            except ResumeCurationGroundingError as exc:
                last_error = exc
                retry_feedback = exc.violations
                logger.warning(
                    "Resume tailoring attempt %s/%s grounding failed: %s",
                    attempt,
                    self.MAX_ATTEMPTS,
                    exc.violations,
                )

        raise last_error

    def rephrase_resume(self, candidate_source_data):
        from resumes.prompts import build_resume_rephrase_prompt

        retry_feedback = None
        last_error = None

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            prompt = build_resume_rephrase_prompt(
                candidate_source_data=json.dumps(
                    candidate_source_data, ensure_ascii=False, sort_keys=True
                ),
                retry_feedback=retry_feedback,
            )
            raw = self.llm_service.generate_structured(prompt, TAILORED_RESUME_SCHEMA)
            try:
                return self.validate_and_ground(raw, candidate_source_data)
            except ResumeCurationGroundingError as exc:
                last_error = exc
                retry_feedback = exc.violations
                logger.warning(
                    "Resume rephrase attempt %s/%s grounding failed: %s",
                    attempt,
                    self.MAX_ATTEMPTS,
                    exc.violations,
                )

        raise last_error

    @classmethod
    def validate_and_ground(cls, raw, candidate_source_data, allowed_missing_skills=None):
        cls._validate_shape(raw)

        candidate_text = cls._candidate_text_blob(candidate_source_data, allowed_missing_skills=allowed_missing_skills)
        candidate_skill_terms = {
            cls._normalise_text(skill)
            for skill in candidate_source_data.get("skills", [])
            if cls._normalise_text(skill)
        }
        # Permit aliases for declared candidate skills (e.g. react -> react.js)
        from resumes.services.job_gap_analysis_service import TECH_ALIASES
        alias_expansions = set()
        for term in list(candidate_skill_terms):
            if term in TECH_ALIASES:
                for alias in TECH_ALIASES[term]:
                    alias_expansions.add(cls._normalise_text(alias))
        candidate_skill_terms.update(alias_expansions)

        # If user explicitly opted into adding missing job skills, permit them
        if allowed_missing_skills:
            for skill in allowed_missing_skills:
                norm = cls._normalise_text(skill)
                if norm:
                    candidate_skill_terms.add(norm)
                    if norm in TECH_ALIASES:
                        for alias in TECH_ALIASES[norm]:
                            candidate_skill_terms.add(cls._normalise_text(alias))

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

                # header/bullets are a formatting split of the same content,
                # so they get the identical grounding check - otherwise they
                # would be an unguarded channel for inventing detail that
                # tailored_text is not allowed to contain.
                header = str(item.get("header") or "").strip()
                bullets = [
                    str(b).strip() for b in (item.get("bullets") or []) if str(b).strip()
                ]
                if header:
                    violations += cls._grounding_violations(field, header, candidate_text)
                for bullet in bullets:
                    violations += cls._grounding_violations(field, bullet, candidate_text)

                # Falling back rather than failing: a missing split is a
                # cosmetic shortfall, and rejecting an otherwise valid,
                # fully-grounded resume over layout would be the wrong
                # trade. Renderers treat an empty bullets list as "show the
                # header alone".
                if not header and not bullets:
                    header = text

                validated_entries.append({
                    "source_index": index,
                    "source_text": source_list[index],
                    "tailored_text": text,
                    "header": header,
                    "bullets": bullets,
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
                if not isinstance(item, dict):
                    raise LLMSchemaValidationError()
                # source_index/tailored_text carry the grounded content and
                # stay mandatory. header/bullets are the layout split and are
                # tolerated as absent: a model that omits them yields a
                # plainer resume, which is far better than failing an
                # otherwise valid, fully-grounded generation over formatting.
                if not {"source_index", "tailored_text"} <= set(item):
                    raise LLMSchemaValidationError()
                if not set(item) <= {"source_index", "tailored_text", "header", "bullets"}:
                    raise LLMSchemaValidationError()
                index = item["source_index"]
                if not isinstance(index, int) or isinstance(index, bool):
                    raise LLMSchemaValidationError()
                if not isinstance(item["tailored_text"], str):
                    raise LLMSchemaValidationError()
                if "header" in item and not isinstance(item["header"], str):
                    raise LLMSchemaValidationError()
                if "bullets" in item:
                    if not isinstance(item["bullets"], list) or not all(
                        isinstance(bullet, str) for bullet in item["bullets"]
                    ):
                        raise LLMSchemaValidationError()

    @classmethod
    def _starts_a_sentence(cls, text, index):
        cursor = index - 1
        while cursor >= 0 and text[cursor].isspace():
            cursor -= 1
        return cursor < 0 or text[cursor] in cls._SENTENCE_BREAKS

    @classmethod
    def _significant_tokens(cls, text):
        """Tokens that look like facts (numbers, dates, proper nouns) and must be grounded.

        Words starting a sentence (e.g. following a period or bullet) and
        standard professional resume vocabulary are excluded unless they
        contain a digit or are an acronym.
        """
        significant = []
        for match in cls._TOKEN_RE.finditer(text):
            raw_token = match.group(0)
            token = raw_token.rstrip(".")
            if not token:
                continue
            has_digit = any(character.isdigit() for character in token)
            is_capitalized = token[0].isupper()
            lowered = token.lower()

            if has_digit:
                significant.append(token)
            elif is_capitalized and len(token) >= 2:
                # Exclude words that start a sentence (capitalized by English grammar)
                if cls._starts_a_sentence(text, match.start()) and not token.isupper():
                    continue
                # Exclude standard professional and grammatical words
                if lowered in cls._COMMON_RESUME_VOCABULARY and not token.isupper():
                    continue
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

    @classmethod
    def _is_grounded(cls, normalised, candidate_text):
        """Boundary-aware search: a fact must appear as a whole token, not merely
        as a substring of an unrelated number or word (e.g. '8' inside a phone
        number), while still tolerating adjacent punctuation like a sentence-
        ending period."""
        pattern = r"(?<![a-z0-9])" + re.escape(normalised) + r"(?![a-z0-9])"
        if re.search(pattern, candidate_text) is not None:
            return True

        # A numeric fact may be written with or without its magnitude or unit suffix
        # on either side ("1.2k" in the resume, "1.2" in the generated text,
        # "30s" in the resume, "30" or "30 seconds" in the generated text,
        # or vice versa). Match the numeric part and allow one optional
        # magnitude/percent/unit character right after it in the source, while
        # still refusing an unrelated longer number ("1.2" must not ground
        # "1.28", "12" must not ground "128").
        numeric = cls._NUMERIC_TOKEN_RE.match(normalised)
        if numeric:
            num_val = re.escape(numeric.group(1))
            loose = (
                r"(?<![a-z0-9])"
                + num_val
                + r"(?:[kmbx%]|s|ms|sec|seconds|min|mins|hr|hrs|h|d|yr|yrs|y|\+)?(?![a-z0-9.])"
            )
            return re.search(loose, candidate_text) is not None
        return False

    @classmethod
    def _candidate_text_blob(cls, candidate_source_data, allowed_missing_skills=None):
        groundable = {
            key: value
            for key, value in candidate_source_data.items()
            if key not in {"resume_id", "personal_information"}
        }
        if allowed_missing_skills:
            groundable["allowed_missing_skills"] = list(allowed_missing_skills)
            from resumes.services.job_gap_analysis_service import TECH_ALIASES
            extra_aliases = []
            for skill in allowed_missing_skills:
                norm = cls._normalise_text(skill)
                if norm in TECH_ALIASES:
                    extra_aliases.extend(TECH_ALIASES[norm])
            if extra_aliases:
                groundable["allowed_missing_skills_aliases"] = extra_aliases
        return cls._normalise_text(
            json.dumps(groundable, ensure_ascii=False, sort_keys=True)
        )

    @staticmethod
    def _normalise_text(value):
        text = (
            str(value)
            .replace("\u2013", " ")
            .replace("\u2014", " ")
            .replace("\u2022", " ")
            .replace("\xa0", " ")
        )
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.+#]+", " ", text.lower())).strip()
