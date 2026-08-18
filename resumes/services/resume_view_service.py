from django.http import Http404
from resumes.models import Resume

class ResumeViewService:
    """
    Service for retrieving the logged-in user's resume(s).
    """

    @staticmethod
    def get_resume(user):
        # No id given: the most recently uploaded resume, for callers that
        # only ever cared about "do you have a usable resume" (dashboard
        # stats, job-curation readiness) and pre-date multi-resume support.
        resume = Resume.objects.filter(user=user).order_by("-id").first()

        if not resume:
            raise Http404("No resume found for this user.")

        return resume

    @staticmethod
    def get_resume_by_id(user, resume_id):
        resume = Resume.objects.filter(user=user, id=resume_id).first()

        if not resume:
            raise Http404("No resume found with that id for this user.")

        return resume