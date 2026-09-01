"""The resume layout template system — the single source of truth for how a
tailored resume is laid out, shared by all three renderers (the React
`ResumePreview`, the reportlab PDF exporter, and the python-docx exporter).

A "template" here is deliberately **not** free-form CSS. IntelliResume's
whole promise is ATS-readability, and the fastest way to break that is a
multi-column, graphically styled resume that a parser cannot read in
document order. So a template is a small, closed set of options
(`TEMPLATE_FIELDS`), every combination of which we can reason about and
test. Anything with a real ATS risk (two-column, an unusual font) is still
selectable but is flagged by :func:`template_ats_report` so the UI can warn.

`resolve_template(template_id, overrides)` is the only function callers
should need: it takes a preset id and an optional dict of user overrides
and returns a fully-populated, validated config. Passing ``None`` for both
yields :data:`DEFAULT_TEMPLATE` unchanged, which every renderer treats as
"exactly what we shipped before this system existed" — so an un-customised
resume renders byte-for-byte as it used to.
"""

from __future__ import annotations

import copy

# The canonical resume sections, in their default order. `section_order`
# and `hidden_sections` in a template config may only ever reference these
# keys; anything else is dropped by `resolve_template`.
SECTION_KEYS = [
    "summary",
    "skills",
    "experience",
    "projects",
    "education",
    "certifications",
]

# Human-readable heading printed for each section. Renderers may upper-case
# it (see `heading_style`) but never invent their own wording, so the three
# outputs always agree.
SECTION_HEADINGS = {
    "summary": "Professional Summary",
    "skills": "Core Skills",
    "experience": "Professional Experience",
    "projects": "Projects",
    "education": "Education",
    "certifications": "Certifications",
}

# Every accent colour a user may pick, as hex. A closed palette (rather than
# a colour picker) keeps contrast legible and the choice reviewable. The
# first entry is the historical navy and is the default — picking it must
# leave output identical to the pre-template renderers.
ACCENT_PALETTE = [
    "#101828",  # navy / ink — the original
    "#2C42B0",  # indigo
    "#0F766E",  # teal
    "#9A3412",  # rust
    "#6D28D9",  # violet
    "#334155",  # slate
]

# Only families that are safe to embed or are guaranteed present in any PDF
# reader / Word install. `sans` and `serif` map, per renderer, to a
# concrete face (Helvetica/Arial, Times/Georgia). Nothing exotic — an
# unusual font is one more way to end up with text an ATS cannot extract.
FONT_FAMILIES = ["sans", "serif"]

HEADING_STYLES = ["caps", "plain"]  # UPPER-CASE (original) vs Title Case
NAME_ALIGNMENTS = ["left", "center"]
DENSITIES = ["compact", "standard", "roomy"]
COLUMN_CHOICES = [1, 2]

# Spacing multiplier applied to every gap/pad by each renderer. `standard`
# is 1.0 so it reproduces the original spacing exactly.
DENSITY_SCALE = {"compact": 0.72, "standard": 1.0, "roomy": 1.3}

DEFAULT_TEMPLATE_ID = "ats_classic"

# The resolved shape every renderer consumes. Also the exact result of
# resolve_template(None, None).
DEFAULT_TEMPLATE = {
    "template_id": DEFAULT_TEMPLATE_ID,
    "section_order": list(SECTION_KEYS),
    "hidden_sections": [],
    "heading_style": "caps",
    "accent_color": ACCENT_PALETTE[0],
    "font_family": "sans",
    "name_alignment": "left",
    "density": "standard",
    "columns": 1,
}

# The built-in presets offered in the UI. Each is a partial config merged
# over DEFAULT_TEMPLATE, so a preset only states what it changes.
TEMPLATE_PRESETS = {
    "ats_classic": {
        "id": "ats_classic",
        "name": "ATS Classic",
        "description": (
            "The original single-column layout. Maximum compatibility with "
            "every applicant tracking system — the safe default."
        ),
        "config": {},
    },
    "modern": {
        "id": "modern",
        "name": "Modern",
        "description": (
            "Single column, but with an indigo accent on your name and "
            "section headings and a touch more breathing room."
        ),
        "config": {
            "accent_color": "#2C42B0",
            "density": "roomy",
            "name_alignment": "center",
        },
    },
    "compact": {
        "id": "compact",
        "name": "Compact",
        "description": (
            "Tighter spacing to fit more onto one page, while staying "
            "single-column and fully parseable."
        ),
        "config": {"density": "compact"},
    },
    "two_column": {
        "id": "two_column",
        "name": "Two Column",
        "description": (
            "Skills and education move to a side column for an on-screen "
            "look. Note: some ATS parsers read two-column resumes out of "
            "order — the downloaded PDF and Word files stay single-column "
            "for safety."
        ),
        "config": {"columns": 2, "accent_color": "#0F766E"},
    },
}

