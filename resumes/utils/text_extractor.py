from pathlib import Path

from docx import Document
from pypdf import PdfReader


class TextExtractor:
    """
    Utility class for extracting text from resume files.
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
        Extract text from a DOCX file.
        """

        document = Document(file)

        text = ""

        for paragraph in document.paragraphs:
            if paragraph.text:
                text += paragraph.text + "\n"

        return text.strip()