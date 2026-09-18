# IntelliResume — Commercial Readiness Audit

**Audit date:** 2026-08-19
**Branch:** `QA`
**Repositories audited:** `intelliresume-backend` (Django 6.0.7 / DRF), `intelliresume-react` (React 19 / Vite 6)
**Scope:** Read-only architecture, security, billing, AI, GDPR, deployment and commercial audit. **No application code was modified.**

> **Verdict (2026-08-19 baseline — SUPERSEDED, see section 0): NOT commercially launchable today.**
> The product core is genuinely strong — notably the evidence-gated gap analysis, which is a real and defensible differentiator. The gaps are almost entirely in the *commercial, legal and operational shell* around that core, not in the core itself. Fifteen P0 items block first revenue.

---

## 0. Re-audit — 2026-08-28 (authoritative current status)

**Everything below section 1 is the original 2026-08-19 baseline audit and is
retained as the historical record. This section supersedes its verdict.**

Method: read-only inspection of the working tree on `main` (commit `7649e8c`),
plus one full local execution of the backend test suite. No application code
was modified. Where a claim below could not be verified from the repository
— anything depending on production environment variables or on Stripe's
live-mode state — it is recorded as *unverified* rather than as passing.

### 0.1 Revised verdict

> **The fifteen P0 items that blocked launch are closed in code**, and the
> full commercial journey — signup → OTP → resume upload → JD analysis →
> evidence-gated gap analysis → tailored resume → paywall → checkout →
> webhook → entitlement upgrade → DOCX download → outcome → GDPR export →
> GDPR deletion — now passes as a single automated test
> (`api/tests_commercial_e2e.py`). The remaining launch risk is no longer
> architectural or structural; it is **configuration**. Stripe has still
> never been driven against real infrastructure in any mode, and the live
> Price IDs cannot be confirmed from this repository. Until that is done the
> product is **launch-capable, and proven against its own code, but not yet
> proven against Stripe**.

The distinction matters commercially. The end-to-end test fakes exactly two
boundaries, both of them someone else's network: the LLM provider and the
Stripe SDK. Everything between them is the real implementation, so the test
now rules out an entire class of failure — an entitlement that doesn't
refresh after activation, a webhook that updates a row nobody reads, a
paywall that returns the wrong contract to the UI. What it cannot rule out
is Stripe-side misconfiguration: a Price ID that doesn't exist in live mode,
a currency mismatch, a webhook secret that doesn't match. That is a
half-day of verification with a real test-mode account, not weeks of
engineering — but it must happen before a real card is charged.

Notably, writing that test surfaced **no defects in the application**. Every
failure encountered while building it was a wrong assumption in the test
itself, corrected against the implementation: the paywall returns `402
Payment Required` rather than `403` (deliberate, and the frontend routes off
it), account deletion is a `POST` that re-authenticates with the password,
and the evidence gate rejected a fixture whose requirements were absent from
the job analysis. A codebase where the end-to-end harness is wrong and the
product is right is in better shape than the reverse.

### 0.2 P0 status — verified against code

| ID | Item | Status | Evidence |
|---|---|---|---|
| P0-1 | Celery + Redis + `AIJob`; AI off the request cycle | **Done** | `ai_jobs/models.py` (6 job types, 6 statuses incl. `RETRYING`/`CANCELLED`), `ai_jobs/tasks.py` `run_ai_job` with duplicate-delivery guard |
| P0-2 | Cloud AI default, Ollama demoted to opt-in | **Done** | `.env.example` sets `LLM_PROVIDER=groq`; automatic fallback provider on primary failure (`llm_service.py`) |
| P0-3 | Gunicorn/LLM timeout inversion | **Done** | `--timeout 60` now that LLM work runs in workers (`Dockerfile`, both compose files) |
| P0-4 | PostgreSQL in production | **Done** | Postgres whenever `DATABASE_URL` is set (`config/settings.py`); droplet compose runs `postgres:16-alpine`; CI runs against real Postgres |
| P0-5 | Centralized multi-currency pricing | **Done** | `billing/plans.py` — EUR/GBP × monthly/annual, a Stripe Price ID per combination, env-overridable amounts |
| P0-6 | Stripe hardening | **Done (code)** | `StripeWebhookEvent` with unique `stripe_event_id` (idempotency + event log); handlers for `checkout.session.completed`, `customer.subscription.*`, `invoice.paid`, `invoice.payment_failed`, `charge.refunded` |
| P0-7 | GDPR account deletion | **Done** | `account_deletion_service.py` — cascade, media file removal, Stripe cancellation, newsletter purge |
| P0-8 | GDPR data export | **Done** | `account_export_service.py` — profile, resumes + versions, curations, subscription, AI usage, consents |
| P0-9 | Cookie consent that actually gates tracking | **Done** | `CookieConsentContext.jsx`; `AdSlot.jsx` requires `marketingAllowed` before the AdSense loader is injected at all |
| P0-10 | Policy version + acceptance tracking | **Done** | `PolicyAcceptance` (type, version, timestamp) and `CookieConsent.consent_version` |
| P0-11 | Expanded AI usage logging | **Done** | `AIUsageLog` — model, latency, input/output tokens, `cost_estimate_usd`, `request_id`, `job` FK |
| P0-12 | Fix failing tests; add CI | **Done** | **548 tests, 0 failures, ~4s** (baseline was 355 tests / 915s / 5 failing). GitHub Actions CI on Postgres, including `makemigrations --check` |
| P0-13 | Usable `phone` field | **Done** | `max_length=30`, nullable, optional |
| P0-14 | Mask admin-config secrets | **Done** | `is_sensitive_key()` masks values in responses and refuses to overwrite with a masked value; provider API keys moved out of the database |
| P0-15 | Real `/health` + `/ready`; backups + restore test | **Done** | `api/views/readiness.py` checks DB + Redis + LLM; `scripts/backup_db.sh` / `restore_db.sh`; `DISASTER_RECOVERY.md` records a completed restore drill |

Beyond the original P0 set, work has also landed on outcome tracking
(`outcome_insights_service.py`, the `/outcome` route), analytics events and
business metrics (`api/views/business_metrics_view.py`), B2B lead capture
(`B2BLead`), the `/for-career-coaches` landing page, an admin dashboard
(`/app/admin`), S3-compatible storage support, and Sentry error tracking.

