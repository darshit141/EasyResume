"""Enforces the free/paid split defined in `billing.entitlements`.

Everything here is a *server-side* gate. The frontend also hides gated
controls, but that is presentation only — these checks are what actually
prevent a free account from using paid features.
"""

from django.utils import timezone

from billing.entitlements import FEATURE_LABELS, entitlements_for, is_paid_plan
from billing.models import AIUsageLog, Subscription
from billing.services.credit_service import CreditService


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
    def record_ai_usage(user, action, provider, job=None, latency_ms=None, model_name=""):
        """Call once, after an AI-backed action actually succeeds. Never
        call this for the `standard` (non-AI) resume parser — it makes no
        AI call and must never count against the quota.

        `job`/`latency_ms`/`model_name` are optional cost/observability
        fields (see AIUsageLog) — populated automatically for anything
        dispatched through ai_jobs.tasks.run_ai_job, left blank for any
        AI-calling endpoint not yet migrated onto that queue.
        """
        AIUsageLog.objects.create(
            user=user,
            action=action,
            provider=provider or "",
            job=job,
            latency_ms=latency_ms,
            model_name=model_name or "",
        )

    @staticmethod
    def bonus_credits(user):
        """Unexpired credits the user earned (e.g. by watching a rewarded
        ad). Each one lifts both free-tier monthly ceilings by one while it
        lasts."""
        return CreditService.active_balance(user)

    @staticmethod
    def uses_own_ai_key(user):
        """Whether this user is paying their provider directly for AI.

        Wrapped because a failure here must not block an AI action: the
        safe direction to be wrong in is "no personal key", which merely
        applies the normal quota.
        """
        try:
            from billing.services.user_ai_key_service import UserAIKeyService

            return UserAIKeyService.has_active_key(user)
        except Exception:  # pragma: no cover - defensive
            return False

    @staticmethod
    def check_can_use_ai(user):
        """The blanket cost-control gate: covers every AI-backed action
        combined, separate from the narrower `tailored_resumes_per_month`
        feature limit. Skip entirely for the `standard` parser — it isn't
        an AI call, so it never needs a permission check."""
        entitlements = EntitlementsService.for_user(user)
        base_limit = entitlements["ai_calls_per_month"]
        if base_limit is None:
            return

        # This ceiling exists to cap *our* provider bill, and nothing else.
        # A user spending their own API quota isn't on that bill, so holding
        # them to it would be charging them twice for the same call. Note
        # what this deliberately does not lift: the tailored-resume limit,
        # Word export, version history. Those are product value rather than
        # compute cost, and a personal API key buys none of them.
        if EntitlementsService.uses_own_ai_key(user):
            return

        bonus = EntitlementsService.bonus_credits(user)
        limit = base_limit + bonus
        used = EntitlementsService.ai_calls_used_this_month(user)
        if used >= limit:
            raise PlanLimitReachedError(
                f"You've used all {base_limit} AI-assisted actions included in the Free plan "
                f"this month. Upgrade to Pro for unlimited AI usage, or wait until your "
                f"allowance resets.",
                feature="ai_usage",
                used=used,
                limit=limit,
                resets_at=_next_month_start().isoformat(),
                earn_credit_available=_earn_credit_available(user),
            )

    @staticmethod
    def usage_summary(user):
        """Everything the UI needs to show plan state without extra requests."""
        from resumes.services.llm_service import provider_selection_enabled

        entitlements = EntitlementsService.for_user(user)
        uses_own_key = EntitlementsService.uses_own_ai_key(user)
        limit = entitlements["tailored_resumes_per_month"]
        used = EntitlementsService.tailored_resumes_used_this_month(user)
        # Reported as unlimited because that is what check_can_use_ai
        # actually does for this user — the summary must describe the gate
        # that runs, not the one that would run without their key.
        ai_limit = None if uses_own_key else entitlements["ai_calls_per_month"]
        ai_used = EntitlementsService.ai_calls_used_this_month(user)
        bonus = EntitlementsService.bonus_credits(user) if ai_limit is not None else 0
        eff_limit = None if limit is None else limit + bonus
        eff_ai_limit = None if ai_limit is None else ai_limit + bonus
        return {
            "plan_id": entitlements["plan_id"],
            "tailored_resumes_used": used,
            "tailored_resumes_limit": eff_limit,
            "tailored_resumes_remaining": None if eff_limit is None else max(0, eff_limit - used),
            "ai_calls_used": ai_used,
            "ai_calls_limit": eff_ai_limit,
            "ai_calls_remaining": None if eff_ai_limit is None else max(0, eff_ai_limit - ai_used),
            "bonus_credits": bonus,
            "allowed_ai_providers": entitlements["allowed_ai_providers"],
            # Whether to show the AI provider picker at all. The server enforces
            # the same switch (resumes.services.llm_service.requested_provider).
            "ai_provider_selection_enabled": provider_selection_enabled(),
            "docx_export": entitlements["docx_export"],
            "tailored_resume_version_history": entitlements["tailored_resume_version_history"],
            "resume_template_customization": entitlements["resume_template_customization"],
            "shows_ads": entitlements["ads"],
            "job_applications_limit": entitlements["job_applications_per_month"],
            "job_applications_used": EntitlementsService.applications_sent_this_month(user),
            # "none" | "alias" | "gmail" — the UI uses this to say whether
            # we are watching for a reply on this account, and how, rather
            # than implying a capability the plan doesn't have.
            "reply_tracking": entitlements["reply_tracking"],
            # True when the user has supplied their own provider key, which
            # is why their AI-action counter has stopped mattering. Without
            # this the UI would keep counting down toward a limit that is no
            # longer being enforced, which reads as a bug.
            "uses_own_ai_key": uses_own_key,
        }

    @staticmethod
    def check_can_generate_tailored_resume(user):
        entitlements = EntitlementsService.for_user(user)
        base_limit = entitlements["tailored_resumes_per_month"]
        if base_limit is None:
            return

        bonus = EntitlementsService.bonus_credits(user)
        limit = base_limit + bonus
        used = EntitlementsService.tailored_resumes_used_this_month(user)
        if used >= limit:
            raise PlanLimitReachedError(
                f"You've used all {base_limit} tailored resumes included in the Free plan "
                f"this month. Upgrade to Pro for unlimited tailored resumes, or wait "
                f"until your allowance resets.",
                feature="tailored_resumes",
                used=used,
                limit=limit,
                resets_at=_next_month_start().isoformat(),
                earn_credit_available=_earn_credit_available(user),
            )

    @staticmethod
    def applications_sent_this_month(user):
        """How many application emails this account has actually delivered
        this calendar month. Counts sends, not drafts — drafting costs us
        nothing and reveals nothing about abuse."""
        from resumes.models import JobApplication

        return JobApplication.objects.filter(
            user=user,
            status=JobApplication.STATUS_SENT,
            sent_at__gte=_month_start(),
        ).count()

    @staticmethod
    def check_can_send_application(user):
        """Gate an outbound application email.

        Unlike the other checks here this one is not really about the paid
        split — it is the control that stops one account burning the sending
        domain every other feature's email depends on. Both plans have a
        ceiling; Pro's is just high enough that no genuine job seeker will
        ever meet it.
        """
        entitlements = EntitlementsService.for_user(user)
        limit = entitlements.get("job_applications_per_month")
        if limit is None:
            return

        used = EntitlementsService.applications_sent_this_month(user)
        if used >= limit:
            is_paid = is_paid_plan(entitlements.get("plan_id"))
            message = (
                f"You've sent all {limit} applications your plan allows this month. "
                + (
                    "This is a fair-use limit that protects delivery for everyone; "
                    "contact support if you genuinely need more."
                    if is_paid
                    else "Upgrade for a much higher monthly allowance."
                )
            )
            raise PlanLimitReachedError(
                message,
                feature="job_applications",
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


def _earn_credit_available(user):
    """Whether to tell the frontend it can offer 'watch an ad for a credit'
    alongside the upgrade prompt: the feature is on, a provider is usable,
    and this account hasn't hit today's cap."""
    try:
        from billing.services.rewarded_ad_providers import get_rewarded_ad_provider

        provider = get_rewarded_ad_provider()
        if not provider.is_configured():
            return False
        return CreditService.can_watch_rewarded_ad(user)
    except Exception:  # never let a nice-to-have hint break the real error
        return False


def _month_start():
    now = timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start():
    now = timezone.now()
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
