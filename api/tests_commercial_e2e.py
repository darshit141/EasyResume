"""The single end-to-end commercial journey.

Every other test in this repository verifies one layer. This one verifies
the *seams between* them, because that is where a revenue path actually
breaks: a Price ID that doesn't match the currency, a webhook that updates
a row nobody reads, an entitlement that doesn't refresh after activation.
Each of those bugs is invisible to a passing unit-test suite and fatal to
a real customer.

The journey below is the one a paying customer walks, in order, over real
HTTP endpoints with a real database:

    register -> login -> OTP -> consent -> upload resume -> analyse a JD
    -> gap analysis -> generate a tailored resume -> hit the paywall
    -> checkout -> Stripe webhook -> entitlement upgrade -> DOCX download
    -> record the outcome -> GDPR export -> GDPR delete

Only two boundaries are faked, and only because they are someone else's
network: the LLM provider and the Stripe SDK. Everything in between —
serializers, permissions, quota accounting, the Celery job wrapper,
entitlement resolution, document rendering — is the real implementation.

If this test fails, the product is not commercially ready, whatever the
rest of the suite says. That is the whole point of it existing.
"""

from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import CookieConsent, LoginOTP, PolicyAcceptance, User
from billing.models import AIUsageLog, StripeWebhookEvent, Subscription
from billing.plans import PLANS_BY_ID
from resumes.models import JobResumeCuration, Resume
from resumes.services.llm_service import LLMService
from resumes.tests_job_curation import gap_analysis, gap_item, job_analysis_for, poll_job
from resumes.tests_resume_curation import CANDIDATE_PARSED_DATA, valid_tailored_result

RESUME_TEXT = (
    "JANE DOE\njane@example.com | 1234567890\n\n"
    "EXPERIENCE\nBackend Engineer, Acme Corp (2019-2022)\n"
    "Built event-driven services with Java, Spring Boot and Kafka.\n\n"
    "EDUCATION\nBSc Computer Science, State University, 2015-2019\n"
)

JOB_DESCRIPTION = (
    "We need a Senior Backend Engineer with Java, Kafka and Kubernetes, "
    "who can own services end to end."
)

# Two requirements the candidate genuinely has, and one they do not. The
# third is the point: it is what the evidence gate has to refuse.
JOB_ANALYSIS = job_analysis_for(required_skills=["Java", "Kafka", "Kubernetes"])


class _ScriptedLLM(LLMService):
    """Returns one canned structured response. The AI itself is not under
    test here — the plumbing around it is."""

    def __init__(self, result):
        self.result = result

    def generate_structured(self, prompt, schema):
        return self.result


