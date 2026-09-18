# Billing

How IntelliResume charges customers, in terms of what is actually
implemented today — not a design proposal. See
`COMMERCIAL_READINESS_AUDIT.md` P0-5/P0-6 for the gaps this closed and
what's still open.

## Plan catalog

`billing/plans.py` is the single source of truth for pricing — nothing
else hardcodes an amount. Two plans:

- **Free** — no Stripe price at all, nothing to check out.
- **Pro** — priced in EUR and GBP, each with a monthly and an annual
  option. Amounts and Stripe Price IDs are read from environment variables
  (see `.env.example`); the shipped defaults (€14.99/mo, €119/yr,
  £12.99/mo, £99/yr) are the audit's starting hypothesis, not a reviewed
  business decision — change them via env, no code change needed.

A plan/currency/period combination is only checkout-able once its
`STRIPE_PRICE_ID_*` env var is set to a real Stripe Price object's id
(created in the Stripe Dashboard — test and live mode have different
ids). Until then, `GET /api/billing/plans/` reports it as
`purchasable: false` and `POST /api/billing/checkout/` returns
`PLAN_NOT_PURCHASABLE` rather than silently charging the wrong amount.

## Currency

`GET /api/billing/plans/?currency=EUR|GBP|INR` resolves the catalog to one
currency (default EUR; anything else falls back to EUR). The pricing page
has an explicit currency toggle and passes the choice through to both the
plan list and `POST /api/billing/checkout/`. Its only "detection" is picking
INR as the starting tab for a browser whose time zone is India; the visitor
can always switch.

## Payment processors

Each currency is charged by the processor that can take it
(`CURRENCY_PROCESSORS` in `billing/plans.py`):

- **EUR, GBP → Stripe.** Prices exclude VAT.
- **INR → Razorpay** (UPI AutoPay, cards, net banking). Prices *include*
  GST. Stripe is invite-only for Indian businesses and offers them neither
  UPI nor Stripe Tax. Setup, webhooks, GST and invoicing are covered in
  `RAZORPAY_SETUP.md`.

`POST /api/billing/checkout/` routes by currency and returns `processor`:
redirect to `checkout_url` for Stripe, or open Razorpay Checkout with the
`razorpay` block. Each price option in the plan list carries `processor`,
`payment_methods`, `tax_inclusive` and `tax_note` so the page never has to
know the routing or hardcode a tax statement.

## VAT (EUR/GBP)

EUR and GBP prices are displayed VAT-exclusive. Actual VAT calculation is **not**
computed by this application — it is Stripe's job. `STRIPE_AUTOMATIC_TAX_ENABLED`
(env, default `False`) turns on Stripe Checkout's automatic tax and
required billing-address collection; it must stay `False` until Stripe
Tax is enabled in the Stripe Dashboard for the account, or checkout-session
creation fails outright. This is a deliberate default: shipping the code
path without the operator's explicit Stripe-side setup would silently
break checkout.

## Checkout and subscription lifecycle

`POST /api/billing/checkout/` creates a Stripe Checkout Session for a
`(plan_id, currency, billing_period)` and returns its URL. **The local
`Subscription` row is never marked active by the checkout flow itself** —
only a confirmed `checkout.session.completed` webhook does that, so a
user can't grant themselves Pro by hitting the success URL without
actually paying.

`POST /api/billing/portal/` opens Stripe's hosted Customer Portal for the
user's own subscription (upgrade/downgrade/cancel/payment-method changes
all happen there — this backend has no separate proration logic of its
own, by design; re-evaluate if that portal experience proves
insufficient).

## Webhook handling (`POST /api/billing/webhook/`)

Every event is verified against `STRIPE_WEBHOOK_SECRET` (signature
checked via the Stripe SDK) before anything is applied.

**Idempotency and audit log**: each event is recorded in
`billing.StripeWebhookEvent`, keyed by Stripe's own event id. A
redelivered event (Stripe retries on timeout, or a manual replay from the
Dashboard) is detected and skipped rather than reprocessed — returns
`200 {"received": true, "duplicate": true}` without touching
`SubscriptionSyncService` a second time. A processing failure records the
error on that row and returns `500`, so Stripe's own retry schedule kicks
in rather than the event being silently dropped.

Handled event types (`billing/services/subscription_sync_service.py`):

| Event | Effect |
|---|---|
| `checkout.session.completed` | Marks the subscription active, fetches the Stripe subscription directly (`StripeService.get_subscription`) to set `current_period_end` immediately rather than waiting for a second webhook. |
| `customer.subscription.created` / `.updated` | Syncs status (`active`/`trialing` → local `active`, anything else passed through as-is, e.g. `past_due`) and `current_period_end`. |
| `customer.subscription.deleted` | Marks `canceled`. |
| `invoice.payment_failed` | Logged only — Stripe's own subscription-status transition (surfaced via `customer.subscription.updated`) remains the actual source of truth for whether access is cut off, so this never revokes access on its own. |
| `invoice.paid` | If the subscription was `past_due`, restores `active` immediately — the recovery signal after a failed payment retry succeeds. |
| `charge.refunded` | A **full** refund cancels the subscription; a partial refund is logged only. |

Unhandled event types are logged and ignored (200), not rejected — Stripe
sends many event types this application has no reason to act on.

## Entitlement enforcement

Server-side only — see `billing/entitlements.py` and
`billing/services/entitlements_service.py`. The frontend hiding a gated
control is a courtesy, never the actual control; every limit is enforced
again on the endpoint itself.

## Coupons

`POST /api/billing/coupons/redeem/` grants Pro access without Stripe
(`billing/services/coupon_service.py`) — used for early access / beta
invites, not a real payment path.

## Known gaps (not implemented here)

- **Upgrade/downgrade/proration logic** — relies entirely on Stripe's
  Customer Portal; there is no local reconciliation beyond what webhooks
  already sync.
- **Per-provider AI token/cost capture** — `AIUsageLog` has the columns
  (`model_name`, `input_tokens`, `output_tokens`, `cost_estimate_usd`) but
  they are not yet populated; only `latency_ms` is captured automatically.
- **Dunning emails** — `invoice.payment_failed` is logged, not surfaced to
  the customer by email; Stripe's own Smart Retries (if enabled on the
  account) handle the retry schedule, and Stripe can also be configured to
  send its own dunning emails independent of this application.
