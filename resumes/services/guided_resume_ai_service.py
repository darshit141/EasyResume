"""AI drafting for the guided resume builder.

The user's interview answers are the only facts. The model reworders them
into resume entries; every generated line is checked against the answer
text with the same token-grounding rule the tailoring pipeline uses. The
difference from tailoring: guided input is thin and informal, so a failed
line is **dropped**, not a reason to fail the whole draft — the user is
building from scratch and a partial draft they can edit beats an error.
"""

from resumes.prompts import (
    GUIDED_SECTION_SCHEMA,
    GUIDED_SUMMARY_SCHEMA,
    build_guided_section_prompt,
)
from resumes.resume_content import normalize_entry
from resumes.services.llm_service import LLMServiceError, get_llm_service
from resumes.services.resume_curation_ai_service import ResumeCurationAIService

_ground = ResumeCurationAIService


class GuidedResumeAIService:
    def __init__(self, llm_service=None, provider=None):
        self.llm_service = llm_service or get_llm_service(provider)

    # -- section drafting -------------------------------------------------

    def draft_section(self, section, target_role, answers_text):
        """Return a list of grounded {header, bullets} entries for a
        repeatable section (experience / projects / education)."""
        prompt = build_guided_section_prompt(section, target_role or "", answers_text)
        raw = self.llm_service.generate_structured(prompt, GUIDED_SECTION_SCHEMA)
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return []

        blob = _ground._normalise_text(answers_text)
        out = []
        for item in entries:
            entry = normalize_entry(item)
            if entry is None:
                continue
            # A header that isn't supported by the answers means the model
            # invented the whole entry — drop it.
            if entry["header"] and _ground._grounding_violations("header", entry["header"], blob):
                continue
            kept_bullets = [
                bullet for bullet in entry["bullets"]
                if not _ground._grounding_violations("bullet", bullet, blob)
            ]
            if not entry["header"] and not kept_bullets:
                continue
            out.append({"header": entry["header"], "bullets": kept_bullets})
        return out

    def draft_summary(self, target_role, answers_text):
        """Return a grounded 2-3 sentence summary string (may be empty)."""
        prompt = build_guided_section_prompt("summary", target_role or "", answers_text)
        raw = self.llm_service.generate_structured(prompt, GUIDED_SUMMARY_SCHEMA)
        summary = (raw.get("summary") if isinstance(raw, dict) else "") or ""
        summary = summary.strip()
        if not summary:
            return ""
        blob = _ground._normalise_text(answers_text)
        # For prose we don't drop the whole thing on one stray proper noun,
        # but if it's wildly ungrounded (several violations) fall back to
        # empty so the user writes their own rather than ship an invention.
        if len(_ground._grounding_violations("summary", summary, blob)) >= 3:
            return ""
        return summary

    # -- inline polish (the "improve this line" action) -----------------

    OPERATIONS = {
        "grammar": "Fix only spelling, grammar and punctuation. Do not change the meaning or the wording beyond what grammar requires.",
        "stronger": "Rewrite as a single crisp resume bullet: start with a strong past-tense verb, keep it concrete. Add nothing that isn't already stated.",
        "shorten": "Make it noticeably shorter while keeping every fact.",
        "expand": "Add a little specificity, but ONLY using detail already implied by the text — invent nothing.",
        "professional": "Make the tone professional and resume-appropriate. Keep every fact; no inflation.",
    }

    def polish(self, text, operation, target_role=None):
        """Return {suggestion, changed:bool}. Grounded: the suggestion may
        not introduce a fact absent from the input."""
        text = (text or "").strip()
        if not text:
            return {"suggestion": "", "changed": False}
        instruction = self.OPERATIONS.get(operation, self.OPERATIONS["grammar"])
        prompt = (
            "You are editing one line of resume text.\n"
            f"{instruction}\n"
            "You must not add any skill, tool, employer, date, metric or achievement "
            "that is not already in the ORIGINAL. Return JSON: {\"text\": \"...\"}.\n"
            + (f"Target role for tone: {target_role}\n" if target_role else "")
            + f"\nORIGINAL:\n{text}"
        )
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
        try:
            raw = self.llm_service.generate_structured(prompt, schema)
        except LLMServiceError:
            raise
        suggestion = ((raw.get("text") if isinstance(raw, dict) else "") or "").strip()
        if not suggestion:
            return {"suggestion": text, "changed": False}
        blob = _ground._normalise_text(text)
        if _ground._grounding_violations("polish", suggestion, blob):
            # The rewrite drifted — hand back the original untouched.
            return {"suggestion": text, "changed": False}
        return {"suggestion": suggestion, "changed": suggestion != text}
