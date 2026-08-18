"""Deterministic, non-AI comparison between a tailored resume version and
the one immediately before it.

This exists to replace an "AI explains why this version is better" style
feature honestly: rather than asking an LLM to invent flattering prose
about a resume it just generated, this computes a factual diff straight
from data already persisted for both versions — no LLM call, nothing to
hallucinate.
"""

from resumes.models import JobResumeCurationVersion


class TailoredResumeVersionComparisonService:
    @staticmethod
    def compare_with_previous(curation, current_version):
        """Returns a comparison dict, or None if there is no earlier
        version to compare against (e.g. this is version 1)."""
        if current_version is None or current_version.version <= 1:
            return None

        previous_version = JobResumeCurationVersion.objects.filter(
            curation=curation,
            version=current_version.version - 1,
        ).first()
        if previous_version is None:
            return None

        current_snapshot = current_version.gap_analysis_snapshot or {}
        previous_snapshot = previous_version.gap_analysis_snapshot or {}
        current_matched = {
            item["requirement"] for item in current_snapshot.get("matched_requirements", [])
        }
        previous_matched = {
            item["requirement"] for item in previous_snapshot.get("matched_requirements", [])
        }

        current_skills = set((current_version.tailored_resume_data or {}).get("skills", []))
        previous_skills = set((previous_version.tailored_resume_data or {}).get("skills", []))

        match_percentage_delta = None
        if current_version.match_percentage is not None and previous_version.match_percentage is not None:
            match_percentage_delta = float(current_version.match_percentage) - float(
                previous_version.match_percentage
            )

        return {
            "previous_version": previous_version.version,
            "previous_match_percentage": previous_version.match_percentage,
            "current_match_percentage": current_version.match_percentage,
            "match_percentage_delta": match_percentage_delta,
            "newly_matched_requirements": sorted(current_matched - previous_matched),
            "no_longer_matched_requirements": sorted(previous_matched - current_matched),
            "newly_emphasized_skills": sorted(current_skills - previous_skills),
        }
