import logging

from django.core.files.storage import default_storage
from django.http import Http404

from resumes.models import Resume

logger = logging.getLogger(__name__)


class ResumeDeleteService:
    """Service to permanently delete one of the logged-in user's resumes.

    Deleting the row cascades to its ResumeVersion rows automatically (see
    ResumeVersion.resume's on_delete=CASCADE) — nothing else references a
    Resume directly (job curations store a data snapshot, not a live FK),
    so this never touches unrelated data.
    """

    @staticmethod
    def delete_resume(user, resume_id):
        resume = Resume.objects.filter(user=user, id=resume_id).first()

        if not resume:
            raise Http404("No resume found with that id for this user.")

        file_name = resume.file.name if resume.file else None
        resume.delete()

        if file_name:
            try:
                default_storage.delete(file_name)
            except PermissionError:
                logger.warning("Deleted resume's file is currently in use and could not be removed from storage.")
