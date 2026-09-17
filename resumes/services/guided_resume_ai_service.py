"""AI drafting for the guided resume builder.

The user's interview answers are the only facts. The model reworders them
into resume entries; every generated line is checked against the answer
text with the same token-grounding rule the tailoring pipeline uses. The
difference from tailoring: guided input is thin and informal, so a failed
line is **dropped**, not a reason to fail the whole draft — the user is
building from scratch and a partial draft they can edit beats an error.
"""

import re

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
    def __init__(self, llm_service=None, provider=None, user=None):
        """``user`` is the account the work is being done for. It is used for
        one thing: if they have saved their own provider API key, the client
        built here uses it instead of the server's (see
        billing.services.user_ai_key_service). Left None — a management
        command, a health check — the server's own credentials are used, which
        is the behaviour this had before BYOK existed."""
        self.user = user
        self.llm_service = llm_service or get_llm_service(provider, user=user)

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

    # Each instruction has to push the model to genuinely REWRITE. The
    # earlier wording ("add nothing", "invent nothing") was so defensive
    # that the model handed back the original with the punctuation tidied,
    # which reads to a user as "the AI does nothing". The fact boundary is
    # enforced separately by `_new_facts` below, so these are free to ask
    # for a real change in register.
    OPERATIONS = {
        "grammar": (
            "Fix spelling, grammar, punctuation and awkward phrasing. Keep the "
            "author's wording where it is already correct."
        ),
        "stronger": (
            "Rewrite as one crisp resume bullet. Open with a specific, strong "
            "past-tense verb and make the contribution and its result read "
            "clearly. Change the sentence structure and word choice as much as "
            "you need to."
        ),
        "shorten": "Cut it to the shortest form that still carries every fact and reads naturally.",
        "expand": (
            "Rewrite it more fully, drawing out what is already implied — the "
            "context, the action and the outcome the author has described. Do "
            "not introduce anything they did not say."
        ),
        "professional": (
            "Rewrite in polished professional resume register. Replace casual "
            "or vague wording with precise, workplace-appropriate language, and "
            "restructure the sentence if that reads better. This should look "
            "clearly rewritten, not lightly edited."
        ),
    }

    def polish(self, text, operation, target_role=None):
        """Return {suggestion, changed:bool}. Grounded: the suggestion may
        not introduce a fact absent from the input."""
        text = (text or "").strip()
        if not text:
            return {"suggestion": "", "changed": False}
        instruction = self.OPERATIONS.get(operation, self.OPERATIONS["grammar"])
        prompt = (
            "You are rewriting one line of a resume for someone who is not a "
            "confident writer. Make it sound like a professional wrote it, "
            "while keeping it completely true.\n\n"
            f"WHAT TO DO: {instruction}\n\n"
            "REWRITE FREELY:\n"
            "- Change vocabulary, verbs and sentence structure. Casual or vague "
            "phrasing should become precise and workplace-appropriate.\n"
            "- Handing back the original with only punctuation changed is a "
            "failure. If it already reads well, still sharpen the word choice.\n\n"
            "NEVER ADD A FACT:\n"
            "- No number, percentage, duration, team size or date that is not "
            "already in the ORIGINAL.\n"
            "- No employer, product, tool, technology or certification that is "
            "not already in the ORIGINAL.\n\n"
            "NEVER INFLATE SENIORITY OR SCOPE - this matters as much as the facts:\n"
            "- 'helped with' must not become 'led' or 'owned'.\n"
            "- 'was part of a team that built' must not become 'built'.\n"
            "- 'learned' must not become 'mastered'; 'used' must not become "
            "'architected'.\n"
            "Describe exactly the contribution they described, in better words.\n\n"
            'Return JSON: {"text": "..."}.\n'
            + (f"\nTarget role, for tone only: {target_role}\n" if target_role else "")
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
        if self._new_facts(suggestion, text):
            # The rewrite asserted something the author didn't — hand back
            # the original untouched rather than put words in their mouth.
            return {"suggestion": text, "changed": False}
        return {"suggestion": suggestion, "changed": suggestion != text}

    # -- what counts as a new fact when *rephrasing* ---------------------
    #
    # Polish is not generation, and grounding it like generation is what
    # made this feature feel broken. `_significant_tokens` treats every
    # capitalised word as a candidate fact, which is right when the model
    # is producing claims out of a whole resume — but here the author wrote
    # the line and is asking for it to read better. Under that rule a
    # perfectly honest rewrite like
    #
    #     "Resolved customer issues. Improved satisfaction."
    #
    # was rejected because "Improved" is capitalised — it just happens to
    # start the second sentence. Any two-sentence rewrite died that way,
    # and so did every ordinary vocabulary upgrade.
    #
    # So a rephrase may say anything it likes in ordinary words, and is
    # blocked only where it *asserts* something new:
    #
    #   * a number      — metrics, percentages, durations, years
    #   * an acronym    — AWS, SQL, KPI: named things, not register
    #   * a proper noun — capitalised MID-sentence, so employers, tools
    #                     and products are caught while a sentence-opening
    #                     verb is not
    #
    # Scope inflation ("helped with" -> "led") is a real risk this cannot
    # see, since both are ordinary lowercase words. That is carried by the
    # prompt instead, which forbids it explicitly.

    _WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*|\d[\d.,]*%?")
    _SENTENCE_BREAKS = set(".!?;:\n\r\t\u2022-\u2013\u2014|/(")

    @classmethod
    def _starts_a_sentence(cls, text, index):
        cursor = index - 1
        while cursor >= 0 and text[cursor].isspace():
            cursor -= 1
        return cursor < 0 or text[cursor] in cls._SENTENCE_BREAKS

    @classmethod
    def _new_facts(cls, suggestion, original):
        """Assertions in `suggestion` that `original` never made."""
        source = _ground._normalise_text(original)
        found = []
        for match in cls._WORD_RE.finditer(suggestion):
            token = match.group(0).strip(".,;:")
            if len(token) < 2:
                continue

            has_digit = any(character.isdigit() for character in token)
            is_acronym = token.isupper()
            is_proper_noun = (
                token[0].isupper()
                and not token.isupper()
                and not cls._starts_a_sentence(suggestion, match.start())
            )
            if not (has_digit or is_acronym or is_proper_noun):
                continue

            normalised = _ground._normalise_text(token)
            if normalised and not _ground._is_grounded(normalised, source):
                found.append(token)
        return found