### 0.3 Remaining blockers

**P0 — do before charging a real customer**

| ID | Blocker | Why it blocks |
|---|---|---|
| R0-1 | **Stripe has never been exercised end-to-end**, not even in test mode. No test-mode checkout, no webhook replay, no upgrade/downgrade/cancel/refund run against real Stripe. | All billing confidence currently comes from code reading and mocked unit tests. The failure modes that matter here — Price ID/currency mismatch, webhook secret mismatch, entitlement not refreshing after activation — are invisible to mocks. |
| ~~R0-2~~ | ~~No automated end-to-end commercial test.~~ **Closed 2026-08-28** — `api/tests_commercial_e2e.py` walks the whole revenue path over real HTTP endpoints and a real database, faking only the LLM and Stripe SDK boundaries. | Was the brief's own definition of commercial readiness (§35). The revenue path now has a regression guard; suite is 548 tests, all passing. |
| R0-3 | **Live Stripe configuration is unverified.** The four `STRIPE_PRICE_ID_PRO_*` values, the API keys and the webhook secret are correctly blank in `.env.example`; whether they are populated in the production environment cannot be determined from this repository. | A blank Price ID fails checkout for that currency/period. It fails safely, but it fails. Confirm on the droplet before launch. |
| R0-4 | **VAT treatment is undecided in practice.** `STRIPE_AUTOMATIC_TAX_ENABLED` defaults to `False`. | Selling to EU consumers with tax disabled creates a VAT liability that is far cheaper to get right before the first invoice than after. This needs an accountant's answer, not an engineering one. |

**P1 — shortly after launch**

- AI providers have still never been benchmarked for latency, cost or quality; the Groq-vs-Gemini default is a reasoned assumption rather than a measurement. Note that `.env.example` says `groq` while the local `.env` says `gemini` — the production value should be a deliberate and recorded choice.
- Droplet capacity is small by design: 2 Gunicorn workers, Celery `--concurrency=1`. Adequate for the first customers; it needs a headroom plan before any acquisition push.
- Object storage is supported but not enabled (`AWS_STORAGE_BUCKET_NAME` is empty), so media sits on the droplet's local disk. Acceptable on a single instance; it blocks horizontal scaling and widens the backup surface.
- The restore drill validated the *mechanism* at roughly 100KB, not at production data volume. Re-run it once real data exists and update the RTO accordingly.

**P2 — scale**

- Documents from the brief's §53 list that do not yet exist: `ARCHITECTURE.md`, `SECURITY.md`, `AI_ARCHITECTURE.md`, `AI_COST_MODEL.md`, `COMMERCIAL_SCORECARD.md`, `GO_TO_MARKET.md`. (`README`, `BILLING`, `DEPLOYMENT`, `GDPR_DATA_FLOW`, `DISASTER_RECOVERY` and `STRIPE_SETUP` do exist.)
- There is no `Organization` / `OrganizationMember` / multi-seat model. **This is a recorded decision, not an oversight** (commit `a7cacf3`): pilot agencies manually through the `/for-career-coaches` lead channel first and build multi-tenancy only once demand is demonstrated. That matches the brief's §23–24 and §51.
- No referral system — also a recorded deferral, pending retention data.

### 0.4 What this re-audit did not verify

1. **Nothing was run against production.** Findings come from the working tree plus one local suite run (SQLite-backed, as configured locally; CI covers Postgres).
2. **Stripe was not contacted**, in either live or test mode.
3. **The frontend test story was not assessed.** The backend suite is strong; no equivalent frontend suite was located.
4. **AI output quality was not evaluated.** The evidence gate is now asserted under adversarial conditions — the end-to-end test scripts the model to claim an unsupported Kubernetes match with invented evidence, and the server correctly overrules it to MISSING. That proves the *guard* holds. It does not measure real-world generation quality against real job descriptions, which still requires human review of real output.

---

## 0A. Re-audit and remediation — 2026-09-16 (authoritative current status)

**Supersedes section 0's open-items list. Section 0's verdict still stands:
the product is launch-capable in code and unproven against Stripe.**

Method: full read of the working tree on `darshit`, one local run of the
backend suite before and after the work below, and a browser walk-through of
the authenticated app against a live local backend. This pass *did* change
application code, unlike the two audits above; what changed is listed in 0A.3.

### 0A.1 What the 2026-08-28 audit's open items look like now

| ID | Item | Status |
|---|---|---|
| R0-1 | Stripe never exercised end-to-end | **Still open.** Needs a real test-mode account; cannot be closed from the repository. |
| R0-3 | Live Stripe Price IDs unverified | **Still open.** Confirm on the droplet. |
| R0-4 | VAT treatment undecided (`STRIPE_AUTOMATIC_TAX_ENABLED=False`) | **Still open.** Needs an accountant, not an engineer. |
| P1 | AI providers never benchmarked | Still open, but materially less risky: provider/model selection, fallback order and model discovery are now all admin-configurable at runtime, so a bad default is a settings change rather than a deploy. |
| P1 | Frontend has no test suite | **Still open.** `package.json` has no test runner at all. This is now the largest single gap in the engineering story. |

### 0A.2 What the master brief asked for that already existed

Recorded because it saves the next audit from re-deriving it. The brief's
sections 11–14 assume a hardcoded single-provider AI integration. That is not
what is here:

- `LLMService` is an ABC with four implementations (`ollama`, `groq`,
  `gemini`, `kira`) and one shared structured-generation contract.
- `FallbackLLMService` retries a failed primary against a configured
  fallback provider, and deliberately does *not* fall back on schema or
  content validation errors.
- Provider, model, base URL, timeouts, retries, temperature, token ceilings,
  Gemini fallback-model lists and live model discovery are all read through
  `get_config_value` — the admin Configuration tab, no redeploy.
- Provider API keys are stored encrypted (Fernet, domain-separated) and
  masked in admin responses.
- Plan limits, quotas, reply-tracking strategy per tier and prices are all
  environment- or admin-configurable (`billing/plans.py`,
  `billing/entitlements.py`).

