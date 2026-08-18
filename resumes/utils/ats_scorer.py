import re

# A resume entry "counts" as quantified if it contains a number a recruiter
# or ATS keyword-scan would treat as an impact metric: a percentage, a
# currency amount, or a bare count/multiplier (e.g. "50+ clients",
# "3 engineers", "2x faster"). Plain years ("2019-2022") are intentionally
# excluded so a normal date range doesn't get credited as a metric.
_METRIC_RE = re.compile(
    r"\d[\d,]*\s*%|"                     # 35%, 12.5%
    r"[$₹€£]\s*\d[\d,]*(?:\.\d+)?[kKmMbB]?|"  # $50k, ₹2,00,000
    r"\b\d[\d,]*\+?\s*(?:x|X)\b|"        # 2x, 10X
    r"\b\d[\d,]*\+"                      # 500+, 50+
)

# A non-exhaustive but broad set of strong resume action verbs. Matched
# case-insensitively against the first word of an entry only — this is a
# heuristic for "does this read like an achievement" vs. a flat job
# description, not a grammar checker.
_ACTION_VERBS = {
    "led", "built", "designed", "developed", "created", "implemented",
    "launched", "managed", "architected", "engineered", "optimized",
    "improved", "increased", "reduced", "delivered", "drove", "scaled",
    "automated", "streamlined", "spearheaded", "established", "founded",
    "coordinated", "directed", "executed", "generated", "achieved",
    "accelerated", "resolved", "mentored", "trained", "negotiated",
    "deployed", "migrated", "integrated", "authored", "researched",
    "analyzed", "presented", "collaborated", "contributed", "maintained",
}


class ATSScorer:
    """
    Deterministic, non-AI resume scoring against ATS-relevant heuristics —
    section completeness plus the specific traits real applicant-tracking
    systems and recruiter screens actually reward: quantified achievements
    and action-oriented phrasing, not just "does this section exist".

    Nothing here is AI-generated or invented per resume; every point comes
    from a fixed, reproducible rule so the same resume always scores the
    same way.
    """

    MAX_SCORE = 100

    @classmethod
    def calculate_score(cls, parsed_data):
        summary = (parsed_data.get("summary") or "").strip()
        skills = parsed_data.get("skills") or []
        experience = parsed_data.get("experience") or []
        education = parsed_data.get("education") or []
        projects = parsed_data.get("projects") or []
        certifications = parsed_data.get("certifications") or []
        achievements = parsed_data.get("achievements") or []

        score = 0
        score += cls._contact_score(parsed_data)
        score += cls._summary_score(summary)
        score += cls._skills_score(skills)
        score += cls._experience_score(experience)
        score += 10 if education else 0
        score += 10 if projects else 0
        score += 5 if certifications else 0
        score += 5 if achievements else 0
        score += cls._completeness_bonus(summary, skills, experience, education)

        return min(score, cls.MAX_SCORE)

    @staticmethod
    def _contact_score(parsed_data):
        email = parsed_data.get("email") or parsed_data.get("personal_information", {}).get("email")
        phone = parsed_data.get("phone") or parsed_data.get("personal_information", {}).get("phone")
        return (5 if email else 0) + (5 if phone else 0)

    @staticmethod
    def _summary_score(summary):
        # A one-word placeholder shouldn't score the same as an actual
        # professional summary an ATS can extract keywords from.
        return 10 if len(summary) >= 40 else (5 if summary else 0)

    @staticmethod
    def _skills_score(skills):
        # Rewards breadth up to a reasonable point (8 skills), rather than
        # an all-or-nothing check — a resume with one skill and a resume
        # with fifteen skills clearly aren't equally ATS-ready.
        if not skills:
            return 0
        return round(min(len(skills), 8) / 8 * 15)

    @classmethod
    def _experience_score(cls, experience):
        if not experience:
            return 0

        entries = [str(entry) for entry in experience if str(entry).strip()]
        if not entries:
            return 0

        quantified_fraction = sum(1 for entry in entries if _METRIC_RE.search(entry)) / len(entries)
        action_fraction = sum(1 for entry in entries if cls._starts_with_action_verb(entry)) / len(entries)

        # 10 for having real experience content, up to 10 more scaled by
        # how many entries include a quantified result, up to 5 more scaled
        # by how many open with a strong action verb.
        return 10 + round(quantified_fraction * 10) + round(action_fraction * 5)

    @staticmethod
    def _starts_with_action_verb(entry):
        match = re.match(r"[A-Za-z]+", entry.strip())
        return bool(match) and match.group(0).lower() in _ACTION_VERBS

    @staticmethod
    def _completeness_bonus(summary, skills, experience, education):
        # A resume that has all four core sections filled in is more
        # ATS-ready than the sum of its parts — most parsers key off
        # exactly these four to build a candidate's core profile.
        return 10 if (summary and skills and experience and education) else 0
