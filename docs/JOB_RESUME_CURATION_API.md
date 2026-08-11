# Job-Specific Resume Curation — API Contract & Frontend Integration Guide

This document is the complete, implementation-accurate contract for the job-specific
resume curation feature: submitting a job description, analyzing it, comparing it
against the candidate's profile, reviewing gaps, updating the profile, and
generating/regenerating a tailored resume.

Everything here is also available live and interactively at `/api/docs/` (Swagger UI,
served by `SpectacularSwaggerView`) and as a raw OpenAPI 3 document at `/api/schema/`
(served by `SpectacularAPIView`). This document adds the workflow narrative and
frontend integration guidance that a raw OpenAPI document doesn't carry well; the two
are meant to be read together, and are generated from — and verified against — the
same `@extend_schema` annotations on the actual view code, so they cannot drift apart
silently.

No new endpoints were invented for this document. Every URL below is a real,
implemented, tested route. Where the conceptual workflow describes a step that this
codebase already covers with an existing endpoint (e.g. "update profile" reuses the
resume-edit API instead of a new job-curation-specific one), that reuse is called out
explicitly.

---

## 1. Base URL and authentication

All endpoints are mounted under `/api/resumes/`. Every endpoint in this feature
requires authentication.

- **Scheme:** JWT bearer token (`rest_framework_simplejwt`).
- **Header:** `Authorization: Bearer <access_token>`
- **Token lifetime:** access tokens expire after 30 minutes by default
  (`ACCESS_TOKEN_LIFETIME`); refresh tokens last 7 days (`REFRESH_TOKEN_LIFETIME`).
  Obtain/refresh tokens via the `authentication` app's endpoints (login, refresh) —
  out of scope for this document.
- Missing or invalid tokens produce `401 Unauthorized` with DRF's default
  `{"detail": "Authentication credentials were not provided."}` (or similar) body.

## 2. Response envelope conventions

This feature does **not** invent a new envelope — it follows the pattern already
established across these endpoints:

- **Success responses** are the raw resource JSON (a serialized object, or a JSON
  array for list endpoints). There is no `{"success": true, "data": ...}` wrapper.
- **Business/AI/database error responses** (4xx/5xx beyond plain validation and
  auth/permission errors) use a single consistent shape:
  ```json
  { "detail": "Human-readable message.", "code": "MACHINE_READABLE_CODE" }
  ```
  This is produced by the shared helper `resumes/views/error_responses.py`. Treat
  `code` as the stable, switchable value for frontend logic (retry, show a specific
  message, disable a button); treat `detail` as display-only text that may be
  reworded over time.
- **Field validation errors** (`400` from a malformed request body) use DRF's default
  serializer error shape: a JSON object keyed by field name, each value a list of
  error strings, e.g. `{"job_title": ["This field is required."]}`.
- **Not-found / permission** responses (`404`) use DRF's default
  `{"detail": "Not found."}`-style body and never leak whether a resource exists for
  another user — a resource that exists but is owned by someone else returns
  identically to a resource that doesn't exist at all.

## 3. Synchronous vs. asynchronous — everything here is synchronous

There is **no background job queue, no polling endpoint, and no "processing" status
the frontend needs to poll for.** Every AI-backed call in this feature (JD analysis,
gap analysis, tailored resume generation) makes its Ollama call inline, within the
HTTP request/response cycle, and blocks until it completes, fails, or times out.

- The backend's own LLM call timeout is `LLM_TIMEOUT` (default **120 seconds**),
  with up to `LLM_MAX_RETRIES` (default **2**) automatic retries on transient
  connection/timeout failures.
- The tailored-resume endpoint may make **up to two sequential LLM calls** in the
  worst case (a gap-analysis re-comparison, then resume tailoring with up to one
  automatic retry-with-stricter-prompt if the first attempt introduces ungrounded
  content) — budget roughly 2× `LLM_TIMEOUT` for the client-side request timeout on
  that endpoint specifically.
