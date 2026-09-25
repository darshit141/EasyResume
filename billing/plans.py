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

import logging
import os

from billing.entitlements import FREE_AI_CALLS_PER_MONTH

SUPPORTED_CURRENCIES = ("EUR", "GBP", "INR")
DEFAULT_CURRENCY = "EUR"
BILLING_PERIODS = ("monthly", "annual")
CURRENCY_SYMBOLS = {"EUR": "€", "GBP": "£", "INR": "₹"}

# Which payment processor can actually take money in each currency.
#
# INR goes through Razorpay, not Stripe, and not by preference. The business
# is an Indian private limited company, and Stripe is invite-only for Indian
# businesses; its UPI support is offered to accounts in other countries
# only, and Stripe Tax cannot be used by an India-based business either.
# Razorpay supports UPI AutoPay, cards and bank e-mandates for recurring
# billing, which is what an Indian subscriber expects to see at checkout.
PROCESSOR_STRIPE = "stripe"
PROCESSOR_RAZORPAY = "razorpay"
CURRENCY_PROCESSORS = {"EUR": PROCESSOR_STRIPE, "GBP": PROCESSOR_STRIPE, "INR": PROCESSOR_RAZORPAY}

# Prices in these currencies already include tax (see billing/gst.py for
# why INR is priced that way). EUR and GBP prices exclude VAT, which Stripe
# adds at checkout.
TAX_INCLUSIVE_CURRENCIES = frozenset({"INR"})

# RBI caps a UPI AutoPay debit at Rs 15,000 without fresh authentication, so
# a recurring INR price above this cannot renew over UPI. Every INR price
# in the catalog sits far below it; the check exists so an admin price edit
# can't quietly break UPI renewals.
UPI_AUTOPAY_MAX_PAISE = 1_500_000

logger = logging.getLogger(__name__)


def processor_for(currency):
    return CURRENCY_PROCESSORS.get(normalize_currency(currency), PROCESSOR_STRIPE)


def _price(plan_id, currency, period, default_cents, env_price_id):
    """One catalog price.

    `processor_price_id` is the identifier checkout hands to the processor:
    a Stripe Price for EUR/GBP, a Razorpay Plan for INR. `stripe_price_id`
    is kept alongside it, blank for non-Stripe currencies, because a great
    deal of existing code (and the admin) reads that key by name.
    """
    processor = CURRENCY_PROCESSORS[currency]
    processor_price_id = os.getenv(env_price_id, "")
    # The plan id is part of the amount's env key: it used to be hardcoded
    # to PRO_, so a second tier would silently have read Pro's override.
    return {
        "amount_cents": int(
            os.getenv(
                f"{plan_id.upper()}_PRICE_{currency}_{period.upper()}_CENTS",
                str(default_cents),
            )
        ),
        "processor": processor,
        "processor_price_id": processor_price_id,
        "stripe_price_id": processor_price_id if processor == PROCESSOR_STRIPE else "",
    }


FREE_PLAN = {
    "id": "free",
    "name": "Free",
    "published": True,
    "features": [
        "Up to 5 resumes",
        "2 job-specific tailored resumes / month",
        "PDF download",
        f"{FREE_AI_CALLS_PER_MONTH} AI-assisted actions / month",
        "Supported by ads",
    ],
    # No prices — free forever, nothing to check out.
    "prices": {},
}

