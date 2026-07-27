from django.shortcuts import get_object_or_404

from resumes.models import Resume


class ResumeViewService:
    """
    Service for retrieving the logged-in user's resume.
    """

    @staticmethod
    def get_resume(user):
        return get_object_or_404(Resume, user=user)