# Overrides that a Free-plan user may set without hitting a Pro gate. They
# only pick a preset; changing individual knobs is the Pro feature. Kept
# here so the entitlement check and the UI agree on what counts as
# "customisation".
FREE_TIER_ALLOWS_PRESET_ONLY = True


def preset_list():
    """Presets in display order, each with its fully-resolved config and an
    ATS report, for the template picker."""
    ordered_ids = ["ats_classic", "modern", "compact", "two_column"]
    out = []
    for preset_id in ordered_ids:
        preset = TEMPLATE_PRESETS[preset_id]
        resolved = resolve_template(preset_id, None)
        out.append(
            {
                "id": preset["id"],
                "name": preset["name"],
                "description": preset["description"],
                "config": resolved,
                "ats": template_ats_report(resolved),
            }
        )
    return out


def _clamp_choice(value, allowed, fallback):
    return value if value in allowed else fallback


def resolve_template(template_id, overrides):
    """Merge a preset id and a dict of user overrides into a complete,
    validated config.

    Resolution order (later wins): :data:`DEFAULT_TEMPLATE` → the named
    preset's partial config → ``overrides``. Every field is then clamped to
    its allowed values, so a malformed or hostile ``overrides`` payload can
    never produce a config a renderer isn't prepared for. Unknown keys are
    ignored. ``resolve_template(None, None)`` returns a copy of
    :data:`DEFAULT_TEMPLATE`.
    """
    config = copy.deepcopy(DEFAULT_TEMPLATE)

    preset = TEMPLATE_PRESETS.get(template_id or DEFAULT_TEMPLATE_ID)
    if preset:
        config["template_id"] = preset["id"]
        config.update(preset["config"])
    else:
        config["template_id"] = DEFAULT_TEMPLATE_ID

    if isinstance(overrides, dict):
        for key in (
            "heading_style",
            "accent_color",
            "font_family",
            "name_alignment",
            "density",
            "columns",
            "section_order",
            "hidden_sections",
        ):
            if key in overrides and overrides[key] is not None:
                config[key] = overrides[key]

    # --- clamp every field to something a renderer can handle -------------
    config["heading_style"] = _clamp_choice(
        config["heading_style"], HEADING_STYLES, "caps"
    )
    config["accent_color"] = (
        config["accent_color"]
        if config["accent_color"] in ACCENT_PALETTE
        else ACCENT_PALETTE[0]
    )
    config["font_family"] = _clamp_choice(config["font_family"], FONT_FAMILIES, "sans")
    config["name_alignment"] = _clamp_choice(
        config["name_alignment"], NAME_ALIGNMENTS, "left"
    )
    config["density"] = _clamp_choice(config["density"], DENSITIES, "standard")
    try:
        config["columns"] = int(config["columns"])
    except (TypeError, ValueError):
        config["columns"] = 1
    if config["columns"] not in COLUMN_CHOICES:
        config["columns"] = 1

    config["section_order"] = _normalise_section_order(config.get("section_order"))
    config["hidden_sections"] = [
        key for key in config.get("hidden_sections") or [] if key in SECTION_KEYS
    ]

    return config


def _normalise_section_order(order):
    """Return a permutation of :data:`SECTION_KEYS`: keep the caller's order
    for keys it lists (ignoring anything unknown or duplicated), then append
    any canonical section it left out so nothing can silently disappear just
    because it was missing from the payload."""
    if not isinstance(order, list):
        return list(SECTION_KEYS)
    seen = []
    for key in order:
        if key in SECTION_KEYS and key not in seen:
            seen.append(key)
    for key in SECTION_KEYS:
        if key not in seen:
            seen.append(key)
    return seen


def visible_sections(config):
    """The sections to render, in order — `section_order` minus
    `hidden_sections`. Renderers should iterate this, not `section_order`."""
    hidden = set(config.get("hidden_sections") or [])
    return [key for key in config.get("section_order") or SECTION_KEYS if key not in hidden]


def template_ats_report(config):
    """A plain-language read on how safely this config will parse in an ATS,
    so the UI can show a badge and the user makes an informed choice.

    Returns ``{"safe": bool, "warnings": [str, ...]}``. "safe" is the
    single fact the badge keys off; "warnings" explains any caveats.
    """
    warnings = []
    if int(config.get("columns", 1)) == 2:
        warnings.append(
            "Two-column layouts are read out of order by some applicant "
            "tracking systems. Your downloaded PDF and Word files stay "
            "single-column, but paste-into-a-form flows may still jumble."
        )
    if config.get("density") == "compact":
        warnings.append(
            "Compact spacing is fine for parsing but can be hard for a "
            "human reviewer to skim — keep an eye on the preview."
        )
    return {"safe": len(warnings) == 0, "warnings": warnings}
