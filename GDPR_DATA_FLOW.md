# GDPR Data Flow

What personal data this application holds, where it goes, and the
self-service controls a user has over it. Reflects what is actually
implemented — see `COMMERCIAL_READINESS_AUDIT.md` P0-7 through P0-10 for
what this closed. **Not legal advice** — the actual Privacy Policy / Terms
of Service text must be reviewed by qualified legal counsel; this
document describes the technical mechanics underneath it.

## What personal data is collected, and why

| Data | Where | Purpose |
|---|---|---|
| Name, email, phone (optional) | `authentication.User` | Account identity, sign-in, contact |
| Resume file + parsed content | `resumes.Resume`, `ResumeVersion` | The core product — resume storage, editing, tailoring |
| Job descriptions the user pastes/imports | `resumes.JobResumeCuration` | Job-specific analysis and tailoring |
| AI-generated tailored resumes/cover letters | `JobResumeCuration`, `JobResumeCurationVersion` | Product output, version history |
| Application tracking (status, notes) | `JobResumeCuration` | User's own record of their job search |
| Billing identity (Stripe or Razorpay customer/subscription id) | `billing.Subscription` | Payment processing |
| Billing state, and optionally GSTIN, business name and address | `billing.BillingProfile` | Indian GST place of supply and tax invoices (rupee payments only) |
| GST invoices (buyer name, email, GSTIN, address, amounts) | `billing.GSTInvoice` | Legal record of each rupee payment — **retained after account deletion**, see below |
| Your own AI provider key (encrypted, optional) | `billing.UserAIKey` | Running your AI requests on your own provider account |
| AI usage records | `billing.AIUsageLog` | Quota enforcement, cost tracking |
| Cookie/consent preference | `authentication.CookieConsent` | Consent record (see below) |
| Policy acceptance history | `authentication.PolicyAcceptance` | Compliance record of what was agreed to, and when |

## Subprocessors (third parties that see personal data)

| Subprocessor | What they receive | When |
|---|---|---|
| **Google (Gemini API)** | Resume text, job description text | Every AI feature (resume parse, job analysis, gap analysis, tailored resume, cover letter, application email) while Gemini is the configured provider (`LLM_PROVIDER`) or the fallback (`LLM_FALLBACK_PROVIDER`). On Google's unpaid tier, submitted content may be used to improve Google's products, including human review - use a paid tier for production data |
| **Groq** | Resume text, job description text | Same features, while Groq is the configured or fallback provider |
| **Kira (kiraai.vn)** | Resume text, job description text | Same features, while Kira is the configured or fallback provider |
| **Stripe** | Name, email, payment details | Only for a user who starts a EUR/GBP checkout — card details never touch this application's servers |
| **Razorpay** | Name, email, and the UPI, card or bank details entered in Razorpay Checkout | Only for a user who starts a rupee checkout — payment details are entered in Razorpay's own window and never touch this application's servers |
| **Resend** | Email address, and the contents of the message sent to it | Every transactional email: login OTP codes, billing/dunning notices, and outcome follow-ups. Used because DigitalOcean blocks outbound SMTP from the droplet, so mail leaves over Resend's HTTPS API instead |
| **Apify** | The job posting URL the user submitted, and the page content at that URL | Only when "Import from link" hits a posting the primary fetch can't read (a page that renders client-side) — used as a last-resort rescue to fetch and render it. Never receives resume data or anything about the user themselves |
| Self-hosted Ollama | Resume/job text | Only if configured as a provider — stays on the operator's own infrastructure, not a third party |
| `standard` parser | Resume text | Only when users are allowed to choose a provider (`AI_PROVIDER_SELECTION_ENABLED`) and pick it — stays on this application's own server, no AI provider involved |

Which provider receives resume and job text is an operator setting in the
admin Configuration tab, not a user choice (unless
`AI_PROVIDER_SELECTION_ENABLED` is switched on). With a hosted provider
configured — the production setup — every AI feature sends the relevant
text to that provider, so the user-facing Privacy Policy names these
providers rather than describing third-party processing as optional.

All three hosted providers are based outside the EU (Google and Groq in the
United States; Kira is a Vietnamese service), so EU users' data is
transferred internationally. Confirm the transfer mechanism (for example
Standard Contractual Clauses) and each provider's data-processing terms with
counsel before relying on them.

## Self-service controls (GDPR Articles 15/17/20)

