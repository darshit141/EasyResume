"""The plan catalog — centralized so pricing can change (amounts, adding a
currency, adding a billing period) without touching any other file.

Pro's EUR/GBP amounts below are the adopted launch pricing, not a
placeholder (COMMERCIAL_READINESS_AUDIT.md's original starting hypothesis,
now finalized):

  - EUR 14.99/mo, GBP 12.99/mo — deliberately below premium competitors in
    this category (comparable AI resume tools commonly run $20-29/mo).
    This is penetration pricing: a new entrant with no reviews or brand
    equity yet competes on price first and moves upmarket once retention
    and NPS data justify it, not before.
  - EUR 119/yr, GBP 99/yr — roughly a 34-36% discount over paying monthly,
    intentionally more generous than a typical "two months free" (~17%)
    structure. For a pre-revenue product, converting a monthly signup into
    an annual one is worth more than the margin given up: it locks in cash
    up front and meaningfully lowers early-churn risk while the product is
    still building a track record.

Override any of them via env without a code change — revisit both numbers
once there's real conversion/retention data to react to, not on a
schedule.

`stripe_price_id` is intentionally blank by default: a real Price object
must be created in the Stripe Dashboard for each (currency, billing
period) combination in each environment (test vs. live mode have
different Price IDs) and set via env. Until it's set, that specific
currency/period combination simply can't be checked out — a clear, safe
failure rather than silently charging the wrong amount.
"""

import os

from billing.entitlements import FREE_AI_CALLS_PER_MONTH

SUPPORTED_CURRENCIES = ("EUR", "GBP")
DEFAULT_CURRENCY = "EUR"
BILLING_PERIODS = ("monthly", "annual")
CURRENCY_SYMBOLS = {"EUR": "€", "GBP": "£"}


def _price(currency, period, default_cents, env_price_id):
    return {
        "amount_cents": int(
            os.getenv(f"PRO_PRICE_{currency}_{period.upper()}_CENTS", str(default_cents))
        ),
        "stripe_price_id": os.getenv(env_price_id, ""),
    }


FREE_PLAN = {
    "id": "free",
    "name": "Free",
    "features": [
        "Up to 5 resumes",
        "2 job-specific tailored resumes / month",
        "PDF download",
        "Choice of AI provider (Ollama, Groq, Gemini)",
        f"{FREE_AI_CALLS_PER_MONTH} AI-assisted actions / month",
        "Supported by ads",
    ],
    # No prices — free forever, nothing to check out.
    "prices": {},
}

PRO_PLAN = {
    "id": "pro",
    "name": "Pro",
    "features": [
        "Unlimited resumes, unlimited versions",
        "Unlimited job-specific tailored resumes",
        "Unlimited AI-assisted actions",
        "PDF + DOCX download",
        "Full version history & comparisons",
        "No ads",
    ],
    "prices": {
        "EUR": {
            "monthly": _price("EUR", "monthly", 1499, "STRIPE_PRICE_ID_PRO_EUR_MONTHLY"),
            "annual": _price("EUR", "annual", 11900, "STRIPE_PRICE_ID_PRO_EUR_ANNUAL"),
        },
        "GBP": {
            "monthly": _price("GBP", "monthly", 1299, "STRIPE_PRICE_ID_PRO_GBP_MONTHLY"),
            "annual": _price("GBP", "annual", 9900, "STRIPE_PRICE_ID_PRO_GBP_ANNUAL"),
        },
    },
}

PLANS = [FREE_PLAN, PRO_PLAN]
PLANS_BY_ID = {plan["id"]: plan for plan in PLANS}


def get_plan(plan_id):
    return PLANS_BY_ID.get(plan_id)


def normalize_currency(currency):
    currency = (currency or DEFAULT_CURRENCY).upper()
    return currency if currency in SUPPORTED_CURRENCIES else DEFAULT_CURRENCY


def find_plan_by_stripe_price_id(stripe_price_id):
    """Reverse lookup for webhook handling: given a Stripe Price ID actually
    on a subscription, return the (plan_id, currency, billing_period) it
    corresponds to in our catalog, or None if it's not one of ours (e.g. a
    stale/manually-created Stripe price). Used so a plan/billing-period
    change made through the Stripe Customer Portal — not through our own
    checkout — still gets reflected locally instead of leaving `Subscription`
    frozen at whatever it was at initial checkout."""
    if not stripe_price_id:
        return None
    for plan in PLANS:
        for currency, periods in plan.get("prices", {}).items():
            for billing_period, price in periods.items():
                if price.get("stripe_price_id") == stripe_price_id:
                    return plan["id"], currency, billing_period
    return None


def get_price(plan_id, currency, billing_period):
    """Returns the {amount_cents, stripe_price_id} dict for a plan/currency/
    period combination, or None if the plan has no prices (free) or that
    combination isn't configured."""
    plan = get_plan(plan_id)
    if not plan or not plan.get("prices"):
        return None
    return plan["prices"].get(normalize_currency(currency), {}).get(billing_period)


def format_price_display(amount_cents, currency, billing_period):
    symbol = CURRENCY_SYMBOLS.get(currency, "")
    amount = amount_cents / 100
    # Whole-number amounts read cleaner without trailing ".00" (matches the
    # audit's own "€119/yr", not "€119.00/yr").
    amount_str = f"{amount:g}"
    suffix = "/mo" if billing_period == "monthly" else "/yr"
    return f"{symbol}{amount_str}{suffix}"
