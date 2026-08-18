from django.http import Http404

from resumes.models import Resume, ResumeVersion
from resumes.utils.ats_scorer import ATSScorer


class ResumeEditService:
    """
    Service to update the logged-in user's resume(s). Each resume keeps its
    own independent version history — editing one never touches another.
    """

    @staticmethod
    def update_resume(user, parsed_data):
        # No id given: most recently uploaded resume, kept for callers that
        # pre-date multi-resume support.
        resume = Resume.objects.filter(user=user).order_by("-id").first()

        if not resume:
            raise Http404("No resume found to edit.")

        return ResumeEditService._apply_edit(resume, parsed_data)

    @staticmethod
    def update_resume_by_id(user, resume_id, parsed_data):
        resume = Resume.objects.filter(user=user, id=resume_id).first()

        if not resume:
            raise Http404("No resume found with that id for this user.")

        return ResumeEditService._apply_edit(resume, parsed_data)

    @staticmethod
    def _apply_edit(resume, parsed_data):
        ats_score = ATSScorer.calculate_score(parsed_data)

        resume.parsed_data = parsed_data
        resume.ats_score = ats_score
        resume.save(update_fields=["parsed_data", "ats_score"])

        latest_version = (
            ResumeVersion.objects.filter(resume=resume)
            .order_by("-version")
            .first()
        )
        next_version = 1 if latest_version is None else latest_version.version + 1

        ResumeVersion.objects.create(
            resume=resume,
            version=next_version,
            parsed_data=resume.parsed_data,
            ats_score=resume.ats_score,
        )

        return resume