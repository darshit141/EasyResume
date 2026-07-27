from django.shortcuts import get_object_or_404

from resumes.models import Resume
from resumes.utils.ats_scorer import ATSScorer


class ResumeEditService:
    @staticmethod
    def update_resume(user, parsed_text):
        resume = get_object_or_404(Resume, user=user)

        ats_score = ATSScorer.calculate_score(parsed_text)

        resume.parsed_text = parsed_text
        resume.ats_score = ats_score

        resume.save(update_fields=["parsed_text", "ats_score"])

        return resume