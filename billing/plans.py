"""The plan catalog. Every price and limit here is a PLACEHOLDER — nothing
in this file has been reviewed as a real business decision. Replace the
amounts, limits, and Stripe Price IDs with real ones before launch.

`stripe_price_id` is intentionally left blank by default: it must be
created in the Stripe Dashboard (Product catalog) for each real environment
(test vs. live mode have different Price IDs) and set via env. Until it's
set, that plan simply can't be checked out — a clear, safe failure rather
than silently charging the wrong amount.
"""

import os

from billing.entitlements import FREE_AI_CALLS_PER_MONTH

FREE_PLAN = {
    "id": "free",
    "name": "Free",
    "price_display": "$0/mo",
    "price_cents": 0,
    "stripe_price_id": "",
    "features": [
        "1 verified resume",
        "2 job-specific tailored resumes / month",
        "PDF download",
        "Choice of AI provider (Ollama, Groq, Gemini)",
        f"{FREE_AI_CALLS_PER_MONTH} AI-assisted actions / month",
        "Supported by ads",
    ],
}

PRO_PLAN = {
    "id": "pro",
    "name": "Pro",
    "price_display": "$12/mo",
    "price_cents": 1200,
    "stripe_price_id": os.getenv("STRIPE_PRICE_ID_PRO", ""),
    "features": [
        "1 verified resume, unlimited versions",
        "Unlimited job-specific tailored resumes",
        "Unlimited AI-assisted actions",
        "PDF + DOCX download",
        "Full version history & comparisons",
        "No ads",
    ],
}

PLANS = [FREE_PLAN, PRO_PLAN]
PLANS_BY_ID = {plan["id"]: plan for plan in PLANS}


def get_plan(plan_id):
    return PLANS_BY_ID.get(plan_id)