The brief's sections 3–10 (discoverability) and 15–16 (BYOK) described real
gaps. Those are what this pass fixed.

### 0A.3 Changed in this pass

**Product / discoverability**

- **`/app/toolkit` — "Everything you can do".** Every capability the product
  actually has, grouped Build / Optimize / Tailor / Apply / Improve / Track,
  each with an outcome-shaped description, a direct link, and a live
  availability badge computed from `/billing/entitlements/`. Capabilities
  that need a job to exist say so rather than appearing locked. The catalog
  lives in one file (`src/lib/capabilities.js`) with the rule that nothing
  may be listed that is not reachable today, and no limit may be written as
  a literal.
- **Plan and usage are now ambient.** A chip in the app header and a card on
  the toolkit page show the plan and remaining allowance *before* it is
  spent. Previously the only mention of any limit was the 402 upgrade prompt
  at the moment it was hit.
- The guided flow is untouched. This is additive.

**Correctness**

- `Settings` decided paid status with `plan_id === "pro"`. A Max subscriber
  would have seen the Free badge, an upgrade CTA, and no route to the
  billing portal they pay for. Paid-plan identity now lives in
  `src/utils/plans.js`, mirroring the server's `PAID_PLAN_IDS`.
- `EntitlementsSerializer` had drifted from `usage_summary` by five fields.
  It shapes nothing at runtime — it is the published OpenAPI contract — so
  the drift was invisible and clients generated from the schema silently
  lost fields. Now complete, with a test asserting the two cannot separate
  again.
- `useEntitlements`'s loading defaults were missing most of the contract,
  so any component reading `ai_calls_remaining` mid-flight got `undefined`.

**Bring your own key (brief §15–16)**

- `billing.UserAIKey`, one row per (user, provider), currently Gemini only.
- Keys are validated against the live provider *before* being stored,
  encrypted at rest under their own key-derivation domain, never returned by
  any endpoint, never logged, and always deletable — including when the
  feature is switched off server-wide.
- While a user's key is active their requests use it, and the monthly
  AI-call ceiling stops applying to them. That ceiling exists solely to cap
  our provider bill, and they are not on it. **It unlocks no paid feature**:
  the tailored-resume limit, Word export and version history are product
  value, not compute cost.
- A user's key is never wrapped in the provider fallback — falling back
  would spend our quota on a request they chose to pay for themselves.
- Off by default (`BYOK_ENABLED`), switchable live from the admin
  Configuration tab. Throttled at 10/hour, because the save endpoint calls
  the provider and would otherwise be a way to test stolen keys from our IP.
- 38 tests, including cross-user isolation, encryption-domain separation,
  and that a rotated encryption key degrades to the server credentials
  rather than to a failed generation.

**Security**

- Job descriptions and uploaded resumes are attacker-controlled text that
  goes straight into prompts. Every prompt that embeds them now states
  plainly that the block is data, not instructions, placed *before* the
  untrusted text. The text itself is still passed through verbatim —
  stripping suspicious phrases would corrupt legitimate postings.
- This is explicitly the weakest of three layers and is documented as such
  in `resumes/prompts.py`. The two that carry the real weight already
  existed: schema-constrained output, and the deterministic evidence
  re-check that overrules any unsupported claim to MISSING.

**Tests:** 885 → 932, all passing, ~12s.

### 0A.4 What this pass did not do

1. Nothing was run against production, and Stripe was still not contacted.
2. No frontend test suite was added. The new UI was verified by hand in a
   browser against a live backend, which is not the same thing.
3. AI output quality is still unmeasured against real postings.
4. BYOK was verified against the real Gemini API only on the rejection path
   (a deliberately invalid key, correctly refused with a clear message). The
   acceptance path is covered by tests with the provider boundary faked.

### 0A.5 Rupee billing — UPI and GST (added later on 2026-09-16)

**Added:** INR pricing (Pro ₹499/mo, ₹3,999/yr; Max ₹999/mo, ₹7,999/yr,
GST-inclusive, admin-editable) charged through **Razorpay** — UPI AutoPay,
cards and bank e-mandates — alongside the unchanged Stripe EUR/GBP path.
Indian GST is calculated in-house (`billing/gst.py`): 18%, back-calculated
from the inclusive price, CGST+SGST/UTGST for the supplier's own state and
IGST otherwise, place of supply from the buyer's state or GSTIN, GSTIN
check-digit validation. Every captured rupee payment gets a numbered
invoice (a tax invoice once `GST_ENABLED` is on with a valid GSTIN, a plain
invoice before), downloadable as PDF and retained after account deletion as
the CGST Act requires. Full detail: `RAZORPAY_SETUP.md`. Tests: 932 → 1,029.

**Why Razorpay:** the Privacy Policy names the operator "IntelliResume Pvt.
Ltd.", an Indian company. Stripe is invite-only for Indian businesses, lists
no India-based accounts among those that can accept UPI, and Stripe Tax
cannot be used by an India-based business.

**New blocker this raises — R0-5: confirm where the business and its Stripe
account are registered.** If the operator is Indian, the existing EUR/GBP
plan depends on (a) Stripe approving an Indian account for live payments and
(b) EU/UK VAT being handled without Stripe Tax, since a non-EU seller of
digital services generally owes EU VAT (non-Union OSS) and UK VAT from the
first sale. That turns R0-4 from "decide whether to turn Stripe Tax on" into
"decide how VAT is collected at all". If the operator is instead registered
abroad, Stripe can take UPI directly and GST on Indian consumer sales falls
under the OIDAR rules — a different setup. Either way this is a question for
the founder and an accountant before live checkout, not for code.

**Not verified:** Razorpay was never contacted; the flow was exercised in a
browser up to the provider boundary (billing details, GSTIN validation,
checkout reporting "not configured", subscriber settings, invoice PDF), and
past it only with the Razorpay API faked in tests.

---

## 1. Current Architecture

### 1.1 As-built

