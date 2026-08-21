"""Builds a structured, machine-readable export of everything personal
data this application holds about one user — GDPR Article 20 (right to
data portability). See GDPR_DATA_FLOW.md for the data inventory this is
expected to cover.

Deliberately excludes the raw AI usage log (one row per AI call — not
meaningfully "the user's data" the way their resumes and job curations
are, and unbounded in size for a heavy user) in favor of an aggregate
summary; everything that IS the user's own content is included in full.
"""

from django.utils import timezone


class AccountExportService:
    @staticmethod
    def build_export(user):
        from billing.models import AIUsageLog, Subscription
        from resumes.models import JobResumeCuration, Resume

        return {
            "exported_at": timezone.now().isoformat(),
            "profile": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "phone": user.phone,
                "date_joined": user.date_joined.isoformat() if user.date_joined else None,
            },
            "subscription": AccountExportService._subscription(user, Subscription),
            "resumes": AccountExportService._resumes(user, Resume),
            "job_curations": AccountExportService._job_curations(user, JobResumeCuration),
            "ai_usage_summary": AccountExportService._ai_usage_summary(user, AIUsageLog),
            "policy_acceptances": AccountExportService._policy_acceptances(user),
            "cookie_consent": AccountExportService._cookie_consent(user),
        }

    @staticmethod
    def _subscription(user, Subscription):
        subscription = Subscription.objects.filter(user=user).first()
        if not subscription:
            return None
        return {
            "plan_id": subscription.plan_id,
            "status": subscription.status,
            "current_period_end": (
                subscription.current_period_end.isoformat() if subscription.current_period_end else None
            ),
            "created_at": subscription.created_at.isoformat(),
        }

    @staticmethod
    def _resumes(user, Resume):
        resumes = []
        for resume in Resume.objects.filter(user=user).prefetch_related("versions"):
            resumes.append({
                "id": resume.id,
                "original_filename": resume.original_filename,
                "status": resume.status,
                "ats_score": float(resume.ats_score) if resume.ats_score is not None else None,
                "parsed_data": resume.parsed_data,
                "created_at": resume.created_at.isoformat(),
                "updated_at": resume.updated_at.isoformat(),
                "versions": [
                    {
                        "version": version.version,
                        "parsed_data": version.parsed_data,
                        "ats_score": float(version.ats_score) if version.ats_score is not None else None,
                        "created_at": version.created_at.isoformat(),
                    }
                    for version in resume.versions.all()
                ],
            })
        return resumes

    @staticmethod
    def _job_curations(user, JobResumeCuration):
        curations = []
        for curation in JobResumeCuration.objects.filter(user=user).prefetch_related("tailored_resume_versions"):
            curations.append({
                "id": curation.id,
                "job_title": curation.job_title,
                "job_description": curation.job_description,
                "job_url": curation.job_url,
                "status": curation.status,
                "analysis_data": curation.analysis_data,
                "gap_analysis_data": curation.gap_analysis_data,
                "tailored_resume_data": curation.tailored_resume_data,
                "cover_letter_data": curation.cover_letter_data,
                "application_status": curation.application_status,
                "applied_at": curation.applied_at.isoformat() if curation.applied_at else None,
                "application_notes": curation.application_notes,
                "created_at": curation.created_at.isoformat(),
                "updated_at": curation.updated_at.isoformat(),
                "tailored_resume_versions": [
                    {
                        "version": version.version,
                        "tailored_resume_data": version.tailored_resume_data,
                        "match_percentage": (
                            float(version.match_percentage) if version.match_percentage is not None else None
                        ),
                        "created_at": version.created_at.isoformat(),
                    }
                    for version in curation.tailored_resume_versions.all()
                ],
            })
        return curations

    @staticmethod
    def _ai_usage_summary(user, AIUsageLog):
        logs = AIUsageLog.objects.filter(user=user)
        by_action = {}
        for action, _ in AIUsageLog.ACTION_CHOICES:
            count = logs.filter(action=action).count()
            if count:
                by_action[action] = count
        return {"total_calls": logs.count(), "by_action": by_action}

    @staticmethod
    def _policy_acceptances(user):
        return [
            {
                "policy_type": acceptance.policy_type,
                "version": acceptance.version,
                "accepted_at": acceptance.accepted_at.isoformat(),
            }
            for acceptance in user.policy_acceptances.all()
        ]

    @staticmethod
    def _cookie_consent(user):
        consent = getattr(user, "cookie_consent", None)
        if not consent:
            return None
        return {
            "analytics": consent.analytics,
            "marketing": consent.marketing,
            "consent_version": consent.consent_version,
            "updated_at": consent.updated_at.isoformat(),
        }
