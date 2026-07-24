import logging
from resumes.models import Resume
from resumes.utils.text_extractor import TextExtractor
from resumes.utils.ats_scorer import ATSScorer

logger = logging.getLogger(__name__)

class ResumeUploadService:
    """
    Service to handle resume upload.
    """

    @staticmethod
    def upload_resume(user, validated_data):
        uploaded_file = validated_data["file"]

        resume = Resume.objects.create(
            user=user,
            file=uploaded_file,
            original_filename=uploaded_file.name,
            status="processing",
        )

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

        except Exception:
            logger.exception(
                "Failed to extract text from uploaded resume."
            )

            resume.status = "failed"

        resume.save(
            update_fields=[
                "parsed_text",
                "ats_score",
                "status",
            ]
        )

        return resume