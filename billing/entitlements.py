"""What each plan is actually allowed to do.

This is the single source of truth for the free/paid split. `plans.py`
describes the tiers to *customers*; this file is what the server *enforces*.
The two must agree — if you add a bullet to a plan's marketing copy, either
enforce it here or don't claim it.

Every check happens server-side. Hiding a button in the UI is a courtesy to
the user, never a control: the API is public and must assume the client is
hostile.
"""

import os

# `None` means unlimited. Both limits are read from the environment so they
# can be retuned (e.g. after seeing real Groq/Gemini usage) without a code
# change or redeploy.
#
# `ai_calls_per_month` is deliberately separate from
# `tailored_resumes_per_month`: the latter is a specific, marketed feature
# limit ("2 tailored resumes/month" on the pricing page), while this is a
# blanket cost-control ceiling covering *every* AI-backed action — resume
# parsing, job analysis, gap analysis, missing-information review, and
# tailored resume generation combined. Without it, a free user could call
# the first four unlimited times; only the final generation step was ever
# capped.
FREE_AI_CALLS_PER_MONTH = int(os.getenv("FREE_AI_CALLS_PER_MONTH", "30"))

# Direct Apply sends real email to third parties from our own domain, which
# makes it the one feature in the product that can damage something we can't
# buy back: our sending reputation. These caps are an abuse control first and
# a plan differentiator second, which is why even Pro has a finite ceiling —
# an "unlimited sends" paid tier is an attractive thing for a spammer to buy.
# Tuned by environment so they can be tightened immediately without a deploy.
FREE_JOB_APPLICATIONS_PER_MONTH = int(os.getenv("FREE_JOB_APPLICATIONS_PER_MONTH", "10"))
PRO_JOB_APPLICATIONS_PER_MONTH = int(os.getenv("PRO_JOB_APPLICATIONS_PER_MONTH", "200"))
MAX_JOB_APPLICATIONS_PER_MONTH = int(os.getenv("MAX_JOB_APPLICATIONS_PER_MONTH", "500"))

# The paid tiers, cheapest first. Everything that used to ask "is this user
# on pro?" must ask "is this user on a paid plan?" instead — the day a
# second paid tier exists, a hardcoded == "pro" silently starts showing ads
# to your most expensive subscribers.
PAID_PLAN_IDS = ("pro", "max")

# How we find out an employer replied. Two real strategies, plus off:
#
#   "alias"  Mail reaches our own domain: each Direct Apply email carries a
#            Reply-To address unique to it, and users forward the rest
#            (portal and LinkedIn applications) to a personal tracking
#            address with a Gmail or Outlook filter. No mailbox access.
#            Implemented in the inbox app.
#   "gmail"  Read the user's mailbox with their consent, which catches
#            portal applications too. Needs Google's *restricted*
#            gmail.readonly scope and therefore an annual third-party
#            security assessment (CASA) - a recurring bill. Not built yet;
#            until it is, "gmail" behaves exactly like "alias".
#
# Which plan gets which is deliberately NOT hardcoded. It is resolved per
# request from the admin Configuration tab (REPLY_TRACKING_<PLAN>), so the
# ladder can be re-cut - give Pro the Gmail route, hand the alias route to
# free users, switch it all off while the CASA assessment is pending -
# without a code change or a redeploy. The values below are only defaults.
REPLY_TRACKING_STRATEGIES = ("none", "alias", "gmail")
REPLY_TRACKING_ENABLED = os.getenv("REPLY_TRACKING_ENABLED", "True") == "True"
REPLY_TRACKING_FREE = os.getenv("REPLY_TRACKING_FREE", "alias")
REPLY_TRACKING_PRO = os.getenv("REPLY_TRACKING_PRO", "alias")
REPLY_TRACKING_MAX = os.getenv("REPLY_TRACKING_MAX", "gmail")

