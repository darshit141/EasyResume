import json
import logging
import re

from django.db import DatabaseError

from resumes.models import JobResumeCuration, Resume
from resumes.prompts import GAP_ANALYSIS_SCHEMA, build_gap_analysis_prompt
from resumes.services.llm_service import (
    LLMServiceError,
    LLMSchemaValidationError,
    get_llm_service,
)


logger = logging.getLogger(__name__)

MATCHED = "MATCHED"
PARTIALLY_MATCHED = "PARTIALLY_MATCHED"
MISSING = "MISSING"
NOT_VERIFIABLE = "NOT_VERIFIABLE"
MATCH_STATUSES = {MATCHED, PARTIALLY_MATCHED, MISSING, NOT_VERIFIABLE}

REQUIREMENT_BUCKETS = [
    ("required_skills", "required_skill"),
    ("preferred_skills", "preferred_skill"),
    ("technologies", "technology"),
    ("frameworks", "framework"),
    ("databases", "database"),
    ("cloud_technologies", "cloud_technology"),
    ("tools", "tool"),
    ("soft_skills", "soft_skill"),
    ("responsibilities", "responsibility"),
    ("experience_requirements", "experience_requirement"),
    ("education_requirements", "education_requirement"),
    ("certification_requirements", "certification_requirement"),
    ("important_keywords", "important_keyword"),
    ("ats_relevant_terms", "ats_relevant_term"),
    ("other_explicit_requirements", "other_explicit_requirement"),
]

EXACT_MATCH_CATEGORIES = {
    "required_skill",
    "preferred_skill",
    "technology",
    "framework",
    "database",
    "cloud_technology",
    "tool",
    "soft_skill",
    "certification_requirement",
    "education_requirement",
    "important_keyword",
    "ats_relevant_term",
}


class CandidateDataUnavailableError(LLMServiceError):
    code = "CANDIDATE_DATA_UNAVAILABLE"
    message = "No completed resume data is available for this user."


class JobRequirementsUnavailableError(LLMServiceError):
    code = "JOB_REQUIREMENTS_UNAVAILABLE"
    message = "Structured job requirements are not available for this curation."


