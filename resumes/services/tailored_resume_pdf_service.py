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

Layout is driven by a resolved template config (see
resumes.resume_template). Passing ``template_config=None`` uses
DEFAULT_TEMPLATE, which reproduces the pre-template output exactly. The PDF
deliberately stays single-column even when the template asks for two - a
two-column PDF is an ATS risk, and the template's ATS report says so.
"""

import io

from reportlab.lib.enums import TA_CENTER, TA_LEFT
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

from resumes.resume_template import (
    DENSITY_SCALE,
    SECTION_HEADINGS,
    resolve_template,
    visible_sections,
)

GRAY = HexColor("#475467")

ITEM_FIELDS = {"experience", "projects", "education", "certifications"}

# sans / serif -> a concrete pair of PDF base-14 faces (always present in
# every reader, nothing embedded, nothing an ATS can fail to extract).
_FONT_MAP = {
    "sans": ("Helvetica", "Helvetica-Bold"),
    "serif": ("Times-Roman", "Times-Bold"),
}


def _styles(config):
    regular, bold = _FONT_MAP.get(config["font_family"], _FONT_MAP["sans"])
    accent = HexColor(config["accent_color"])
    scale = DENSITY_SCALE.get(config["density"], 1.0)
    name_align = TA_CENTER if config["name_alignment"] == "center" else TA_LEFT

    def sp(value):
        return round(value * scale, 1)

    return {
        "name": ParagraphStyle(
            "Name", fontName=bold, fontSize=20, leading=24,
            textColor=accent, alignment=name_align, spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "Contact", fontName=regular, fontSize=9.5, leading=13,
            textColor=GRAY, alignment=name_align, spaceAfter=sp(10),
        ),
        "heading": ParagraphStyle(
            "Heading", fontName=bold, fontSize=11, leading=14,
            textColor=accent, spaceBefore=sp(10), spaceAfter=sp(4),
        ),
        "body": ParagraphStyle(
            "Body", fontName=regular, fontSize=10, leading=14,
            textColor=HexColor("#101828"), spaceAfter=sp(2),
        ),
        "entry_header": ParagraphStyle(
            "EntryHeader", fontName=bold, fontSize=10.5, leading=14,
            textColor=accent, spaceBefore=sp(6), spaceAfter=sp(2),
        ),
    }


def _heading_text(key, config):
    label = SECTION_HEADINGS.get(key, key.title())
    return label.upper() if config["heading_style"] == "caps" else label


def _clean(value):
    """Escape for ReportLab's mini-markup.

    Resume text legitimately contains & and < (e.g. "R&D", "<5% churn"),
    which would otherwise raise a parse error or silently truncate the line.
    """
    return escape(str(value or "").strip())


def _entry_parts(item):
    """Split one experience/project/education entry into its heading line and
    its bullets.

    Newer curations carry `header` and `bullets` directly. Older ones - and
    any generation where the model skipped the split - only have the flat
    `tailored_text`, which is rendered as a single bullet with no heading
    rather than being dropped. Existing users' saved resumes therefore keep
    working unchanged instead of rendering blank.
    """
    if not isinstance(item, dict):
        return "", [str(item)] if item else []

    header = str(item.get("header") or "").strip()
    bullets = [str(b).strip() for b in (item.get("bullets") or []) if str(b).strip()]

    if not header and not bullets:
        flat = str(item.get("tailored_text") or "").strip()
        return "", ([flat] if flat else [])

    return header, bullets


class TailoredResumePdfService:
    """Builds a text-based PDF from a curation's tailored_resume_data."""

    @staticmethod
    def build_pdf(tailored_resume_data, template_config=None):
        config = template_config or resolve_template(None, None)
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

        styles = _styles(config)
        story = []

        personal = tailored_resume_data.get("personal_information") or {}
        name = personal.get("name") or "Candidate"
        story.append(Paragraph(_clean(name), styles["name"]))

        contact_parts = [personal.get("email"), personal.get("phone")]
        contact_line = "  ·  ".join(part for part in contact_parts if part)
        if contact_line:
            story.append(Paragraph(_clean(contact_line), styles["contact"]))

        for field in visible_sections(config):
            value = tailored_resume_data.get(field)
            if not value:
                continue

            story.append(Paragraph(_heading_text(field, config), styles["heading"]))

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
                for item in value:
                    header, bullets = _entry_parts(item)
                    if not header and not bullets:
                        continue

                    if header:
                        # The role/company/date line reads as a heading, not
                        # as one more bullet - which is how a human skims a
                        # resume and how the section structure stays legible.
                        story.append(Paragraph(_clean(header), styles["entry_header"]))

                    if bullets:
                        story.append(
                            ListFlowable(
                                [
                                    ListItem(
                                        Paragraph(_clean(b), styles["body"]), leftIndent=10
                                    )
                                    for b in bullets
                                ],
                                bulletType="bullet",
                                start="•",
                                leftIndent=12,
                                spaceAfter=4,
                            )
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
