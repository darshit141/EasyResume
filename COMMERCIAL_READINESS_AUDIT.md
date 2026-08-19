# IntelliResume — Commercial Readiness Audit

**Audit date:** 2026-08-19
**Branch:** `QA`
**Repositories audited:** `intelliresume-backend` (Django 6.0.7 / DRF), `intelliresume-react` (React 19 / Vite 6)
**Scope:** Read-only architecture, security, billing, AI, GDPR, deployment and commercial audit. **No application code was modified.**

> **Verdict: NOT commercially launchable today.**
> The product core is genuinely strong — notably the evidence-gated gap analysis, which is a real and defensible differentiator. The gaps are almost entirely in the *commercial, legal and operational shell* around that core, not in the core itself. Fifteen P0 items block first revenue.

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
| S8 | Medium | **Media served from local disk**, `MEDIA_ROOT` on the app filesystem, no signed URLs, no object-level access control on the files themselves. Resumes are personal data. | `config/settings.py` |
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
| B5 | P1 | **No upgrade/downgrade/proration logic**; plan changes rely entirely on the Stripe portal with no local reconciliation. |
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
| G1 | **P0** | **No account deletion.** `grep -ri "delete_account"` → **0 results**. A user cannot exercise Article 17 (erasure) at all. Resumes, versions, curations, generated documents, uploaded files and AI logs all persist indefinitely with no path to removal. |
| G2 | **P0** | **No data export.** `grep -ri "data_export"` → **0 results**. Article 20 (portability) is not implemented. |
| G3 | **P0** | **No cookie consent mechanism anywhere.** `grep -ri "consent"` in the frontend returns only prose in the Terms page. There is no banner, no category model, no preference store, no consent version or timestamp. |
| G4 | **P0** | **Ad script has no consent gate.** `AdSlot.jsx` injects `pagead2.googlesyndication.com/…/adsbygoogle.js` for every non-Pro visitor as soon as `VITE_ADSENSE_CLIENT` is configured. It is *currently dormant* because that variable is unset in the checked-in env — but the moment ads are enabled, non-essential third-party tracking loads for EU visitors **before consent**, which is an ePrivacy/GDPR violation. The Privacy Policy already *describes* these ad cookies, so the policy currently documents behaviour the product cannot lawfully perform. |
| G5 | **P0** | **No policy version tracking or acceptance records.** No `PolicyVersion`, no `PolicyAcceptance`, no record of what the user agreed to or when. §8's requirement not to silently overwrite accepted versions cannot be met. |
| G6 | P1 | **No privacy/data-control section in Settings** (§7 requires stored-data visibility, AI processing status, export, delete, privacy contact). |
| G7 | P1 | **No subprocessor disclosure.** Resume text — by definition personal data, and often containing special-category data — is transmitted to Groq and/or Google Gemini. There is no DPA reference, no subprocessor list, no documented lawful basis, and no `GDPR_DATA_FLOW.md`. |
| G8 | P1 | **Mandatory phone collection** conflicts with data minimisation (and is technically broken for EU numbers — §5.3). |
| G9 | P2 | No retention policy or automatic expiry for uploaded files and parsed personal data. |

**Positive:** `PrivacyPolicy.jsx` and `TermsOfService.jsx` exist and are written honestly — the ATS score is explicitly described as an internal heuristic rather than an industry standard, which is both good faith and the right instinct.

---

## 10. Deployment Issues

| # | Severity | Issue |
|---|---|---|
| D1 | **P0** | **SQLite is the deployed database.** `docker-compose.yml` sets `SQLITE_DB_PATH=/app/data/db.sqlite3`; Postgres engages only if `DATABASE_URL` is set, which compose never sets. SQLite under concurrent write load will lock or corrupt. §27 requires Postgres only. |
| D2 | **P0** | **No CI/CD.** Neither repository contains `.github/`. No lint, no test gate, no migration validation, no build check, no deploy pipeline, no rollback. |
| D3 | **P0** | **No Redis service** in compose — required for Celery, for a shared throttle cache, and for shared locks. |
| D4 | **P0** | **No backups and no restore test.** No `DISASTER_RECOVERY.md`, no RPO/RTO, no documented restoration procedure. |
| D5 | **P0** | **Media on a local Docker volume.** Horizontal scaling is impossible without object storage; no S3 abstraction exists (`grep -c "boto3\|S3\|storages"` → **0**). |
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

Object storage (S3) abstraction; Sentry + structured logging + correlation IDs; conversion analytics and funnel instrumentation; MRR/admin business dashboard; customer outcome tracking (§19); paywall redesign (§22); refresh-token rotation; shared Redis throttle cache; B2B lead capture + `/for-career-coaches`; provider fallback; upgrade/downgrade/proration; privacy controls in Settings; `GDPR_DATA_FLOW.md` and subprocessor list.

### P2 — scale

Organization / multi-seat model (**only after pilot validation**); pricing experiments; referral system; retention-policy automation; performance indexes; read replicas.

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
