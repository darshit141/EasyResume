"""Renders a job-tailored resume's already-validated, source-grounded data
into a real .docx file. Never generates or infers content itself — it only
formats exactly what tailored_resume_data already contains.

Layout is driven by a resolved template config (see
resumes.resume_template). ``template_config=None`` reproduces the
pre-template output exactly. Like the PDF exporter, the .docx stays
single-column even when the template asks for two columns — a genuinely
two-column Word doc is an ATS risk.
"""

import io

from docx import Document
from docx.shared import Pt, RGBColor

from resumes.resume_template import (
    DENSITY_SCALE,
    SECTION_HEADINGS,
    resolve_template,
    visible_sections,
)

GRAY = RGBColor(0x47, 0x54, 0x67)

ITEM_FIELDS = {"experience", "projects", "education", "certifications"}

# sans / serif -> a font name Word maps to something sensible on every
# install. Nothing exotic — an unusual face is a route to text an ATS
# cannot extract.
_FONT_MAP = {"sans": "Calibri", "serif": "Cambria"}


def _accent_rgb(config):
    hex_value = config["accent_color"].lstrip("#")
    return RGBColor(
        int(hex_value[0:2], 16),
        int(hex_value[2:4], 16),
        int(hex_value[4:6], 16),
    )


def _heading_text(key, config):
    label = SECTION_HEADINGS.get(key, key.title())
    return label.upper() if config["heading_style"] == "caps" else label


def _entry_parts(item):
    """Split one entry into its heading line and its bullets.

    Shares the exact fallback behaviour of the PDF exporter: older
    curations (and any generation where the model skipped the split) carry
    only the flat `tailored_text`, which renders as a single bullet rather
    than being dropped - so previously generated resumes keep working.
    """
    if not isinstance(item, dict):
        return "", [str(item)] if item else []

    header = str(item.get("header") or "").strip()
    bullets = [str(b).strip() for b in (item.get("bullets") or []) if str(b).strip()]

    if not header and not bullets:
        flat = str(item.get("tailored_text") or "").strip()
        return "", ([flat] if flat else [])

    return header, bullets


class TailoredResumeExportService:
    """Builds a .docx document from a curation's tailored_resume_data."""

    @staticmethod
    def build_docx(tailored_resume_data, template_config=None):
        config = template_config or resolve_template(None, None)
        accent = _accent_rgb(config)
        font_name = _FONT_MAP.get(config["font_family"], _FONT_MAP["sans"])
        scale = DENSITY_SCALE.get(config["density"], 1.0)
        center = config["name_alignment"] == "center"

        document = Document()

        for section in document.sections:
            section.top_margin = Pt(36)
            section.bottom_margin = Pt(36)
            section.left_margin = Pt(50)
            section.right_margin = Pt(50)

        normal = document.styles["Normal"]
        normal.font.name = font_name

        personal = tailored_resume_data.get("personal_information") or {}
        name = personal.get("name") or "Candidate"
        contact_parts = [personal.get("email"), personal.get("phone")]
        contact_line = "  ·  ".join(part for part in contact_parts if part)

        from docx.enum.text import WD_ALIGN_PARAGRAPH

        align = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT

        name_paragraph = document.add_paragraph()
        name_paragraph.alignment = align
        name_run = name_paragraph.add_run(name)
        name_run.bold = True
        name_run.font.size = Pt(20)
        name_run.font.color.rgb = accent

        if contact_line:
            contact_paragraph = document.add_paragraph()
            contact_paragraph.alignment = align
            contact_run = contact_paragraph.add_run(contact_line)
            contact_run.font.size = Pt(10)
            contact_run.font.color.rgb = GRAY

        for field in visible_sections(config):
            value = tailored_resume_data.get(field)
            if not value:
                continue

            heading_paragraph = document.add_paragraph()
            heading_paragraph.paragraph_format.space_before = Pt(round(8 * scale, 1))
            heading_run = heading_paragraph.add_run(_heading_text(field, config))
            heading_run.bold = True
            heading_run.font.size = Pt(11)
            heading_run.font.color.rgb = accent

            if field == "summary":
                body_paragraph = document.add_paragraph()
                body_run = body_paragraph.add_run(value)
                body_run.font.size = Pt(10.5)
            elif field == "skills":
                skills_paragraph = document.add_paragraph()
                skills_run = skills_paragraph.add_run(" • ".join(value))
                skills_run.font.size = Pt(10.5)
            elif field in ITEM_FIELDS:
                for item in value:
                    entry_header, bullets = _entry_parts(item)
                    if not entry_header and not bullets:
                        continue

                    if entry_header:
                        # Role/company/dates as a bold line above its bullets,
                        # rather than as one more bullet in the list - the
                        # layout a resume is actually read in.
                        entry_paragraph = document.add_paragraph()
                        entry_run = entry_paragraph.add_run(entry_header)
                        entry_run.bold = True
                        entry_run.font.size = Pt(10.5)
                        entry_run.font.color.rgb = accent

                    for bullet in bullets:
                        item_paragraph = document.add_paragraph(style="List Bullet")
                        item_run = item_paragraph.add_run(bullet)
                        item_run.font.size = Pt(10.5)

        buffer = io.BytesIO()
        document.save(buffer)
        buffer.seek(0)
        return buffer

    @staticmethod
    def build_filename(job_title):
        safe_title = "".join(
            char if char.isalnum() or char in (" ", "-", "_") else ""
            for char in (job_title or "resume")
        ).strip().replace(" ", "_")
        return f"tailored_resume_{safe_title or 'resume'}.docx"