- Set your HTTP client timeout comfortably above these bounds (e.g. 150s for
  single-call endpoints, 300s for the tailored-resume endpoint) and show a loading
  state in the UI rather than implementing polling — there is nothing to poll.
- The one exception: `JobResumeCuration.status` (`processing` → `completed` |
  `failed`) and `Resume.status` do exist as persisted fields, but by the time any
  endpoint in this feature returns a response, the relevant status transition has
  already happened synchronously inside that same request. They reflect **outcome**,
  not an in-flight background process.

## 4. IDs the frontend must persist

| ID | Where you first get it | What it's used for |
|---|---|---|
| `curation_id` (a `JobResumeCuration.id`) | Response of `POST /job-curations/analyze/` (`id` field) | Path parameter for every other endpoint in this feature: gap analysis, missing-information review, tailored-resume generate/regenerate, version history. **Persist this as soon as you get it** (e.g. in route state or local storage) — there is no way to look it up other than the list endpoint. |
| `tailored_resume_version.id` / `.version` | Response of `POST /job-curations/{curation_id}/tailored-resume/` | Optional: only needed if you want to deep-link to a specific historical version via the version-detail endpoint. Not required for the core generate/regenerate flow, since that always operates on "the latest version for this curation" implicitly. |

Nothing else needs to be persisted client-side — the candidate's own resume/profile
is looked up server-side from the authenticated user on every call, always using the
latest data (see §3 in each relevant endpoint below).

---

## 5. Endpoint reference

### 5.1 Submit job description and analyze it (Steps 1-2)

| | |
|---|---|
| **Method / URL** | `POST /api/resumes/job-curations/analyze/` |
| **Auth** | Required |
| **Path params** | none |
| **Query params** | none |

This single endpoint covers both "create job curation session" and "analyze job
description" — there is no separate create-then-analyze pair of calls. It is
synchronous: the AI call happens inline and the response already contains the
finished analysis.

**Request body** (`JobCurationRequestSerializer`):

| Field | Type | Required | Validation |
|---|---|---|---|
| `job_title` | string | yes | 2-200 characters after trimming whitespace |
| `job_description` | string | yes | 30-20000 characters after trimming whitespace |
| `curate_resume` | boolean | yes | — |

If `curate_resume` is `false`, the job description is **not** analyzed and no
session is created — use this only when the user wants a plain, non-tailored flow.

```json
// Example request
{
  "job_title": "Senior Backend Engineer",
  "job_description": "We need a Senior Backend Engineer with Python, Django, PostgreSQL, Docker, and AWS experience...",
  "curate_resume": true
}
```

**Success responses:**

- `201 Created` (when `curate_resume: true`) — the created session, with `id` (persist
  this — see §4) and the structured `analysis_data`:
  ```json
  {
    "id": 1,
    "job_title": "Senior Backend Engineer",
    "curate_resume": true,
    "status": "completed",
    "analysis_data": {
      "required_skills": ["Python", "REST APIs"],
      "preferred_skills": ["Django REST Framework"],
      "technologies": ["Python"],
      "frameworks": ["Django"],
      "databases": ["PostgreSQL"],
      "cloud_technologies": ["AWS"],
      "tools": ["Docker", "Git"],
      "soft_skills": ["Communication"],
      "responsibilities": ["Build backend services"],
      "experience_requirements": ["5+ years backend engineering"],
      "education_requirements": ["Bachelor's degree or equivalent experience"],
      "certification_requirements": [],
      "important_keywords": ["backend", "API", "scalable"],
      "ats_relevant_terms": ["Python", "Django", "PostgreSQL"],
      "seniority_level": "Senior",
      "other_explicit_requirements": ["On-call participation"]
    },
    "error_code": "",
    "created_at": "2026-08-11T10:00:00Z",
    "updated_at": "2026-08-11T10:00:03Z"
  }
  ```
- `200 OK` (when `curate_resume: false`) — no session created:
  ```json
  { "curate_resume": false, "message": "Job-specific resume curation was not requested." }
  ```

