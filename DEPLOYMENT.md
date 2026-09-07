# Going live — the full checklist

Everything required to take IntelliResume from "runs on my machine" to a
real, chargeable product at a real domain. Follow it in order — later
steps assume earlier ones are done. Related docs this cross-references:
[STRIPE_SETUP.md](STRIPE_SETUP.md) (test mode, do that first if you
haven't), [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) (backups),
[GDPR_DATA_FLOW.md](GDPR_DATA_FLOW.md), [BILLING.md](BILLING.md).

This is two deployable things, not one: the Django API (backend repo) and
the React SPA (frontend repo) are separate services with separate hosts.
The backend also needs a Celery **worker** process (runs queued AI jobs)
and a Celery **beat** process (fires the daily OTP cleanup) running
alongside the web process — three processes from one image, not one.

---

## 0. Prerequisites

- [ ] A domain you control, with DNS access
- [ ] A hosting account for the backend (Render, Railway, Fly.io, or
      anything that runs a Docker image + lets you run a worker process —
      the `Dockerfile` and `docker-compose.yml` in this repo are the
      reference for what each process needs)
- [ ] A hosting account for the frontend (Vercel, Netlify, or any static
      host — `npm run build` produces a plain static `dist/` folder)
- [ ] A Stripe account, switched to **live mode** (top-right toggle) —
      separate keys and separate Price IDs from test mode; nothing from
      `STRIPE_SETUP.md` carries over automatically
- [ ] (Optional but recommended) A Sentry account for error tracking
- [ ] (Optional) An S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze
      B2, DigitalOcean Spaces) if you want uploaded resumes to survive a
      redeploy — see the note in step 3

---

## 1. Database — Postgres

SQLite is the dev-only default; production **must** be Postgres
(`resumes` under concurrent write load will lock or corrupt on SQLite).

- [ ] Provision a managed Postgres instance (your host's addon, or
      Supabase/Neon/RDS if hosting Postgres separately from the app)
- [ ] Note the connection string — you'll set it as `DATABASE_URL` in
      step 5. Format: `postgres://user:password@host:port/dbname`

---

## 2. Redis

Required for both the Celery job queue (every AI-backed action) and the
shared rate-limit cache. Without it, AI jobs never run and throttling
falls back to a per-process cache that under-counts with more than one
worker.

- [ ] Provision a managed Redis instance
- [ ] Note the connection string for `REDIS_URL` in step 5

---

## 3. Object storage (optional, but do this before real users upload real resumes)

Without this, uploaded resumes live on the container's local disk, which
most hosts wipe on every deploy/restart. If that's an acceptable risk for
launch day, skip this and revisit before it bites someone's data.

- [ ] Create a **private** bucket (AWS S3, Cloudflare R2, Backblaze B2, or
      DigitalOcean Spaces all work — `AWS_S3_ENDPOINT_URL` is only needed
      for a non-AWS provider)
- [ ] Create an access key scoped to that bucket only
- [ ] Note bucket name, region, access key, secret key for step 5

---

## 4. Stripe — live mode

Do this even if you already did `STRIPE_SETUP.md` — that was test mode,
this is a fully separate set of keys and prices.

- [ ] Switch the Stripe Dashboard to **Live mode**
- [ ] Developers → API keys → copy the live secret key (`sk_live_...`)
- [ ] Settings → Tax → activate it with your real business address (the
      app already ships with `STRIPE_AUTOMATIC_TAX_ENABLED=True` —
      checkout fails outright until this is done)
