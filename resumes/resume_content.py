"""One shape for a resume, used everywhere a resume is read or written.

The problem this solves: ``experience`` / ``projects`` / ``education`` /
``certifications`` used to be flat lists of strings, so a role with twelve
bullet points had nowhere to put eleven of them — the parser compressed
each job into a single sentence and the rest of the content was lost on
upload. Now every entry in those four sections is::

    {"header": "<role/company/dates line>", "bullets": ["<achievement>", ...]}

``header`` is the identifying line only; ``bullets`` is every distinct
responsibility or achievement, one per bullet, nothing merged and nothing
dropped.

Backward compatibility is total and needs no data migration: a plain
string entry (everything already stored, and anything a caller still sends
that way) is read as ``{"header": "<the string>", "bullets": []}``.
``normalize_parsed_data`` coerces a whole document into the canonical shape
on the way in; ``entry_text`` flattens one entry back to plain text for the
places that reason over text (ATS scoring, the grounding check, the
candidate-source snapshot the tailoring prompt sees).
"""

from __future__ import annotations

# The sections whose entries carry a header + bullets. `skills`,
# `achievements` and `languages` stay flat string lists — they have no
# sub-structure to lose.
ENTRY_SECTIONS = ("experience", "projects", "education", "certifications")

# Caps, mirrored in RESUME_JSON_SCHEMA. Generous enough that a real resume
# is never truncated; tight enough that a runaway model can't balloon the
# document.
MAX_ENTRIES_PER_SECTION = 30
MAX_BULLETS_PER_ENTRY = 40
MAX_HEADER_CHARS = 400
MAX_BULLET_CHARS = 1200


def _clean_str(value, limit):
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def normalize_entry(item):
    """Coerce one entry — a string (legacy/simple) or a dict — into
    ``{"header": str, "bullets": [str]}``. Returns ``None`` for an entry
    with nothing usable in it, so callers can drop empties."""
    if isinstance(item, str):
        header = _clean_str(item, MAX_HEADER_CHARS + MAX_BULLET_CHARS)
        return {"header": header, "bullets": []} if header else None

    if isinstance(item, dict):
        header = _clean_str(item.get("header"), MAX_HEADER_CHARS)
        raw_bullets = item.get("bullets")
        bullets = []
        if isinstance(raw_bullets, list):
            for bullet in raw_bullets[:MAX_BULLETS_PER_ENTRY]:
                cleaned = _clean_str(bullet, MAX_BULLET_CHARS)
                if cleaned:
                    bullets.append(cleaned)
        # An entry that arrived as a dict but only really has body text
        # (no identifying line) still keeps its content — promote the first
        # bullet to the header rather than lose it.
        if not header and bullets:
            header = bullets.pop(0)
        if not header and not bullets:
            return None
        return {"header": header, "bullets": bullets}

    return None


def normalize_entry_list(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:MAX_ENTRIES_PER_SECTION]:
        entry = normalize_entry(item)
        if entry is not None:
            out.append(entry)
    return out


def _normalize_string_list(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            # Tolerate a mis-shaped entry in a flat section.
            text = entry_text(item)
            if text:
                out.append(text)
    return out


def normalize_parsed_data(parsed_data):
    """Return a copy of a parsed-resume document with every entry section in
    the canonical header/bullets shape. Non-entry fields are passed through
    untouched. Safe to call on already-normalized data and on legacy data."""
    if not isinstance(parsed_data, dict):
        return parsed_data

    result = dict(parsed_data)
    for section in ENTRY_SECTIONS:
        if section in result:
            result[section] = normalize_entry_list(result[section])
    for section in ("skills", "achievements", "languages"):
        if section in result:
            result[section] = _normalize_string_list(result[section])
    return result


def entry_text(item, *, bullet_prefix="- "):
    """Flatten one entry to plain text: the header, then each bullet on its
    own line. Used wherever a text view of the resume is needed — ATS
    scoring, source grounding, the candidate snapshot fed to the tailoring
    prompt — so those paths see every bullet, not just the header."""
    entry = normalize_entry(item)
    if entry is None:
        return ""
    lines = [entry["header"]] if entry["header"] else []
    lines.extend(f"{bullet_prefix}{bullet}" for bullet in entry["bullets"])
    return "\n".join(lines).strip()


def entry_list_as_text(value):
    """A whole section flattened to a list of per-entry text blocks."""
    return [text for text in (entry_text(item) for item in (value or [])) if text]


def flatten_parsed_data_for_grounding(parsed_data):
    """A copy of the document with every entry section replaced by a flat
    list of text blocks — the shape the tailoring/grounding pipeline still
    expects for `candidate_source_data`, now lossless because each block
    carries the header and all of its bullets."""
    if not isinstance(parsed_data, dict):
        return parsed_data
    result = dict(parsed_data)
    for section in ENTRY_SECTIONS:
        if section in result:
            result[section] = entry_list_as_text(result[section])
    return result