All under `/api/auth/`, `IsAuthenticated`:

| Right | Endpoint | Behavior |
|---|---|---|
| Erasure (Art. 17) | `POST /api/auth/account/delete/` | Requires current password. Permanently deletes the user and everything that cascades from it (resumes + files, resume versions, job curations + tailored-resume/cover-letter versions, AI usage records, AI jobs, subscription, coupon redemptions, OTPs, policy acceptances, cookie consent) — see `authentication/services/account_deletion_service.py`. Cancels any live Stripe subscription first. Removes any newsletter subscription matching the account's email. |
| Portability (Art. 20) | `GET /api/auth/account/export/` | Downloadable structured JSON: profile, resumes + version history, job curations + tailored-resume version history, subscription status, an AI-usage *summary* (counts by action, not the raw per-call log), policy acceptances, cookie consent. |
| Access (Art. 15) | `GET /api/auth/me/` (profile) + the export endpoint above | The export endpoint is the complete answer; `/me/` is the lightweight profile-only view the UI uses day-to-day. |

Every one of these is scoped to `request.user` only — verified by
dedicated cross-user isolation tests (`authentication/tests_gdpr.py`).

## Cascade / cleanup mechanics

Deleting the `User` row cascades automatically through `on_delete=CASCADE`
foreign keys on every model above **except**:

- **Uploaded files** — a `FileField`'s underlying file is never deleted
  automatically when its row is deleted; `AccountDeletionService`
  collects every resume's file path before deletion and removes each one
  from storage afterward.
- **The Stripe subscription** — an external system; canceled via the
  Stripe API before the local record is deleted, so a deleted account
  doesn't keep being billed.
- **The Razorpay subscription** — likewise cancelled immediately via the
  Razorpay API, so a UPI AutoPay mandate can't keep debiting someone who can
  no longer sign in to stop it.
- **GST invoices** — deliberately *kept*. Section 36 of India's CGST Act
  requires tax invoices to be retained for 72 months after the annual return
  is due, and GDPR Article 17(3)(b) exempts data held to comply with a legal
  obligation. The link to the user is cleared (`on_delete=SET_NULL`); the
  buyer details printed on each invoice remain, because an issued invoice
  cannot change. They are included in the data export while the account
  exists.
- **Newsletter subscription** (`blog.NewsletterSubscriber`) — keyed by
  email, not a foreign key to `User` (a visitor can subscribe without an
  account), so it's cleaned up by matching email address explicitly.

## Cookie consent

Categories: **Necessary** (always on — the app cannot function without an
auth token), **Analytics**, **Marketing**. Default is necessary-only;
non-essential tracking (the AdSense loader in `AdSlot.jsx`) does not
activate until the marketing category is explicitly accepted.

- **Anonymous visitors**: consent lives in the browser (`localStorage`),
  since there is no account to attach a database row to. This is the
  actual enforcement point — `AdSlot.jsx` checks it before ever injecting
  the third-party ad script.
- **Signed-in users**: `GET`/`PUT /api/auth/cookie-consent/` persists the
  same preference server-side, for cross-device consistency and a durable
  audit record. This is a convenience sync, not a second enforcement
  mechanism — the frontend gate is what actually stops the script.

## Policy version tracking

`authentication.PolicyAcceptance` is **append-only**: accepting a policy
always creates a new row, never updates a previous one, so what a user
agreed to under an old version of the Privacy Policy or Terms of Service
is never silently overwritten. Recorded automatically for Terms of
Service and Privacy Policy at registration
(`RegisterService.register_user`); recorded on demand via
`POST /api/auth/policy-acceptance/` for cookie policy or a re-acceptance
after a version bump. The current version string for each policy type is
config (`CURRENT_PRIVACY_POLICY_VERSION`, `CURRENT_TERMS_OF_SERVICE_VERSION`,
`CURRENT_COOKIE_POLICY_VERSION` — see `.env.example`); bump it when the
actual legal text changes.

## Data retention

GST invoices are the one category with a statutory minimum: 72 months
after the relevant annual return is due (see above). Nothing deletes them
automatically yet; a purge after that period is future work.

For everything else, no automated retention/expiry policy exists yet (e.g.
auto-deleting resumes N months after last login) — data persists until the user
deletes their account or a specific resume. Flagged as a future
improvement, not a P0 blocker (COMMERCIAL_READINESS_AUDIT.md G9).