**Error responses:**

| Status | `code` | Meaning |
|---|---|---|
| 400 | — (field errors) | `job_title`/`job_description` missing or out of length range, or `curate_resume` missing/not boolean |
| 401 | — | authentication required |
| 502 | `LLM_EMPTY_RESPONSE`, `LLM_INVALID_RESPONSE`, `LLM_SCHEMA_VALIDATION_FAILED` | AI responded but the output was empty, unparseable, or failed strict schema validation |
| 503 | `LLM_UNAVAILABLE`, `LLM_MODEL_UNAVAILABLE`, `LLM_TIMEOUT` | Ollama unreachable, model not pulled/available, or timed out |
| 500 | `DATABASE_ERROR` | failed to persist the session |

```json
// Example error response (503)
{ "detail": "The configured AI model is unavailable.", "code": "LLM_MODEL_UNAVAILABLE" }
```

---

### 5.2 Get job analysis / curation result (Steps 3, 5)

| | |
|---|---|
| **Method / URL** | `GET /api/resumes/job-curations/{curation_id}/` |
| **Auth** | Required |
| **Path params** | `curation_id` (integer, required) — from §5.1's response |
| **Query params** | none |
| **Request body** | none |

Re-fetches a previously created session (status, `analysis_data`, `error_code`)
without calling the LLM again. Use this to redisplay a session, e.g. after
navigating back to it.

**Success:** `200 OK`, body identical in shape to the `201` example in §5.1.

**Errors:** `401` (auth required), `404` (no such curation for this user — also
returned, indistinguishably, if the ID belongs to a different user).

---

### 5.3 List job curation history (Step 11)

| | |
|---|---|
| **Method / URL** | `GET /api/resumes/job-curations/` |
| **Auth** | Required |
| **Path / Query params** | none |

Returns every curation session owned by the authenticated user, most recent first —
each item shaped like the §5.1 success response. Use this to render a "my tailoring
sessions" list.

**Success:** `200 OK`, JSON array. **Errors:** `401`.

---

### 5.4 Get candidate-vs-job match / gap analysis (Step 3-4)

| | |
|---|---|
| **Method / URL** | `GET /api/resumes/job-curations/{curation_id}/gap-analysis/` |
| **Auth** | Required |
| **Path params** | `curation_id` (integer, required) |
| **Query params** | none |

Compares the user's latest **completed** resume against the curation's structured
job requirements. The first call computes and caches the result; later calls return
the cached result instantly (no LLM call) until explicitly refreshed — refreshing
happens implicitly via the tailored-resume endpoint's `regenerate: true` (§5.7), or
via the missing-information endpoint's own `?regenerate=true` (§5.5). There is no
manual-refresh parameter on this endpoint itself.

**Success `200 OK`:**

```json
{
  "id": 1,
  "job_title": "Senior Backend Engineer",
  "status": "completed",
  "gap_analysis_data": {
    "overall_match_percentage": 50,
    "matched_requirements": [
      {
        "requirement": "Python",
        "category": "required_skill",
        "match_status": "MATCHED",
        "candidate_evidence": ["Python"],
        "confidence": 1,
        "reasoning": "Python is listed in candidate skills.",
        "recommendation": "Keep Python prominent in the profile."
      }
    ],
    "partially_matched_requirements": [],
    "missing_requirements": [],
    "not_verifiable_requirements": [],
    "strengths": ["Backend API experience"],
    "weaknesses_gaps": ["AWS is not present in the verified resume data."],
    "important_missing_information": ["Cloud experience details"],
    "recommendations_for_profile_improvement": [
      {
        "recommendation": "Add AWS only if you genuinely have AWS experience.",
        "recommendation_type": "MISSING_PROFILE_INFORMATION"
      }
    ],
    "ats_keyword_coverage": {
      "covered_keywords": ["Python"],
      "missing_keywords": ["AWS"],
      "coverage_percentage": 50
    },
    "interview_readiness_observations": ["Prepare examples around verified backend work."]
  },
  "gap_analysis_error_code": "",
  "created_at": "2026-08-11T10:00:00Z",
  "updated_at": "2026-08-11T10:00:03Z"
}
```

