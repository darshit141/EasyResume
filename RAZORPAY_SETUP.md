# Rupee billing: Razorpay (UPI) and GST

How INR subscriptions work, what has to be set up before the first rupee is
charged, and which decisions belong to an accountant rather than to code.

## Why Razorpay, and not Stripe, for INR

IntelliResume is operated by an Indian private limited company. For an
India-based business, Stripe is currently invite-only, its UPI support is
offered to accounts in other countries only, and Stripe Tax cannot be used.
Razorpay supports UPI AutoPay, cards (standing instructions) and bank
e-mandates for recurring billing, which is what an Indian subscriber expects
at checkout.

EUR and GBP stay on Stripe, unchanged. Checkout routes by currency
(`billing/plans.py` → `CURRENCY_PROCESSORS`); nothing else in the app needs to
know which processor a customer is on.

> **If the business is actually registered outside India**, this changes. A
> foreign Stripe account *can* take UPI in INR, and GST on B2C sales into
> India then falls under the OIDAR rules (registration from the first sale,
> 18% IGST), which Stripe Tax calculates. Tell an engineer before going live
> if that is the case — the routing is a one-line change, the tax treatment
> is not.

## Setup, in order

### 1. Razorpay account and keys

1. Create or log into a Razorpay account and enable **Subscriptions**.
2. Settings → API Keys → generate a key pair (test mode first).
3. Put them in the environment — never in the admin panel:

   ```
   RAZORPAY_KEY_ID=rzp_test_...
   RAZORPAY_KEY_SECRET=...
   ```

### 2. Create the plans

Subscriptions → Plans → create one plan per price. **The plan amount must be
the GST-inclusive price exactly as shown on the pricing page**, because INR
prices include tax and Razorpay charges exactly what the plan says.

| Env var | Plan | Period | Default amount |
|---|---|---|---|
| `RAZORPAY_PLAN_ID_PRO_INR_MONTHLY` | Pro | monthly | ₹499 |
| `RAZORPAY_PLAN_ID_PRO_INR_ANNUAL` | Pro | yearly | ₹3,999 |
| `RAZORPAY_PLAN_ID_MAX_INR_MONTHLY` | Max | monthly | ₹999 |
| `RAZORPAY_PLAN_ID_MAX_INR_ANNUAL` | Max | yearly | ₹7,999 |

Until a plan id is set, that combination shows as not purchasable rather than
charging the wrong amount. Prices and plan ids can also be set per row in
Django admin → **Plan prices** (`razorpay_plan_id`), which wins over the env.

Every recurring INR price must stay at or below **₹15,000**: that is RBI's cap
on a UPI AutoPay debit without fresh authentication, and a test enforces it.

### 3. Webhook

Settings → Webhooks → add `https://<api-domain>/api/billing/razorpay/webhook/`
with these events:

- `subscription.authenticated`, `subscription.activated`,
  `subscription.charged`, `subscription.pending`, `subscription.halted`,
  `subscription.cancelled`, `subscription.completed`, `subscription.paused`,
  `subscription.resumed`, `subscription.updated`
- `refund.processed`

Copy the secret into `RAZORPAY_WEBHOOK_SECRET`. Without it every webhook is
rejected — deliberately, since an unverifiable webhook must never grant
access.

### 4. GST — only once the business holds a GSTIN

An unregistered supplier must not collect GST, so `GST_ENABLED` stays off
until registration. Prices don't change either way; before registration the
customer gets a plain invoice with no tax lines, after it a tax invoice with
the GST breakdown.

All of these are editable live in the admin **Configuration → Indian GST**
section (env values are only the defaults):

| Setting | Meaning |
|---|---|
| `GST_ENABLED` | Charge GST. Ignored if the GSTIN below is missing or invalid. |
| `GST_SELLER_GSTIN` | The business's GSTIN. Its first two digits set the supplier state. |
| `GST_SELLER_LEGAL_NAME`, `GST_SELLER_ADDRESS` | Printed on every invoice. |
| `GST_RATE_PERCENT` | 18 for software services. |
| `GST_SAC_CODE` | **Blank by default — confirm with your accountant.** |
| `GST_INVOICE_PREFIX` | Up to four letters; invoices are numbered `IR/2627/000001`. |

### 5. Test the purchase (test mode)

1. Restart the backend, open Pricing, pick **₹ INR** (Indian time-zone
   visitors land on it automatically).
2. Subscribe → enter a state (and optionally a GSTIN) → Razorpay Checkout
   opens.
3. Pay with a Razorpay test UPI id or card.
4. Settings should show the plan, the renewal date, and an invoice you can
   download as PDF.
5. Cancel from Settings: access continues to the period end, then the
   `subscription.cancelled` webhook moves the account to Free.

## How it behaves

- **Access is granted only after verification.** The browser's success
  callback is checked two ways: Razorpay's HMAC signature (with the key
  secret) and a fresh read of the subscription from Razorpay's API, which
  must be paid and belong to the same user. If the browser never reports
  back, the webhook applies the same payment.
- **Out-of-order webhooks can't regress state.** Each event's creation time
  is compared with the last one applied; an older event is recorded and
  skipped.
- **Duplicate deliveries are harmless.** Events are deduplicated on
  `x-razorpay-event-id`, and invoices on the payment id.
- **A failed renewal keeps access while Razorpay retries** (`pending`), and
  the customer gets an email written for UPI/mandate payers. Once retries are
  exhausted (`halted`), paid features pause.
- **Cancelling from Settings** stops renewal at cycle end. Cancelling the
  mandate from the customer's own UPI app arrives as a webhook and is applied
  the same way.
- **Account deletion** cancels the mandate immediately.

## Invoices and retention

`billing.GSTInvoice` holds one invoice per captured INR payment, with every
printed detail copied onto it at issue time. Numbers are consecutive within
each Indian financial year (April–March, in IST) and restart each April.

Invoices are **not deleted with the account**. The CGST Act requires them to
be kept for 72 months after the annual return is due, and GDPR Article
17(3)(b) permits keeping data held for a legal obligation; the user link is
cleared, the invoice stays. They are read-only in Django admin: a mistake is
corrected with a credit note, never by editing the original.

## For the accountant

These are decisions, not engineering questions, and the code deliberately
leaves them configurable:

1. When to register for GST (and whether to register voluntarily below the
   threshold in order to file an LUT for export sales).
2. The SAC code for the service.
3. Whether EUR/GBP sales to customers abroad should carry an export
   declaration (LUT) on their invoices — those invoices are issued by
   Stripe, not by this application.
4. EU VAT and UK VAT on sales to consumers there. A non-EU seller of digital
   services generally owes VAT from the first sale (non-Union OSS; UK VAT
   registration), and Stripe Tax is not available to an India-based
   business, so this needs an answer before EUR/GBP checkout goes live.
