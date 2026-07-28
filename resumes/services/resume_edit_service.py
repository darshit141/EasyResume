from django.http import Http404
from resumes.models import Resume
from resumes.utils.ats_scorer import ATSScorer

class ResumeEditService:
    @staticmethod
    def update_resume(user, parsed_text):
        # get_object_or_404 ki jagah latest single instance fetch karein
        resume = Resume.objects.filter(user=user).order_by("-id").first()

        if not resume:
            raise Http404("No resume found to edit.")

        ats_score = ATSScorer.calculate_score(parsed_text)

        resume.parsed_text = parsed_text
        resume.ats_score = ats_score

        resume.save(update_fields=["parsed_text", "ats_score"])

        return resume