"""The admin business dashboard's numbers — computed from real product
data, not modeled assumptions (COMMERCIAL_READINESS_AUDIT.md §20/§45).
Where a number genuinely can't be computed yet (AI cost — see BILLING.md's
documented gap: per-provider token/cost capture isn't wired up), this
returns null rather than a fabricated zero, so the admin UI can show
"not yet tracked" instead of a misleading number.
"""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import AnalyticsEvent
from authentication.models import User
from billing.models import AIUsageLog, Subscription
from billing.plans import get_price
from resumes.models import JobResumeCuration, Resume
from resumes.services.outcome_insights_service import outcome_insights


def _monthly_equivalent_cents(subscription):
    """An annual subscriber's contribution to *monthly* recurring revenue
    is their annual price divided by 12 — MRR is always expressed as a
    monthly figure regardless of how the customer actually pays."""
    price = get_price(subscription.plan_id, subscription.currency, subscription.billing_period or "monthly")
    if not price:
        return 0
    if subscription.billing_period == "annual":
        return price["amount_cents"] / 12
    return price["amount_cents"]


def _revenue_metrics():
    active_paid = Subscription.objects.filter(status="active").exclude(plan_id="").exclude(plan_id="free")
    # A coupon-granted comp subscription has no currency set (nothing was
    # ever actually charged) — excluded from MRR, which is about money
    # that is actually recurring, not access that was granted for free.
    paying = [s for s in active_paid if s.currency]

    mrr_by_currency = {}
    for subscription in paying:
        mrr_by_currency[subscription.currency] = (
            mrr_by_currency.get(subscription.currency, 0) + _monthly_equivalent_cents(subscription)
        )
    mrr_by_currency = {currency: round(cents / 100, 2) for currency, cents in mrr_by_currency.items()}
    arr_by_currency = {currency: round(amount * 12, 2) for currency, amount in mrr_by_currency.items()}

    return {
        "mrr_by_currency": mrr_by_currency,
        "arr_by_currency": arr_by_currency,
        "paying_subscriber_count": len(paying),
        "comp_subscriber_count": active_paid.count() - len(paying),
        "note": (
            "Reported per-currency rather than blended into one number — "
            "doing that honestly requires a real exchange rate, which "
            "this application does not fetch."
        ),
    }


def _ai_cost_metrics():
    aggregate = AIUsageLog.objects.aggregate(
        total_calls=Count("id"),
        total_cost_usd=Sum("cost_estimate_usd"),
        calls_with_cost=Count("id", filter=Q(cost_estimate_usd__isnull=False)),
    )
    total_calls = aggregate["total_calls"] or 0
    return {
        "total_ai_calls": total_calls,
        # Explicitly null, not 0, when nothing has been costed yet — see
        # BILLING.md: per-provider token/cost capture is a documented,
        # deliberate gap, and a 0 here would misleadingly read as "AI is
        # free" rather than "not measured."
        "total_ai_cost_usd": (
            float(aggregate["total_cost_usd"]) if aggregate["total_cost_usd"] is not None else None
        ),
        "cost_tracking_coverage": (
            round(aggregate["calls_with_cost"] / total_calls, 4) if total_calls else 0
        ),
    }


def _outcome_metrics():
    counts = dict(
        JobResumeCuration.objects.values("application_status")
        .annotate(count=Count("id"))
        .values_list("application_status", "count")
    )
    applied_or_later = sum(
        counts.get(status, 0)
        for status in (
            JobResumeCuration.APPLICATION_STATUS_APPLIED,
            JobResumeCuration.APPLICATION_STATUS_INTERVIEWING,
            JobResumeCuration.APPLICATION_STATUS_OFFER,
        )
    )
    interviewed_or_later = counts.get(JobResumeCuration.APPLICATION_STATUS_INTERVIEWING, 0) + counts.get(
        JobResumeCuration.APPLICATION_STATUS_OFFER, 0
    )
    offers = counts.get(JobResumeCuration.APPLICATION_STATUS_OFFER, 0)

    return {
        "by_status": {
            status_key: counts.get(status_key, 0)
            for status_key, _ in JobResumeCuration.APPLICATION_STATUS_CHOICES
        },
        # Both rates are of applications actually marked "applied" or
        # later — not of every tailored resume generated, most of which
        # the user may not have gotten around to marking yet. A null
        # (not 0) when there's no applied-or-later data yet to divide by.
        "interview_rate": round(interviewed_or_later / applied_or_later, 4) if applied_or_later else None,
        "offer_rate": round(offers / applied_or_later, 4) if applied_or_later else None,
        **_outcome_capture_metrics(counts),
    }


