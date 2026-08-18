from resumes.models import Resume


class ResumeListService:
    """Service to list all of the logged-in user's resumes."""

    @staticmethod
    def list_resumes(user):
        return Resume.objects.filter(user=user).order_by("-created_at")