`match_status` is one of `MATCHED` | `PARTIALLY_MATCHED` | `MISSING` |
`NOT_VERIFIABLE`. `recommendation_type` is one of `MISSING_PROFILE_INFORMATION`
(candidate may genuinely have this, just hasn't recorded it) | `SKILL_TO_ACQUIRE`
(candidate does not appear to have this).

**Errors:**

| Status | `code` | Meaning |
|---|---|---|
| 401 | — | authentication required |
| 404 | — | no such curation for this user |
| 502 | `LLM_SCHEMA_VALIDATION_FAILED`, `LLM_INVALID_RESPONSE` | malformed AI output |
| 503 | `LLM_UNAVAILABLE`, `LLM_MODEL_UNAVAILABLE`, `LLM_TIMEOUT`, `CANDIDATE_DATA_UNAVAILABLE`, `JOB_REQUIREMENTS_UNAVAILABLE` | AI unreachable, or no completed resume / job analysis exists yet to compare |

---

### 5.5 Get missing information / recommendations (Step 4)

| | |
|---|---|
| **Method / URL** | `GET /api/resumes/job-curations/{curation_id}/missing-information/` |
| **Auth** | Required |
| **Path params** | `curation_id` (integer, required) |
| **Query params** | `regenerate` (boolean, optional, default `false`) — forces a fresh gap analysis instead of reusing a cached one; use after the user updates their profile (§5.6) |

Turns the gap analysis into a screen-ready review: overall match, strong matches,
partial matches, missing requirements (each mapped to a `profile_section` — one of
`skills`, `experience`, `education`, `certifications`, `summary`, `profile` — so the
frontend knows which form section to highlight), missing candidate information,
recommendations, ATS keyword gaps, and a deduplicated list of profile sections worth
revisiting.

**Every missing-requirement `message`/`guidance` pair is fixed, backend-generated
text — never AI free text — and is written to never instruct the user to simply "add"
a skill.** `message` states only that the requirement isn't present in the profile;
`guidance` separately offers the disjunctive framing your product spec requires:

```json
{
  "requirement": "AWS",
  "category": "cloud_technology",
  "profile_section": "skills",
  "message": "AWS is listed as a job requirement but is not present in your current profile.",
  "guidance": "If you genuinely have AWS experience that is not currently recorded, add it to your profile. Otherwise, consider AWS a current skill gap."
}
```

**Success `200 OK` (full shape):**

```json
{
  "id": 1,
  "job_title": "Senior Java Backend Engineer",
  "overall_match_percentage": 66.67,
  "strong_matches": [
    { "requirement": "Java", "category": "required_skill", "match_status": "MATCHED", "reasoning": "Java is listed in verified candidate skills." }
  ],
  "partial_matches": [
    { "requirement": "5 years backend experience", "category": "experience_requirement", "match_status": "PARTIALLY_MATCHED", "reasoning": "Candidate data indicates 3 years against a 5-year requirement." }
  ],
  "missing_requirements": [
    {
      "requirement": "AWS",
      "category": "cloud_technology",
      "profile_section": "skills",
      "message": "AWS is listed as a job requirement but is not present in your current profile.",
      "guidance": "If you genuinely have AWS experience that is not currently recorded, add it to your profile. Otherwise, consider AWS a current skill gap."
    }
  ],
  "missing_candidate_information": ["Cloud deployment experience details are not present in the profile."],
  "recommendations": [
    { "recommendation": "Add AWS only if you genuinely have AWS experience.", "recommendation_type": "MISSING_PROFILE_INFORMATION" }
  ],
  "ats_keyword_gaps": { "covered_keywords": ["Java", "Spring Boot"], "missing_keywords": ["AWS"], "coverage_percentage": 66.67 },
  "suggested_profile_sections": ["skills"]
}
```

**Errors:** same table as §5.4, plus `400` if `regenerate` is supplied but isn't a
valid boolean.

---

### 5.6 Update candidate profile (Step 5 — existing profile API, reused)

| | |
|---|---|
| **Method / URL** | `PUT /api/resumes/edit/` |
| **Auth** | Required |
| **Path / Query params** | none |

**This is not a job-curation-specific endpoint.** Per the "reuse, don't duplicate"
requirement, profile updates go through the resume module's existing edit API — the
same one used outside the job-curation feature entirely. There is no
`curation_id` in the request: it always updates the authenticated user's own most
recent resume, so cross-user edits are structurally impossible (no ID to spoof).

**Important:** this replaces the *entire* `parsed_data` document — it is not a
partial/PATCH update. Fetch the current profile first with `GET /api/resumes/view/`,
modify it client-side, and send the complete object back.

**Request body:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `parsed_data` | object | yes | `{personal_information: {name, email, phone}, name, email, phone, summary, skills: [string], experience: [string], education: [string], projects: [string], certifications: [string], achievements: [string], languages: [string]}`. Every list field is a flat array of free-text strings (e.g. one string per job under `experience`), not nested objects. |

```json
// Example request
{
  "parsed_data": {
    "personal_information": { "name": "Jane Doe", "email": "jane@example.com", "phone": "1234567890" },
    "name": "Jane Doe", "email": "jane@example.com", "phone": "1234567890",
    "summary": "Backend engineer with 5 years of experience.",
    "skills": ["Java", "Spring Boot", "Kafka", "AWS"],
    "experience": ["Backend Engineer at Acme Corp (2019-2024) - built event-driven services with Java, Spring Boot, and Kafka."],
    "education": ["B.Sc. Computer Science, State University, 2015-2019."],
    "projects": [],
    "certifications": ["AWS Certified Developer - Associate, 2023."],
    "achievements": [],
    "languages": ["English"]
  }
}
```

**Success `200 OK`:**

```json
{ "message": "Your profile has been updated. You can regenerate your resume to reflect the new information.", "ats_score": 85.0 }
```

A new resume version is created automatically (`GET /api/resumes/versions/`); no
prior version is ever deleted.

**Errors:** `400` (`parsed_data` missing — field-error body), `401`, `404` (the user
has never uploaded a resume — upload one first via `POST /api/resumes/upload/`).

---

### 5.7 Generate / get / regenerate tailored resume (Steps 6-7, 10)

| | |
|---|---|
| **Method / URL** | `POST /api/resumes/job-curations/{curation_id}/tailored-resume/` |
| **Auth** | Required |
| **Path params** | `curation_id` (integer, required) |
| **Query params** | none |

This one endpoint intentionally covers three conceptual steps — **generate**, **get**,
and **regenerate** — via a single boolean flag, rather than three separate routes:

| Conceptual step | How to call it |
|---|---|
| Generate the first tailored resume | `{"regenerate": false}` (or omit the field — defaults to `false`) when none exists yet |
| Get the current/generated resume | `{"regenerate": false}` when one already exists — returns the cached version instantly, no AI call |
| Regenerate after a profile update | `{"regenerate": true}` |

**Request body** (`ResumeCurationGenerateRequestSerializer`):

| Field | Type | Required | Default |
|---|---|---|---|
| `regenerate` | boolean | no | `false` |

**What `regenerate: true` actually runs, end to end** (this is the full "Profile
Update → Re-run Candidate vs Job Analysis → Recalculate Gaps → Regenerate Tailored
Resume" pipeline from the product spec, all inside one synchronous call):

1. Load the authenticated user's **latest completed** profile (never a stale/cached
   copy — read fresh from the database every call).
2. Load this job curation session's structured job requirements.
3. Re-run the candidate-vs-job comparison (this also refreshes what §5.4/§5.5 return).
4. Recalculate the match score and gap summary from that fresh comparison.
5. Generate the tailored resume using only the latest verified candidate data.
6. Validate every generated skill, experience, project, education, and certification
   entry against the candidate's verified source data — content that cannot be traced
   back to something the candidate actually has is rejected (with one automatic
   retry using a stricter prompt) rather than silently returned.
7. Persist the result as a new, numbered version. **Prior versions are never deleted
   or overwritten** — see §5.8/§5.9.

If generation fails for any reason — AI unavailable, malformed output, content that
still fails verification after the retry, or a database error — **the previously
persisted tailored resume and its entire version history are left completely
untouched.** A failed regeneration never corrupts or removes a working previous
result.

**Success `200 OK`:**

```json
{
  "id": 1,
  "job_title": "Senior Java Backend Engineer",
  "status": "completed",
  "tailored_resume_version": { "id": 7, "version": 2, "created_at": "2026-08-11T10:00:05Z" },
  "tailored_resume_data": {
    "personal_information": { "name": "Jane Doe", "email": "jane@example.com", "phone": "1234567890" },
    "summary": "Backend engineer with hands-on Java, Spring Boot, and Kafka experience building reliable, containerized services.",
    "skills": ["Java", "Spring Boot", "Kafka", "Docker", "PostgreSQL"],
    "experience": [
      {
        "source_index": 0,
        "source_text": "Backend Engineer at Acme Corp (2019-2022) - built event-driven services with Java, Spring Boot, and Kafka.",
        "tailored_text": "Backend Engineer at Acme Corp (2019-2022) - designed and shipped event-driven microservices with Java, Spring Boot, and Kafka."
      }
    ],
    "projects": [],
    "education": [],
    "certifications": []
  },
  "tailored_resume_error_code": "",
  "gap_review": { "...": "same shape as the §5.5 response body, freshly consistent with this tailored resume" },
  "created_at": "2026-08-11T10:00:00Z",
  "updated_at": "2026-08-11T10:00:05Z"
}
```

`personal_information` always comes straight from the verified profile, never from
the AI — contact details cannot be fabricated. Each `experience`/`projects`/
`education`/`certifications` entry carries `source_index` + `source_text` (the
untouched original) alongside `tailored_text` (the AI's reworded version), so the
frontend can show provenance if desired.

**Errors:**

| Status | `code` | Meaning |
|---|---|---|
| 400 | — | `regenerate` supplied but not a valid boolean |
| 401 | — | authentication required |
| 404 | — | no such curation for this user (a user may regenerate only their own sessions) |
| 502 | `LLM_SCHEMA_VALIDATION_FAILED`, `LLM_INVALID_RESPONSE`, `RESUME_CURATION_GROUNDING_FAILED` | AI output couldn't be trusted — `RESUME_CURATION_GROUNDING_FAILED` specifically means the AI tried to introduce a skill/employer/date/metric not present in the verified profile, and a retry with a stricter prompt still failed |
| 503 | `LLM_UNAVAILABLE`, `LLM_MODEL_UNAVAILABLE`, `LLM_TIMEOUT`, `CANDIDATE_DATA_UNAVAILABLE`, `JOB_REQUIREMENTS_UNAVAILABLE` | AI unreachable, or prerequisite data missing |
| 500 | `DATABASE_ERROR` | failed to persist the new version |

```json
// Example error response (502)
{ "detail": "Generated resume content could not be verified against candidate data.", "code": "RESUME_CURATION_GROUNDING_FAILED" }
```

---

### 5.8 List tailored resume version history (Step 11)

| | |
|---|---|
| **Method / URL** | `GET /api/resumes/job-curations/{curation_id}/tailored-resume/versions/` |
| **Auth** | Required |
| **Path params** | `curation_id` (integer, required) |

Every regeneration in §5.7 creates a new version rather than overwriting the last
one; this lists them, newest first, for a "previous versions" picker.

**Success `200 OK`:**

```json
[
  { "id": 7, "version": 2, "match_percentage": "80.00", "created_at": "2026-08-11T10:05:00Z" },
  { "id": 4, "version": 1, "match_percentage": "60.00", "created_at": "2026-08-11T10:00:05Z" }
]
```

**Errors:** `401`. (Returns an empty array — not a 404 — for a curation the user
doesn't own; curation ownership itself is enforced on the curation's own endpoints.)

---

### 5.9 Get one tailored resume version's full content

| | |
|---|---|
| **Method / URL** | `GET /api/resumes/job-curations/{curation_id}/tailored-resume/versions/{version_id}/` |
| **Auth** | Required |
| **Path params** | `curation_id` (integer, required), `version_id` (integer, required — from §5.7's `tailored_resume_version.id` or §5.8's list) |

**Success `200 OK`:**

```json
{
  "id": 7,
  "version": 2,
  "tailored_resume_data": { "...": "full tailored resume, same shape as §5.7" },
  "match_percentage": "80.00",
  "gap_analysis_snapshot": { "...": "the gap_analysis_data this version was generated from" },
  "created_at": "2026-08-11T10:05:00Z"
}
```

**Errors:** `401`, `404` (no such version for this curation, owned by this user).

---

## 6. Full workflow

```
STEP 1  POST /job-curations/analyze/                     Submit job title + JD
            │  { job_title, job_description, curate_resume: true }
            ▼
STEP 2  ← 201 { id, status: "completed", analysis_data }  Persist `id` as curation_id
            │
            ▼
STEP 3  GET /job-curations/{curation_id}/gap-analysis/    Retrieve candidate-job match
            │
            ▼
STEP 4  Render: match score, matching skills, partial     (all in gap_analysis_data /
        matches, missing requirements, recommendations     missing-information response)
            │
            ▼
STEP 5  User edits profile → PUT /api/resumes/edit/       Existing profile API, reused
            │  { parsed_data: {...full updated profile...} }
            ▼
STEP 6  POST /job-curations/{curation_id}/tailored-resume/  Regeneration endpoint
            │  { regenerate: true }
            ▼
STEP 7  ← 200 { tailored_resume_version, tailored_resume_data, gap_review }
        Updated tailored resume AND updated match/gap analysis, in one response
```

Steps 3-7 can repeat indefinitely as the user keeps refining their profile — there is
no limit on regenerations, and every regeneration is preserved in the version
history (§5.8/§5.9).

---

## 7. Frontend API integration table

| API | Method | Purpose | Frontend usage |
|---|---|---|---|
| `/api/resumes/job-curations/analyze/` | POST | Create curation session + AI-analyze the job description in one call | Call once when the user submits a job title + description. Persist the returned `id` as `curation_id` for every following call. |
| `/api/resumes/job-curations/` | GET | List all of the user's curation sessions | "My tailoring sessions" / history list screen. |
| `/api/resumes/job-curations/{curation_id}/` | GET | Re-fetch a session's stored job analysis without re-running AI | Redisplay a session, e.g. on navigating back to it. |
| `/api/resumes/job-curations/{curation_id}/gap-analysis/` | GET | Candidate-vs-job match / gap analysis | Drive the "match score / matching skills / gaps" screen. |
| `/api/resumes/job-curations/{curation_id}/missing-information/` | GET | Actionable, screen-ready missing-info review with safe wording and suggested profile sections | Drive the "here's what's missing, and where to fix it" UI; pass `?regenerate=true` after a profile edit to refresh without touching the tailored resume. |
| `/api/resumes/edit/` | PUT | Update the candidate's profile (existing, reused API) | Call after the user edits their profile in the "add missing info" flow. Send the full `parsed_data` object. |
| `/api/resumes/job-curations/{curation_id}/tailored-resume/` | POST | Generate, fetch-cached, or regenerate the tailored resume | `{regenerate: false}` to generate/fetch; `{regenerate: true}` immediately after a profile update. Response bundles the resume and a fresh gap review in one call. |
| `/api/resumes/job-curations/{curation_id}/tailored-resume/versions/` | GET | List all previously generated tailored-resume versions | "Previous versions" picker / audit trail. |
| `/api/resumes/job-curations/{curation_id}/tailored-resume/versions/{version_id}/` | GET | Fetch one historical version's full content | Viewing/restoring a specific past version. |

---

## 8. Example end-to-end frontend flow

```http
### 1. Submit the job
POST /api/resumes/job-curations/analyze/
Authorization: Bearer <token>
Content-Type: application/json

{
  "job_title": "Senior Java Backend Engineer",
  "job_description": "We need a Senior Java Backend Engineer with Spring Boot, Kafka, Docker, PostgreSQL, and AWS experience...",
  "curate_resume": true
}

→ 201
{ "id": 42, "status": "completed", "analysis_data": { "required_skills": ["Java"], "cloud_technologies": ["AWS"], ... } }

# Frontend stores curation_id = 42


### 2. Show the match
GET /api/resumes/job-curations/42/gap-analysis/
Authorization: Bearer <token>

→ 200
{
  "gap_analysis_data": {
    "overall_match_percentage": 66.67,
    "matched_requirements": [ { "requirement": "Java", "match_status": "MATCHED", ... } ],
    "missing_requirements": [ { "requirement": "AWS", "match_status": "MISSING", ... } ],
    ...
  }
}


### 3. Show the actionable review (safe wording, mapped to profile sections)
GET /api/resumes/job-curations/42/missing-information/
Authorization: Bearer <token>

→ 200
{
  "overall_match_percentage": 66.67,
  "missing_requirements": [
    {
      "requirement": "AWS", "profile_section": "skills",
      "message": "AWS is listed as a job requirement but is not present in your current profile.",
      "guidance": "If you genuinely have AWS experience that is not currently recorded, add it to your profile. Otherwise, consider AWS a current skill gap."
    }
  ],
  "suggested_profile_sections": ["skills"]
}

# User confirms they do have AWS experience and just never recorded it.


### 4. Update the profile (existing endpoint, reused)
PUT /api/resumes/edit/
Authorization: Bearer <token>
Content-Type: application/json

{ "parsed_data": { "skills": ["Java", "Spring Boot", "Kafka", "AWS"], ... } }

→ 200
{ "message": "Your profile has been updated. You can regenerate your resume to reflect the new information.", "ats_score": 90.0 }


### 5. Regenerate — re-analyzes match, recalculates gaps, and regenerates the resume
POST /api/resumes/job-curations/42/tailored-resume/
Authorization: Bearer <token>
Content-Type: application/json

{ "regenerate": true }

→ 200
{
  "tailored_resume_version": { "id": 108, "version": 2, "created_at": "2026-08-11T10:05:00Z" },
  "tailored_resume_data": { "skills": ["Java", "Spring Boot", "Kafka", "AWS", ...], ... },
  "gap_review": { "overall_match_percentage": 100, "missing_requirements": [], ... }
}

# AWS now shows as matched, and the tailored resume includes it — because the
# candidate genuinely added it to their verified profile first. The AI never
# added it on its own, and version 1 of the tailored resume still exists
# untouched at GET /api/resumes/job-curations/42/tailored-resume/versions/.
```

---

## 9. Verification

- `python manage.py check` — passes.
- `python manage.py spectacular --validate` — the generated OpenAPI 3 document
  validates cleanly (verified with `openapi-spec-validator`). The 7 pre-existing
  generation warnings/errors in this project all originate from unrelated
  `authentication`/`api` (health, readiness) views that predate this feature and
  lack a `serializer_class`; **zero** originate from any endpoint documented here.
- `GET /api/schema/` returns `200` with a valid OpenAPI document; `GET /api/docs/`
  renders the actual Swagger UI (`200`, contains the `swagger-ui` mount point) and
  lists all 9 endpoints above.
- Full backend test suite (`python manage.py test`) passes, covering every endpoint
  in this document including authorization, cross-user access, malformed/fabricated
  AI output, AI-unavailable, and regeneration-preserves-history scenarios.
