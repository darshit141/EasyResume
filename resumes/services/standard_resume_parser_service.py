"""Deterministic, non-AI resume field extraction.

Regex plus section-header heuristics only — no LLM call, no API key, no
external network request, and the resume text never leaves this server.
This trades extraction quality for zero AI dependency: it is a reasonable
fallback for privacy-sensitive users or when no AI provider is configured,
not a replacement for AI accuracy on unusually formatted resumes.
"""

import re

from resumes.resume_content import ENTRY_SECTIONS, normalize_entry_list


class StandardResumeParserService:
    EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
    PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
    NAME_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){0,3}$")

    # Alias -> canonical field. Order matters only for matching; longer,
    # more specific aliases are listed before generic ones where they could
    # otherwise collide (none currently do).
    SECTION_ALIASES = {
        "summary": ["summary", "professional summary", "objective", "profile", "about me"],
        "skills": ["skills", "technical skills", "skills & interests", "skills and interests", "core competencies"],
        "experience": ["experience", "work experience", "professional experience", "employment history"],
        "education": ["education", "academic background", "qualifications"],
        "projects": ["projects", "personal projects", "academic projects"],
        "certifications": ["certifications", "certificates", "licenses", "licenses & certifications"],
        "achievements": ["achievements", "awards", "honors", "achievements & awards"],
        "languages": ["languages", "language proficiency"],
    }

    LIST_FIELDS = ("skills", "experience", "education", "projects", "certifications", "achievements", "languages")
    SPLIT_ON_COMMA_FIELDS = ("skills", "languages")

    # Recognizable resume sub-labels that don't map to any schema field.
    # Content under these is dropped rather than merged into an unrelated
    # field (e.g. "Hobbies: ..." must never end up inside "skills").
    EXCLUDED_SUB_LABELS = {"soft skills", "hobbies", "interests"}

    def __init__(self):
        self._header_lookup = {
            alias: field
            for field, aliases in self.SECTION_ALIASES.items()
            for alias in aliases
        }

    def parse(self, text):
        lines = [line.strip() for line in text.splitlines()]
        non_empty_lines = [line for line in lines if line]

        email = self._first_match(self.EMAIL_RE, text)
        phone = self._first_match(self.PHONE_RE, text)
        name = self._guess_name(non_empty_lines, email, phone)

        sections = self._split_sections(lines)

        summary = " ".join(sections.get("summary", [])).strip() or None

        result = {
            "personal_information": {"name": name, "email": email, "phone": phone},
            "name": name,
            "email": email,
            "phone": phone,
            "summary": summary,
        }
        for field in self.LIST_FIELDS:
            items = sections.get(field, [])
            if field in self.SPLIT_ON_COMMA_FIELDS:
                items = self._split_items(items, field)
            elif field in ENTRY_SECTIONS:
                items = normalize_entry_list(self._group_entries(items))
            result[field] = items
        return result

    # A line that opens with one of these is a bullet under the current
    # entry, not the start of a new one.
    _BULLET_RE = re.compile(r"^\s*(?:[-*•●▪◦‣·]|o\s|\d+[.)])\s+(.*\S)")

    @classmethod
    def _group_entries(cls, lines):
        """Turn a run of raw section lines into header/bullets entries: a
        bullet-marked line joins the current entry's bullets, any other
        non-empty line starts a new entry (its header). Bullets seen before
        any header become a headerless entry, which normalize_entry then
        repairs by promoting the first bullet."""
        entries = []
        current = None
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            bullet_match = cls._BULLET_RE.match(line)
            if bullet_match:
                if current is None:
                    current = {"header": "", "bullets": []}
                    entries.append(current)
                current["bullets"].append(bullet_match.group(1).strip())
            else:
                current = {"header": line, "bullets": []}
                entries.append(current)
        return entries

    @classmethod
    def _first_match(cls, pattern, text):
        match = pattern.search(text)
        return match.group(0).strip() if match else None

    @classmethod
    def _guess_name(cls, non_empty_lines, email, phone):
        for line in non_empty_lines[:5]:
            if email and email in line:
                continue
            if phone and phone in line:
                continue
            candidate = line.strip(" -|:")
            if cls.NAME_LINE_RE.match(candidate):
                return candidate
        return None

    def _matched_section(self, line):
        normalised = re.sub(r"[:\-–—]+$", "", line.strip().lower()).strip()
        return self._header_lookup.get(normalised)

    def _split_sections(self, lines):
        sections = {}
        current = None
        for line in lines:
            section = self._matched_section(line)
            if section:
                current = section
                sections.setdefault(current, [])
                continue
            if current and line:
                sections[current].append(line)
        return sections

    def _split_items(self, lines, field):
        """Comma-split a section's lines into items. A line prefixed with a
        label (e.g. "Soft Skills:", "Hobbies:") that names a *different*
        known section is skipped rather than merged in — the schema has no
        field for it, and the AI-backed parsers make the same judgment call
        by simply omitting anything the schema can't represent."""
        own_aliases = self.SECTION_ALIASES[field]
        items = []
        for line in lines:
            label_match = re.match(r"^([A-Za-z][A-Za-z &]{1,30}):\s*(.*)$", line)
            if label_match:
                label = label_match.group(1).strip().lower()
                if label in own_aliases:
                    content = label_match.group(2)
                elif label in self._header_lookup or label in self.EXCLUDED_SUB_LABELS:
                    continue
                else:
                    content = line
            else:
                content = line
            for piece in re.split(r"[,;]", content):
                piece = piece.strip(" \t-•*")
                if piece:
                    items.append(piece)
        return items
