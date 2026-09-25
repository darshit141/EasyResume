"""Which of the user's tracked jobs an email is about.

A reply to a Direct Apply email needs no guessing: its address names the
application. Forwarded mail does. Recruiter mail arrives from the company's
own domain, from an applicant-tracking system ("Acme via Greenhouse"), or
from a recruiter's personal account, so no single signal is reliable; each
job is scored on the company name, the sender's domain and the job title,
and only a clear winner is accepted. Anything closer than that is left for
the AI classifier to decide among the top candidates, or for the user.
"""

import re
from datetime import timedelta

from django.utils import timezone

from resumes.models import JobResumeCuration

# Mail from these says nothing about the employer through its domain.
_PERSONAL_DOMAINS = {
    "gmail", "googlemail", "yahoo", "ymail", "outlook", "hotmail", "live", "msn",
    "icloud", "me", "aol", "proton", "protonmail", "rediffmail", "zoho", "gmx", "mail",
}
_ATS_DOMAINS = {
    "greenhouse", "greenhouse-mail", "lever", "hire", "myworkday", "workday", "myworkdayjobs",
    "smartrecruiters", "ashbyhq", "ashby", "icims", "taleo", "successfactors", "jobvite",
    "bamboohr", "breezy", "recruitee", "workable", "teamtailor", "personio", "naukri",
    "linkedin", "indeed", "glassdoor", "wellfound", "instahyre", "hirist", "cutshort",
    "zohorecruit", "freshteam", "keka", "darwinbox", "oraclecloud", "brassring",
}
# Second-level suffixes that are not the organisation's name (acme.co.in).
_PUBLIC_SUFFIX_PARTS = {"co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc"}
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "gmbh", "ag", "sa", "bv",
    "pvt", "private", "corp", "corporation", "co", "company", "the",
}
_CANDIDATE_WINDOW = timedelta(days=365)
MAX_CANDIDATES = 15
ACCEPT_SCORE = 3.0
ACCEPT_MARGIN = 1.5


def _compact(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def company_key(name):
    tokens = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t and t not in _LEGAL_SUFFIXES]
    return "".join(tokens)


def domain_label(domain):
    """'acme' for careers.acme.co.in; '' for personal and ATS domains."""
    parts = [p for p in (domain or "").lower().split(".") if p]
    while len(parts) > 1 and (parts[-1] in _PUBLIC_SUFFIX_PARTS or len(parts[-1]) == 2):
        parts = parts[:-1]
    label = parts[-1] if parts else ""
    if label in _PERSONAL_DOMAINS or label in _ATS_DOMAINS:
        return ""
    return label


def company_for(curation):
    if curation.company_name:
        return curation.company_name
    for application in curation.applications.all():
        if application.company_name:
            return application.company_name
    match = re.search(r"extension at (.+?)\.$", curation.application_notes or "")
    return match.group(1) if match else ""


def candidates_for(user):
    since = timezone.now() - _CANDIDATE_WINDOW
    return list(
        JobResumeCuration.objects.filter(user=user, created_at__gte=since)
        .prefetch_related("applications")
        .order_by("-updated_at")[:200]
    )


def _score(curation, parsed):
    score = 0.0
    company = company_key(company_for(curation))
    sender_label = domain_label(parsed.domain)
    header = _compact(f"{parsed.from_name} {parsed.subject}")
    body = _compact(parsed.text_body[:5000])

    if company and len(company) >= 3:
        if sender_label and (sender_label == company or company.startswith(sender_label) or sender_label.startswith(company)):
            score += 3
        if company in header:
            score += 3
        elif company in body:
            score += 1.5

    job_label = ""
    if curation.job_url:
        host = re.sub(r"^https?://", "", curation.job_url).split("/")[0].split(":")[0]
        job_label = domain_label(host)
    if job_label and sender_label and job_label == sender_label:
        score += 2

    title = _compact(curation.job_title)
    if len(title) >= 6:
        if title in _compact(parsed.subject):
            score += 2
        elif title in body:
            score += 1
    return score


def match(parsed, user, application=None):
    """(curation or None, method, shortlist). The shortlist holds the most
    likely jobs, for the classifier to choose among when there's no clear
    winner."""
    if application is not None:
        return application.curation, "reply_address", []

    candidates = candidates_for(user)
    if not candidates:
        return None, "", []

    scored = sorted(((_score(c, parsed), c) for c in candidates), key=lambda pair: -pair[0])
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= ACCEPT_SCORE and best_score - runner_up >= ACCEPT_MARGIN:
        return best, "signals", []

    # Most plausible first, then most recently active.
    shortlist = [c for _, c in scored[:MAX_CANDIDATES]]
    return None, "", shortlist
