import time

from django.conf import settings
from django.db import models
from django.db.models import Count
from rest_framework import status, permissions, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from authentication.models import User
from api.models import AdminAuditLog, SystemConfiguration
from api.utils.audit import mask_secret_for_audit, record_admin_action
from api.utils.secrets import encrypt_value, is_secret_key
from api.serializers.admin_serializers import AdminAuditLogSerializer, AdminUserSerializer
from resumes.services.llm_service import (
    SUPPORTED_PROVIDERS,
    resolve_provider,
    _provider_api_keys,
    _parse_api_keys,
    _build_client,
)

# Dropdown options for settings with a fixed set of values. They are served
# from here, to staff only, rather than hardcoded in the admin page, so the
# names of the vendors behind the product never ship in the public bundle.
_LLM_PROVIDER_CHOICES = [
    {"value": "ollama", "label": "Ollama (Local/Self-hosted)"},
    {"value": "groq", "label": "Groq (hosted, fast JSON completions)"},
    {"value": "gemini", "label": "Google Gemini (hosted, native JSON schema)"},
    {"value": "kira", "label": "Kira (hosted, large context, high limits)"},
    {"value": "openrouter", "label": "OpenRouter (hosted, hundreds of models, Pro/Max only)"},
    {"value": "dahl", "label": "Dahl Global (hosted: MiniMax, GLM, DeepSeek)"},
]

