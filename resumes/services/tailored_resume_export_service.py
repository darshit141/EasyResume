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
                    text = item.get("tailored_text") if isinstance(item, dict) else item
                    if not text:
                        continue
                    item_paragraph = document.add_paragraph(style="List Bullet")
                    item_run = item_paragraph.add_run(text)
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
