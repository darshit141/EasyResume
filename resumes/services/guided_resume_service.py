"""Orchestrates a GuidedResumeSession: collect answers, draft sections with
AI, assemble a parsed_data document, and finalize it into a real Resume."""

from django.db import transaction

from resumes.guided_questions import DRAFTED_SECTIONS, section_by_id
from resumes.models import GuidedResumeSession, Resume, ResumeVersion
from resumes.resume_content import normalize_entry_list, normalize_parsed_data
from resumes.services.guided_resume_ai_service import GuidedResumeAIService
from resumes.utils.ats_scorer import ATSScorer


class GuidedResumeService:
    def __init__(self, provider=None):
        self.provider = provider

    # -- session lifecycle --------------------------------------------------

    @staticmethod
    def get_or_start(user):
        session = (
            GuidedResumeSession.objects.filter(user=user, status=GuidedResumeSession.STATUS_IN_PROGRESS)
            .order_by("-updated_at")
            .first()
        )
        if session is None:
            session = GuidedResumeSession.objects.create(user=user)
        return session

    @staticmethod
    def reset(user):
        GuidedResumeSession.objects.filter(
            user=user, status=GuidedResumeSession.STATUS_IN_PROGRESS
        ).delete()
        return GuidedResumeSession.objects.create(user=user)

    @staticmethod
    def save_answers(session, patch):
        """Shallow-merge a patch of answers. A repeatable section's value is
        the full replacement list for that section."""
        answers = dict(session.answers or {})
        for key, value in (patch or {}).items():
            answers[key] = value
        session.answers = answers
        session.target_role = (answers.get("target_role") or session.target_role or "").strip()[:120]
        session.situation = (answers.get("situation") or session.situation or "").strip()[:30]
        session.save(update_fields=["answers", "target_role", "situation", "updated_at"])
        return session

    # -- AI drafting ------------------------------------------------------

    def draft_section(self, session, section):
        """Draft (but do not commit) a section from its collected answers."""
        answers_text = _section_answers_text(section, session.answers or {})
        if not answers_text.strip():
            return {"summary": ""} if section == "summary" else {"entries": []}
        ai = GuidedResumeAIService(provider=self.provider)
        if section == "summary":
            return {"summary": ai.draft_summary(session.target_role, answers_text)}
        return {"entries": ai.draft_section(section, session.target_role, answers_text)}

    @staticmethod
    def accept_section(session, section, payload):
        """Commit reviewed/edited content into the draft."""
        draft = dict(session.draft_data or {})
        if section == "summary":
            draft["summary"] = (payload.get("summary") or "").strip()
        elif section in DRAFTED_SECTIONS:
            draft[section] = normalize_entry_list(payload.get("entries") or [])
        session.draft_data = draft
        session.save(update_fields=["draft_data", "updated_at"])
        return session

    # -- assembly & finalize -------------------------------------------

    @staticmethod
    def assemble(session):
        answers = session.answers or {}
        draft = session.draft_data or {}

        parsed = {
            "personal_information": {
                "name": (answers.get("full_name") or "").strip() or None,
                "email": (answers.get("email") or "").strip() or None,
                "phone": (answers.get("phone") or "").strip() or None,
            },
            "summary": (draft.get("summary") or "").strip() or None,
            "skills": _as_list(answers.get("skills")),
            "experience": normalize_entry_list(draft.get("experience") or []),
            "projects": normalize_entry_list(draft.get("projects") or []),
            "education": _education_entries(answers.get("education")),
            "certifications": [
                {"header": cert, "bullets": []} for cert in _as_list(answers.get("certifications"))
            ],
            "achievements": [],
            "languages": _as_list(answers.get("languages")),
        }
        return normalize_parsed_data(parsed)

    def finalize(self, session):
        parsed_data = self.assemble(session)
        ats_score = ATSScorer.calculate_score(parsed_data)

        with transaction.atomic():
            resume = Resume.objects.create(
                user=session.user,
                source=Resume.SOURCE_GUIDED,
                status="completed",
                original_filename="Guided resume",
                parsed_data=parsed_data,
                ats_score=ats_score,
            )
            ResumeVersion.objects.create(
                resume=resume, version=1, parsed_data=parsed_data, ats_score=ats_score
            )
            session.resume = resume
            session.status = GuidedResumeSession.STATUS_FINALIZED
            session.save(update_fields=["resume", "status", "updated_at"])
        return resume


# -- helpers -----------------------------------------------------------------

def _as_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    return []


def _education_entries(items):
    """Education is assembled straight from the answers — no AI needed."""
    entries = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        qual = (item.get("qualification") or "").strip()
        inst = (item.get("institution") or "").strip()
        if not qual and not inst:
            continue
        header = " — ".join(p for p in (qual, inst) if p)
        dates = (item.get("dates") or "").strip()
        if dates:
            header = f"{header} | {dates}"
        if (item.get("status") or "") == "in_progress":
            header = f"{header} (in progress)"
        bullets = [
            line.strip()
            for line in (item.get("highlights") or "").replace("•", "\n").splitlines()
            if line.strip()
        ]
        entries.append({"header": header, "bullets": bullets})
    return normalize_entry_list(entries)


_ITEM_LABELS = {
    "experience": ["title", "org", "employment_type", "dates", "day_to_day", "achievements", "metric", "tools"],
    "projects": ["name", "what", "outcome", "tools", "link"],
}
_PROMPTS = {
    "title": "Role", "org": "Organisation", "employment_type": "Type", "dates": "When",
    "day_to_day": "Day to day", "achievements": "Improved/built/fixed", "metric": "Number",
    "tools": "Tools used", "name": "Project", "what": "What it is / did", "outcome": "Result",
    "link": "Link", "who_you_are": "Who I am", "strengths": "Strengths", "goal": "Goal",
}


def _section_answers_text(section, answers):
    """Flatten a section's answers into the plain text the drafting prompt
    reads. The user's own words, lightly labelled."""
    if section == "summary":
        parts = []
        for qid in ("who_you_are", "strengths", "goal"):
            val = (answers.get(qid) or "").strip()
            if val:
                parts.append(f"{_PROMPTS.get(qid, qid)}: {val}")
        return "\n".join(parts)

    items = answers.get(section)
    if not isinstance(items, list):
        return ""
    blocks = []
    order = _ITEM_LABELS.get(section, [])
    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        lines = [f"--- {section[:-1] if section.endswith('s') else section} #{idx} ---"]
        keys = order or list(item.keys())
        for key in keys:
            val = item.get(key)
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val if str(v).strip())
            val = (str(val) if val is not None else "").strip()
            if val:
                lines.append(f"{_PROMPTS.get(key, key)}: {val}")
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