```
React 19 SPA (Vite)  ──►  Django 6.0.7 + DRF  ──►  SQLite (default) / Postgres (only if DATABASE_URL set)
   localStorage JWT          Gunicorn, 2 workers          local disk media/
                                    │
                                    ├──► LLM (SYNCHRONOUS, in request cycle)
                                    │      ├── Ollama (compose default)
                                    │      ├── Groq
                                    │      └── Gemini
                                    └──► Stripe (checkout / portal / webhook)
```

### 1.2 Backend applications

| App | Responsibility | Size |
|---|---|---|
| `authentication` | Custom user, login OTP, password-reset OTP, JWT, profile | ~0.9k LOC |
| `resumes` | Upload, parse, version, JD analysis, gap analysis, tailoring, cover letter, export | ~8.5k LOC |
| `billing` | Plans, entitlements, quotas, Stripe, coupons, AI usage log | ~1.7k LOC |
| `api` | Health/readiness, runtime `SystemConfiguration`, admin endpoints | ~0.6k LOC |
| `blog` | Marketing posts, newsletter subscribers | ~0.7k LOC |

Total ≈ **17,233 lines** of Python, of which roughly **4,400 lines are tests** — an unusually healthy test-to-code ratio for a pre-launch product.

### 1.3 Architectural observations

The codebase is **well-layered and disciplined**: views → serializers → services → models, with AI access funnelled through a single `LLMService` abstraction (`resumes/services/llm_service.py`) and all Stripe access funnelled through `billing/services/stripe_service.py`. There is very little duplicated logic. Code comments are unusually high quality and explain *why*, not *what*.

**This is not a codebase that needs rebuilding.** It needs a commercial, legal and operational shell built around it.

---

## 2. Existing Functionality

| Capability | Present | Evidence |
|---|---|---|
| Email/password registration + login | Yes | `authentication/urls.py` |
| Login OTP (2-step) | Yes | `authentication/views/verify_login_otp_view.py` |
| Password reset via OTP | Yes | `authentication/services/verify_otp_service.py` |
| JWT access/refresh + blacklist on logout | Yes | `config/settings.py` |
| Resume upload (PDF/DOCX) | Yes | `resumes/services/resume_upload_service.py` |
| AI resume parsing → structured JSON | Yes | `resumes/services/resume_parser_service.py` |
| Deterministic non-AI parser fallback | Yes | `resumes/services/standard_resume_parser_service.py` |
| ATS scoring | Yes | `resumes/utils/ats_scorer.py` |
| Resume version history + restore | Yes | `resumes/models.py` — `ResumeVersion` |
| Manual resume editing | Yes | `resumes/services/resume_edit_service.py` |
| Job description analysis | Yes | `resumes/views/job_curation_view.py` |
| Job URL import (domain-allowlisted) | Yes | `resumes/services/job_url_fetch_service.py` |
| **Evidence-gated gap analysis** | **Yes** | `resumes/services/job_gap_analysis_service.py` |
| Tailored resume generation | Yes | `resumes/services/job_resume_curation_service.py` |
| Tailored resume versioning | Yes | `JobResumeCurationVersion` |
| Cover letter generation | Yes | `resumes/services/cover_letter_ai_service.py` |
| PDF + DOCX export | Yes | `resumes/services/tailored_resume_export_service.py` |
| Basic application tracking | Partial | `JobResumeCuration.application_status` |
| Stripe checkout + portal + webhook | Partial | `billing/views/billing_view.py` |
| Entitlements + quota enforcement | Yes | `billing/services/entitlements_service.py` |
| Coupons / comp access | Yes | `billing/services/coupon_service.py` |
| AI usage logging | Partial | `billing/models.py` — `AIUsageLog` |
| Marketing site + blog | Yes | `intelliresume-react/src/pages` |
| Admin config + user list | Yes | `api/views/admin_views.py` |

---

## 3. Production-Ready Functionality

These are genuinely solid and should **not** be touched:

1. **Evidence-gated gap analysis.** `JobGapAnalysisService.validate_gap_analysis()` does not merely *prompt* the model to behave — it **deterministically re-validates every AI claim in Python** against the candidate's verified resume text, downgrades any `MATCHED`/`PARTIALLY_MATCHED` item whose evidence cannot be located to `MISSING`, forcibly inserts any requirement the model silently omitted as `MISSING`, and **recomputes the match percentage itself** rather than trusting the model's number. This is exactly the invariant §15 demands, and it is already implemented properly. **This is the commercial moat. Do not weaken it.**

2. **Layered service architecture.** Clean separation, single AI abstraction point, single Stripe abstraction point.

3. **Server-side entitlement enforcement.** `EntitlementsService` gates on the server, and the code comments explicitly state that hiding UI controls is "a courtesy to the user, never a control."

4. **Graceful degradation when unconfigured.** Stripe and each AI provider fail with clean, explicit error codes rather than 500s when keys are absent.

5. **Never-destructive versioning.** Uploading, editing and regenerating all create new versions; the original is never overwritten. Satisfies §17 already.

6. **Webhook signature verification.** `StripeService.construct_webhook_event()` verifies properly, and local state is only marked active by webhook — never by the browser hitting the success URL. Satisfies the "never trust frontend payment state" rule in §11.

---

## 4. Partially Implemented Functionality

| Item | What exists | What is missing |
|---|---|---|
| Stripe lifecycle | checkout, portal, 4 event types | idempotency, event log, payment-failure handling, refunds, proration |
| AI cost tracking | `AIUsageLog(user, action, provider, created_at)` | model, tokens, latency, cost, job id, request id, org — i.e. **cost cannot actually be computed** |
| Application tracking | 5 statuses on the curation row | no recruiter-response / interview-stage / offer outcome funnel (§19) |
| Admin panel | users, config, basic stats | no revenue, MRR, AI cost, funnel, leads, GDPR queue |
| Health checks | `/api/health/`, `/api/ready/` | `/health` returns `{"status":"OK"}` unconditionally — **does not check the database**; `/ready` checks the LLM only |
| Pricing config | centralized in `billing/plans.py` | USD-only placeholder, no EUR/GBP, no annual, no VAT |
| Feature flags | `SystemConfiguration` table exists | not used as flags; no rollout/experiment concept |

---

## 5. Broken / Actively Wrong

### 5.1 Gunicorn timeout is shorter than the LLM timeout — P0

