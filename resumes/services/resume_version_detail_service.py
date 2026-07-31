from django.http import Http404

from resumes.models import ResumeVersion


class ResumeVersionDetailService:
    """
    Service to retrieve a specific resume version
    for the logged-in user.
    """

    @staticmethod
    def get_version(user, version_id):
        version = (
            ResumeVersion.objects
            .filter(
                id=version_id,
                resume__user=user,
            )
            .first()
        )

        if not version:
            raise Http404(
                "Resume version not found."
            )

        return version