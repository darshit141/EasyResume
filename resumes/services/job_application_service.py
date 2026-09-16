"""Drafting, reviewing and sending outbound job applications.

The whole module is built around one rule: **nothing is ever sent without an
explicit, separate act by the user.** Drafting creates a row the user can
read and rewrite; only `send()` puts anything on the wire, and it is reached
only from an endpoint the user hits deliberately. There is no scheduler, no
"send when ready" flag, and no bulk path — one application, one send, one
recipient. That is a product requirement, not a technical accident: this
feature writes to strangers under the user's name.

Two things are deliberately *not* automated:

* We never guess the recipient address. Scraping a "careers@" out of a job
  page and mailing it unattended is how a helpful tool becomes a spam
  cannon, and a wrong guess sends someone's real application into a void.
  The user supplies the address.
* We never mark an application as replied-to on our own. Detecting replies
  would mean reading the user's mailbox, which under Google's scope rules
  is a *restricted* scope (`gmail.readonly`) carrying an annual third-party
  security assessment. Until that is worth paying for, replies go straight
  to the candidate's own inbox via `Reply-To` and the tracker status stays
  user-driven, which is honest rather than half-working.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import DatabaseError, transaction
from django.utils import timezone

from billing.services.entitlements_service import EntitlementsService
from resumes.models import JobApplication, JobResumeCuration, Resume
from resumes.services.autofill_resume_file_service import content_type_for
from resumes.services.job_application_ai_service import JobApplicationAIService
from resumes.services.job_curation_service import JobCurationDatabaseError
from resumes.services.job_gap_analysis_service import (
    JobGapAnalysisService,
    JobRequirementsUnavailableError,
)
from resumes.services.job_resume_curation_service import JobResumeCurationService
from resumes.services.resume_template_service import ResumeTemplateService
from resumes.services.tailored_resume_pdf_service import TailoredResumePdfService

logger = logging.getLogger(__name__)


def _master_resume(user, resume_id=None):
    """The account's verified master resume: the one this job was curated
    against when there is one, otherwise the most recent."""
    if resume_id:
        chosen = Resume.objects.filter(id=resume_id, user=user, status="completed").first()
        if chosen:
            return chosen
    return (
        Resume.objects.filter(user=user, status="completed")
        .order_by("-updated_at", "-id")
        .first()
    )


class ApplicationNotSendableError(Exception):
    """The application exists but is not in a state that can be sent."""

    code = "APPLICATION_NOT_SENDABLE"

    def __init__(self, message, code=None):
        self.message = message
        if code:
            self.code = code
        super().__init__(message)


class ApplicationDeliveryError(Exception):
    """The mail provider refused or failed to deliver the message."""

    code = "APPLICATION_DELIVERY_FAILED"
    message = "We couldn't deliver this application. Nothing was sent — please try again."


class JobApplicationService:
    def __init__(self, ai_service=None, provider=None):
        self.ai_service = ai_service or JobApplicationAIService(provider=provider)

    # -- drafting ---------------------------------------------------------

    def create_draft(
        self,
        user,
        curation_id,
        recipient_email,
        recipient_name="",
        company_name="",
        include_resume=True,
        include_cover_letter=False,
        resume_source=None,
    ):
        """Draft an application email for a curation. Sends nothing."""
        curation = JobResumeCuration.objects.get(id=curation_id, user=user)
        resume_source = self._resolve_resume_source(user, curation, resume_source, include_resume)

        candidate_source_data = JobResumeCurationService.build_candidate_source_data(
            user, resume_id=curation.resume_id
        )
        try:
            job_requirements = JobGapAnalysisService.build_job_requirements(curation.analysis_data)
        except JobRequirementsUnavailableError:
            # A job can be applied to without ever being analysed. The
            # requirements only help the draft decide what to lead with;
            # every claim in it is grounded in the resume either way.
            job_requirements = []

        # Resolved before drafting so the email can describe what is really
        # being sent rather than guessing.
        will_include_cover_letter = include_cover_letter and bool(curation.cover_letter_data)

        logger.info(
            "Application draft started user_id=%s curation_id=%s", user.id, curation_id
        )
        drafted = self.ai_service.draft_email(
            candidate_source_data=candidate_source_data,
            job_title=curation.job_title,
            job_description=curation.job_description,
            job_requirements=job_requirements,
            company_name=company_name,
            recipient_name=recipient_name,
            include_resume=include_resume,
            include_cover_letter=will_include_cover_letter,
        )

        try:
            application = JobApplication.objects.create(
                user=user,
                curation=curation,
                recipient_email=recipient_email,
                recipient_name=recipient_name or "",
                company_name=company_name or "",
                subject=drafted["subject"],
                body=drafted["body"],
                include_resume=include_resume,
                include_cover_letter=will_include_cover_letter,
                resume_source=resume_source,
                status=JobApplication.STATUS_DRAFT,
            )
        except DatabaseError as exc:
            logger.exception("Failed to save application draft.")
            raise JobCurationDatabaseError() from exc

        logger.info(
            "Application draft created user_id=%s application_id=%s", user.id, application.id
        )
        return application

    @staticmethod
    def _resolve_resume_source(user, curation, requested, include_resume):
        """Which resume to attach, defaulting to whichever actually exists.

        Tailoring is optional, so a missing tailored resume is only an error
        when the user explicitly asked to send that one.
        """
        has_tailored = bool(curation.tailored_resume_data)
        source = requested or (
            JobApplication.RESUME_SOURCE_TAILORED
            if has_tailored
            else JobApplication.RESUME_SOURCE_MASTER
        )

        if source == JobApplication.RESUME_SOURCE_TAILORED and not has_tailored:
            raise ApplicationNotSendableError(
                "There is no tailored resume for this job yet. Generate one, or apply "
                "with your master resume instead.",
                code="TAILORED_RESUME_REQUIRED",
            )
        if (
            source == JobApplication.RESUME_SOURCE_MASTER
            and include_resume
            and _master_resume(user, curation.resume_id) is None
        ):
            raise ApplicationNotSendableError(
                "Upload or build a resume before applying, so there is something to attach.",
                code="MASTER_RESUME_REQUIRED",
            )
        return source

    # -- sending ----------------------------------------------------------

    @staticmethod
    def send(user, application_id):
        """Deliver a reviewed draft. The only method in the product that
        puts user-authored content in front of a third party."""
        application = JobApplication.objects.select_related("curation").get(
            id=application_id, user=user
        )

        if application.status == JobApplication.STATUS_SENT:
            # Idempotent rather than an error: a double-clicked Send button
            # must never mail an employer the same application twice.
            return application

        EntitlementsService.check_can_send_application(user)

        curation = application.curation

        body = application.body
        if application.include_cover_letter and curation.cover_letter_data:
            body = f"{body}\n\n---\n\n{_render_cover_letter(curation.cover_letter_data)}"

        message = EmailMessage(
            subject=application.subject,
            body=body,
            # Its own sender identity, so a spam complaint about an
            # application can't take login codes down with it. See
            # APPLICATION_FROM_EMAIL in settings.
            from_email=settings.APPLICATION_FROM_EMAIL,
            to=[application.recipient_email],
            # The employer must be able to reply to the candidate, not to us.
            # Without this the entire conversation would dead-end in a
            # no-reply mailbox and the user would never hear back.
            reply_to=[user.email],
        )

        filename = ""
        if application.include_resume:
            filename, content, content_type = JobApplicationService._build_attachment(
                user, application, curation
            )
            message.attach(filename, content, content_type)

        try:
            # fail_silently=False on purpose: telling someone their
            # application was sent when it wasn't is the worst possible
            # failure mode for this feature.
            message.send(fail_silently=False)
        except Exception as exc:
            logger.exception(
                "Application delivery failed user_id=%s application_id=%s",
                user.id, application.id,
            )
            JobApplicationService._mark_failed(application)
            raise ApplicationDeliveryError() from exc

        return JobApplicationService._mark_sent(application, filename)

    @staticmethod
    def _build_attachment(user, application, curation):
        """``(filename, bytes, content_type)`` for the resume being sent."""
        if application.resume_source == JobApplication.RESUME_SOURCE_TAILORED:
            if not curation.tailored_resume_data:
                raise ApplicationNotSendableError(
                    "The tailored resume for this job is missing, so there is "
                    "nothing to attach.",
                    code="TAILORED_RESUME_REQUIRED",
                )
            pdf = TailoredResumePdfService.build_pdf(
                curation.tailored_resume_data,
                template_config=ResumeTemplateService.config_for_version(
                    curation.tailored_resume_versions.order_by("-version").first(),
                    user=user,
                ),
            )
            return (
                TailoredResumePdfService.build_filename(curation.job_title),
                pdf.getvalue(),
                "application/pdf",
            )

        resume = _master_resume(user, curation.resume_id)
        if resume is None:
            raise ApplicationNotSendableError(
                "There is no resume on this account to attach.",
                code="MASTER_RESUME_REQUIRED",
            )

        # The uploaded file is what the candidate actually wrote and what an
        # employer expects to receive, so it wins. A resume built in the
        # guided builder has no file, so its verified profile is rendered
        # instead - through the same renderer the tailored PDF uses.
        if resume.file:
            try:
                with resume.file.open("rb") as handle:
                    content = handle.read()
                name = (resume.original_filename or "").strip() or "resume.pdf"
                return name, content, content_type_for(name)
            except (OSError, ValueError):
                logger.warning(
                    "Master resume file unreadable for user_id=%s; sending the rendered profile.",
                    user.id,
                )

        if not resume.parsed_data:
            raise ApplicationNotSendableError(
                "There is no resume on this account to attach.",
                code="MASTER_RESUME_REQUIRED",
            )
        pdf = TailoredResumePdfService.build_pdf(
            resume.parsed_data,
            template_config=ResumeTemplateService.config_for_version(None, user=user),
        )
        return "resume.pdf", pdf.getvalue(), "application/pdf"

    @staticmethod
    def _mark_sent(application, filename):
        now = timezone.now()
        try:
            with transaction.atomic():
                application.status = JobApplication.STATUS_SENT
                application.sent_at = now
                application.error_code = ""
                application.attachment_filename = filename
                application.save(
                    update_fields=[
                        "status", "sent_at", "error_code", "attachment_filename", "updated_at",
                    ]
                )

                # Keep the tracker honest without overwriting the user's own
                # record: if they had already moved this job past "saved"
                # themselves, their status wins.
                curation = application.curation
                if curation.application_status == JobResumeCuration.APPLICATION_STATUS_SAVED:
                    curation.application_status = JobResumeCuration.APPLICATION_STATUS_APPLIED
                    curation.applied_at = now.date()
                    curation.save(
                        update_fields=["application_status", "applied_at", "updated_at"]
                    )
        except DatabaseError as exc:
            # The mail is already gone; losing the record of it would be
            # worse than failing loudly here.
            logger.exception(
                "Application was delivered but its record could not be saved "
                "application_id=%s", application.id,
            )
            raise JobCurationDatabaseError() from exc

        logger.info(
            "Application sent user_id=%s application_id=%s", application.user_id, application.id
        )
        return application

    @staticmethod
    def _mark_failed(application):
        try:
            application.status = JobApplication.STATUS_FAILED
            application.error_code = ApplicationDeliveryError.code
            application.save(update_fields=["status", "error_code", "updated_at"])
        except DatabaseError:
            logger.exception("Failed to persist application failure state.")


def _render_cover_letter(cover_letter_data):
    """Flatten stored cover letter JSON into plain text for the email body."""
    parts = [
        cover_letter_data.get("greeting", ""),
        cover_letter_data.get("opening_paragraph", ""),
        *(cover_letter_data.get("body_paragraphs") or []),
        cover_letter_data.get("closing_paragraph", ""),
        cover_letter_data.get("signature_name", ""),
    ]
    return "\n\n".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