PRO_PLAN = {
    "id": "pro",
    "name": "Pro",
    "published": True,
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
            "monthly": _price("pro", "EUR", "monthly", 1499, "STRIPE_PRICE_ID_PRO_EUR_MONTHLY"),
            "annual": _price("pro", "EUR", "annual", 11900, "STRIPE_PRICE_ID_PRO_EUR_ANNUAL"),
        },
        "GBP": {
            "monthly": _price("pro", "GBP", "monthly", 1299, "STRIPE_PRICE_ID_PRO_GBP_MONTHLY"),
            "annual": _price("pro", "GBP", "annual", 9900, "STRIPE_PRICE_ID_PRO_GBP_ANNUAL"),
        },
        # Rs 499/mo and Rs 3,999/yr, GST included - a starting hypothesis,
        # not a finding, and editable from the admin like every other price.
        # India-native resume tools start around Rs 249/mo and international
        # ones are sold at around Rs 1,999/mo; this sits between them, which
        # is where "cheaper, but more value per outcome" belongs. It is
        # roughly purchasing-power parity with the EUR price rather than an
        # exchange-rate conversion of it, and the annual discount (~33%)
        # matches the EUR/GBP structure for the reason given above.
        "INR": {
            "monthly": _price("pro", "INR", "monthly", 49900, "RAZORPAY_PLAN_ID_PRO_INR_MONTHLY"),
            "annual": _price("pro", "INR", "annual", 399900, "RAZORPAY_PLAN_ID_PRO_INR_ANNUAL"),
        },
    },
}

# The tier that exists to fund third-party services billed to us per
# account, rather than to withhold something cheap from cheaper plans.
# Today that is Gmail reply tracking, which needs Google's restricted
# gmail.readonly scope and so an annual third-party security assessment
# (CASA) - a few thousand a year, fixed. Priced so a modest number of
# subscribers covers that bill rather than eating the Pro margin.
#
# Roughly 2x Pro. The annual discount (~31%) is kept in line with Pro's,
# for the same reason: cash up front and lower early churn are worth more
# than the margin given up while the product has no track record.
#
# Deliberately NOT listed here: anything not yet enforced in
# billing.entitlements. The header of that file is the rule - if it is a
# bullet on the pricing page it has to be a real check on the server.
MAX_PLAN = {
    "id": "max",
    "name": "Max",
    # Off until the tier can honestly be sold. Two of the bullets below
    # describe work that does not exist yet: reply tracking has an
    # entitlement but no machinery behind it, and there is no support
    # system to prioritise. Publishing it would advertise things we cannot
    # deliver - the rule at the top of billing/entitlements.py, applied to
    # a whole plan instead of a single flag.
    #
    # Flip PUBLISH_MAX_PLAN=True once reply tracking actually works and
    # Stripe prices exist. The tier is fully built and tested behind this;
    # only the shopfront is closed.
    "published": os.getenv("PUBLISH_MAX_PLAN", "False") == "True",
    "features": [
        "Everything in Pro",
        "Employer reply tracking on your applications",
        "500 applications sent / month",
        "Priority support",
    ],
    "prices": {
        "EUR": {
            "monthly": _price("max", "EUR", "monthly", 2999, "STRIPE_PRICE_ID_MAX_EUR_MONTHLY"),
            "annual": _price("max", "EUR", "annual", 24900, "STRIPE_PRICE_ID_MAX_EUR_ANNUAL"),
        },
        "GBP": {
            "monthly": _price("max", "GBP", "monthly", 2499, "STRIPE_PRICE_ID_MAX_GBP_MONTHLY"),
            "annual": _price("max", "GBP", "annual", 20900, "STRIPE_PRICE_ID_MAX_GBP_ANNUAL"),
        },
        "INR": {
            "monthly": _price("max", "INR", "monthly", 99900, "RAZORPAY_PLAN_ID_MAX_INR_MONTHLY"),
            "annual": _price("max", "INR", "annual", 799900, "RAZORPAY_PLAN_ID_MAX_INR_ANNUAL"),
        },
    },
}

PLANS = [FREE_PLAN, PRO_PLAN, MAX_PLAN]
PLANS_BY_ID = {plan["id"]: plan for plan in PLANS}


