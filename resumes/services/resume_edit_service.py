from django.http import Http404

from resumes.models import Resume, ResumeVersion
from resumes.utils.ats_scorer import ATSScorer


class ResumeEditService:
    """
    Service to update the logged-in user's resume.
    """

    @staticmethod
    def update_resume(user, parsed_data):
        resume = Resume.objects.filter(user=user).order_by("-id").first()

        if not resume:
            raise Http404("No resume found to edit.")

        # Calculate ATS score
        ats_score = ATSScorer.calculate_score(
            parsed_data
        )

        # Update the latest resume
        resume.parsed_data = parsed_data
        resume.ats_score = ats_score

        resume.save(
            update_fields=[
                "parsed_data",
                "ats_score",
            ]
        )

        # Get the latest version
        latest_version = (
            ResumeVersion.objects.filter(resume=resume)
            .order_by("-version")
            .first()
        )

        # Calculate next version number
        next_version = (
            1 if latest_version is None
            else latest_version.version + 1
        )

        # Save the updated resume as a new version
        ResumeVersion.objects.create(
            resume=resume,
            version=next_version,
            parsed_data=resume.parsed_data,
            ats_score=resume.ats_score,
        )

        return resume