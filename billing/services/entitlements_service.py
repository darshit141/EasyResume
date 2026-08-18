"""Enforces the free/paid split defined in `billing.entitlements`.

Everything here is a *server-side* gate. The frontend also hides gated
controls, but that is presentation only — these checks are what actually
prevent a free account from using paid features.
"""

from django.utils import timezone

from billing.entitlements import FEATURE_LABELS, entitlements_for
from billing.models import AIUsageLog, Subscription


class PlanRestrictionError(Exception):
    """Base for 'you need to upgrade' failures.

    Deliberately NOT a subclass of the Stripe-facing BillingError: those map
    to 400/503 for a broken integration, whereas these are a working system
    correctly telling a user they've hit their plan's limit. Different
    meaning, different status code, different UI.
    """

    code = "UPGRADE_REQUIRED"

    def __init__(self, message, **extra):
        self.message = message
        self.extra = extra
        super().__init__(message)

    def to_payload(self):
        return {"detail": self.message, "code": self.code, **self.extra}


class PlanLimitReachedError(PlanRestrictionError):
    code = "PLAN_LIMIT_REACHED"


class FeatureRequiresProError(PlanRestrictionError):
    code = "UPGRADE_REQUIRED"


class EntitlementsService:
    @staticmethod
    def for_user(user):
        subscription = Subscription.objects.filter(user=user).first()
        if subscription is None:
            return entitlements_for(None, False)
        return entitlements_for(subscription.plan_id, subscription.is_active)

    @staticmethod
    def tailored_resumes_used_this_month(user):
        """How many tailored resumes the user has generated this calendar month.

        Counts generated *versions*, not curations: regenerating produces new
        AI output and costs us a model call, so it consumes quota exactly the
        same way a first generation does. Counting curations instead would
        let one curation be regenerated without limit.
        """
        from resumes.models import JobResumeCurationVersion

        now = timezone.now()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return JobResumeCurationVersion.objects.filter(
            curation__user=user,
            created_at__gte=period_start,
        ).count()

    @staticmethod
    def ai_calls_used_this_month(user):
        """How many AI-backed calls (of any kind) this user has made this
        calendar month, from the tracked usage log — not inferred from a
        side effect of some other model, so it actually covers every
        AI-calling action, not just the ones that happen to leave a row
        behind elsewhere."""
        period_start = _month_start()
        return AIUsageLog.objects.filter(user=user, created_at__gte=period_start).count()

    @staticmethod
    def record_ai_usage(user, action, provider):
        """Call once, after an AI-backed action actually succeeds. Never
        call this for the `standard` (non-AI) resume parser — it makes no
        AI call and must never count against the quota."""
        AIUsageLog.objects.create(user=user, action=action, provider=provider or "")

    @staticmethod
    def check_can_use_ai(user):
        """The blanket cost-control gate: covers every AI-backed action
        combined, separate from the narrower `tailored_resumes_per_month`
        feature limit. Skip entirely for the `standard` parser — it isn't
        an AI call, so it never needs a permission check."""
        entitlements = EntitlementsService.for_user(user)
        limit = entitlements["ai_calls_per_month"]
        if limit is None:
            return

        used = EntitlementsService.ai_calls_used_this_month(user)
        if used >= limit:
            raise PlanLimitReachedError(
                f"You've used all {limit} AI-assisted actions included in the Free plan "
                f"this month. Upgrade to Pro for unlimited AI usage, or wait until your "
                f"allowance resets.",
                feature="ai_usage",
                used=used,
                limit=limit,
                resets_at=_next_month_start().isoformat(),
            )

    @staticmethod
    def usage_summary(user):
        """Everything the UI needs to show plan state without extra requests."""
        entitlements = EntitlementsService.for_user(user)
        limit = entitlements["tailored_resumes_per_month"]
        used = EntitlementsService.tailored_resumes_used_this_month(user)
        ai_limit = entitlements["ai_calls_per_month"]
        ai_used = EntitlementsService.ai_calls_used_this_month(user)
        return {
            "plan_id": entitlements["plan_id"],
            "tailored_resumes_used": used,
            "tailored_resumes_limit": limit,
            "tailored_resumes_remaining": None if limit is None else max(0, limit - used),
            "ai_calls_used": ai_used,
            "ai_calls_limit": ai_limit,
            "ai_calls_remaining": None if ai_limit is None else max(0, ai_limit - ai_used),
            "allowed_ai_providers": entitlements["allowed_ai_providers"],
            "docx_export": entitlements["docx_export"],
            "tailored_resume_version_history": entitlements["tailored_resume_version_history"],
            "shows_ads": entitlements["ads"],
        }

    @staticmethod
    def check_can_generate_tailored_resume(user):
        entitlements = EntitlementsService.for_user(user)
        limit = entitlements["tailored_resumes_per_month"]
        if limit is None:
            return

        used = EntitlementsService.tailored_resumes_used_this_month(user)
        if used >= limit:
            raise PlanLimitReachedError(
                f"You've used all {limit} tailored resumes included in the Free plan "
                f"this month. Upgrade to Pro for unlimited tailored resumes, or wait "
                f"until your allowance resets.",
                feature="tailored_resumes",
                used=used,
                limit=limit,
                resets_at=_next_month_start().isoformat(),
            )

    @staticmethod
    def check_ai_provider_allowed(user, provider):
        # No explicit provider means "use the server default", which is always
        # permitted — a free user is never blocked from the standard flow.
        if not provider:
            return

        entitlements = EntitlementsService.for_user(user)
        if provider not in entitlements["allowed_ai_providers"]:
            raise FeatureRequiresProError(
                f"The \"{provider}\" AI provider is available on the Pro plan. "
                f"Your current plan can use: "
                f"{', '.join(entitlements['allowed_ai_providers'])}.",
                feature="ai_provider",
                requested_provider=provider,
                allowed_providers=entitlements["allowed_ai_providers"],
            )

    @staticmethod
    def check_feature(user, feature):
        """Gate a boolean entitlement such as `docx_export`."""
        entitlements = EntitlementsService.for_user(user)
        if not entitlements.get(feature):
            label = FEATURE_LABELS.get(feature, feature.replace("_", " "))
            raise FeatureRequiresProError(
                f"{label} is a Pro feature. Upgrade to unlock it.",
                feature=feature,
            )


def _month_start():
    now = timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start():
    now = timezone.now()
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
