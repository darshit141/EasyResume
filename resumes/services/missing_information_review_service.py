"""Deterministic, non-AI transformation of gap analysis data into an
actionable "what's missing and where" review for the frontend.

The wording used for every missing requirement is a fixed backend template,
never AI-generated free text, so the safety rule below is a guarantee rather
than a best effort: the candidate is never told to add a skill they may not
have. They are told what is missing from their *profile* and left to decide,
for themselves, whether that is a recording gap or a genuine skill gap.
"""

from resumes.services.job_gap_analysis_service import JobGapAnalysisService


CATEGORY_PROFILE_SECTIONS = {
    "required_skill": "skills",
    "preferred_skill": "skills",
    "technology": "skills",
    "framework": "skills",
    "database": "skills",
    "cloud_technology": "skills",
    "tool": "skills",
    "soft_skill": "skills",
    "certification_requirement": "certifications",
    "education_requirement": "education",
    "experience_requirement": "experience",
    "responsibility": "experience",
    "important_keyword": "summary",
    "ats_relevant_term": "summary",
    "seniority_level": "summary",
    "other_explicit_requirement": "summary",
}

DEFAULT_PROFILE_SECTION = "profile"


class MissingInformationReviewService:
    """Builds the frontend-facing missing-information review for a curation."""

    def __init__(self, gap_analysis_service=None):
        self.gap_analysis_service = gap_analysis_service or JobGapAnalysisService()

    def build_review(self, user, curation_id, regenerate=False):
        curation = self.gap_analysis_service.get_or_create_gap_analysis(
            user,
            curation_id,
            regenerate=regenerate,
        )
        gap_data = curation.gap_analysis_data

        missing_requirements = [
            self._missing_requirement(item)
            for item in gap_data["missing_requirements"]
        ]
        suggested_profile_sections = sorted({
            item["profile_section"] for item in missing_requirements
        })

        return {
            "id": curation.id,
            "job_title": curation.job_title,
            "overall_match_percentage": gap_data["overall_match_percentage"],
            "strong_matches": [
                self._requirement_summary(item)
                for item in gap_data["matched_requirements"]
            ],
            "partial_matches": [
                self._requirement_summary(item)
                for item in gap_data["partially_matched_requirements"]
            ],
            "missing_requirements": missing_requirements,
            "missing_candidate_information": gap_data["important_missing_information"],
            "recommendations": gap_data["recommendations_for_profile_improvement"],
            "ats_keyword_gaps": gap_data["ats_keyword_coverage"],
            "suggested_profile_sections": suggested_profile_sections,
        }

    @staticmethod
    def _requirement_summary(item):
        return {
            "requirement": item["requirement"],
            "category": item["category"],
            "match_status": item["match_status"],
            "reasoning": item["reasoning"],
        }

    @classmethod
    def _missing_requirement(cls, item):
        requirement = item["requirement"]
        return {
            "requirement": requirement,
            "category": item["category"],
            "profile_section": CATEGORY_PROFILE_SECTIONS.get(
                item["category"],
                DEFAULT_PROFILE_SECTION,
            ),
            "message": (
                f"{requirement} is listed as a job requirement but is not "
                "present in your current profile."
            ),
            "guidance": (
                f"If you genuinely have {requirement} experience that is not "
                "currently recorded, add it to your profile. Otherwise, "
                f"consider {requirement} a current skill gap."
            ),
        }