# Every AI setting - provider, models, fallbacks and the provider API keys - is
# editable here and takes effect on the next request, so a discontinued model
# or a revoked token is fixed from the admin panel instead of by a deploy.
#
# API keys were once kept out of this list because the table stored them in
# plaintext, so a database dump exposed working credentials. They are back,
# encrypted at rest (api/utils/secrets.py), masked in every response, and
# never cleared by Reset to Defaults.
DEFAULT_CONFIGS = [
    {
        "key": "LLM_PROVIDER",
        "category": "llm",
        "value_type": "string",
        "description": "Default LLM provider (ollama, groq, gemini, kira, openrouter, dahl)",
        "choices": _LLM_PROVIDER_CHOICES,
    },
    {
        "key": "LLM_FALLBACK_PROVIDER",
        "category": "llm",
        "value_type": "string",
        "description": "Provider to retry once with when the default provider fails. Empty = no fallback.",
        "choices": [{"value": "", "label": "None (no fallback)"}] + _LLM_PROVIDER_CHOICES,
    },
    {
        "key": "AI_PROVIDER_SELECTION_ENABLED",
        "category": "llm",
        "value_type": "boolean",
        "description": (
            "Let users pick the AI provider on each request. Off hides the picker everywhere "
            "and every request uses LLM_PROVIDER (and its fallback); a provider a client "
            "still sends is ignored."
        ),
    },
    {
        "key": "LLM_TIMEOUT",
        "category": "llm",
        "value_type": "integer",
        "description": "Timeout in seconds for LLM API calls",
    },
    {
        "key": "LLM_MAX_RETRIES",
        "category": "llm",
        "value_type": "integer",
        "description": "Maximum number of retries for LLM calls",
    },
    {
        "key": "LLM_TEMPERATURE",
        "category": "llm",
        "value_type": "float",
        "description": "Temperature setting for LLM generation",
    },
    {
        "key": "LLM_MAX_OUTPUT_TOKENS",
        "category": "llm",
        "value_type": "integer",
        "description": "Max tokens generated per LLM call",
    },
    {
        "key": "OLLAMA_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for local Ollama service",
    },
    {
        "key": "OLLAMA_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": "Ollama model name",
    },
    {
        "key": "OLLAMA_KEEP_ALIVE",
        "category": "llm",
        "value_type": "string",
        "description": "Model retention time in Ollama (e.g. 30m)",
    },
    {
        "key": "GROQ_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": "Groq model name",
    },
    {
        "key": "GROQ_FALLBACK_MODELS",
        "category": "llm",
        "value_type": "list",
        "description": "Comma-separated Groq models to try, in order, when the first choice is rate-limited or unavailable.",
    },
    {
        "key": "GROQ_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for Groq API",
    },
    {
        "key": "GROQ_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Groq API key(s). Supports multiple comma or newline-separated tokens for quota failover. Stored encrypted.",
    },
    {
        "key": "LLM_KEY_ROTATION_ENABLED",
        "category": "llm",
        "value_type": "boolean",
        "description": "Enable atomic round-robin rotation across multiple API keys in the key pool to distribute traffic evenly.",
    },
    {
        "key": "LLM_KEY_COOLDOWN_SECONDS",
        "category": "llm",
        "value_type": "integer",
        "description": "Cooldown period in seconds for an API key when it receives an HTTP 429 quota exhaustion.",
    },
    {
        "key": "GEMINI_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Google Gemini API key(s). Supports multiple comma or newline-separated tokens for automatic round-robin and rate-limit failover. Stored encrypted.",
    },
    {
        "key": "GEMINI_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": (
            "First-choice Gemini model id. If it is discontinued it is skipped automatically. "
            "Leave empty to pick from the live model list. Run `python manage.py check_ai` "
            "to see which models the key is offered."
        ),
    },
    {
        "key": "GEMINI_FALLBACK_MODELS",
        "category": "llm",
        "value_type": "list",
        "description": "Comma-separated Gemini models to try, in order, when the first choice is overloaded, out of quota or discontinued.",
    },
    {
        "key": "GEMINI_MODEL_DISCOVERY",
        "category": "llm",
        "value_type": "boolean",
        "description": "Read Google's live model list to skip discontinued models and find further fallbacks automatically.",
    },
    {
        "key": "GEMINI_AUTO_FALLBACK_FAMILIES",
        "category": "llm",
        "value_type": "list",
        "description": "Automatic fallbacks must have one of these words in their id (e.g. flash). Empty turns automatic fallback off.",
    },
    {
        "key": "GEMINI_AUTO_EXCLUDE",
        "category": "llm",
        "value_type": "list",
        "description": "Automatic fallbacks must have none of these words in their id (e.g. image, tts, preview).",
    },
    {
        "key": "GEMINI_MAX_MODEL_ATTEMPTS",
        "category": "llm",
        "value_type": "integer",
        "description": "Most Gemini models a single request may try before failing.",
    },
    {
        "key": "GEMINI_MODEL_LIST_TTL",
        "category": "llm",
        "value_type": "integer",
        "description": "Seconds Google's model list is cached for (a new key always gets a fresh list).",
    },
    {
        "key": "GEMINI_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for Gemini API",
    },
    {
        "key": "KIRA_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": "Kira (kiraai.vn) chat model id, as listed in your Kira account. Text models only.",
    },
    {
        "key": "KIRA_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for the Kira API",
    },
    {
        "key": "KIRA_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Kira API key(s). Supports multiple comma or newline-separated tokens. Stored encrypted.",
    },
    {
        "key": "OPENROUTER_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": (
            "OpenRouter chat model id (e.g. a :free-suffixed one). Pro/Max plans only — "
            "see billing.entitlements. OpenRouter's free-model roster changes often; check "
            "openrouter.ai/models?max_price=0 for what's currently live."
        ),
    },
    {
        "key": "OPENROUTER_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for the OpenRouter API",
    },
    {
        "key": "OPENROUTER_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "OpenRouter API key(s). Supports multiple comma or newline-separated tokens. Stored encrypted.",
    },
    {
        "key": "DAHL_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": (
            "First-choice Dahl Global chat model id (e.g. MiniMaxAI/MiniMax-M2.7, "
            "zai-org/GLM-5.3-Flash, deepseek-ai/DeepSeek-V4-Flash-0731)."
        ),
    },
    {
        "key": "DAHL_FALLBACK_MODELS",
        "category": "llm",
        "value_type": "list",
        "description": "Comma-separated Dahl models to try, in order, when the first choice is unavailable or rate-limited.",
    },
    {
        "key": "DAHL_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for the Dahl API (default: https://inference.dahl.global/v1)",
    },
    {
        "key": "DAHL_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Dahl Global API key(s). Supports multiple comma or newline-separated tokens for automatic failover pool. Stored encrypted.",
    },
    {
        "key": "TAILOR_ADD_MISSING_SKILLS_DEFAULT",
        "category": "llm",
        "value_type": "boolean",
        "description": (
            "Default setting for whether to automatically incorporate verified missing job requirements "
            "into tailored resumes when not explicitly specified in the tailoring request."
        ),
    },
    {
        "key": "TAILOR_DEFAULT_MODE",
        "category": "llm",
        "value_type": "string",
        "description": "Default mode for tailoring resumes: 'system_baseline' (instant master resume in ATS layout), 'rephrase_only' (safe active-verb polish), or 'full_tailored' (deep AI adaptation).",
        "choices": [
            {"value": "system_baseline", "label": "System Baseline (Instant, 0 cost, 100% genuine)"},
            {"value": "rephrase_only", "label": "Rephrase Only (Active voice & polish, no fact changes)"},
            {"value": "full_tailored", "label": "Full AI Tailored (Role-specific deep adaptation)"},
        ],
    },
    {
        "key": "BYOK_ENABLED",
        "category": "llm",
        "value_type": "boolean",
        "description": (
            "Let users save their own Google Gemini API key. While a user's own key is "
            "active their AI requests use it, so the monthly AI-action limit — which only "
            "exists to cap our provider bill — stops applying to them. It unlocks no paid "
            "features. Switching this off immediately stops every saved key being used, "
            "and users can still delete theirs."
        ),
    },
    {
        "key": "RAZORPAY_KEY_ID",
        "category": "payments",
        "value_type": "string",
        "description": (
            "Razorpay Key ID (starts with rzp_live_ or rzp_test_). Identifies your account to "
            "checkout; it is public by design. Rupee payments (UPI AutoPay, cards, netbanking) "
            "stay unavailable until this and the Key Secret are both set."
        ),
    },
    {
        "key": "RAZORPAY_KEY_SECRET",
        "category": "payments",
        "value_type": "string",
        "description": "Razorpay Key Secret. Stored encrypted; never shown again after saving.",
    },
    {
        "key": "RAZORPAY_WEBHOOK_SECRET",
        "category": "payments",
        "value_type": "string",
        "description": (
            "Secret you choose when adding the webhook in the Razorpay Dashboard "
            "(URL: <api>/api/billing/razorpay/webhook/). Stored encrypted. Without it, renewals "
            "and failed payments are not picked up automatically."
        ),
    },
    {
        "key": "RAZORPAY_PLAN_ID_PRO_INR_MONTHLY",
        "category": "payments",
        "value_type": "string",
        "description": (
            "Razorpay Plan id (plan_...) for Pro monthly in rupees. Create the plan in the Razorpay "
            "Dashboard with the same amount shown on the pricing page, then paste its id here."
        ),
    },
    {
        "key": "RAZORPAY_PLAN_ID_PRO_INR_ANNUAL",
        "category": "payments",
        "value_type": "string",
        "description": (
            "Razorpay Plan id (plan_...) for Pro annual in rupees. Create the plan in the Razorpay "
            "Dashboard with the same amount shown on the pricing page, then paste its id here."
        ),
    },
    {
        "key": "RAZORPAY_PLAN_ID_MAX_INR_MONTHLY",
        "category": "payments",
        "value_type": "string",
        "description": (
            "Razorpay Plan id (plan_...) for Max monthly in rupees. Create the plan in the Razorpay "
            "Dashboard with the same amount shown on the pricing page, then paste its id here."
        ),
    },
    {
        "key": "RAZORPAY_PLAN_ID_MAX_INR_ANNUAL",
        "category": "payments",
        "value_type": "string",
        "description": (
            "Razorpay Plan id (plan_...) for Max annual in rupees. Create the plan in the Razorpay "
            "Dashboard with the same amount shown on the pricing page, then paste its id here."
        ),
    },
    {
        "key": "STRIPE_ENABLED",
        "category": "payments",
        "value_type": "boolean",
        "description": (
            "Turn on euro and pound checkout through Stripe. Off for the India launch: only "
            "rupee payments through Razorpay are offered. Stripe keys are still read from the "
            "server environment."
        ),
    },
    {
        "key": "GST_ENABLED",
        "category": "tax",
        "value_type": "boolean",
        "description": (
            "Charge Indian GST on rupee subscriptions. Turn on only once the business holds "
            "a GSTIN — an unregistered supplier must not collect GST. Prices stay the same "
            "either way (they include GST); this changes what the invoice shows."
        ),
    },
    {
        "key": "GST_RATE_PERCENT",
        "category": "tax",
        "value_type": "string",
        "description": "GST rate for the subscription, in percent (18 for software services).",
    },
    {
        "key": "GST_SELLER_GSTIN",
        "category": "tax",
        "value_type": "string",
        "description": "The business's 15-character GSTIN. An invalid one is ignored and no GST is charged until it is corrected.",
    },
    {
        "key": "GST_SELLER_STATE_CODE",
        "category": "tax",
        "value_type": "string",
        "description": "Two-digit GST state code of the business (e.g. 27 for Maharashtra). Read from the GSTIN when that is set.",
    },
    {
        "key": "GST_SELLER_LEGAL_NAME",
        "category": "tax",
        "value_type": "string",
        "description": "Legal name printed on invoices.",
    },
    {
        "key": "GST_SELLER_ADDRESS",
        "category": "tax",
        "value_type": "string",
        "description": "Registered address printed on invoices.",
    },
    {
        "key": "GST_SAC_CODE",
        "category": "tax",
        "value_type": "string",
        "description": "Services Accounting Code printed on tax invoices. Confirm the right code with your accountant.",
    },
    {
        "key": "GST_INVOICE_PREFIX",
        "category": "tax",
        "value_type": "string",
        "description": "Up to four letters starting every invoice number (PREFIX/2627/000001). Changing it mid-year starts a visibly different series.",
    },
    {
        "key": "MAX_RESUMES_PER_USER",
        "category": "limits",
        "value_type": "integer",
        "description": "Maximum number of resumes a user can upload",
    },
    {
        "key": "REWARDED_ADS_ENABLED",
        "category": "ads",
        "value_type": "boolean",
        "description": "Let free-tier users watch a rewarded video to earn a bonus credit.",
    },
    {
        "key": "REWARDED_AD_PROVIDER",
        "category": "ads",
        "value_type": "string",
        "description": "Which rewarded-ad network serves the video: admanager (Google Ad Manager) or partner (a third-party SDK configured via env).",
        "choices": [
            {"value": "admanager", "label": "Google Ad Manager (SSV verified)"},
            {"value": "partner", "label": "Partner SDK (third-party, configured via env)"},
        ],
    },
    {
        "key": "REWARDED_AD_CREDITS_PER_VIEW",
        "category": "ads",
        "value_type": "integer",
        "description": "Bonus credits granted per completed rewarded ad. One credit = one extra AI action and one extra tailored resume.",
    },
    {
        "key": "REWARDED_AD_MAX_PER_DAY",
        "category": "ads",
        "value_type": "integer",
        "description": "Most rewarded ads one account may be credited for per day.",
    },
    {
        "key": "REWARDED_AD_CREDIT_TTL_DAYS",
        "category": "ads",
        "value_type": "integer",
        "description": "Days an earned credit stays usable before it expires.",
    },
    {
        "key": "REPLY_TRACKING_ENABLED",
        "category": "limits",
        "value_type": "boolean",
        "description": "Master switch for detecting employer replies to applications. Off means no plan gets it, whatever the per-plan settings say - use this if the Gmail security assessment lapses.",
    },
    {
        "key": "REPLY_TRACKING_FREE",
        "category": "limits",
        "value_type": "string",
        "description": "Recruiter email tracking on the Free plan: alias (reply addresses + forwarding address, limited to REPLY_TRACKING_FREE_JOB_LIMIT jobs) or none. Anything unrecognised means none.",
    },
    {
        "key": "REPLY_TRACKING_PRO",
        "category": "limits",
        "value_type": "string",
        "description": "Recruiter email tracking on Pro: alias (reply addresses on Direct Apply emails plus a personal forwarding address - no mailbox access) or none. gmail is not built yet and behaves as alias.",
    },
    {
        "key": "REPLY_TRACKING_MAX",
        "category": "limits",
        "value_type": "string",
        "description": "Recruiter email tracking on Max: alias or none. gmail is not built yet and behaves as alias.",
    },
    {
        "key": "REPLY_TRACKING_FREE_JOB_LIMIT",
        "category": "limits",
        "value_type": "integer",
        "description": "How many jobs a Free account can track recruiter emails for. Paid plans are unlimited.",
    },
    {
        "key": "INBOUND_EMAIL_ENABLED",
        "category": "limits",
        "value_type": "boolean",
        "description": "Recruiter email receiving is live. Turn on only after Cloudflare Email Routing delivers test mail end to end; while off, Direct Apply replies go only to the user's own address.",
    },
    {
        "key": "PRO_JOB_APPLICATIONS_PER_MONTH",
        "category": "limits",
        "value_type": "integer",
        "description": "Applications a Pro account may send per month. An abuse ceiling on our sending reputation before it is a plan feature.",
    },
    {
        "key": "MAX_JOB_APPLICATIONS_PER_MONTH",
        "category": "limits",
        "value_type": "integer",
        "description": "Applications a Max account may send per month.",
    },
    {
        "key": "DISPLAY_ADS_ENABLED",
        "category": "ads",
        "value_type": "boolean",
        "description": "Show banner ads to free-tier visitors on marketing and blog pages. Pro subscribers never see them.",
    },
    {
        "key": "DISPLAY_AD_PROVIDER",
        "category": "ads",
        "value_type": "string",
        "description": "Which network serves the banners: adsense (Google AdSense), admanager (Google Ad Manager), or none. Anything unrecognised serves no ads rather than guessing.",
    },
    {
        "key": "ADSENSE_CLIENT_ID",
        "category": "ads",
        "value_type": "string",
        "description": "AdSense publisher id (ca-pub-…), used when DISPLAY_AD_PROVIDER is adsense. Public — it appears in the page HTML.",
    },
    {
        "key": "GAM_NETWORK_CODE",
        "category": "ads",
        "value_type": "string",
        "description": "Google Ad Manager network code, used when DISPLAY_AD_PROVIDER is admanager. Also used by the rewarded-video provider.",
    },
    {
        "key": "DISPLAY_AD_UNIT_ARTICLE_TOP",
        "category": "ads",
        "value_type": "string",
        "description": "Ad id for the top-of-article placement: an AdSense slot id, or an Ad Manager ad unit path (/network/unit).",
    },
    {
        "key": "DISPLAY_AD_UNIT_ARTICLE_BOTTOM",
        "category": "ads",
        "value_type": "string",
        "description": "Ad id for the end-of-article placement.",
    },
    {
        "key": "DISPLAY_AD_UNIT_BLOG_INDEX",
        "category": "ads",
        "value_type": "string",
        "description": "Ad id for the blog index placement.",
    },
    {
        "key": "FREE_AI_CALLS_PER_MONTH",
        "category": "limits",
        "value_type": "integer",
        "description": "Blanket cap on AI-backed actions per month for a Free-plan user (resume parsing, job analysis, gap analysis, missing-information review, tailored resume generation combined). Pro is unlimited.",
    },
    {
        "key": "FREE_TAILORED_RESUMES_PER_MONTH",
        "category": "limits",
        "value_type": "integer",
        "description": "How many job-specific tailored resumes a Free-plan user can generate per month. Pro is unlimited.",
    },
    {
        "key": "LAUNCH_FREE_ACCESS",
        "category": "limits",
        "value_type": "boolean",
        "description": "Free launch offer. On: every user without a paid plan gets all features free, checkout is closed, and AI use is limited per rolling window (settings below) instead of per month. Existing paid subscribers are unaffected.",
    },
    {
        "key": "LAUNCH_FREE_UNTIL",
        "category": "limits",
        "value_type": "string",
        "description": "Optional last day of the free launch offer, as YYYY-MM-DD. The offer switches off automatically after that day. Leave empty for no end date.",
    },
    {
        "key": "LAUNCH_QUOTA_WINDOW_HOURS",
        "category": "limits",
        "value_type": "integer",
        "description": "Length of the rolling usage window during the free launch offer, in hours (default 12).",
    },
    {
        "key": "LAUNCH_TAILORED_RESUMES_PER_WINDOW",
        "category": "limits",
        "value_type": "integer",
        "description": "Tailored resumes a user can generate per window during the free launch offer (default 3).",
    },
    {
        "key": "LAUNCH_AI_CALLS_PER_WINDOW",
        "category": "limits",
        "value_type": "integer",
        "description": "AI-backed actions per window during the free launch offer, covering every AI feature combined (default 15). One tailored resume uses 3-4.",
    },
    {
        "key": "LAUNCH_SHOW_ADS",
        "category": "limits",
        "value_type": "boolean",
        "description": "Show ads to users during the free launch offer (only when display ads are enabled in the Ads section).",
    },
    {
        "key": "AI_GLOBAL_DAILY_CAP",
        "category": "limits",
        "value_type": "integer",
        "description": "Site-wide ceiling on AI-backed actions per day (midnight to midnight IST) for users without a paid plan. Protects the AI bill from sign-up spikes and abuse. Paying users and users on their own AI key are never stopped. 0 = no ceiling.",
    },
    {
        "key": "AI_CAPACITY_ALERT_PERCENT",
        "category": "limits",
        "value_type": "integer",
        "description": "Email the ops address when today's AI usage reaches this percentage of the daily ceiling (and again at 100%).",
    },
    {
        "key": "ALLOWED_JOB_URL_DOMAINS",
        "category": "limits",
        "value_type": "list",
        "description": "Comma-separated list of domains allowed for job URL parsing. Only used when 'allow any public site' is off.",
    },
    {
        "key": "JOB_URL_ALLOW_ANY_PUBLIC_SITE",
        "category": "limits",
        "value_type": "boolean",
        "description": "Import job links from any public website, including companies' own career pages. Server-side safety checks still apply. Off = only the allowed domains list.",
    },
    {
        "key": "BOT_WALLED_JOB_DOMAINS",
        "category": "limits",
        "value_type": "list",
        "description": "Job sites that block server requests. Imports from these fail instantly with a clear message instead of waiting. 'name.*' matches any suffix, e.g. indeed.*",
    },
    {
        "key": "JOB_IMPORT_TIME_BUDGET_SECONDS",
        "category": "limits",
        "value_type": "float",
        "description": "Maximum total seconds one job-link import may take.",
    },
    {
        "key": "JOB_IMPORT_CACHE_SECONDS",
        "category": "limits",
        "value_type": "integer",
        "description": "How long (seconds) a successful job-link import is reused for the same link.",
    },
    {
        "key": "SITE_THEME",
        "category": "appearance",
        "value_type": "string",
        "description": (
            "The brand colour across the whole site: buttons, links, highlights "
            "and focus rings, in both light and dark mode. Every option keeps "
            "text readable (WCAG AA contrast). Takes effect on the next page "
            "load, no redeploy needed."
        ),
        "choices": [
            {"value": "indigo", "label": "Indigo (default)"},
            {"value": "ocean", "label": "Ocean blue"},
            {"value": "emerald", "label": "Emerald green"},
            {"value": "violet", "label": "Violet"},
            {"value": "rose", "label": "Rose"},
            {"value": "sunset", "label": "Sunset orange"},
        ],
    },
    {
        "key": "SITE_DEFAULT_COLOR_MODE",
        "category": "appearance",
        "value_type": "string",
        "description": (
            "Light or dark for a visitor who hasn't picked one themselves. "
            "\"Match their device\" follows the visitor's system setting. A "
            "visitor's own choice from the theme toggle always wins."
        ),
        "choices": [
            {"value": "system", "label": "Match their device"},
            {"value": "light", "label": "Light"},
            {"value": "dark", "label": "Dark"},
        ],
    },
    {
        "key": "LOGIN_OTP_REQUIRED",
        "category": "security",
        "value_type": "boolean",
        "description": (
            "Require an emailed one-time code after the password when logging in. "
            "Enabled = two-factor login (recommended). Disabled = the password "
            "alone signs a user in, so a leaked or reused password is enough to "
            "take over an account. Takes effect on the next login attempt, no "
            "redeploy needed."
        ),
    },
    {
        "key": "LOGIN_MAX_FAILED_ATTEMPTS",
        "category": "security",
        "value_type": "integer",
        "description": (
            "Wrong passwords in a row before an account is locked. While locked, "
            "every password is refused, right or wrong. 0 turns the lockout off. "
            "Allowed range 0-100."
        ),
    },
    {
        "key": "LOGIN_LOCKOUT_MINUTES",
        "category": "security",
        "value_type": "integer",
        "description": (
            "How long a locked account stays locked. Kept short on purpose so an "
            "attacker can't lock a real user out for good; resetting the password "
            "unlocks it straight away. Allowed range 1-1440."
        ),
    },
    {
        "key": "OTP_MAX_ATTEMPTS",
        "category": "security",
        "value_type": "integer",
        "description": (
            "Wrong guesses allowed on one sign-in or password-reset code (and on an "
            "authenticator prompt) before the code is void and a new one is needed. "
            "Allowed range 1-20."
        ),
    },
    {
        "key": "OTP_EXPIRY_MINUTES",
        "category": "security",
        "value_type": "integer",
        "description": (
            "How long an emailed code (sign-in or password reset) stays valid, "
            "and how long the authenticator prompt waits. The email states this "
            "number. Allowed range 1-60."
        ),
    },
    {
        "key": "OTP_RESEND_COOLDOWN_SECONDS",
        "category": "security",
        "value_type": "integer",
        "description": (
            "Minimum seconds between two emailed codes to the same account. Asking "
            "again sooner keeps the code already sent. Allowed range 0-3600."
        ),
    },
    {
        "key": "TOTP_2FA_ENABLED",
        "category": "security",
        "value_type": "boolean",
        "description": (
            "Let users add an authenticator app (Google Authenticator, 1Password, "
            "Authy...) from Settings. Users who have one are asked for its code at "
            "every sign-in, including Google sign-in, instead of the emailed code. "
            "Turning this off stops new setups and stops asking, but keeps each "
            "user's setup so turning it back on restores it."
        ),
    },
    {
        "key": "TOTP_ISSUER_NAME",
        "category": "security",
        "value_type": "string",
        "description": "The name shown next to the account inside the user's authenticator app.",
    },
]


