from django.http import Http404

from resumes.models import JobResumeCurationVersion


class TailoredResumeVersionService:
    """
    Retrieves tailored-resume version history for a job curation, always
    scoped to the requesting user so versions are never exposed cross-user.
    """

    @staticmethod
    def get_versions(user, curation_id):
        return JobResumeCurationVersion.objects.filter(
            curation_id=curation_id,
            curation__user=user,
        ).order_by("-version")

    @staticmethod
    def get_version(user, curation_id, version_id):
        version = JobResumeCurationVersion.objects.filter(
            id=version_id,
            curation_id=curation_id,
            curation__user=user,
        ).first()

        if not version:
            raise Http404("Tailored resume version not found.")

        return version
