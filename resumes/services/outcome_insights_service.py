"""What actually distinguishes applications that got interviews.

This is the payoff of collecting outcome data: every competitor reports a
score, nobody reports whether the score predicted anything. Once enough
outcomes exist, this compares the applications that succeeded against the
ones that didn't and reports the difference.

Two rules make it honest rather than another invented metric:

1. It refuses to report below a minimum sample. A "100% interview rate"
   across three applications is noise, and publishing it would be exactly
   the unfalsifiable-score problem this product exists to avoid.
2. It reports differences with their sample sizes attached, and never
   claims causation. "Applications above 70% match were interviewed more
   often" is a correlation - candidates who tailor carefully also tend to
   apply more selectively, and this data cannot separate the two.

A finding of "no meaningful difference" is a real, publishable result here.
If match percentage turns out not to predict interviews, that is worth
knowing, and burying it would make every other number untrustworthy.
"""

from resumes.models import JobResumeCuration

# Below this many resolved outcomes, no comparison is reported at all.
# Chosen as the point where a difference between two groups starts to mean
# something rather than reflecting who happened to reply.
MIN_RESOLVED_OUTCOMES = 30

# Both groups need their own floor: 40 rejections and 2 interviews cannot
# support a claim about what interviews have in common.
MIN_PER_GROUP = 5

# How much better a group has to do before it is called a difference rather
# than noise, in percentage points.
MEANINGFUL_DIFFERENCE_POINTS = 5.0


def _mean(values):
    return round(sum(values) / len(values), 2) if values else None


def _match_percentage(curation):
    gap = curation.gap_analysis_data or {}
    value = gap.get("overall_match_percentage")
    return float(value) if isinstance(value, (int, float)) else None


def _ats_coverage(curation):
    gap = curation.gap_analysis_data or {}
    coverage = gap.get("ats_keyword_coverage") or {}
    value = coverage.get("coverage_percentage")
    return float(value) if isinstance(value, (int, float)) else None


def _compare(label, succeeded_values, failed_values):
    """Compare one signal between the two outcome groups."""
    succeeded_mean = _mean(succeeded_values)
    failed_mean = _mean(failed_values)

    if succeeded_mean is None or failed_mean is None:
        return None

    difference = round(succeeded_mean - failed_mean, 2)
    return {
        "signal": label,
        "interviewed_average": succeeded_mean,
        "rejected_average": failed_mean,
        "difference": difference,
        # Explicitly says when the answer is "this doesn't appear to
        # matter", rather than dressing a rounding error up as a finding.
        "is_meaningful": abs(difference) >= MEANINGFUL_DIFFERENCE_POINTS,
        "sample_interviewed": len(succeeded_values),
        "sample_rejected": len(failed_values),
    }


def outcome_insights():
    """Compare succeeded vs. rejected applications across the signals this
    product actually controls."""

    resolved_statuses = (
        JobResumeCuration.APPLICATION_STATUS_INTERVIEWING,
        JobResumeCuration.APPLICATION_STATUS_OFFER,
        JobResumeCuration.APPLICATION_STATUS_REJECTED,
    )

    curations = list(
        JobResumeCuration.objects.filter(
            application_status__in=resolved_statuses
        ).only("application_status", "gap_analysis_data", "tailored_resume_data")
    )

    succeeded = [
        c for c in curations
        if c.application_status in (
            JobResumeCuration.APPLICATION_STATUS_INTERVIEWING,
            JobResumeCuration.APPLICATION_STATUS_OFFER,
        )
    ]
    rejected = [
        c for c in curations
        if c.application_status == JobResumeCuration.APPLICATION_STATUS_REJECTED
    ]

    total = len(curations)

    if total < MIN_RESOLVED_OUTCOMES or len(succeeded) < MIN_PER_GROUP or len(rejected) < MIN_PER_GROUP:
        # Deliberately returns nothing to display rather than a weak
        # finding. Reporting a comparison from this little data would be
        # indistinguishable from making it up.
        return {
            "status": "insufficient_data",
            "resolved_outcomes": total,
            "interviewed": len(succeeded),
            "rejected": len(rejected),
            "minimum_required": MIN_RESOLVED_OUTCOMES,
            "minimum_per_group": MIN_PER_GROUP,
            "outcomes_still_needed": max(0, MIN_RESOLVED_OUTCOMES - total),
            "findings": [],
            "note": (
                "Not enough resolved outcomes yet to say anything honest about "
                "what distinguishes a successful application. Nothing is "
                "reported until there is enough data for the comparison to "
                "mean something."
            ),
        }

    findings = []

    for label, extractor in (
        ("Job match percentage", _match_percentage),
        ("ATS keyword coverage", _ats_coverage),
    ):
        succeeded_values = [v for v in (extractor(c) for c in succeeded) if v is not None]
        rejected_values = [v for v in (extractor(c) for c in rejected) if v is not None]

        if len(succeeded_values) < MIN_PER_GROUP or len(rejected_values) < MIN_PER_GROUP:
            continue

        comparison = _compare(label, succeeded_values, rejected_values)
        if comparison:
            findings.append(comparison)

    # Whether generating a tailored resume at all made a difference - the
    # most direct question that can be asked of this product.
    tailored_success = sum(1 for c in succeeded if c.tailored_resume_data)
    tailored_rejected = sum(1 for c in rejected if c.tailored_resume_data)
    findings.append(
        {
            "signal": "Used a tailored resume",
            "interviewed_average": round(tailored_success / len(succeeded) * 100, 2),
            "rejected_average": round(tailored_rejected / len(rejected) * 100, 2),
            "difference": round(
                (tailored_success / len(succeeded) - tailored_rejected / len(rejected)) * 100, 2
            ),
            "is_meaningful": abs(
                (tailored_success / len(succeeded) - tailored_rejected / len(rejected)) * 100
            ) >= MEANINGFUL_DIFFERENCE_POINTS,
            "sample_interviewed": len(succeeded),
            "sample_rejected": len(rejected),
        }
    )

    return {
        "status": "ok",
        "resolved_outcomes": total,
        "interviewed": len(succeeded),
        "rejected": len(rejected),
        "findings": findings,
        "note": (
            "Differences between applications that reached interview and those "
            "rejected, from real recorded outcomes. These are correlations, not "
            "causes: candidates who tailor carefully also tend to apply more "
            "selectively, and this data cannot separate the two."
        ),
    }