FREE_ENTITLEMENTS = {
    "plan_id": "free",
    "tailored_resumes_per_month": int(os.getenv("FREE_TAILORED_RESUMES_PER_MONTH", "2")),
    "ai_calls_per_month": FREE_AI_CALLS_PER_MONTH,
    "job_applications_per_month": FREE_JOB_APPLICATIONS_PER_MONTH,
    # Every provider is available on every plan — Groq and Gemini are no
    # longer Pro-exclusive. Cost is controlled entirely by
    # `ai_calls_per_month` above, not by which provider a request picks.
    "allowed_ai_providers": ["ollama", "standard", "groq", "gemini", "kira", "dahl", "nvidia"],
    "docx_export": False,
    "tailored_resume_version_history": False,
    # Free users may pick one of the built-in resume layout presets; tuning
    # the individual knobs (section order, accent colour, font, spacing) is
    # the Pro feature. Enforced in ResumeTemplateService.set_preference.
    "resume_template_customization": False,
    # Resolved per request from admin config; this is only the fallback.
    # See REPLY_TRACKING_* above.
    "reply_tracking": REPLY_TRACKING_FREE,
    # Adopted, not a placeholder: free-tier ads offset AI compute cost for
    # the non-paying cohort without touching paying users (Pro is ads:
    # False below) and without hiding the free tier's actual usefulness
    # behind a paywall. Standard freemium-with-ads pattern; revisit only if
    # ad revenue turns out not to be worth the UX cost once there's real
    # traffic data. Actually serving ads still requires a real AdSense
    # publisher id (VITE_ADSENSE_CLIENT) — this flag is dormant until then.
    "ads": True,
    # Quotas on this plan are calendar-monthly. The free launch offer
    # (billing.launch) swaps this for a short rolling window, in hours.
    "quota_window_hours": None,
    "launch_offer": False,
}

PRO_ENTITLEMENTS = {
    "plan_id": "pro",
    "tailored_resumes_per_month": None,
    "ai_calls_per_month": None,
    # Not None: see the note on the constants above. This is a spam ceiling,
    # not a paywall — no honest job seeker emails 200 applications a month.
    "job_applications_per_month": PRO_JOB_APPLICATIONS_PER_MONTH,
    # "openrouter" is Pro/Max only, unlike the rest of this list — it runs on
    # the server's own OpenRouter account, offered as an extra provider for
    # paying users rather than as free-tier capacity. See OPENROUTER_* in
    # settings and resumes.services.llm_service.OpenRouterLLMService.
    "allowed_ai_providers": ["ollama", "standard", "groq", "gemini", "kira", "openrouter", "dahl", "nvidia"],
    "docx_export": True,
    "tailored_resume_version_history": True,
    "resume_template_customization": True,
    "reply_tracking": REPLY_TRACKING_PRO,
    "ads": False,
    "quota_window_hours": None,
    "launch_offer": False,
}

# The tier that exists to fund third-party services we pay for per-account.
# Right now that means Gmail reply tracking, which needs Google's
# *restricted* gmail.readonly scope and therefore an annual third-party
# security assessment (CASA) — a real, recurring bill that the Pro price
# does not cover. Anything added here in future should meet the same test:
# it costs us money per subscriber, rather than merely being withheld from
# cheaper tiers.
MAX_ENTITLEMENTS = {
    **PRO_ENTITLEMENTS,
    "plan_id": "max",
    "job_applications_per_month": MAX_JOB_APPLICATIONS_PER_MONTH,
    "reply_tracking": REPLY_TRACKING_MAX,
}

# Human-readable names used in upgrade messages, so the error a user sees
# names the thing they were trying to do rather than an internal flag.
FEATURE_LABELS = {
    "docx_export": "Word (.docx) export",
    "tailored_resume_version_history": "Version history and comparisons",
    "ai_provider": "Choosing an AI provider",
    "resume_template_customization": "Customising the resume layout",
    "job_applications": "Sending applications from IntelliResume",
    "reply_tracking": "Tracking employer replies",
}

# Every paid tier, keyed by the plan id stored on Subscription. Resolving
# through this rather than an if/elif chain means adding a tier is one dict
# entry, and an unknown id can only ever fall through to free.
PAID_ENTITLEMENTS = {
    PRO_ENTITLEMENTS["plan_id"]: PRO_ENTITLEMENTS,
    MAX_ENTITLEMENTS["plan_id"]: MAX_ENTITLEMENTS,
}


def is_paid_plan(plan_id):
    """Whether a plan id is a paying tier.

    Use this instead of comparing to "pro". Ads, rewarded-ad offers and
    "upgrade to remove this" prompts all keyed off == "pro", which would
    have started showing ads to Max subscribers the moment that tier
    existed.
    """
    return plan_id in PAID_ENTITLEMENTS


