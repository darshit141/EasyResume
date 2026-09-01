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

FREE_ENTITLEMENTS = {
    "plan_id": "free",
    "tailored_resumes_per_month": int(os.getenv("FREE_TAILORED_RESUMES_PER_MONTH", "2")),
    "ai_calls_per_month": FREE_AI_CALLS_PER_MONTH,
    # Every provider is available on every plan — Groq and Gemini are no
    # longer Pro-exclusive. Cost is controlled entirely by
    # `ai_calls_per_month` above, not by which provider a request picks.
    "allowed_ai_providers": ["ollama", "standard", "groq", "gemini"],
    "docx_export": False,
    "tailored_resume_version_history": False,
    # Free users may pick one of the built-in resume layout presets; tuning
    # the individual knobs (section order, accent colour, font, spacing) is
    # the Pro feature. Enforced in ResumeTemplateService.set_preference.
    "resume_template_customization": False,
    # Adopted, not a placeholder: free-tier ads offset AI compute cost for
    # the non-paying cohort without touching paying users (Pro is ads:
    # False below) and without hiding the free tier's actual usefulness
    # behind a paywall. Standard freemium-with-ads pattern; revisit only if
    # ad revenue turns out not to be worth the UX cost once there's real
    # traffic data. Actually serving ads still requires a real AdSense
    # publisher id (VITE_ADSENSE_CLIENT) — this flag is dormant until then.
    "ads": True,
}

PRO_ENTITLEMENTS = {
    "plan_id": "pro",
    "tailored_resumes_per_month": None,
    "ai_calls_per_month": None,
    "allowed_ai_providers": ["ollama", "standard", "groq", "gemini"],
    "docx_export": True,
    "tailored_resume_version_history": True,
    "resume_template_customization": True,
    "ads": False,
}

# Human-readable names used in upgrade messages, so the error a user sees
# names the thing they were trying to do rather than an internal flag.
FEATURE_LABELS = {
    "docx_export": "Word (.docx) export",
    "tailored_resume_version_history": "Version history and comparisons",
    "ai_provider": "Choosing an AI provider",
    "resume_template_customization": "Customising the resume layout",
}


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

    if is_active and plan_id == "pro":
        return PRO_ENTITLEMENTS

    return {
        **FREE_ENTITLEMENTS,
        "tailored_resumes_per_month": get_config_value(
            "FREE_TAILORED_RESUMES_PER_MONTH",
            FREE_ENTITLEMENTS["tailored_resumes_per_month"],
        ),
        "ai_calls_per_month": get_config_value(
            "FREE_AI_CALLS_PER_MONTH", FREE_ENTITLEMENTS["ai_calls_per_month"]
        ),
    }