`config/settings.py` sets `LLM_TIMEOUT` default **300s**. `Dockerfile` and `docker-compose.yml` both run Gunicorn with `--timeout 150`.

A slow AI call cannot ever reach its own timeout — Gunicorn kills the worker at 150s first. With **only 2 workers**, two concurrent slow generations consume the entire server and every other user — including the pricing page and Stripe checkout — sees a hang or 502. The AI layer's careful retry/timeout handling is unreachable in this configuration.

### 5.2 Test suite is failing and too slow to gate CI — P0

`python manage.py test` was executed against the repository. Result:

```
Ran 355 tests in 915.656s
FAILED (failures=3, errors=2)
```

**Two distinct problems.**

**(a) Five tests do not pass.** All three failures are in `billing/tests_entitlements.py` — the file that verifies the paid/free split, i.e. the exact code path that protects revenue:

| Test | Expected | Actual |
|---|---|---|
| `test_paid_provider_on_missing_information_returns_402_before_any_ai_call` | 402 | **200** |
| `test_paid_provider_on_upload_returns_402_before_any_ai_call` | 402 | 422 |
| `test_standard_resume_parse_bypasses_the_ai_quota_entirely` | 201 | 422 |

The two errors are `MemoryError` raised inside `unittest.mock._increment_mock_call` — a runaway mock call-chain in the test setup, not application code.

**Most likely cause is stale tests, not a live revenue leak.** Both `billing/entitlements.py` and `billing/tests_entitlements.py` have **uncommitted local modifications**. The shipped `FREE_ENTITLEMENTS` now sets `allowed_ai_providers` to *all four* providers with the comment *"Groq and Gemini are no longer Pro-exclusive"*, while these tests still assert those providers return 402 for free users. That is test/code drift from an in-flight change, and it explains the `200 != 402` result.

**This must still be resolved before launch, and must not be assumed benign.** Until the tests are reconciled with intended behaviour, the entitlement suite is not verifying the paid/free boundary at all — so there is currently *no passing automated evidence* that quota and feature gating hold. The separate `422` responses on the upload path need their own investigation.

**(b) 15.3 minutes is too slow to gate every pull request.** Likely dominated by default PBKDF2 password hashing across a ~4,400-line suite; overriding `PASSWORD_HASHERS` to MD5 under test typically cuts this by an order of magnitude. `resumes/tests_job_url_fetch.py` should also be checked for real outbound network calls.

### 5.3 `phone` field is unusable for European customers — P0 for EU launch

`authentication/models.py`: `phone = models.CharField(max_length=10, unique=True)`, and it is in `REQUIRED_FIELDS`.

- **10 characters cannot hold an international European phone number** (`+353 87 123 4567`, `+44 7700 900123`). EU signups will fail at registration.
- It is **mandatory and globally unique**, which also blocks legitimate shared or absent numbers.
- Under GDPR data minimisation, a mandatory phone number for a resume product is difficult to justify at all.

### 5.4 Runtime config swallows all exceptions

`api/utils/config.py` wraps its `SystemConfiguration` lookup in a bare `except Exception`. Every AI call performs several such lookups, so a database problem degrades silently to `settings.py` defaults with no signal, and each AI request issues roughly **eight extra queries** it does not need.

---

## 6. Security Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| S1 | **High** | **Provider API keys returned in plaintext.** `GROQ_API_KEY` and `GEMINI_API_KEY` are `SystemConfiguration` rows; `SystemConfigurationSerializer` exposes `value` with **no masking**, so `GET /api/admin/config/` returns live API keys in cleartext to any staff user, and stores them unencrypted in the database. | `api/serializers/admin_serializers.py`, `api/views/admin_views.py` |
| S2 | **High** | **JWT stored in `localStorage`.** Any XSS anywhere on the origin exfiltrates a 7-day refresh token. | `intelliresume-react/src/services/api.js` |
| S3 | **High** | **No account deletion path at all** — see §9. | — |
| S4 | Medium | **No webhook idempotency.** Stripe retries and replays are reprocessed unconditionally; no processed-event table. | `billing/views/billing_view.py` |
| S5 | Medium | **Refresh tokens are not rotated.** `ROTATE_REFRESH_TOKENS: False` with a 7-day lifetime — a stolen refresh token is valid for a full week with no detection. | `config/settings.py` |
| S6 | Medium | **Throttling is process-local.** `ScopedRateThrottle` uses the default LocMem cache; with multiple workers or replicas the effective rate limit becomes *N× the configured value*, and it resets on restart. Needs a shared Redis cache. | `config/settings.py` |
| S7 | Medium | **Only two throttle scopes exist** (`auth_sensitive`, `ai_resume`); registration, password reset and upload paths should be verified individually. | `config/settings.py` |
| ~~S8~~ | ~~Medium~~ | ~~Media served from local disk, no signed URLs, no object-level access control.~~ **Fixed**: `AWS_STORAGE_BUCKET_NAME` switches to S3-compatible storage (private bucket, time-limited signed URLs) with no code changes; local disk remains the zero-config dev default. | `config/settings.py` |
| S9 | Low-Med | **No malware/content scanning on upload**; MIME and extension validation should be re-verified against magic bytes. | `resumes/services/resume_upload_service.py` |
| S10 | Low | `SECRET_KEY` has no fail-fast guard — if the env var is missing in production, Django starts with `None`. | `config/settings.py` |
| S11 | Low | `DEBUG` defaults to **`True`** when unset. Safe-by-default would be `False`. | `config/settings.py` |

**Positives:** IDOR protection appears to be applied consistently (`filter(user=request.user)` / `get(id=..., user=user)` throughout), and there is a dedicated `resumes/tests_security_audit.py` (438 lines). SSRF is mitigated on job-URL import via a domain allowlist. Production security headers (HSTS, nosniff, SSL redirect, `X-Frame-Options: DENY`) are correctly gated behind `not DEBUG`. CORS is correctly restricted in production and `.env` is not tracked in git.

---

## 7. Billing Issues