def entitlements_for(plan_id, is_active):
    """Resolve a plan id + subscription state into an entitlements dict.

    Anything other than a confirmed-active paid plan resolves to free. That
    default is deliberate: an unknown plan id, a lapsed subscription, or a
    half-written row must never accidentally grant paid access.

    The two free-tier quotas are resolved *per call* through the runtime
    config, not baked in at import. They are the controls that cap real AI
    spend, so an operator changing them in the admin panel has to take effect
    immediately — the module-level dict below is only the fallback default.
    """
    # Imported lazily: this module is imported by plans.py at app-load time,
    # and api.utils.config reaches into the ORM.
    from api.utils.config import get_config_value

    if is_active and plan_id in PAID_ENTITLEMENTS:
        entitlements = PAID_ENTITLEMENTS[plan_id]
        return {
            **entitlements,
            "job_applications_per_month": get_config_value(
                f"{plan_id.upper()}_JOB_APPLICATIONS_PER_MONTH",
                entitlements["job_applications_per_month"],
            ),
            "reply_tracking": resolve_reply_tracking(plan_id, entitlements["reply_tracking"]),
        }

    from billing import launch

    if launch.is_active():
        return launch_entitlements()

    return {
        **FREE_ENTITLEMENTS,
        "tailored_resumes_per_month": get_config_value(
            "FREE_TAILORED_RESUMES_PER_MONTH",
            FREE_ENTITLEMENTS["tailored_resumes_per_month"],
        ),
        "ai_calls_per_month": get_config_value(
            "FREE_AI_CALLS_PER_MONTH", FREE_ENTITLEMENTS["ai_calls_per_month"]
        ),
        "job_applications_per_month": get_config_value(
            "FREE_JOB_APPLICATIONS_PER_MONTH",
            FREE_ENTITLEMENTS["job_applications_per_month"],
        ),
        "reply_tracking": resolve_reply_tracking("free", FREE_ENTITLEMENTS["reply_tracking"]),
    }


def launch_entitlements():
    """What an unpaid account gets while the free launch offer is on.

    Every paid *feature* is unlocked; what stays metered is AI spend, over a
    short rolling window (``quota_window_hours``) instead of a month. The
    two quota keys keep their historical ``_per_month`` names so every
    caller keeps working - when ``quota_window_hours`` is set they mean
    "per window". OpenRouter stays paid-only: it runs on our own account
    and is the most expensive route, so it is not part of a free offer.
    """
    from api.utils.config import get_config_value
    from billing import launch

    return {
        **FREE_ENTITLEMENTS,
        "plan_id": "free",
        "launch_offer": True,
        "quota_window_hours": launch.window_hours(),
        "tailored_resumes_per_month": launch.tailored_resumes_per_window(),
        "ai_calls_per_month": launch.ai_calls_per_window(),
        "job_applications_per_month": get_config_value(
            "FREE_JOB_APPLICATIONS_PER_MONTH",
            FREE_ENTITLEMENTS["job_applications_per_month"],
        ),
        "docx_export": True,
        "tailored_resume_version_history": True,
        "resume_template_customization": True,
        "reply_tracking": resolve_reply_tracking("free", FREE_ENTITLEMENTS["reply_tracking"]),
        "ads": launch.shows_ads(),
    }


def resolve_reply_tracking(plan_id, default):
    """Which reply-tracking strategy a plan gets, right now.

    Read through the admin Configuration tab rather than baked in, so the
    ladder can be re-cut without a deploy — the point of that being that
    the Gmail route depends on a compliance assessment that may lapse,
    arrive late, or turn out not to be worth paying for. When it is not
    available, an operator turns it off and Max subscribers fall back to
    the alias route rather than seeing a feature that quietly does nothing.

    An unrecognised value resolves to "none". Guessing wrong here would
    mean either promising tracking we don't perform, or reading a mailbox
    under a setting nobody deliberately chose.
    """
    from api.utils.config import get_config_value

    if not get_config_value("REPLY_TRACKING_ENABLED", REPLY_TRACKING_ENABLED):
        return "none"

    value = get_config_value(f"REPLY_TRACKING_{plan_id.upper()}", default)
    value = (value or "none").strip().lower()
    return value if value in REPLY_TRACKING_STRATEGIES else "none"