def _outcome_capture_metrics(counts):
    """How much outcome data actually exists — the health metric for the
    follow-up loop itself.

    Every rate above is computed from user-reported outcomes, so they are
    only as trustworthy as the share of applications that ever got one. A
    100% interview rate across three reported applications says nothing.
    This measures the denominator's credibility, and is the number to watch
    to know whether the nudge emails are working.

    "Resolved" means the application moved past `applied` to a real result.
    Something still sitting at `applied` is an application whose outcome we
    simply do not know.
    """
    resolved = (
        counts.get(JobResumeCuration.APPLICATION_STATUS_INTERVIEWING, 0)
        + counts.get(JobResumeCuration.APPLICATION_STATUS_OFFER, 0)
        + counts.get(JobResumeCuration.APPLICATION_STATUS_REJECTED, 0)
    )
    submitted = counts.get(JobResumeCuration.APPLICATION_STATUS_APPLIED, 0) + resolved

    return {
        "applications_submitted": submitted,
        "outcomes_resolved": resolved,
        "awaiting_outcome": counts.get(JobResumeCuration.APPLICATION_STATUS_APPLIED, 0),
        "outcome_capture_rate": round(resolved / submitted, 4) if submitted else None,
        "nudges_sent": JobResumeCuration.objects.filter(
            outcome_nudge_sent_at__isnull=False
        ).count(),
    }


def _funnel_metrics():
    event_counts = dict(
        AnalyticsEvent.objects.values("event_type").annotate(count=Count("id")).values_list("event_type", "count")
    )
    return {
        "landing_page_views": event_counts.get(AnalyticsEvent.EVENT_LANDING_PAGE_VIEW, 0),
        "signups": User.objects.count(),
        "resume_uploads": Resume.objects.filter(status="completed").values("user").distinct().count(),
        "job_analyses": JobResumeCuration.objects.filter(status="completed").values("user").distinct().count(),
        "tailored_resumes_generated": (
            JobResumeCuration.objects.exclude(tailored_resume_data={}).values("user").distinct().count()
        ),
        "checkout_started": event_counts.get(AnalyticsEvent.EVENT_CHECKOUT_STARTED, 0),
        "paid": Subscription.objects.filter(status="active").exclude(plan_id="").exclude(plan_id="free").count(),
    }


@extend_schema(
    tags=["Admin"],
    summary="Business/commercial metrics for the admin dashboard",
    description=(
        "Acquisition, activation, conversion, revenue (per-currency MRR/"
        "ARR), AI cost, the acquisition funnel, and customer outcome "
        "tracking — every number computed from real product data, none "
        "modeled or estimated. See COMMERCIAL_READINESS_AUDIT.md §45/§20. "
        "Staff only."
    ),
)
class AdminBusinessMetricsAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        total_signups = User.objects.count()
        activated_users = Resume.objects.filter(status="completed").values("user").distinct().count()
        paid_users = Subscription.objects.filter(status="active").exclude(plan_id="").exclude(plan_id="free").count()

        return Response({
            "acquisition": {
                "total_signups": total_signups,
                "signups_this_month": User.objects.filter(date_joined__gte=month_start).count(),
                "signups_today": User.objects.filter(date_joined__gte=today_start).count(),
            },
            "activation": {
                "activated_users": activated_users,
                "activation_rate": round(activated_users / total_signups, 4) if total_signups else None,
            },
            "conversion": {
                "paid_users": paid_users,
                "free_users": total_signups - paid_users,
                "conversion_rate": round(paid_users / activated_users, 4) if activated_users else None,
            },
            "product_usage": {
                "resumes_uploaded": Resume.objects.count(),
                "job_analyses": JobResumeCuration.objects.filter(status="completed").count(),
                "tailored_resumes_generated": JobResumeCuration.objects.exclude(tailored_resume_data={}).count(),
                "cover_letters_generated": JobResumeCuration.objects.exclude(cover_letter_data={}).count(),
            },
            "revenue": _revenue_metrics(),
            "ai_cost": _ai_cost_metrics(),
            "funnel": _funnel_metrics(),
            "customer_outcomes": _outcome_metrics(),
            "outcome_insights": outcome_insights(),
        })