- [ ] Create the Pro product with real prices for every currency/period
      you want live at launch (EUR monthly, EUR annual, GBP monthly, GBP
      annual — or fewer; anything left unconfigured just shows as "not
      yet available" on the pricing page, it doesn't break anything)
- [ ] Copy each Price ID (`price_...`)
- [ ] **After** the backend is deployed and has a real public URL (step
      6): Developers → Webhooks → Add endpoint →
      `https://<your-api-domain>/api/billing/webhook/`, select events
      `checkout.session.completed`, `customer.subscription.created`,
      `customer.subscription.updated`, `customer.subscription.deleted`,
      `invoice.payment_failed`, `invoice.paid`, `charge.refunded`. Copy
      the resulting signing secret (`whsec_...`) — this is different from
      the Stripe CLI's local one.

---

## 5. Backend environment variables

Set every one of these on your hosting platform (not committed to git —
`.env` is for local dev only).

### Required — the app won't run correctly without these

| Variable | Value |
|---|---|
| `SECRET_KEY` | A long random string, unique to production. Generate one, don't reuse the dev value. |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | Your API domain, e.g. `api.intelliresume.com` |
| `CORS_ALLOWED_ORIGINS` | Your frontend's origin, e.g. `https://intelliresume.com` |
| `CSRF_TRUSTED_ORIGINS` | Same as above |
| `DATABASE_URL` | From step 1 |
| `REDIS_URL` | From step 2 |
| `FRONTEND_URL` | Your frontend's full URL — used for Stripe checkout success/cancel redirects and the account-deletion/portal return URL |
| `STRIPE_SECRET_KEY` | Live secret key from step 4 |
| `STRIPE_WEBHOOK_SECRET` | Live webhook signing secret from step 4 |
| `STRIPE_PRICE_ID_PRO_EUR_MONTHLY` etc. | Live Price IDs from step 4 (whichever currency/period combos you're launching with) |
| `STRIPE_AUTOMATIC_TAX_ENABLED` | `True` (already the case if you copied `.env` forward — leave it; Stripe Tax must already be active from step 4) |
| `GROQ_API_KEY` and/or `GEMINI_API_KEY` and/or `KIRA_API_KEY` | Production-tier keys, not a personal free-tier key that'll rate-limit under real traffic. `KIRA_API_KEY` (kiraai.vn) is an OpenAI-compatible provider with a large context window and high rate limits — a good choice for one key serving many users; set `LLM_PROVIDER=kira` to make it the default. API keys are env-var-only (never the admin Configuration tab); `KIRA_MODEL` / `KIRA_BASE_URL` can be tuned from that tab. |

### Recommended

| Variable | Value |
|---|---|
| `AWS_STORAGE_BUCKET_NAME` + `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_S3_REGION_NAME` | From step 3 |
| `SENTRY_DSN` | From your Sentry project — error tracking is a silent no-op until this is set |
| `SENTRY_ENVIRONMENT` | `production` |
| `EMAIL_HOST` + `EMAIL_HOST_USER` + `EMAIL_HOST_PASSWORD` | A real SMTP provider — without this, OTP codes only ever print to the server log, which means **no one can actually log in in production**. This one is easy to miss because everything looks fine until the first real user tries to sign in. |

### Sign in with Google (optional, but it removes the OTP problem)

Unset, the Google button simply doesn't render and password login is
unaffected. Set, it becomes the fastest way in — and the only route that
skips the emailed OTP, because Google has already applied whatever second
factor is on the user's own account.

1. In the [Google Cloud console](https://console.cloud.google.com/apis/credentials),
   create an **OAuth 2.0 Client ID** of type **Web application**.
2. Add your frontend origin (e.g. `https://intelliresume.com`) under
   *Authorised JavaScript origins*.
3. Configure the OAuth consent screen. Only the `openid`, `email` and
   `profile` scopes are used — all three are non-sensitive, so this needs
   no Google verification review and no security assessment.

| Variable | Where | Value |
|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | API | The client ID from step 1. Every incoming Google token's `aud` is checked against it — that check is the whole security of the feature, so it must be the real value, never a wildcard. |
| `VITE_GOOGLE_CLIENT_ID` | Frontend build | The same client ID. Baked in at build time, so rebuild the frontend after changing it. |
| `GOOGLE_OAUTH_EXTRA_CLIENT_IDS` | API | Optional, comma separated. Only needed for the Chrome extension, which registers as its own OAuth client and therefore sends a different `aud`. Each entry is still matched exactly. |

Accounts are matched on Google's immutable `sub`, then by **verified**
email — so an existing password account signing in with Google is linked,
not duplicated, and keeps its password.

### Rewarded ads → credits (optional)

Off unless `REWARDED_ADS_ENABLED=true` is set in the admin Configuration tab. The **provider is chosen there too** (`REWARDED_AD_PROVIDER` = `admanager` or `partner`); credit value, daily cap, and expiry are the other `REWARDED_AD_*` keys on that screen. Only the network ids and the S2S secret are env vars:

| Variable | Value |
|---|---|
| `GAM_NETWORK_CODE` + `GAM_REWARDED_AD_UNIT_PATH` | For `REWARDED_AD_PROVIDER=admanager`. SSV is verified against Google's published keys, so no key is stored. Point the ad unit's SSV callback at `https://<api-domain>/api/billing/credits/rewarded-ad/callback/`. |
| `PARTNER_REWARDED_SCRIPT_URL` + `PARTNER_REWARDED_PLACEMENT_ID` + `PARTNER_REWARDED_SHOW_FN` + `PARTNER_REWARDED_S2S_SECRET` | For `REWARDED_AD_PROVIDER=partner` — whichever third-party rewarded-video SDK you sign with. The S2S postback is expected HMAC-SHA256 signed (see `PartnerRewardedAdProvider.verify_callback`); adjust that one method if the chosen network signs differently. Postback URL is the same callback path. |

### Leave at their defaults unless you have a specific reason

`LLM_PROVIDER` (groq; set to `kira` to route AI through kiraai.vn),
`KIRA_MODEL` (`kira-3.5-flash`), `FREE_AI_CALLS_PER_MONTH`, `FREE_TAILORED_RESUMES_PER_MONTH`,
`MAX_RESUMES_PER_USER`, `ACCESS_TOKEN_LIFETIME`, `REFRESH_TOKEN_LIFETIME`,
throttle rates (`THROTTLE_*`) — all tunable later without a code change,
none of them block launch.

---

## 6. Deploy the backend

- [ ] Confirm CI is green on the commit you're deploying (`.github/workflows/ci.yml`
      runs automatically on push — don't deploy a red commit)
- [ ] Build/deploy the Docker image (`.github/workflows/publish-image.yml`
      already publishes to `ghcr.io/<org>/<repo>` on every push to `main` —
      point your host at that image, or build directly from the
      `Dockerfile`)
- [ ] Run **three** processes from that same image, not one:
  - Web: `gunicorn --bind 0.0.0.0:8000 --workers 4 --timeout 60 config.wsgi:application`
  - Worker: `celery -A config worker --loglevel=info --concurrency=4`
  - Beat: `celery -A config beat --loglevel=info`
  - (`docker-compose.yml` is the reference for exactly this shape if your
    host runs Compose directly)
- [ ] Run migrations against the production database:
      `python manage.py migrate --noinput`
- [ ] Confirm `python manage.py check` passes with production env vars
      loaded before pointing real traffic at it
- [ ] Point your domain's DNS at the host, confirm HTTPS is terminated
      (most managed hosts do this automatically; if running your own
      reverse proxy, that's on you — `SECURE_SSL_REDIRECT` is already on
      by default in production and will redirect-loop if nothing is
      actually terminating TLS in front of it)

---

## 7. Deploy the frontend

- [ ] Set `VITE_API_BASE_URL` to the real backend URL, e.g.
      `https://api.intelliresume.com/api/`
- [ ] Set any of the optional `VITE_*` vars you want (AdSense, socials —
      see `.env.example`); leave them blank to keep those features off
- [ ] `npm run build`, deploy the resulting `dist/` folder (or let your
      host run the build — Vercel/Netlify both auto-detect a Vite
      project)
- [ ] Confirm the SPA's routing works on a hard refresh of a nested route
      (e.g. `/pricing`) — a static host serving `index.html` only for `/`
      and 404ing everything else is the classic SPA deploy mistake; make
      sure it's configured to serve `index.html` for any unmatched path

---

## 8. Wire the Stripe webhook to the real domain

You already created the webhook endpoint in step 4, pointed at the real
backend URL — now that the backend is actually deployed and reachable,
send Stripe a test event from the Dashboard's webhook page and confirm it
shows a `200` in the Stripe event log, not a timeout or a signature
failure.

---

## 9. Smoke test — do this before telling anyone it's live

- [ ] `GET https://<api-domain>/api/health/` → `{"status":"OK"}`
- [ ] `GET https://<api-domain>/api/ready/` → `{"status":"READY", ...}`
      (this one actually checks the database, Redis, and the AI provider —
      if it 503s, something in steps 1/2/4's env vars is wrong)
- [ ] Sign up a real account through the real frontend, confirm the OTP
      email actually arrives (this is the step that silently fails if
      `EMAIL_HOST` wasn't set — see step 5)
- [ ] Upload a resume, confirm it parses (this exercises the AI queue —
      if it hangs, the worker process from step 6 isn't running)
- [ ] Load `/pricing`, confirm both currencies and both billing periods
      show real prices, not "not yet available"
- [ ] Start a Pro checkout and confirm it reaches Stripe's real hosted
      checkout page with tax calculated — you don't need to complete a
      real charge to confirm this step works
- [ ] Confirm `/for-career-coaches` renders and its form submits
      (check Django admin → B2B Leads afterward)

---

## 10. Backups, monitoring, and what happens after this

- [ ] Confirm your Postgres host's backups are actually running (most
      managed Postgres does this automatically — verify it isn't relying
      on this repo's `scripts/backup_db.sh`, which is written for the
      Docker Compose self-hosted path, not a managed database)
- [ ] Know how to actually restore one before you need to — see
      [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md), which documents a
      real restore drill already performed against this schema
- [ ] Confirm Sentry is receiving events (trigger a harmless error,
      check it shows up)
- [ ] Set up uptime monitoring against `/api/health/` — this is separate
      from Sentry and not currently configured anywhere in this repo

Once this checklist is done, the remaining open items are business
decisions, not engineering — resume/file retention window, a referral
system, the multi-seat model, pricing experiments. None of them block
going live; all of them are better made with real usage data in hand than
guessed at now (see `COMMERCIAL_READINESS_AUDIT.md`'s P2 section).
