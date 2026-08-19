import logging

from django.conf import settings
from django.db import transaction
from api.utils.config import get_config_value


from resumes.models import Resume, ResumeVersion
from resumes.services.llm_service import LLMServiceError
from resumes.utils.ats_scorer import ATSScorer
from resumes.utils.resume_parser import ResumeParser
from resumes.utils.text_extractor import TextExtractor

logger = logging.getLogger(__name__)


class ResumeLimitReachedError(Exception):
    """Raised before any AI call when the user is already at
    settings.MAX_RESUMES_PER_USER — delete one first, matching the same
    "never silently destroy data" rule the rest of this service follows."""

    code = "RESUME_LIMIT_REACHED"

    def __init__(self, limit):
        self.limit = limit
        self.message = (
            f"You've reached your limit of {limit} resumes. Delete one from "
            f"\"My Resumes\" before uploading another."
        )
        super().__init__(self.message)

# Below this much extracted text there is nothing worth sending to an AI
# model. Set low deliberately: the goal is to catch scanned/image PDFs that
# yield nothing at all, not to judge how short a legitimate resume may be.
_MIN_EXTRACTED_CHARS = 50


class ResumeTextExtractionError(Exception):
    """The uploaded file's text could not be extracted (unsupported/corrupt
    file). Distinct from LLMServiceError: this is never an AI-service
    problem, so it is never mapped to a 502/503."""

    code = "RESUME_TEXT_EXTRACTION_FAILED"

    # The generic version of this message ("could not read this file") left
    # users with no idea what to change. By far the most common real cause is
    # a PDF that is a scan or export of an image, which has no extractable
    # text layer at all, so the message now names that case directly.
    DEFAULT_MESSAGE = (
        "We couldn't read any text from this file. If it's a scanned or "
        "image-based PDF, export or save it as a text PDF (or upload a DOCX) "
        "and try again."
    )

    def __init__(self, message=DEFAULT_MESSAGE):
        self.message = message
        super().__init__(message)


class ResumeUploadService:
    """
    Service to handle resume upload. Each user may keep up to
    settings.MAX_RESUMES_PER_USER resumes at once, each a fully independent
    row with its own file, parsed data, and version history — uploading
    never replaces or touches an existing resume.
    """

    @staticmethod
    def check_can_upload(user):
        """Pure quota check, run before any text extraction or AI call —
        matching the same "check first, never do doomed work" rule already
        used for every other quota in this codebase."""
        limit = get_config_value("MAX_RESUMES_PER_USER", settings.MAX_RESUMES_PER_USER)
        if Resume.objects.filter(user=user).count() >= limit:
            raise ResumeLimitReachedError(limit)

    @staticmethod
    def upload_resume(user, validated_data, provider=None):
        uploaded_file = validated_data["file"]

        try:
            extracted_text = TextExtractor.extract_text(uploaded_file)
        except LLMServiceError:
            raise
        except Exception as exc:
            logger.exception("Failed to extract text from uploaded resume.")
            raise ResumeTextExtractionError() from exc

        # A scanned/image PDF raises nothing — it simply yields empty text.
        # Without this guard that empty string went on to the AI parser,
        # which burned a full generation only to return an empty resume that
        # then scored 0, with nothing anywhere telling the user why.
        if len(extracted_text.strip()) < _MIN_EXTRACTED_CHARS:
            logger.info(
                "Rejected upload: extracted only %d characters of text.",
                len(extracted_text.strip()),
            )
            raise ResumeTextExtractionError()

        # LLMServiceError subclasses (timeout, model unavailable, schema
        # validation, empty response) propagate to the view unchanged, where
        # they're mapped to the same 502/503 responses used everywhere else
        # AI-backed calls can fail.
        parsed_data = ResumeParser.parse(extracted_text, provider=provider)
        ats_score = ATSScorer.calculate_score(parsed_data)

        # Only now, with a fully successful parse in hand, touch the
        # database — so a mid-request crash can't leave a half-written row.
        with transaction.atomic():
            resume = Resume.objects.create(
                user=user,
                file=uploaded_file,
                original_filename=uploaded_file.name,
                parsed_data=parsed_data,
                ats_score=ats_score,
                status="completed",
            )
            ResumeVersion.objects.create(
                resume=resume,
                version=1,
                parsed_data=resume.parsed_data,
                ats_score=resume.ats_score,
            )

        return resume