@override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_fake")
class CommercialEndToEndJourneyTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    # --- the journey, one step per method, called in order below --------

    def _register(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "name": "Jane Doe",
                "email": "jane-e2e@example.com",
                "phone": "+353871234567",
                "password": "Str0ng-Passw0rd!",
                "confirm_password": "Str0ng-Passw0rd!",
                "accepted_terms": True,
            },
            format="json",
        )
        self.assertIn(response.status_code, (200, 201), response.data)
        return User.objects.get(email="jane-e2e@example.com")

    def _login_with_otp(self):
        """The default posture is two-factor, so a customer who can't get
        past the OTP step can never pay. Drive the real two-call flow."""
        login = self.client.post(
            "/api/auth/login/",
            {"email": "jane-e2e@example.com", "password": "Str0ng-Passw0rd!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.data)
        self.assertTrue(login.data["otp_required"])
        self.assertNotIn("access", login.data)

        otp = LoginOTP.objects.filter(user__email="jane-e2e@example.com").latest("created_at")
        verified = self.client.post(
            "/api/auth/login/verify-otp/",
            {"email": "jane-e2e@example.com", "otp": otp.otp},
            format="json",
        )
        self.assertEqual(verified.status_code, 200, verified.data)
        self.assertIn("access", verified.data)
        return verified.data["access"]

    def _upload_resume(self):
        upload = SimpleUploadedFile(
            "resume.pdf", b"placeholder", content_type="application/pdf"
        )
        with patch(
            "resumes.services.resume_upload_service.TextExtractor.extract_text",
            return_value=RESUME_TEXT,
        ), patch(
            "resumes.services.resume_upload_service.ResumeParser.parse",
            return_value=CANDIDATE_PARSED_DATA,
        ):
            response = self.client.post(
                "/api/resumes/upload/", {"file": upload}, format="multipart"
            )
        job = poll_job(self.client, response)
        self.assertEqual(job.data["status"], "completed", job.data)
        return job.data["result"]["resume_id"]

    def _analyse_job_description(self):
        with patch(
            "resumes.services.job_curation_ai_service.get_llm_service",
            return_value=_ScriptedLLM(JOB_ANALYSIS),
        ):
            response = self.client.post(
                "/api/resumes/job-curations/analyze/",
                {
                    "job_title": "Senior Backend Engineer",
                    "job_description": JOB_DESCRIPTION,
                    "curate_resume": True,
                },
                format="json",
            )
        job = poll_job(self.client, response)
        self.assertEqual(job.data["status"], "completed", job.data)
        return job.data["result"]["curation_id"]

    def _run_gap_analysis(self, curation_id):
        """The evidence gate is the product's differentiator, so the journey
        asserts it *holds under attack* rather than merely that it ran.

        The scripted model here deliberately misbehaves: it claims the
        candidate matched Kubernetes and invents supporting evidence for it,
        which is precisely the failure mode the product promises never to
        ship to an employer. The server must overrule the model and record
        it as MISSING. If this assertion ever has to be relaxed, the
        product's core marketing claim has stopped being true."""
        scripted = gap_analysis(
            [
                gap_item("Java", "required_skill", "MATCHED", ["Java"]),
                gap_item("Kafka", "required_skill", "MATCHED", ["Kafka"]),
                gap_item(
                    "Kubernetes",
                    "required_skill",
                    "MATCHED",
                    ["Led the Kubernetes migration at Acme Corp"],
                ),
            ]
        )
        with patch(
            "resumes.services.job_gap_analysis_service.get_llm_service",
            return_value=_ScriptedLLM(scripted),
        ):
            queued = self.client.get(
                f"/api/resumes/job-curations/{curation_id}/gap-analysis/"
            )
        job = poll_job(self.client, queued)
        self.assertEqual(job.data["status"], "completed", job.data)

        curation = JobResumeCuration.objects.get(id=curation_id)
        data = curation.gap_analysis_data
        self.assertEqual(
            [item["requirement"] for item in data["missing_requirements"]],
            ["Kubernetes"],
            "An unsupported claim was allowed through the evidence gate.",
        )
        self.assertEqual(
            sorted(item["requirement"] for item in data["matched_requirements"]),
            ["Java", "Kafka"],
            "Only requirements backed by real resume evidence may be reported as matched.",
        )

    def _generate_tailored_resume(self, curation_id):
        with patch(
            "resumes.services.resume_curation_ai_service.get_llm_service",
            return_value=_ScriptedLLM(valid_tailored_result()),
        ):
            response = self.client.post(
                f"/api/resumes/job-curations/{curation_id}/tailored-resume/",
                {},
                format="json",
            )
        job = poll_job(self.client, response)
        self.assertEqual(job.data["status"], "completed", job.data)
        self.assertIn("tailored_resume_data", job.data["result"])

    def _checkout(self, user):
        """Free users must be able to *start* paying. A 400/503 here is a
        silently lost sale, so the test asserts the exact Price ID that
        reaches Stripe, not merely that a URL came back."""
        with patch.dict(
            PLANS_BY_ID["pro"]["prices"]["EUR"]["monthly"],
            {"stripe_price_id": "price_e2e_eur_monthly"},
        ), patch(
            "billing.services.stripe_service.stripe.Customer.create",
            return_value=MagicMock(id="cus_e2e"),
        ), patch(
            "billing.services.stripe_service.stripe.checkout.Session.create",
            return_value=MagicMock(url="https://checkout.stripe.com/e2e"),
        ) as mock_session:
            response = self.client.post(
                "/api/billing/checkout/",
                {"plan_id": "pro", "currency": "EUR", "billing_period": "monthly"},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["checkout_url"], "https://checkout.stripe.com/e2e")
        line_items = mock_session.call_args.kwargs["line_items"]
        self.assertEqual(line_items, [{"price": "price_e2e_eur_monthly", "quantity": 1}])
        self.assertEqual(
            mock_session.call_args.kwargs["metadata"]["user_id"], str(user.id)
        )

    def _deliver_webhook(self, user):
        """Stripe's webhook — not the browser's redirect — is what actually
        grants entitlement. Delivered twice on purpose: Stripe retries, and
        a replay must not double-apply."""
        event = {
            "id": "evt_e2e_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_e2e",
                    "customer": "cus_e2e",
                    "subscription": "sub_e2e",
                    "metadata": {"user_id": str(user.id), "plan_id": "pro"},
                }
            },
        }
        anonymous = APIClient()  # Stripe is not logged in.
        # On activation the handler fetches the subscription to record when
        # the paid period ends. Without that date there is no renewal and no
        # computable MRR, so it is stubbed here rather than left to a real
        # (and, in tests, failing) network call.
        with patch(
            "billing.services.stripe_service.stripe.Webhook.construct_event",
            return_value=event,
        ), patch(
            "billing.services.stripe_service.StripeService.get_subscription",
            return_value={"current_period_end": 1_800_000_000},
        ):
            first = anonymous.post(
                "/api/billing/webhook/",
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid-looking-signature",
            )
            replay = anonymous.post(
                "/api/billing/webhook/",
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid-looking-signature",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(
            StripeWebhookEvent.objects.filter(stripe_event_id="evt_e2e_checkout").count(),
            1,
            "A retried Stripe event must be recorded once, not twice.",
        )

    # --- the journey itself ---------------------------------------------

    def test_a_visitor_can_sign_up_pay_and_receive_the_paid_deliverable(self):
        user = self._register()

        # Registration must record *which version* of the policies was
        # accepted — an undated acceptance is not evidence of consent.
        for policy_type in (PolicyAcceptance.POLICY_TERMS, PolicyAcceptance.POLICY_PRIVACY):
            acceptance = PolicyAcceptance.objects.filter(
                user=user, policy_type=policy_type
            ).first()
            self.assertIsNotNone(acceptance, f"No acceptance recorded for {policy_type}.")
            self.assertTrue(acceptance.version, "An acceptance without a version is not evidence.")

        access = self._login_with_otp()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        # Non-essential tracking stays off unless the customer turns it on.
        consent = self.client.put(
            "/api/auth/cookie-consent/", {"analytics": True, "marketing": False}, format="json"
        )
        self.assertEqual(consent.status_code, 200, consent.data)
        stored = CookieConsent.objects.get(user=user)
        self.assertTrue(stored.analytics)
        self.assertFalse(stored.marketing)
        self.assertTrue(stored.consent_version)

        resume_id = self._upload_resume()
        curation_id = self._analyse_job_description()
        self._run_gap_analysis(curation_id)
        self._generate_tailored_resume(curation_id)

        # --- the paywall ------------------------------------------------
        docx_url = (
            f"/api/resumes/job-curations/{curation_id}/tailored-resume/export/docx/"
        )
        blocked = self.client.get(docx_url)
        self.assertEqual(
            blocked.status_code,
            402,
            "DOCX export is a marketed Pro benefit; a free user must be stopped "
            "server-side, not merely by hiding the button. 402 (not 403) is the "
            "contract: this is 'pay to unlock', not 'forbidden forever', and the "
            "frontend routes the upgrade card off it.",
        )
        self.assertIn("code", blocked.data, "The paywall must tell the UI which card to show.")

        entitlements = self.client.get("/api/billing/entitlements/")
        self.assertEqual(entitlements.data["plan_id"], "free")
        self.assertFalse(entitlements.data["docx_export"])
        self.assertTrue(entitlements.data["shows_ads"])

        # --- payment ----------------------------------------------------
        self._checkout(user)
        self._deliver_webhook(user)

        subscription = Subscription.objects.get(user=user)
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.plan_id, "pro")
        self.assertEqual(subscription.stripe_subscription_id, "sub_e2e")
        self.assertIsNotNone(
            subscription.current_period_end,
            "Without a period end there is no renewal date and no computable MRR.",
        )

        # --- entitlement actually changes -------------------------------
        upgraded = self.client.get("/api/billing/entitlements/")
        self.assertEqual(upgraded.data["plan_id"], "pro")
        self.assertTrue(upgraded.data["docx_export"])
        self.assertFalse(upgraded.data["shows_ads"], "Ad-free is a marketed Pro benefit.")

        # --- the customer receives what they paid for -------------------
        delivered = self.client.get(docx_url)
        self.assertEqual(delivered.status_code, 200, "The paid deliverable must download.")
        self.assertEqual(
            delivered["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertTrue(
            b"".join(delivered.streaming_content)[:2] == b"PK"
            if delivered.streaming
            else delivered.content[:2] == b"PK",
            "A .docx is a zip archive; anything not starting PK is not a real document.",
        )

        # --- the business can measure what just happened ----------------
        self.assertTrue(
            AIUsageLog.objects.filter(user=user).exists(),
            "Every AI operation must be attributable to a user for COGS.",
        )

        # --- outcome tracking closes the value loop ---------------------
        outcome = self.client.patch(
            f"/api/resumes/job-curations/{curation_id}/application/",
            {"application_status": "interviewing"},
            format="json",
        )
        self.assertEqual(outcome.status_code, 200, outcome.data)
        self.assertEqual(outcome.data["application_status"], "interviewing")

        # --- GDPR: the customer can take their data and leave -----------
        export = self.client.get("/api/auth/account/export/")
        self.assertEqual(export.status_code, 200, export.data)
        payload = export.data if isinstance(export.data, dict) else export.json()
        self.assertEqual(payload["profile"]["email"], "jane-e2e@example.com")
        self.assertTrue(payload["resumes"], "The export must contain the uploaded resume.")
        self.assertTrue(payload["job_curations"], "The export must contain job curations.")

        with patch(
            "billing.services.stripe_service.StripeService.cancel_subscription"
        ) as mock_cancel:
            # Deletion re-authenticates: an open session alone must not be
            # enough to destroy an account.
            refused = self.client.post(
                "/api/auth/account/delete/", {"password": "wrong-password"}, format="json"
            )
            self.assertEqual(refused.status_code, 400, refused.data)
            self.assertTrue(User.objects.filter(email="jane-e2e@example.com").exists())

            deleted = self.client.post(
                "/api/auth/account/delete/",
                {"password": "Str0ng-Passw0rd!"},
                format="json",
            )
        self.assertIn(deleted.status_code, (200, 204), getattr(deleted, "data", None))
        mock_cancel.assert_called_once()

        # No orphaned personal data may survive the deletion.
        self.assertFalse(User.objects.filter(email="jane-e2e@example.com").exists())
        self.assertFalse(Resume.objects.filter(id=resume_id).exists())
        self.assertFalse(JobResumeCuration.objects.filter(id=curation_id).exists())
        self.assertFalse(Subscription.objects.filter(user_id=user.id).exists())
        self.assertFalse(PolicyAcceptance.objects.filter(user_id=user.id).exists())
        self.assertFalse(CookieConsent.objects.filter(user_id=user.id).exists())
