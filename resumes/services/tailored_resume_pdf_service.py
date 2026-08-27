"""Renders a job-tailored resume into a real, text-based PDF.

Why this exists rather than letting the browser print to PDF: the output of
`window.print()` depends entirely on which "printer" the user happens to
pick. On Windows, "Microsoft Print to PDF" rasterises the page into images,
producing a PDF with no text layer at all - which an applicant tracking
system cannot read a single word of. For a product whose entire promise is
ATS-readability, shipping a resume an ATS cannot parse is the worst
possible failure, and it is silent: the file opens and looks perfect.

Generating it server-side removes the user's print dialog, their browser
and their OS from the equation. The output is always the same, and it
always has a real text layer - which resumes/tests_pdf_export.py verifies
by extracting the text back out of the generated file.

Like the .docx exporter, this never generates or infers content. It only
formats exactly what tailored_resume_data already contains.
"""

import io

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)
from xml.sax.saxutils import escape

NAVY = HexColor("#101828")
GRAY = HexColor("#475467")

SECTION_ORDER = [
    ("summary", "Professional Summary"),
    ("skills", "Core Skills"),
    ("experience", "Professional Experience"),
    ("projects", "Projects"),
    ("education", "Education"),
    ("certifications", "Certifications"),
]

ITEM_FIELDS = {"experience", "projects", "education", "certifications"}


def _styles():
    """Standard fonts only (Helvetica is one of the PDF base-14).

    Embedding an exotic font risks a reader falling back to glyph outlines,
    which is another route to text an ATS cannot extract. Plain, boring and
    parseable is the entire point here.
    """
    return {
        "name": ParagraphStyle(
            "Name", fontName="Helvetica-Bold", fontSize=20, leading=24,
            textColor=NAVY, alignment=TA_LEFT, spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "Contact", fontName="Helvetica", fontSize=9.5, leading=13,
            textColor=GRAY, spaceAfter=10,
        ),
        "heading": ParagraphStyle(
            "Heading", fontName="Helvetica-Bold", fontSize=11, leading=14,
            textColor=NAVY, spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body", fontName="Helvetica", fontSize=10, leading=14,
            textColor=NAVY, spaceAfter=2,
        ),
    }


def _clean(value):
    """Escape for ReportLab's mini-markup.

    Resume text legitimately contains & and < (e.g. "R&D", "<5% churn"),
    which would otherwise raise a parse error or silently truncate the line.
    """
    return escape(str(value or "").strip())


class TailoredResumePdfService:
    """Builds a text-based PDF from a curation's tailored_resume_data."""

    @staticmethod
    def build_pdf(tailored_resume_data):
        buffer = io.BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            topMargin=14 * mm,
            bottomMargin=14 * mm,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            title="Tailored Resume",
        )

        styles = _styles()
        story = []

        personal = tailored_resume_data.get("personal_information") or {}
        name = personal.get("name") or "Candidate"
        story.append(Paragraph(_clean(name), styles["name"]))

        contact_parts = [personal.get("email"), personal.get("phone")]
        contact_line = "  ·  ".join(part for part in contact_parts if part)
        if contact_line:
            story.append(Paragraph(_clean(contact_line), styles["contact"]))

        for field, heading in SECTION_ORDER:
            value = tailored_resume_data.get(field)
            if not value:
                continue

            story.append(Paragraph(heading.upper(), styles["heading"]))

            if field == "summary":
                story.append(Paragraph(_clean(value), styles["body"]))
            elif field == "skills":
                # Comma-separated rather than bulleted: skills are the single
                # most keyword-dense line on a resume, and a plain delimited
                # list is what parsers split on most reliably.
                story.append(
                    Paragraph(_clean(", ".join(str(s) for s in value)), styles["body"])
                )
            elif field in ITEM_FIELDS:
                items = []
                for item in value:
                    text = item.get("tailored_text") if isinstance(item, dict) else item
                    if not text:
                        continue
                    items.append(
                        ListItem(Paragraph(_clean(text), styles["body"]), leftIndent=10)
                    )
                if items:
                    story.append(
                        ListFlowable(items, bulletType="bullet", start="•", leftIndent=12)
                    )

        if len(story) <= 1:
            # Nothing but a name would produce a near-empty PDF that reads as
            # a broken download rather than an empty resume.
            story.append(Spacer(1, 4))

        document.build(story)
        buffer.seek(0)
        return buffer

    @staticmethod
    def build_filename(job_title):
        safe_title = "".join(
            char if char.isalnum() or char in (" ", "-", "_") else ""
            for char in (job_title or "resume")
        ).strip().replace(" ", "_")
        return f"tailored_resume_{safe_title or 'resume'}.pdf"