def serialize_setting_value(value, value_type):
    if value_type == "list" and isinstance(value, list):
        return ",".join(value)
    if value_type == "boolean":
        return "true" if value else "false"
    return str(value) if value is not None else ""


def is_sensitive_key(key):
    return is_secret_key(key)


class SystemConfigurationListUpdateAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        configs = {c.key: c for c in SystemConfiguration.objects.all()}
        data = []
        for default in DEFAULT_CONFIGS:
            key = default["key"]
            if key in configs:
                config = configs[key]
                val = config.value
                if is_sensitive_key(key) and val:
                    val = "********"
                data.append({
                    "key": key,
                    "value": val,
                    "description": config.description or default["description"],
                    "category": config.category or default["category"],
                    "value_type": config.value_type or default["value_type"],
                    "updated_at": config.updated_at,
                    "is_custom": True,
                    "is_secret": is_sensitive_key(key),
                    "choices": default.get("choices"),
                })
            else:
                setting_val = getattr(settings, key, None)
                serialized_val = serialize_setting_value(setting_val, default["value_type"])
                if is_sensitive_key(key) and serialized_val:
                    serialized_val = "********"
                data.append({
                    "key": key,
                    "value": serialized_val,
                    "description": default["description"],
                    "category": default["category"],
                    "value_type": default["value_type"],
                    "updated_at": None,
                    "is_custom": False,
                    "is_secret": is_sensitive_key(key),
                    "choices": default.get("choices"),
                })
        return Response(data, status=status.HTTP_200_OK)

    def put(self, request):
        if not isinstance(request.data, list):
            return Response({"detail": "Expected a list of configurations."}, status=status.HTTP_400_BAD_REQUEST)
        
        errors = []
        # Pre-validate all configurations before writing to DB
        for item in request.data:
            key = item.get("key")
            value = item.get("value")
            
            meta = next((d for d in DEFAULT_CONFIGS if d["key"] == key), None)
            if not meta:
                errors.append({"key": key, "detail": f"Unknown configuration key: {key}"})
                continue
                
            # If sensitive and value is masked, skip validation
            if is_sensitive_key(key) and value == "********":
                continue
                
            value_str = str(value) if value is not None else ""
            value_type = meta["value_type"]
            
            if value_type == "integer":
                try:
                    int(value_str)
                except ValueError:
                    errors.append({"key": key, "detail": f"{key} must be an integer."})
                    continue
            elif value_type == "float":
                try:
                    float(value_str)
                except ValueError:
                    errors.append({"key": key, "detail": f"{key} must be a float."})
                    continue
            elif value_type == "boolean":
                if value_str.strip().lower() not in ("true", "false", "yes", "no", "1", "0", "t", "f", "y", "n"):
                    errors.append({"key": key, "detail": f"{key} must be a boolean."})
                    continue

            choices = meta.get("choices")
            if choices and value_str not in {c["value"] for c in choices}:
                errors.append({"key": key, "detail": f"{key} must be one of the listed options."})
                continue
                    
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
            
        # Write valid ones
        existing = {c.key: c for c in SystemConfiguration.objects.filter(
            key__in=[item.get("key") for item in request.data]
        )}
        for item in request.data:
            key = item.get("key")
            value = item.get("value")

            # If sensitive and value is masked, do not modify/overwrite the configuration
            if is_sensitive_key(key) and value == "********":
                continue

            meta = next(d for d in DEFAULT_CONFIGS if d["key"] == key)
            value_str = (str(value) if value is not None else "").strip()
            previous = existing.get(key)

            if is_sensitive_key(key):
                if not value_str:
                    # Clearing a key removes the stored one; the server
                    # environment's key (if any) is used again.
                    if previous is not None:
                        SystemConfiguration.objects.filter(key=key).delete()
                        record_admin_action(
                            request, AdminAuditLog.ACTION_CONFIG_UPDATED, key,
                            detail={"change": "cleared", "was_configured": True},
                        )
                    continue
                # Encrypted at rest: a database dump or backup must not be a
                # list of working provider credentials.
                detail = {
                    "change": "updated" if previous is not None else "set",
                    "new_value_hint": mask_secret_for_audit(value_str),
                }
                value_str = encrypt_value(value_str)
            elif previous is not None and previous.value == value_str:
                # No actual change - a no-op save from the admin UI resending
                # every field. Not worth a row in the trail.
                continue
            else:
                detail = {"old_value": previous.value if previous else None, "new_value": value_str}

            SystemConfiguration.objects.update_or_create(
                key=key,
                defaults={
                    "value": value_str,
                    "description": meta["description"],
                    "category": meta["category"],
                    "value_type": meta["value_type"],
                }
            )
            record_admin_action(request, AdminAuditLog.ACTION_CONFIG_UPDATED, key, detail=detail)

        return self.get(request)


class SystemConfigurationResetAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        # Stored API keys are credentials, not tunables: wiping them would take
        # the AI down on a click meant to undo a bad setting.
        rows = SystemConfiguration.objects.all()
        secret_ids = [row.pk for row in rows if is_sensitive_key(row.key)]
        reset_keys = list(rows.exclude(pk__in=secret_ids).values_list("key", flat=True))
        rows.exclude(pk__in=secret_ids).delete()
        record_admin_action(
            request, AdminAuditLog.ACTION_CONFIG_RESET, "all",
            detail={"reset_keys": reset_keys},
        )
        return Response(
            {"detail": "Settings reset to defaults. Stored API keys were kept."},
            status=status.HTTP_200_OK,
        )


class SystemConfigurationTestAIAPIView(APIView):
    """Staff-only diagnostic endpoint to test an AI provider/model live.

    Allows an admin to test saved credentials or verify newly entered
    API keys/tokens before saving them to SystemConfiguration.
    """
    permission_classes = [permissions.IsAdminUser]
    throttle_scope = "admin_test_ai"

    def post(self, request):
        provider = (request.data.get("provider") or "").strip().lower()
        if not provider:
            provider = resolve_provider()
        if provider not in SUPPORTED_PROVIDERS:
            return Response(
                {"ok": False, "message": f"Unsupported provider: {provider}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_key_input = request.data.get("api_key")
        model_input = (request.data.get("model") or "").strip()

        # If masked or empty, resolve the configured provider keys
        if not api_key_input or api_key_input.strip() == "********":
            resolved_keys = _provider_api_keys(f"{provider.upper()}_API_KEY")
            api_key_to_use = None
        else:
            resolved_keys = _parse_api_keys(api_key_input)
            api_key_to_use = api_key_input

        token_count = len(resolved_keys)
        if not resolved_keys and provider != "ollama":
            return Response(
                {
                    "ok": False,
                    "provider": provider,
                    "token_count": 0,
                    "active_tokens": 0,
                    "error_code": "LLM_AUTH_FAILED",
                    "message": f"No API token configured for {provider}. Enter a valid token first.",
                },
                status=status.HTTP_200_OK,
            )

        timeout = min(int(request.data.get("timeout") or 15), 30)
        try:
            client = _build_client(
                provider=provider,
                timeout=timeout,
                max_retries=1,
                temperature=0.0,
                max_output_tokens=64,
                api_key_override=api_key_to_use,
            )
        except Exception as exc:
            return Response(
                {
                    "ok": False,
                    "provider": provider,
                    "token_count": token_count,
                    "message": f"Failed to initialize {provider} client: {exc}",
                },
                status=status.HTTP_200_OK,
            )

        # Determine target model to test
        target_model = model_input or getattr(client, "model", None)
        if not target_model:
            candidates = getattr(client, "candidate_models", lambda: [])()
            target_model = candidates[0] if candidates else None

        if not target_model and provider != "ollama":
            return Response(
                {
                    "ok": False,
                    "provider": provider,
                    "token_count": token_count,
                    "message": f"No model specified or configured for {provider}.",
                },
                status=status.HTTP_200_OK,
            )

        started = time.monotonic()
        tested_models = []
        overall_ok = False
        message = ""

        probe_fn = getattr(client, "probe_model", None)
        if callable(probe_fn):
            ok, detail = probe_fn(target_model)
            latency_ms = int((time.monotonic() - started) * 1000)
            tested_models.append({
                "model": target_model,
                "ok": ok,
                "latency_ms": latency_ms,
                "detail": detail,
            })
            overall_ok = ok
            if ok:
                message = f"{provider.capitalize()} model '{target_model}' responded successfully in {latency_ms}ms."
            else:
                message = f"{provider.capitalize()} model '{target_model}' test failed: {detail}"
        else:
            try:
                available = client.check_model_available()
                latency_ms = int((time.monotonic() - started) * 1000)
                overall_ok = bool(available)
                tested_models.append({
                    "model": target_model or "default",
                    "ok": overall_ok,
                    "latency_ms": latency_ms,
                    "detail": "Model is available",
                })
                message = f"{provider.capitalize()} is reachable and model is available ({latency_ms}ms)."
            except Exception as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                overall_ok = False
                tested_models.append({
                    "model": target_model or "default",
                    "ok": False,
                    "latency_ms": latency_ms,
                    "detail": str(exc),
                })
                message = f"{provider.capitalize()} connection failed: {exc}"

        candidate_list = []
        if hasattr(client, "candidate_models"):
            try:
                candidate_list = client.candidate_models()
            except Exception:
                pass

        active_tokens_count = len(getattr(getattr(client, "key_pool", None), "active_keys", lambda: resolved_keys)())

        return Response(
            {
                "ok": overall_ok,
                "provider": provider,
                "model": target_model,
                "latency_ms": latency_ms,
                "token_count": token_count,
                "active_tokens": active_tokens_count,
                "message": message,
                "tested_models": tested_models,
                "candidate_models": candidate_list,
            },
            status=status.HTTP_200_OK,
        )


class AdminUserPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminUserListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        search_query = request.query_params.get("search", "").strip()
        queryset = User.objects.annotate(resume_count=Count("resumes")).order_by("-date_joined")
        
        if search_query:
            queryset = queryset.filter(
                models.Q(email__icontains=search_query) |
                models.Q(name__icontains=search_query) |
                models.Q(phone__icontains=search_query)
            )

        paginator = AdminUserPagination()
        paginated_queryset = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AdminUserSerializer(paginated_queryset, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminUserToggleActiveAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, user_id):
        if request.user.id == user_id:
            return Response(
                {"detail": "You cannot deactivate your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
            
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        if user.is_superuser and not request.user.is_superuser:
            return Response(
                {"detail": "Only superusers can modify another superuser account."},
                status=status.HTTP_403_FORBIDDEN,
            )
            
        user.is_active = not user.is_active
        user.save()
        record_admin_action(
            request,
            AdminAuditLog.ACTION_USER_ACTIVATED if user.is_active else AdminAuditLog.ACTION_USER_DEACTIVATED,
            f"user:{user.id}",
            detail={"user_email": user.email},
        )

        annotated_user = User.objects.annotate(resume_count=Count("resumes")).get(id=user_id)
        return Response(AdminUserSerializer(annotated_user).data, status=status.HTTP_200_OK)


class AdminUserSecurityResetAPIView(APIView):
    """Support tools for sign-in trouble: clear a lockout, or remove the
    user's authenticator app when they've lost their phone and their backup
    codes. Body: {"unlock": true} and/or {"remove_two_factor": true}.
    Only do the second after confirming who you're talking to - it removes
    their strongest protection."""

    permission_classes = [permissions.IsAdminUser]

    def post(self, request, user_id):
        from authentication.services import auth_security

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        if user.is_superuser and not request.user.is_superuser:
            return Response(
                {"detail": "Only superusers can modify another superuser account."},
                status=status.HTTP_403_FORBIDDEN,
            )

        unlock = bool(request.data.get("unlock"))
        remove_two_factor = bool(request.data.get("remove_two_factor"))
        if not unlock and not remove_two_factor:
            return Response(
                {"detail": "Nothing to do: send unlock and/or remove_two_factor."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if unlock:
            user.failed_login_attempts = 0
            user.locked_until = None
            user.save(update_fields=["failed_login_attempts", "locked_until"])
            record_admin_action(
                request, AdminAuditLog.ACTION_USER_UNLOCKED, f"user:{user.id}",
                detail={"user_email": user.email},
            )
        if remove_two_factor:
            auth_security.disable_totp(user)
            record_admin_action(
                request, AdminAuditLog.ACTION_USER_2FA_RESET, f"user:{user.id}",
                detail={"user_email": user.email},
            )

        annotated_user = User.objects.annotate(resume_count=Count("resumes")).get(id=user_id)
        return Response(AdminUserSerializer(annotated_user).data, status=status.HTTP_200_OK)


class AdminUserToggleStaffAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, user_id):
        if not request.user.is_superuser:
            return Response(
                {"detail": "Only superusers can grant or revoke admin privileges."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.user.id == user_id:
            return Response(
                {"detail": "You cannot remove your own admin status."},
                status=status.HTTP_400_BAD_REQUEST,
            )
            
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
            
        user.is_staff = not user.is_staff
        user.save()
        record_admin_action(
            request,
            AdminAuditLog.ACTION_USER_STAFF_GRANTED if user.is_staff else AdminAuditLog.ACTION_USER_STAFF_REVOKED,
            f"user:{user.id}",
            detail={"user_email": user.email},
        )

        annotated_user = User.objects.annotate(resume_count=Count("resumes")).get(id=user_id)
        return Response(AdminUserSerializer(annotated_user).data, status=status.HTTP_200_OK)


class AdminStatsAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from resumes.models import Resume
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        staff_users = User.objects.filter(is_staff=True).count()
        total_resumes = Resume.objects.count()

        return Response({
            "total_users": total_users,
            "active_users": active_users,
            "staff_users": staff_users,
            "total_resumes": total_resumes,
        }, status=status.HTTP_200_OK)


class AdminAuditLogPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminAuditLogListAPIView(APIView):
    """Read-only: who did what from the admin panel, and when. The only
    write path into AdminAuditLog is api.utils.audit.record_admin_action,
    called from every mutating view above - this just lists what has
    accumulated."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        queryset = AdminAuditLog.objects.select_related("actor").all()

        action = request.query_params.get("action", "").strip()
        if action:
            queryset = queryset.filter(action=action)

        target = request.query_params.get("target", "").strip()
        if target:
            queryset = queryset.filter(target__icontains=target)

        paginator = AdminAuditLogPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AdminAuditLogSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminAIModelStatsAPIView(APIView):
    """Aggregate statistics on AI model usage and latency for admin observability."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from datetime import timedelta
        from django.utils import timezone
        from django.db.models import Avg, Count, Max
        from billing.models import AIUsageLog

        time_range = request.query_params.get("range", "7d").strip().lower()
        now = timezone.now()
        qs = AIUsageLog.objects.all()

        if time_range == "24h":
            qs = qs.filter(created_at__gte=now - timedelta(hours=24))
        elif time_range == "7d":
            qs = qs.filter(created_at__gte=now - timedelta(days=7))
        elif time_range == "30d":
            qs = qs.filter(created_at__gte=now - timedelta(days=30))
        # "all" does not filter created_at

        total_calls = qs.count()

        model_stats_raw = (
            qs.values("model_name", "provider")
            .annotate(
                count=Count("id"),
                avg_latency=Avg("latency_ms"),
                last_used=Max("created_at"),
            )
            .order_by("-count")
        )
        models = [
            {
                "model_name": item["model_name"] or "default",
                "provider": item["provider"],
                "total_calls": item["count"],
                "avg_latency_ms": int(item["avg_latency"]) if item["avg_latency"] is not None else None,
                "last_used": item["last_used"].isoformat() if item["last_used"] else None,
            }
            for item in model_stats_raw
        ]

        provider_stats_raw = (
            qs.values("provider")
            .annotate(
                count=Count("id"),
                avg_latency=Avg("latency_ms"),
            )
            .order_by("-count")
        )
        providers = [
            {
                "provider": item["provider"] or "unknown",
                "total_calls": item["count"],
                "avg_latency_ms": int(item["avg_latency"]) if item["avg_latency"] is not None else None,
            }
            for item in provider_stats_raw
        ]

        action_stats_raw = (
            qs.values("action")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        actions = [
            {
                "action": item["action"],
                "total_calls": item["count"],
            }
            for item in action_stats_raw
        ]

        recent_calls = [
            {
                "id": log.id,
                "user_email": log.user.email if log.user else "",
                "model_name": log.model_name or "default",
                "provider": log.provider,
                "action": log.action,
                "latency_ms": log.latency_ms,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in qs.select_related("user").order_by("-created_at")[:25]
        ]

        overall_avg_latency = qs.aggregate(avg=Avg("latency_ms"))["avg"]

        return Response({
            "range": time_range,
            "total_calls": total_calls,
            "avg_latency_ms": int(overall_avg_latency) if overall_avg_latency is not None else None,
            "models": models,
            "providers": providers,
            "actions": actions,
            "recent_calls": recent_calls,
        }, status=status.HTTP_200_OK)