def published_plans():
    """The plans a customer may see and buy.

    Separate from PLANS so a tier can be finished, tested and deployed
    before it is advertised - which is what lets an unfinished plan ship
    dark instead of sitting on a branch.
    """
    return [plan for plan in PLANS if plan.get("published")]


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

    # Admin-set prices and promotional prices first. A subscription bought
    # during a sale carries the *offer's* Stripe Price, so a catalog-only
    # lookup would fail to recognise it and leave that subscriber's plan
    # unset — they would pay and get nothing.
    for model_name in ("PlanPrice", "PricingOffer"):
        try:
            from django.apps import apps

            model = apps.get_model("billing", model_name)
            row = model.objects.filter(stripe_price_id=stripe_price_id).first()
            if row is not None:
                return row.plan_id, row.currency, row.billing_period
        except Exception:
            logger.exception("Could not search %s for a Stripe price.", model_name)

    for plan in PLANS:
        for currency, periods in plan.get("prices", {}).items():
            for billing_period, price in periods.items():
                if price.get("stripe_price_id") == stripe_price_id:
                    return plan["id"], currency, billing_period
    return None


def find_plan_by_razorpay_plan_id(razorpay_plan_id):
    """The Razorpay counterpart of find_plan_by_stripe_price_id: which
    (plan_id, currency, billing_period) a Razorpay Plan on a subscription
    belongs to, or None if it isn't one of ours."""
    if not razorpay_plan_id:
        return None

    try:
        from billing.models import PlanPrice

        row = PlanPrice.objects.filter(razorpay_plan_id=razorpay_plan_id).first()
        if row is not None:
            return row.plan_id, row.currency, row.billing_period
    except Exception:
        logger.exception("Could not search PlanPrice for a Razorpay plan.")

    for plan in PLANS:
        for currency, periods in plan.get("prices", {}).items():
            if CURRENCY_PROCESSORS.get(currency) != PROCESSOR_RAZORPAY:
                continue
            for billing_period, price in periods.items():
                if price.get("processor_price_id") == razorpay_plan_id:
                    return plan["id"], currency, billing_period
    return None


def catalog_price(plan_id, currency, billing_period):
    """The built-in price, before any admin override or offer. The fallback
    that keeps the product working with an empty database."""
    plan = get_plan(plan_id)
    if not plan or not plan.get("prices"):
        return None
    return plan["prices"].get(normalize_currency(currency), {}).get(billing_period)


def get_price(plan_id, currency, billing_period, now=None):
    """The price to display *and* to charge, for one plan/currency/period.

    Resolution order: a live promotional offer, then an admin-set base
    price, then the built-in catalog. Returns None when the plan has no
    prices at all (free) or the combination isn't configured.

    Both the pricing page and Stripe Checkout call this one function. That
    is the point: display and charge cannot disagree, because there is no
    second place for either to read a number from. A discount that existed
    only on the pricing page would show one amount and take another, which
    is misleading pricing rather than a cosmetic bug.

    Shape is a superset of what it used to return, so existing callers
    reading `amount_cents` / `stripe_price_id` are unaffected:

        amount_cents           what will actually be charged
        stripe_price_id        the Stripe Price checkout will use
        original_amount_cents  the pre-offer amount, or None
        offer                  {label, badge, ends_at} while a sale runs
    """
    currency = normalize_currency(currency)
    base = _base_price(plan_id, currency, billing_period)
    if base is None:
        return None

    processor = CURRENCY_PROCESSORS[currency]
    resolved = {
        "amount_cents": base["amount_cents"],
        "processor": processor,
        "processor_price_id": base["processor_price_id"],
        "stripe_price_id": base["processor_price_id"] if processor == PROCESSOR_STRIPE else "",
        "tax_inclusive": currency in TAX_INCLUSIVE_CURRENCIES,
        "original_amount_cents": None,
        "offer": None,
    }

    # Promotional offers are Stripe Prices, so they only exist for Stripe
    # currencies. An INR sale would need its own Razorpay plan; until that
    # is built, an INR offer row simply never goes live rather than showing
    # a discount Razorpay would not charge.
    if processor != PROCESSOR_STRIPE:
        return resolved

    offer = _live_offer(plan_id, currency, billing_period, now)
    if offer is None:
        return resolved

    # Only ever a *discount*. An "offer" priced above the standard price is
    # a misconfiguration, and quietly charging more than the normal rate
    # because a row said so is the one outcome worth hard-coding against.
    if offer.amount_cents >= base["amount_cents"]:
        logger.warning(
            "Ignoring offer %s: %s is not below the standard price %s.",
            offer.campaign, offer.amount_cents, base["amount_cents"],
        )
        return resolved

    resolved.update(
        amount_cents=offer.amount_cents,
        processor_price_id=offer.stripe_price_id,
        stripe_price_id=offer.stripe_price_id,
        original_amount_cents=base["amount_cents"],
        offer={
            "label": offer.label,
            "badge": offer.badge,
            "ends_at": offer.ends_at.isoformat(),
        },
    )
    return resolved


