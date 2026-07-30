from django.http import Http404

from resumes.models import Resume
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

        ats_score = ATSScorer.calculate_score(
            parsed_data
        )

        resume.parsed_data = parsed_data
        resume.ats_score = ats_score

        resume.save(
            update_fields=[
                "parsed_data",
                "ats_score",
            ]
        )

        return resume