| # | Severity | Issue |
|---|---|---|
| B1 | **P0** | **Pricing is a USD placeholder.** `billing/plans.py` ships `"$12/mo"` / `price_cents: 1200`, and its own docstring says *"Every price and limit here is a PLACEHOLDER."* No EUR, no GBP, no annual plan, no VAT treatment, no currency localisation. The target market is Europe. |
| B2 | **P0** | **No `invoice.payment_failed` / `invoice.paid` handling.** `SubscriptionSyncService.apply_event()` handles only `checkout.session.completed` and `customer.subscription.created/updated/deleted`. A failed renewal is never surfaced to the customer, and dunning does not exist. |
| B3 | **P0** | **No webhook idempotency or event log.** Stripe *will* redeliver. There is no stored event id, no replay protection, and no record of what was processed — so a billing dispute cannot be reconstructed. |
| B4 | **P0** | **Annual billing does not exist.** §2 requires €119/year. There is a single price id (`STRIPE_PRICE_ID_PRO`). |
| ~~B5~~ | ~~P1~~ | ~~No upgrade/downgrade/proration logic; plan changes via the Stripe portal have no local reconciliation.~~ **Fixed**: plan changes still go through the Stripe Customer Portal (proration is Stripe's own math — not worth reimplementing), but `customer.subscription.updated` now re-derives `plan_id`/`currency`/`billing_period` from the subscription's actual current price, so a monthly↔annual switch (or a future second paid plan) no longer leaves local state — and MRR/ARR reporting — frozen at whatever the original checkout set. | `billing/services/subscription_sync_service.py` |
| B6 | P1 | **No refund handling** (`charge.refunded` unhandled). |
| B7 | P1 | `_handle_checkout_completed` sets `status="active"` **without setting `current_period_end`**, so the first period has no expiry until a later `subscription.updated` event arrives. |
| B8 | P1 | Subscription lookup by `stripe_customer_id` uses `.first()` with no uniqueness constraint on that column. |
| B9 | P2 | Marketing copy vs. enforcement drift: `plans.py` advertises "1 verified resume" while `MAX_RESUMES_PER_USER` defaults to **5**. |

---

## 8. AI Issues

| # | Severity | Issue |
|---|---|---|
| A1 | **P0** | **All AI runs synchronously inside the HTTP request.** No Celery, no Redis, no queue — `grep -c celery` across the backend returns **0**. The API documentation confirms it: *"Synchronous call, not a background job: this request blocks until the LLM responds."* With `--workers 2`, **two concurrent AI users saturate production.** This is the single largest architectural risk, exactly as the source audit stated. |
| A2 | **P0** | **Ollama is the default production path.** `LLM_PROVIDER` defaults to `ollama`; `docker-compose.yml` provisions Ollama with 6 CPUs / 6 GB and makes the backend *depend on it being healthy*. CPU inference cannot serve concurrent paying traffic. |
| A3 | **P0** | Gunicorn timeout (150s) is shorter than the LLM timeout (300s) — see §5.1. |
| A4 | **P0** | **AI cost cannot be calculated.** `AIUsageLog` stores only `(user, action, provider, created_at)`. No model, no token counts, no latency, no cost, no request id. Gross margin, AI COGS ratio, LTV and payback — every unit-economics metric in §45 — are therefore **currently uncomputable**. |
| A5 | P1 | **No provider fallback.** A provider outage is a hard user-facing failure; there is no automatic Groq→Gemini failover. |
| A6 | P1 | **Rate limits are not retried.** HTTP 429 raises immediately; retries cover only timeout and connection errors, and use no exponential backoff. |
| A7 | P1 | **No job cancellation, no job status model, no progress feedback.** |
| A8 | P2 | Ollama `keep_alive=30m` holds a multi-gigabyte model resident per instance — expensive at rest. |

**Positive:** the anti-hallucination invariant is real and deterministic (see §3.1), and `resumes/tests_missing_information.py`, `tests_resume_regeneration.py` and `tests_resume_curation.py` already exercise parts of it.

---

## 9. GDPR Issues

**This is the weakest area of the product, and the hardest legal blocker for an EU launch.**

| # | Severity | Issue |
|---|---|---|
| ~~G1~~ | ~~P0~~ | ~~No account deletion.~~ **Fixed**: `deleteAccount()` + `DeleteAccountModal` in Settings, backed by `authentication/services/account_deletion_service.py`. |
| ~~G2~~ | ~~P0~~ | ~~No data export.~~ **Fixed**: "Export My Data" in Settings (`exportAccountData()`), Article 20 JSON export. |
| ~~G3~~ | ~~P0~~ | ~~No cookie consent mechanism.~~ **Fixed** — cookie-consent banner with necessary/analytics/marketing categories. |
| ~~G4~~ | ~~P0~~ | ~~Ad script has no consent gate.~~ **Fixed** — gated behind the consent categories from G3. |
| ~~G5~~ | ~~P0~~ | ~~No policy version tracking or acceptance records.~~ **Fixed** — append-only policy-acceptance versioning. |
| ~~G6~~ | ~~P1~~ | ~~No privacy/data-control section in Settings.~~ **Fixed** — see G1/G2. |
| ~~G7~~ | ~~P1~~ | ~~No subprocessor disclosure.~~ **Fixed** — `GDPR_DATA_FLOW.md` documents Groq/Gemini as subprocessors, what they receive, and when. |
| G8 | P1 | **Mandatory phone collection** conflicts with data minimisation (and is technically broken for EU numbers — §5.3). |
| ~~G9~~ | ~~P2~~ | ~~No retention policy or automatic expiry.~~ **Partially fixed**: expired login/password-reset OTPs are now auto-deleted daily (`authentication/tasks.py`, `CELERY_BEAT_SCHEDULE`) — pure housekeeping, zero product risk. Resumes/curations/uploaded files are deliberately **not** auto-expired: users on the Free plan reasonably expect their data to persist until they delete it themselves (already possible — see G1), and picking a retention window for someone else's content is a product/legal decision this pass didn't make unilaterally. |

**Positive:** `PrivacyPolicy.jsx` and `TermsOfService.jsx` exist and are written honestly — the ATS score is explicitly described as an internal heuristic rather than an industry standard, which is both good faith and the right instinct.

---

## 10. Deployment Issues

| # | Severity | Issue |
|---|---|---|
| D1 | **P0** | **SQLite is the deployed database.** `docker-compose.yml` sets `SQLITE_DB_PATH=/app/data/db.sqlite3`; Postgres engages only if `DATABASE_URL` is set, which compose never sets. SQLite under concurrent write load will lock or corrupt. §27 requires Postgres only. |
| D2 | **P0** | **No CI/CD.** Neither repository contains `.github/`. No lint, no test gate, no migration validation, no build check, no deploy pipeline, no rollback. |
| D3 | **P0** | **No Redis service** in compose — required for Celery, for a shared throttle cache, and for shared locks. |
| D4 | **P0** | **No backups and no restore test.** No `DISASTER_RECOVERY.md`, no RPO/RTO, no documented restoration procedure. |
| ~~D5~~ | ~~P0~~ | ~~Media on a local Docker volume; no S3 abstraction.~~ **Fixed** — see S8. |
| D6 | P1 | **No reverse proxy or TLS termination** in compose. `SECURE_SSL_REDIRECT` defaults on in production but nothing terminates HTTPS. |
| D7 | P1 | **No static file strategy** — no WhiteNoise, no `STATIC_ROOT`, no `collectstatic` step. Django admin renders unstyled in production. |
| D8 | P1 | **`/api/health/` never touches the database** — it returns `{"status":"OK"}` unconditionally, and compose uses it as the container healthcheck. A container with a dead database reports healthy. Directly violates §31. |
| D9 | P1 | **No error tracking or structured logging.** No Sentry (`grep -c sentry` → **0**), no `LOGGING` configuration, no correlation or request IDs. |
| D10 | P2 | Migrations run inline in the container `command`, which races across replicas. |
| D11 | P2 | `api/migrations/0001_initial.py` is untracked in git while `api/models.py` is modified — migration state is not committed. |

---

## 11. Analytics Gaps

**Essentially nothing exists.** A grep for `gtag|analytics|posthog|plausible|mixpanel` across the frontend returns **0 results**.

Missing outright: visitor tracking, signup source and UTM attribution, an activation definition and its measurement, the §20 funnel (landing → signup → upload → analysis → tailored resume → export → checkout → paid), drop-off measurement at any stage, MRR/ARR/ARPU, churn, CAC, LTV, LTV:CAC, payback, refunds, failed payments, gross and contribution margin, and AI cost ratio.

**Consequence:** every commercial metric required by §45 is currently uncomputable, and §47's go/no-go scorecard cannot be populated with real data. The AI-cost half additionally depends on fixing A4 first.

---

## 12. B2B2C Gaps

Total greenfield. `grep -c "Organization"` → **1** (an unrelated string).

Missing: `Organization`, `OrganizationMember`, roles (OWNER/ADMIN/COACH/RECRUITER/CANDIDATE), candidate workspaces, seat-based subscriptions, per-org usage and cost attribution, org-level data isolation, agency admin workflow, B2B lead capture (`Lead` / `LeadActivity` / `LeadStatus`), `/for-career-coaches` and `/for-recruiters` landing pages, and agency pilot metrics.

**Recommendation — agree with the source report, and go slightly further:** do **not** build multi-seat infrastructure now. Per §23/§24/§43, run 2–3 founder-operated agency pilots first. The only things worth building pre-validation are:

1. the B2B lead-capture form and the career-coach landing page, and
2. a **nullable `organization` foreign key reserved on the billing and usage models**, so the eventual migration does not require rewriting entitlement enforcement.

That is roughly one day of work, versus 4–8 weeks for the full platform.

---

## 13. Revenue Blockers

Ranked by what actually stops money arriving:

1. **Cannot charge a European customer a correct price.** USD placeholder, no EUR/GBP, no VAT treatment, no annual plan. (B1, B4)
2. **Cannot lawfully operate in the EU.** No deletion, no export, no cookie consent, no policy versioning. (G1–G5)
3. **Cannot survive concurrent paid usage.** Synchronous AI + 2 workers + CPU Ollama + timeout inversion + SQLite. (A1, A2, A3, D1)
4. **Cannot detect or recover a failed payment.** No `invoice.payment_failed`, no dunning, no idempotency. (B2, B3)
5. **Cannot prove the business works.** No analytics, no funnel, no MRR, no AI cost → no CAC, no LTV, no margin. (§11, A4)
6. **Cannot recover from disaster.** No backups, no restore test, SQLite. (D1, D4)
7. **Cannot ship safely.** No CI, no deploy gate, and 5 failing tests — all in the entitlement suite that guards the paid/free split. (D2, §5.2)
8. **Cannot prove customer value.** No outcome tracking, so ROI claims remain theoretical. (§19)

---

## 14. Recommended Priority

### P0 — blocks launch / revenue

| ID | Item | Est. |
|---|---|---|
| P0-1 | Celery + Redis + `AIJob` model; move all AI to background workers with status polling | 5–7 d |
| P0-2 | Default production AI to a cloud provider (Groq or Gemini, after benchmarking); demote Ollama to opt-in/self-hosted | 1–2 d |
| P0-3 | Fix the Gunicorn/LLM timeout inversion; raise worker count | 0.5 d |
| P0-4 | PostgreSQL as the only production database; remove SQLite from compose | 1 d |
| P0-5 | Centralized multi-currency pricing config (EUR/GBP, monthly + annual, VAT-aware, per-currency Stripe price ids) | 2–3 d |
| P0-6 | Stripe hardening: idempotency table, event log, `invoice.payment_failed`/`paid`, `charge.refunded`, period-end on activation | 3–4 d |
| P0-7 | GDPR account deletion (cascade + anonymise, file removal) | 2–3 d |
| P0-8 | GDPR data export (structured, downloadable) | 2 d |
| P0-9 | Cookie consent system (Necessary/Analytics/Marketing, default necessary-only) **that actually gates the AdSense loader** | 2–3 d |
| P0-10 | Policy version + acceptance tracking | 1 d |
| P0-11 | Expand `AIUsageLog` to model / tokens / latency / cost / request-id / job-id | 1–2 d |
| P0-12 | Fix the 5 failing entitlement tests; speed the suite up (`PASSWORD_HASHERS`); add CI (lint + test + migration check) | 2 d |
| P0-13 | Fix the `phone` field (length, optional, international) | 0.5 d |
| P0-14 | Mask secrets in the admin config API; move API keys out of the database | 0.5 d |
| P0-15 | Real `/health` (DB + Redis) and `/ready`; backups plus a **verified restore test** | 2 d |

**P0 total ≈ 26–34 engineer-days.**

### P1 — required shortly after launch

~~Object storage (S3) abstraction~~ done; ~~Sentry + structured logging + correlation IDs~~ done; ~~conversion analytics and funnel instrumentation~~ done; ~~MRR/admin business dashboard~~ done; ~~customer outcome tracking (§19)~~ done; ~~refresh-token rotation~~ done; ~~shared Redis throttle cache~~ done; ~~provider fallback~~ done; ~~upgrade/downgrade/proration~~ done; ~~privacy controls in Settings~~ done; ~~`GDPR_DATA_FLOW.md` and subprocessor list~~ done; ~~paywall redesign (§22)~~ done — the `UpgradePrompt` sales-moment component existed but two real gated screens (`JobAnalysis.jsx`'s AI-quota gate, `TailoredResumeVersions.jsx`'s Pro-only gate) fell through to a generic broken-looking error instead, fixed and verified live; ~~B2B lead capture + `/for-career-coaches`~~ done — new public landing page + `POST /api/leads/b2b/`, leads triaged via Django admin (no bespoke admin UI needed).

**P1 backlog is now fully closed.**

### P2 — scale

~~retention-policy automation~~ partially done — see G9; performance indexes (`AIUsageLog` already has the one composite index its hot query needs; no other index gap justified by real query data — adding more without observed slow queries under real traffic would be guessing, not engineering); read replicas (still correctly deferred — no production traffic yet to justify one).

Three items below were explicit business decisions, made deliberately rather than left as unreviewed placeholders. Deciding not to build something is still a decision — spreading engineering effort across three unvalidated bets pre-launch is worse capital allocation than saying no to all three and revisiting each on its own trigger.

**Resume/file retention window — decided: no automatic deletion.** Self-service delete (G1) already satisfies GDPR Article 17; nothing here is legally required beyond that. Resumes are KB-sized text/docx — storage cost is immaterial at any realistic near-term scale, so there's no cost lever to pull yet either. An inactivity-based auto-purge risks deleting an infrequent-but-real user's data (someone job-hunting who logs back in every few months) for a savings that doesn't matter. Revisit only if storage cost becomes material at real scale, or a specific jurisdiction mandates a retention limit — neither is true today.

**Referral system — decided: not yet.** A referral program amplifies existing retention; it's worthless as a growth lever without retention data to amplify, and its reward economics (credits/free months against real revenue) can't be sized responsibly with zero subscriber history to base them on. Revisit after ~60-90 days of real Pro subscriber renewal data shows what's actually worth incentivizing.

**Multi-seat/org model — decided: not yet, pilot manually first.** Building real multi-tenant billing/permissions is the single largest engineering lift of anything decided this pass — committing it without a validated paying buyer is exactly the kind of bet that stalls an early-stage product. The right next step already exists: `/for-career-coaches` (built this session) is the demand-signal channel. If a coach or agency lead actually wants multiple seats, handle it manually — a few individually-managed accounts under one negotiated arrangement — rather than building the product speculatively. Revisit once 2+ real paying customers have been piloted that way and actually want the real thing.

### P3 — future

SSO, SOC 2, ISO 27001, white-label, ATS integrations, mobile. **Per §51, do not start these.**

---

## 15. Documentation Status

| Document | Status |
|---|---|
| `README.md` | Exists |
| `COMMERCIAL_READINESS_AUDIT.md` | **This document** |
| `ARCHITECTURE.md` · `DEPLOYMENT.md` · `SECURITY.md` · `GDPR_DATA_FLOW.md` · `BILLING.md` · `AI_ARCHITECTURE.md` · `AI_COST_MODEL.md` · `DISASTER_RECOVERY.md` · `COMMERCIAL_SCORECARD.md` · `GO_TO_MARKET.md` | **Missing** |

---

## 16. Assumptions and Limits of This Audit

1. **No code was executed against production.** All findings come from static reading plus one local test-suite run.
2. **The test suite was run to completion**: 355 tests, 915s, 3 failures + 2 errors (§5.2). It was run on the working tree, which contains **uncommitted modifications to 10 files** — including both `billing/entitlements.py` and `billing/tests_entitlements.py`, the code and tests involved in every failure. Results on a clean checkout of `QA` may differ.
3. **Stripe was not exercised live.** No test-mode checkout, webhook replay, or lifecycle test was run; all billing findings are from code reading.
4. **AI providers were not benchmarked.** The Groq-vs-Gemini production-default decision (P0-2) requires real latency, cost and quality measurement.
5. Effort estimates assume one experienced full-stack engineer already familiar with this codebase.
6. Pricing (€14.99/mo, €119/yr) is treated as a **starting hypothesis** per §2 — the recommendation is to build currency and price as configuration, not to hard-code those numbers.

---

## 17. Recommended Immediate Sequence

Do these in order; each is a prerequisite for the next.

1. **Fix the 5 failing entitlement tests** (§5.2) — they guard the paid/free boundary, and until they pass there is no automated evidence that quota and feature gating actually hold.
2. **Postgres + Redis + Celery** (P0-1, P0-4) — the foundation everything else sits on.
3. **Cloud AI default + timeout fix** (P0-2, P0-3) — makes concurrent paid traffic survivable.
4. **Pricing + Stripe hardening** (P0-5, P0-6) — makes money arrive correctly.
5. **GDPR: delete, export, consent, policy versions** (P0-7 … P0-10) — makes an EU launch lawful.
6. **Cost and analytics instrumentation** (P0-11) — makes the business measurable.
7. **CI, health checks, backups, restore test** (P0-12, P0-15) — makes it operable.

Only then: funnel, paywall, outcome tracking, admin dashboard, B2B landing page.

---

*Prepared as the Week 1 deliverable defined in §6 and §48. No application code was modified in producing this audit.*