def _base_price(plan_id, currency, billing_period):
    """Admin-set price if there is one, otherwise the built-in catalog."""
    catalog = catalog_price(plan_id, currency, billing_period)
    if catalog is None:
        # Nothing to override — a plan with no prices stays free.
        return None

    row = _price_row(plan_id, currency, billing_period)
    if row is None:
        # For Stripe currencies `stripe_price_id` stays the key that counts:
        # it is the name every existing override, test and admin screen
        # uses, so reading anything else would silently ignore them.
        if CURRENCY_PROCESSORS.get(currency) == PROCESSOR_RAZORPAY:
            processor_price_id = catalog.get("processor_price_id", "")
        else:
            processor_price_id = catalog.get("stripe_price_id", "")
        return {"amount_cents": catalog["amount_cents"], "processor_price_id": processor_price_id}
    if CURRENCY_PROCESSORS.get(currency) == PROCESSOR_RAZORPAY:
        processor_price_id = row.razorpay_plan_id
    else:
        processor_price_id = row.stripe_price_id
    return {"amount_cents": row.amount_cents, "processor_price_id": processor_price_id}


def _price_row(plan_id, currency, billing_period):
    # Imported lazily: this module is imported at app-load time, before the
    # app registry is ready to answer model queries.
    try:
        from billing.models import PlanPrice

        return PlanPrice.objects.filter(
            plan_id=plan_id,
            currency=currency,
            billing_period=billing_period,
            is_active=True,
        ).first()
    except Exception:
        # A missing table (fresh checkout, mid-migration) must degrade to
        # the built-in catalog, not take the pricing page down.
        logger.exception("Could not read PlanPrice; falling back to the catalog.")
        return None


def _live_offer(plan_id, currency, billing_period, now=None):
    from django.utils import timezone

    now = now or timezone.now()
    try:
        from billing.models import PricingOffer

        offers = PricingOffer.objects.filter(
            plan_id=plan_id,
            currency=currency,
            billing_period=billing_period,
            is_active=True,
            starts_at__lte=now,
            ends_at__gte=now,
        ).exclude(stripe_price_id="")
        # Overlapping campaigns are an operator mistake, not a crash: take
        # the cheapest, which is the one a customer would expect to get.
        return offers.order_by("amount_cents").first()
    except Exception:
        logger.exception("Could not read PricingOffer; serving standard pricing.")
        return None


def format_price_display(amount_cents, currency, billing_period):
    symbol = CURRENCY_SYMBOLS.get(currency, "")
    amount = amount_cents / 100
    # Whole-number amounts read cleaner without trailing ".00" (matches the
    # audit's own "€119/yr", not "€119.00/yr").
    if currency == "INR":
        # Indian digit grouping: "₹3,999/yr", never "₹3999/yr".
        from billing.gst import rupees

        amount_str = rupees(amount_cents).removesuffix(".00")
    else:
        amount_str = f"{amount:g}"
    suffix = "/mo" if billing_period == "monthly" else "/yr"
    return f"{symbol}{amount_str}{suffix}"
