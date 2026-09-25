"""What a recruiter email means for the application it belongs to.

The AI reads the email and, when matching was inconclusive, picks which of
the user's shortlisted jobs it is about. Its answer is validated rather than
trusted: an unknown category or a job id that wasn't offered is discarded.
When the model is unavailable, keyword rules still give a category, with low
confidence, so the email is filed and a status is suggested but never
changed automatically on a guess.
"""

import logging
import re

from inbox.models import InboundEmail
from resumes.prompts import UNTRUSTED_INPUT_RULE
from resumes.services.llm_service import LLMServiceError, get_llm_service

logger = logging.getLogger(__name__)

CATEGORIES = [value for value, _ in InboundEmail.CATEGORY_CHOICES]

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "confidence": {"type": "number"},
        "job_id": {"type": "integer"},
        "summary": {"type": "string"},
        "interview_at": {"type": "string"},
        "meeting_link": {"type": "string"},
    },
    "required": ["category", "confidence", "job_id", "summary", "interview_at", "meeting_link"],
    "additionalProperties": False,
}

_MAX_BODY_CHARS = 6000


def _prompt(parsed, shortlist):
    jobs = "\n".join(
        f"- id {c.id}: {c.job_title}" + (f" at {c.company_name}" if c.company_name else "")
        for c in shortlist
    ) or "(none)"
    return f"""You sort emails a job seeker receives from employers and recruiters.

Decide:
- category, exactly one of:
  interview       an invitation to interview or to book a call / pick a time
  assessment      a test, coding challenge, take-home or online assessment
  offer           a job offer
  rejection       the application will not go further
  acknowledgement an automatic "we received your application"
  info_request    they need documents, details or answers before continuing
  other           any other message about an application
  not_job_related newsletters, job alerts, marketing, or unrelated mail
- confidence: 0.0 to 1.0, how certain the category is.
- job_id: the id from CANDIDATE JOBS this email is about, or -1 if none clearly
  matches. Never guess between two plausible jobs; use -1.
- summary: one plain sentence (max 25 words) of what the sender wants or said.
- interview_at: the proposed interview date and time as written in the email,
  or "" if none is given.
- meeting_link: a video-call or scheduling URL present in the email, or "".

Job alerts and "jobs you may like" digests are not_job_related even though they
mention jobs. Return only JSON matching the schema.

CANDIDATE JOBS:
{jobs}

{UNTRUSTED_INPUT_RULE}

EMAIL:
---
From: {parsed.from_name} <{parsed.from_address}>
Subject: {parsed.subject}

{parsed.text_body[:_MAX_BODY_CHARS]}
---"""


_KEYWORD_RULES = (
    (InboundEmail.CATEGORY_OFFER, r"\b(pleased|delighted|happy) to (extend|offer)|offer letter|job offer\b"),
    (InboundEmail.CATEGORY_REJECTION, r"\bunfortunately\b|not (be )?moving forward|decided to (pursue|proceed with) other|"
                                      r"not been successful|will not be progressing|regret to inform|position has been filled"),
    (InboundEmail.CATEGORY_INTERVIEW, r"\binterview\b|schedule (a|an|the) (call|chat|meeting)|your availability|calendly\.com|book a (time|slot)"),
    (InboundEmail.CATEGORY_ASSESSMENT, r"\bassessment\b|coding (challenge|test)|take[- ]home|hackerrank|codility|online test"),
    (InboundEmail.CATEGORY_ACKNOWLEDGEMENT, r"(received|receipt of) your application|thank you for (applying|your application|your interest)"),
)
_URL = re.compile(r"https://[^\s<>\"')]+")
_MEETING_HOSTS = re.compile(r"(zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|calendly\.com|webex\.com)", re.I)


def classify_with_keywords(parsed):
    text = f"{parsed.subject}\n{parsed.text_body[:_MAX_BODY_CHARS]}".lower()
    category = InboundEmail.CATEGORY_OTHER
    for name, pattern in _KEYWORD_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            category = name
            break
    link = next((u for u in _URL.findall(parsed.text_body or "") if _MEETING_HOSTS.search(u)), "")
    return {
        "category": category,
        "confidence": 0.4,
        "job_id": None,
        "summary": "",
        "interview_at": "",
        "meeting_link": link[:500],
        "source": "keywords",
    }


def classify(parsed, shortlist, llm_service=None):
    """Returns dict(category, confidence, job_id, summary, interview_at,
    meeting_link, source)."""
    try:
        service = llm_service or get_llm_service()
        answer = service.generate_structured(_prompt(parsed, shortlist), CLASSIFICATION_SCHEMA)
    except LLMServiceError as exc:
        logger.warning("Email classification fell back to keywords: %s", exc)
        return classify_with_keywords(parsed)
    except Exception:  # noqa: BLE001 - a model failure must not lose the email
        logger.exception("Email classification failed; falling back to keywords.")
        return classify_with_keywords(parsed)

    if not isinstance(answer, dict) or answer.get("category") not in CATEGORIES:
        return classify_with_keywords(parsed)

    try:
        confidence = min(max(float(answer.get("confidence", 0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    offered = {c.id for c in shortlist}
    job_id = answer.get("job_id")
    job_id = job_id if isinstance(job_id, int) and job_id in offered else None

    link = str(answer.get("meeting_link") or "").strip()
    # Only a link that is really in the email: the model must not be able to
    # put a URL of its own in front of the user.
    if not link.startswith("https://") or link not in (parsed.text_body or ""):
        link = ""

    return {
        "category": answer["category"],
        "confidence": confidence,
        "job_id": job_id,
        "summary": re.sub(r"\s+", " ", str(answer.get("summary") or "")).strip()[:300],
        "interview_at": str(answer.get("interview_at") or "").strip()[:60],
        "meeting_link": link[:500],
        "source": "ai",
    }
