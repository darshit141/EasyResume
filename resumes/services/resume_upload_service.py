import logging

from resumes.models import Resume
from resumes.utils.ats_scorer import ATSScorer
from resumes.utils.text_extractor import TextExtractor
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


class ResumeUploadService:
    """
    Service to handle resume upload.
    """

    @staticmethod
    def upload_resume(user, validated_data):
        uploaded_file = validated_data["file"]

        resume = Resume.objects.filter(user=user).first()
        old_file_name = None

        if resume:
            if resume.file:
                old_file_name = resume.file.name

            resume.file = uploaded_file
            resume.original_filename = uploaded_file.name
            resume.status = "processing"
            resume.parsed_text = ""
            resume.ats_score = None

        else:
            resume = Resume(
                user=user,
                file=uploaded_file,
                original_filename=uploaded_file.name,
                status="processing",
            )

        resume.save()

        try:
            extracted_text = TextExtractor.extract_text(
                resume.file
            )

            ats_score = ATSScorer.calculate_score(
                extracted_text
            )

            resume.parsed_text = extracted_text
            resume.ats_score = ats_score
            resume.status = "completed"

        except Exception:
            logger.exception(
                "Failed to extract text from uploaded resume."
            )
            resume.status = "failed"

        resume.save(
            update_fields=[
                "file",
                "original_filename",
                "parsed_text",
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