class JobGapAnalysisService:
    """
    Builds verified candidate data and compares it to analyzed job requirements.
    """

    def __init__(self, llm_service=None):
        self.llm_service = llm_service or get_llm_service()

    def get_or_create_gap_analysis(self, user, curation_id, regenerate=False):
        curation = JobResumeCuration.objects.get(id=curation_id, user=user)
        if curation.gap_analysis_data and not regenerate:
            return curation

        if curation.status != "completed" or not curation.analysis_data:
            raise JobRequirementsUnavailableError()

        candidate_data = self.build_candidate_data(user)
        job_requirements = self.build_job_requirements(curation.analysis_data)
        prompt = build_gap_analysis_prompt(
            json.dumps(candidate_data, sort_keys=True),
            json.dumps(job_requirements, sort_keys=True),
        )

        logger.info(
            "Candidate matching started user_id=%s curation_id=%s regenerate=%s",
            user.id,
            curation_id,
            regenerate,
        )
        try:
            raw_analysis = self.llm_service.generate_structured(
                prompt,
                GAP_ANALYSIS_SCHEMA,
            )
            analysis = self.validate_gap_analysis(
                raw_analysis,
                candidate_data,
                job_requirements,
            )
            curation.candidate_data_snapshot = candidate_data
            curation.gap_analysis_data = analysis
            curation.gap_analysis_error_code = ""
            curation.save(
                update_fields=[
                    "candidate_data_snapshot",
                    "gap_analysis_data",
                    "gap_analysis_error_code",
                    "updated_at",
                ]
            )
            logger.info(
                "Candidate matching completed user_id=%s curation_id=%s "
                "match_percentage=%s",
                user.id,
                curation_id,
                analysis.get("overall_match_percentage"),
            )
            return curation
        except LLMServiceError as exc:
            logger.warning(
                "Candidate matching failed user_id=%s curation_id=%s code=%s",
                user.id,
                curation_id,
                exc.code,
            )
            self._mark_failed(curation, exc.code)
            raise
        except DatabaseError as exc:
            logger.exception("Failed to save job gap analysis.")
            raise

    @staticmethod
    def build_candidate_data(user):
        resume = Resume.objects.filter(
            user=user,
            status="completed",
        ).order_by("-updated_at", "-id").first()
        if not resume or not resume.parsed_data:
            raise CandidateDataUnavailableError()

        return {
            "source": "latest_completed_resume.parsed_data",
            "resume_id": resume.id,
            "original_filename": resume.original_filename,
            "parsed_data": resume.parsed_data,
        }

    @staticmethod
    def build_job_requirements(analysis_data):
        requirements = []
        for field, category in REQUIREMENT_BUCKETS:
            for item in analysis_data.get(field, []):
                if isinstance(item, str) and item.strip():
                    requirements.append({
                        "requirement": item.strip(),
                        "category": category,
                    })

        seniority = analysis_data.get("seniority_level")
        if isinstance(seniority, str) and seniority.strip():
            requirements.append({
                "requirement": seniority.strip(),
                "category": "seniority_level",
            })

        if not requirements:
            raise JobRequirementsUnavailableError()

        return requirements

    def validate_gap_analysis(self, analysis, candidate_data, job_requirements):
        if not isinstance(analysis, dict):
            raise LLMSchemaValidationError()

        required_keys = set(GAP_ANALYSIS_SCHEMA["required"])
        if set(analysis) != required_keys:
            raise LLMSchemaValidationError()

        candidate_terms = self._candidate_terms(candidate_data)
        candidate_text = self._normalise_text(json.dumps(candidate_data, sort_keys=True))
        requirements_by_key = {
            self._requirement_key(item["requirement"], item["category"]): item
            for item in job_requirements
        }

        validated_items = []
        for bucket in [
            "matched_requirements",
            "partially_matched_requirements",
            "missing_requirements",
            "not_verifiable_requirements",
        ]:
            if not isinstance(analysis.get(bucket), list):
                raise LLMSchemaValidationError()
            for item in analysis[bucket]:
                validated_items.append(
                    self._validate_requirement_item(
                        item,
                        requirements_by_key,
                        candidate_terms,
                        candidate_text,
                    )
                )

        seen_keys = {
            self._requirement_key(item["requirement"], item["category"])
            for item in validated_items
        }
        for requirement in job_requirements:
            key = self._requirement_key(
                requirement["requirement"],
                requirement["category"],
            )
            if key not in seen_keys:
                validated_items.append({
                    **requirement,
                    "match_status": MISSING,
                    "candidate_evidence": [],
                    "confidence": 1,
                    "reasoning": "No candidate evidence was provided for this requirement.",
                    "recommendation": "Acquire this requirement or add it only if it is genuinely part of your experience.",
                })

        result = {
            **analysis,
            "matched_requirements": [],
            "partially_matched_requirements": [],
            "missing_requirements": [],
            "not_verifiable_requirements": [],
        }
        for item in validated_items:
            if item["match_status"] == MATCHED:
                result["matched_requirements"].append(item)
            elif item["match_status"] == PARTIALLY_MATCHED:
                result["partially_matched_requirements"].append(item)
            elif item["match_status"] == NOT_VERIFIABLE:
                result["not_verifiable_requirements"].append(item)
            else:
                result["missing_requirements"].append(item)

        total = len(validated_items)
        score = sum(
            1 if item["match_status"] == MATCHED else 0.5 if item["match_status"] == PARTIALLY_MATCHED else 0
            for item in validated_items
        )
        result["overall_match_percentage"] = round((score / total) * 100, 2) if total else 0
        self._validate_summary_fields(result)
        return result

    def _validate_requirement_item(
        self,
        item,
        requirements_by_key,
        candidate_terms,
        candidate_text,
    ):
        if not isinstance(item, dict):
            raise LLMSchemaValidationError()

        expected = {
            "requirement", "category", "match_status", "candidate_evidence",
            "confidence", "reasoning", "recommendation",
        }
        if set(item) != expected:
            raise LLMSchemaValidationError()
        if item["match_status"] not in MATCH_STATUSES:
            raise LLMSchemaValidationError()
        if not isinstance(item["candidate_evidence"], list):
            raise LLMSchemaValidationError()
        if not isinstance(item["confidence"], (int, float)):
            raise LLMSchemaValidationError()

        requirement_key = self._requirement_key(item["requirement"], item["category"])
        if requirement_key not in requirements_by_key:
            raise LLMSchemaValidationError()

        evidence_is_supported = self._evidence_is_supported(
            item["candidate_evidence"],
            candidate_text,
        )
        requirement_norm = self._normalise_text(item["requirement"])

        if item["category"] == "experience_requirement":
            return self._enforce_experience_match(item, candidate_text)

        if item["match_status"] in {MATCHED, PARTIALLY_MATCHED} and not evidence_is_supported:
            return self._downgrade_to_missing(item, "Candidate evidence was not found in verified resume data.")

        if item["category"] in EXACT_MATCH_CATEGORIES:
            has_exact_term = requirement_norm in candidate_terms
            if item["match_status"] == MATCHED and not has_exact_term:
                return self._downgrade_to_missing(
                    item,
                    "This requirement was not explicitly present in verified candidate data.",
                )
            if item["match_status"] == PARTIALLY_MATCHED and not self._has_partial_overlap(requirement_norm, candidate_terms):
                return self._downgrade_to_missing(
                    item,
                    "No supported partial candidate evidence was found.",
                )

        return {
            **item,
            "confidence": max(0, min(float(item["confidence"]), 1)),
        }

    def _enforce_experience_match(self, item, candidate_text):
        required_years = self._max_years(item["requirement"])
        candidate_years = self._max_years(candidate_text)
        if required_years is None:
            return item
        if candidate_years is None:
            return self._downgrade_to_missing(
                item,
                "Candidate years of experience are not present in verified resume data.",
            )
        if candidate_years >= required_years:
            return {**item, "match_status": MATCHED, "confidence": max(0, min(float(item["confidence"]), 1))}
        return {
            **item,
            "match_status": PARTIALLY_MATCHED,
            "confidence": max(0, min(float(item["confidence"]), 1)),
            "reasoning": f"Candidate data indicates {candidate_years:g} years against a {required_years:g}-year requirement.",
        }

    @staticmethod
    def _downgrade_to_missing(item, reason):
        return {
            **item,
            "match_status": MISSING,
            "candidate_evidence": [],
            "confidence": 1,
            "reasoning": reason,
            "recommendation": "Do not add this to the resume unless it is genuinely part of the candidate's experience.",
        }

    @staticmethod
    def _validate_summary_fields(result):
        for field in [
            "strengths",
            "weaknesses_gaps",
            "important_missing_information",
            "interview_readiness_observations",
        ]:
            if not isinstance(result.get(field), list):
                raise LLMSchemaValidationError()
        recommendations = result.get("recommendations_for_profile_improvement")
        if not isinstance(recommendations, list):
            raise LLMSchemaValidationError()
        for item in recommendations:
            if not isinstance(item, dict):
                raise LLMSchemaValidationError()
            if set(item) != {"recommendation", "recommendation_type"}:
                raise LLMSchemaValidationError()
            if item["recommendation_type"] not in {"MISSING_PROFILE_INFORMATION", "SKILL_TO_ACQUIRE"}:
                raise LLMSchemaValidationError()
        coverage = result.get("ats_keyword_coverage")
        if not isinstance(coverage, dict):
            raise LLMSchemaValidationError()
        if set(coverage) != {"covered_keywords", "missing_keywords", "coverage_percentage"}:
            raise LLMSchemaValidationError()

    @staticmethod
    def _candidate_terms(candidate_data):
        terms = set()

        def collect(value):
            if isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)
            elif isinstance(value, str):
                normalised = JobGapAnalysisService._normalise_text(value)
                if normalised:
                    terms.add(normalised)

        collect(candidate_data.get("parsed_data", {}))
        return terms

    @staticmethod
    def _evidence_is_supported(evidence, candidate_text):
        if not evidence:
            return False
        for item in evidence:
            if not isinstance(item, str):
                raise LLMSchemaValidationError()
            normalised = JobGapAnalysisService._normalise_text(item)
            if normalised and normalised in candidate_text:
                return True
        return False

    @staticmethod
    def _has_partial_overlap(requirement, candidate_terms):
        requirement_tokens = set(requirement.split())
        if not requirement_tokens:
            return False
        for term in candidate_terms:
            term_tokens = set(term.split())
            if requirement_tokens & term_tokens:
                return True
        return False

    @staticmethod
    def _max_years(text):
        matches = re.findall(r"(\d+(?:\.\d+)?)\+?\s*(?:years|year|yrs|yr)", text, flags=re.IGNORECASE)
        if not matches:
            return None
        return max(float(match) for match in matches)

    @staticmethod
    def _normalise_text(value):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.+#]+", " ", str(value).lower())).strip()

    @staticmethod
    def _requirement_key(requirement, category):
        return f"{JobGapAnalysisService._normalise_text(category)}::{JobGapAnalysisService._normalise_text(requirement)}"

    @staticmethod
    def _mark_failed(curation, error_code):
        try:
            curation.gap_analysis_error_code = error_code
            curation.save(update_fields=["gap_analysis_error_code", "updated_at"])
        except DatabaseError:
            logger.exception("Failed to persist job gap analysis failure state.")
