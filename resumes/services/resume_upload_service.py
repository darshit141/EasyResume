import logging

from django.core.files.storage import default_storage

from resumes.models import Resume
from resumes.utils.ats_scorer import ATSScorer
from resumes.utils.resume_parser import ResumeParser
from resumes.utils.text_extractor import TextExtractor

logger = logging.getLogger(__name__)


class ResumeUploadService:
    """
    Service to handle resume upload.
    """

    @staticmethod
    def upload_resume(user, validated_data):
        uploaded_file = validated_data["file"]

        # Fetch latest resume for update
        resume = Resume.objects.filter(user=user).order_by("-id").first()
        old_file_name = None

        if resume:
            if resume.file:
                old_file_name = resume.file.name

            resume.file = uploaded_file
            resume.original_filename = uploaded_file.name
            resume.status = "processing"
            resume.parsed_data = {}
            resume.ats_score = None

        else:
            resume = Resume(
                user=user,
                file=uploaded_file,
                original_filename=uploaded_file.name,
                status="processing",
                parsed_data={},
            )

        resume.save()

        try:
            # Step 1: Extract resume text
            extracted_text = TextExtractor.extract_text(
                resume.file
            )
            
            # Step 2: Convert extracted text into structured JSON
            parsed_data = ResumeParser.parse(
                extracted_text
            )

            # Step 3: Calculate ATS score using structured JSON
            ats_score = ATSScorer.calculate_score(
                parsed_data
            )

            # Step 4: Save parsed data and ATS score
            resume.parsed_data = parsed_data
            resume.ats_score = ats_score
            resume.status = "completed"

        except Exception:
            logger.exception(
                "Failed to process uploaded resume."
            )
            resume.status = "failed"

        resume.save(
            update_fields=[
                "file",
                "original_filename",
                "parsed_data",
                "ats_score",
                "status",
            ]
        )

        if old_file_name:
            try:
                default_storage.delete(old_file_name)
            except PermissionError:
                logger.warning(
                    "Old resume file is currently in use and could not be deleted."
                )

        return resume