"""The user's details, reshaped into the atomic fields an application form asks for.

`parsed_data` is resume-shaped: a name blob, and experience/education as
``{header, bullets}`` where the header packs role, employer and dates into
one human-readable line. An ATS form wants none of that — it wants
`first_name`, `company`, `school`, one field at a time. This service does
that translation in one place so the browser extension never has to guess.

The rule that governs everything here: **a field we cannot source is
returned empty, never inferred.** The extension leaves an empty field
alone, so the worst case is the user types it themselves. Guessing a
location or a graduation year and having it silently submitted under their
name is a far worse failure than a blank box, and it is exactly the kind
of fabrication the rest of this product refuses to do.
"""

import re

# Header formats seen in the wild across the parsers and the guided
# builder: "Role | Company | Dates", "Role — Company · Dates",
# "Role at Company (2019-2022)". Split on whichever separator is present
# rather than assuming one.
_SEPARATORS = re.compile(r"\s*(?:\||—|–|·|,\s|\bat\b)\s*", re.IGNORECASE)
# Month names need a closing word boundary, and the full forms listed
# before the abbreviations. Without the trailing \b, "Mar" matched inside
# "St Mary's Hospital" and the employer was thrown away as a date.
_DATE_HINT = re.compile(
    r"\b(?:19|20)\d{2}\b"
    r"|\bpresent\b|\bcurrent\b|\bongoing\b"
    r"|\b(?:january|february|march|april|june|july|august|september|october"
    r"|november|december|jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)\b",
    re.IGNORECASE,
)
_LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?", re.I)
_GITHUB = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_-]+/?", re.I)
_URL = re.compile(r"https?://[^\s\"'<>,)]+", re.I)


def _looks_like_dates(part):
    return bool(_DATE_HINT.search(part))


def _split_header(header):
    """Return (title, organisation) from an entry header, best effort.

    Anything that reads as a date range is dropped rather than mistaken for
    an employer. Returns empty strings when the header can't be split, so
    the caller sends nothing rather than something wrong.
    """
    if not isinstance(header, str) or not header.strip():
        return "", ""

    parts = [part.strip(" -\u2013\u2014|\u00b7,") for part in _SEPARATORS.split(header) if part and part.strip()]
    # Strip a trailing "(2019-2022)" BEFORE testing for dates. Doing it the
    # other way round threw away "Acme Corp (2019-2022)" whole, because the
    # bracketed years made the entire fragment look like a date.
    parts = [re.sub(r"\s*\([^)]*\)\s*$", "", part).strip() for part in parts]
    parts = [part for part in parts if part and not _looks_like_dates(part)]

    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _first_url(pattern, blob):
    match = pattern.search(blob)
    if not match:
        return ""
    url = match.group(0).rstrip("/")
    return url if url.lower().startswith("http") else f"https://{url}"


class AutofillProfileService:
    @staticmethod
    def build(user, resume):
        """Atomic form fields for `user`. `resume` may be None."""
        parsed = (resume.parsed_data if resume else None) or {}
        personal = parsed.get("personal_information") or {}

        full_name = (personal.get("name") or user.name or "").strip()
        first_name, last_name = AutofillProfileService._split_name(full_name)

        # The whole resume as text, only so links can be found in it —
        # candidates put their LinkedIn/GitHub in the header, the summary,
        # or a contact line, and which one varies by parser.
        blob = AutofillProfileService._text_blob(parsed)

        current_title, current_company = _split_header(
            AutofillProfileService._first_header(parsed.get("experience"))
        )
        degree, institution = _split_header(
            AutofillProfileService._first_header(parsed.get("education"))
        )

        return {
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            # personal_information wins over the account fields: it came off
            # the resume the employer will read, so the two agree.
            "email": (personal.get("email") or user.email or "").strip(),
            "phone": (personal.get("phone") or user.phone or "").strip(),
            # Not present in parsed_data today. Returned as an empty string
            # rather than guessed from anything — see the module docstring.
            "location": (personal.get("location") or "").strip(),
            "linkedin_url": _first_url(_LINKEDIN, blob),
            "github_url": _first_url(_GITHUB, blob),
            "website_url": AutofillProfileService._portfolio_url(blob),
            "current_title": current_title,
            "current_company": current_company,
            "degree": degree,
            "institution": institution,
            "summary": (parsed.get("summary") or "").strip(),
            "skills": [s for s in (parsed.get("skills") or []) if isinstance(s, str)][:30],
            "has_resume": bool(resume),
        }

    @staticmethod
    def _split_name(full_name):
        parts = [part for part in full_name.split() if part]
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])

    @staticmethod
    def _first_header(entries):
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("header"):
                return entry["header"]
            if isinstance(entry, str) and entry.strip():
                return entry
        return ""

    @staticmethod
    def _text_blob(parsed):
        chunks = []
        for value in parsed.values():
            if isinstance(value, str):
                chunks.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        chunks.append(item)
                    elif isinstance(item, dict):
                        chunks.append(str(item.get("header", "")))
                        chunks.extend(
                            b for b in (item.get("bullets") or []) if isinstance(b, str)
                        )
            elif isinstance(value, dict):
                chunks.extend(str(v) for v in value.values())
        return " ".join(chunks)

    @staticmethod
    def _portfolio_url(blob):
        """A personal site, if there is one — explicitly not LinkedIn or
        GitHub, which have their own fields on most application forms."""
        for match in _URL.finditer(blob):
            url = match.group(0).rstrip("/.,")
            lowered = url.lower()
            if "linkedin.com" in lowered or "github.com" in lowered:
                continue
            return url
        return ""
