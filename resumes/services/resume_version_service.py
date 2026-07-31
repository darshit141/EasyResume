from django.http import Http404

from resumes.models import Resume


class ResumeVersionService:
    """
    Service to retrieve all versions of the logged-in user's resume.
    """

    @staticmethod
    def get_versions(user):
        resume = Resume.objects.filter(
            user=user
        ).order_by("-id").first()

        if not resume:
            raise Http404(
                "No resume found for this user."
            )

        return resume.versions.all().order_by("-version")