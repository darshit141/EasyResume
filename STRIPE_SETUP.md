# Stripe test-mode setup

Needed once, locally, before Pro checkout can actually be tested end to end.
`STRIPE_AUTOMATIC_TAX_ENABLED` is already set to `True` in `.env` — checkout
session creation will fail outright until Stripe Tax is activated (step 3),
so do these in order.

## 1. Get your test-mode secret key

1. Log into <https://dashboard.stripe.com> (or create an account — no card
   required for test mode).
2. Confirm the **Test mode** toggle (top right) is on.
3. Developers → API keys → copy the **Secret key** (starts with `sk_test_`).
4. Paste it into `.env` as `STRIPE_SECRET_KEY`.

## 2. Create the Pro product + price

Either do this in the Dashboard (Product catalog → + Add product → Pro,
recurring price, €14.99/month), or give me the secret key from step 1 and
I'll create it for you via the API in one shot — same result, faster.
Either way you end up with a Price ID (`price_...`) to paste into
`STRIPE_PRICE_ID_PRO_EUR_MONTHLY` in `.env`. One price is enough to test
checkout; the other three (EUR annual, GBP monthly/annual) can stay empty
for now — the pricing page just shows those combinations as "not yet
available."

## 3. Activate Stripe Tax

Settings → Tax → Get started. Add your business's origin address (this is
what "collecting VAT" is calculated from). Without this, every checkout
attempt fails immediately with a Stripe-side error — not a code bug on our
end, just Stripe refusing to calculate tax it hasn't been told how to
calculate.

## 4. Forward webhooks to your machine

Stripe can't reach `localhost` directly, so local testing needs the
[Stripe CLI](https://docs.stripe.com/stripe-cli):

```bash
stripe login
stripe listen --forward-to localhost:8000/api/billing/webhook/
```

Leave that running in its own terminal — it prints a webhook signing
secret (`whsec_...`) the moment it starts. Paste that into `.env` as
`STRIPE_WEBHOOK_SECRET`. Without this step, Checkout will *look* like it
succeeded (Stripe's own confirmation page) but the subscription will never
actually flip to "active" in our database, because that only happens when
our webhook receives and verifies the event.

## 5. Test the purchase

Restart the backend after editing `.env`, then check out with Stripe's
published test card: `4242 4242 4242 4242`, any future expiry, any 3-digit
CVC, any postal code. Use a German billing address to see 19% VAT
calculated live, or a UK one to see 20%.

---

Send me the values once you have them (secret key, price ID, webhook
secret) and I'll wire them in, restart everything, and run the purchase
through myself to confirm it works before you try it live.
