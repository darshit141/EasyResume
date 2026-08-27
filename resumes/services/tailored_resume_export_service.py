"""Renders a job-tailored resume's already-validated, source-grounded data
into a real .docx file. Never generates or infers content itself — it only
formats exactly what tailored_resume_data already contains."""

import io

from docx import Document
from docx.shared import Pt, RGBColor

NAVY = RGBColor(0x10, 0x18, 0x28)
BLUE = RGBColor(0x2C, 0x42, 0xB0)
GRAY = RGBColor(0x47, 0x54, 0x67)

SECTION_ORDER = [
    ("summary", "Professional Summary"),
    ("skills", "Core Skills"),
    ("experience", "Professional Experience"),
    ("projects", "Projects"),
    ("education", "Education"),
    ("certifications", "Certifications"),
]

ITEM_FIELDS = {"experience", "projects", "education", "certifications"}


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
    def build_docx(tailored_resume_data):
        document = Document()

        for section in document.sections:
            section.top_margin = Pt(36)
            section.bottom_margin = Pt(36)
            section.left_margin = Pt(50)
            section.right_margin = Pt(50)

        personal = tailored_resume_data.get("personal_information") or {}
        name = personal.get("name") or "Candidate"
        contact_parts = [personal.get("email"), personal.get("phone")]
        contact_line = "  ·  ".join(part for part in contact_parts if part)

        name_paragraph = document.add_paragraph()
        name_run = name_paragraph.add_run(name)
        name_run.bold = True
        name_run.font.size = Pt(20)
        name_run.font.color.rgb = NAVY

        if contact_line:
            contact_paragraph = document.add_paragraph()
            contact_run = contact_paragraph.add_run(contact_line)
            contact_run.font.size = Pt(10)
            contact_run.font.color.rgb = GRAY

        for field, heading in SECTION_ORDER:
            value = tailored_resume_data.get(field)
            if not value:
                continue

            heading_paragraph = document.add_paragraph()
            heading_run = heading_paragraph.add_run(heading.upper())
            heading_run.bold = True
            heading_run.font.size = Pt(11)
            heading_run.font.color.rgb = NAVY

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
                        entry_run.font.color.rgb = NAVY

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
