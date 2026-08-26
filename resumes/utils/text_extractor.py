from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


class TextExtractor:
    """
    Utility class for extracting text from resume files.

    Extraction quality is not a detail here - everything downstream (AI
    parsing, ATS scoring, job matching) only ever sees this output. Text
    dropped at this stage doesn't degrade the result, it silently
    fabricates a worse candidate: the score then reports "no skills
    listed" about a resume that listed twenty.
    """

    @staticmethod
    def extract_text(file):
        """
        Extract text based on the uploaded file type.
        """

        extension = Path(file.name).suffix.lower()

        if extension == ".pdf":
            return TextExtractor._extract_pdf(file)

        if extension == ".docx":
            return TextExtractor._extract_docx(file)

        raise ValueError(
            "Unsupported file type."
        )

    @staticmethod
    def _extract_pdf(file):
        """
        Extract text from a PDF file.
        """

        reader = PdfReader(file)

        text = ""

        for page in reader.pages:
            page_text = page.extract_text()

            if page_text:
                text += page_text + "\n"

        return text.strip()

    @staticmethod
    def _extract_docx(file):
        """
        Extract text from a DOCX file, including table cells and
        headers/footers.

        Walking only ``document.paragraphs`` (the obvious approach) silently
        drops every table cell, and resume templates lean on tables
        constantly - two-column skill grids, contact blocks, whole-page
        layout tables. A table-based resume would lose its email, phone,
        skills and achievements while still "succeeding", so the AI parsed a
        gutted document and the ATS score confidently described a resume
        that was never actually read.
        """

        document = Document(file)

        parts = []

        # Headers and footers are not part of the body flow, so they need
        # collecting separately. Contact details live there more often than
        # you would expect - and an ATS that misses them treats the
        # candidate as having no contact information at all.
        for section in document.sections:
            for container in (section.header, section.footer):
                if container is None:
                    continue
                parts.extend(TextExtractor._collect_container_text(container))

        parts.extend(TextExtractor._collect_block_text(document))

        # Deduplicate consecutive repeats: the same header text repeats per
        # section, which would otherwise inflate keyword counts.
        deduped = []
        for line in parts:
            if not deduped or deduped[-1] != line:
                deduped.append(line)

        return "\n".join(deduped).strip()

    @staticmethod
    def _iter_block_items(parent):
        """
        Yield paragraphs and tables in true document order.

        ``document.paragraphs`` and ``document.tables`` are separate flat
        lists, so using them loses the interleaving - a skills table between
        two sections would be appended at the end, out of context. Walking
        the XML body preserves the reading order the candidate actually
        wrote.
        """

        if isinstance(parent, DocxDocument):
            parent_element = parent.element.body
        elif isinstance(parent, _Cell):
            parent_element = parent._tc
        else:
            return

        for child in parent_element.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    @classmethod
    def _collect_block_text(cls, parent):
        """Recursively collect text from paragraphs and tables (including
        tables nested inside table cells)."""

        lines = []

        for block in cls._iter_block_items(parent):
            if isinstance(block, Paragraph):
                if block.text and block.text.strip():
                    lines.append(block.text.strip())
            elif isinstance(block, Table):
                for row in block.rows:
                    for cell in row.cells:
                        lines.extend(cls._collect_block_text(cell))

        return lines

    @classmethod
    def _collect_container_text(cls, container):
        """Header/footer containers expose paragraphs and tables but are not
        walkable as a document body, so they are handled directly."""

        lines = []

        for paragraph in getattr(container, "paragraphs", []) or []:
            if paragraph.text and paragraph.text.strip():
                lines.append(paragraph.text.strip())

        for table in getattr(container, "tables", []) or []:
            for row in table.rows:
                for cell in row.cells:
                    lines.extend(cls._collect_block_text(cell))

        return lines
