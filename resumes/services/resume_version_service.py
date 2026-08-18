from django.http import Http404

from resumes.models import Resume


class ResumeVersionService:
    """
    Service to retrieve version history for one of the logged-in user's
    resumes.
    """

    @staticmethod
    def get_versions(user, resume_id=None):
        if resume_id is None:
            # No id given: most recently uploaded resume, kept for callers
            # that pre-date multi-resume support.
            resume = Resume.objects.filter(user=user).order_by("-id").first()
        else:
            resume = Resume.objects.filter(user=user, id=resume_id).first()

        if not resume:
            raise Http404(
                "No resume found for this user."
            )

        return resume.versions.all().order_by("-version")