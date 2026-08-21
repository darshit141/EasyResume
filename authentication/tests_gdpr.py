from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import CookieConsent, PolicyAcceptance, User
from billing.models import AIUsageLog, Subscription
from blog.models import NewsletterSubscriber
from resumes.models import JobResumeCuration, Resume


VALID_REGISTRATION = {
    "name": "Jane Doe",
    "email": "jane-register@example.com",
    "password": "Str0ng!Pass99",
    "confirm_password": "Str0ng!Pass99",
    "accepted_terms": True,
}


class RegistrationPolicyAcceptanceTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_registration_without_accepting_terms_is_rejected(self):
        payload = {**VALID_REGISTRATION, "accepted_terms": False}
        response = self.client.post("/api/auth/register/", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("accepted_terms", response.data)
        self.assertEqual(User.objects.filter(email=VALID_REGISTRATION["email"]).count(), 0)

    def test_registration_missing_accepted_terms_is_rejected(self):
        payload = {k: v for k, v in VALID_REGISTRATION.items() if k != "accepted_terms"}
        response = self.client.post("/api/auth/register/", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("accepted_terms", response.data)

    def test_successful_registration_records_policy_acceptance(self):
        response = self.client.post("/api/auth/register/", VALID_REGISTRATION, format="json")

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email=VALID_REGISTRATION["email"])
        acceptances = PolicyAcceptance.objects.filter(user=user)
        self.assertEqual(
            set(acceptances.values_list("policy_type", flat=True)),
            {PolicyAcceptance.POLICY_TERMS, PolicyAcceptance.POLICY_PRIVACY},
        )

    def test_phone_is_optional(self):
        response = self.client.post("/api/auth/register/", VALID_REGISTRATION, format="json")
        self.assertEqual(response.status_code, 201)

    def test_two_users_may_both_omit_phone(self):
        # phone is a unique column — both must land as NULL, not colliding
        # empty strings.
        first = self.client.post("/api/auth/register/", VALID_REGISTRATION, format="json")
        second_payload = {**VALID_REGISTRATION, "email": "jane-register-2@example.com"}
        second = self.client.post("/api/auth/register/", second_payload, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

    def test_international_phone_number_is_accepted(self):
        payload = {**VALID_REGISTRATION, "phone": "+353871234567"}
        response = self.client.post("/api/auth/register/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get(email=VALID_REGISTRATION["email"]).phone, "+353871234567")

    def test_malformed_phone_number_is_rejected(self):
        payload = {**VALID_REGISTRATION, "phone": "not-a-phone"}
        response = self.client.post("/api/auth/register/", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.data)


class PolicyAcceptanceAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-policy@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_authentication(self):
        response = APIClient().get("/api/auth/policy-acceptance/")
        self.assertEqual(response.status_code, 401)

    def test_post_records_current_version(self):
        response = self.client.post(
            "/api/auth/policy-acceptance/", {"policy_type": "cookie_policy"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["policy_type"], "cookie_policy")
        self.assertTrue(response.data["version"])

    def test_re_accepting_creates_a_new_row_not_an_update(self):
        self.client.post("/api/auth/policy-acceptance/", {"policy_type": "terms_of_service"}, format="json")
        self.client.post("/api/auth/policy-acceptance/", {"policy_type": "terms_of_service"}, format="json")

        self.assertEqual(
            PolicyAcceptance.objects.filter(user=self.user, policy_type="terms_of_service").count(), 2
        )

    def test_get_lists_history_newest_first(self):
        first = PolicyAcceptance.objects.create(
            user=self.user, policy_type="privacy_policy", version="v1"
        )
        second = PolicyAcceptance.objects.create(
            user=self.user, policy_type="privacy_policy", version="v2"
        )

        response = self.client.get("/api/auth/policy-acceptance/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["version"], "v2")
        self.assertEqual(response.data[1]["version"], "v1")


class CookieConsentAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-cookie@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_authentication(self):
        response = APIClient().get("/api/auth/cookie-consent/")
        self.assertEqual(response.status_code, 401)

    def test_get_404s_when_never_set(self):
        response = self.client.get("/api/auth/cookie-consent/")
        self.assertEqual(response.status_code, 404)

    def test_put_creates_and_persists_preference(self):
        response = self.client.put(
            "/api/auth/cookie-consent/", {"analytics": True, "marketing": False}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["analytics"])
        self.assertFalse(response.data["marketing"])
        self.assertTrue(response.data["consent_version"])

        get_response = self.client.get("/api/auth/cookie-consent/")
        self.assertEqual(get_response.status_code, 200)
        self.assertTrue(get_response.data["analytics"])

    def test_put_again_updates_the_same_record_not_a_new_one(self):
        self.client.put("/api/auth/cookie-consent/", {"analytics": True, "marketing": True}, format="json")
        self.client.put("/api/auth/cookie-consent/", {"analytics": False, "marketing": False}, format="json")

        self.assertEqual(CookieConsent.objects.filter(user=self.user).count(), 1)
        self.assertFalse(CookieConsent.objects.get(user=self.user).analytics)

    def test_defaults_to_no_non_essential_tracking(self):
        response = self.client.put("/api/auth/cookie-consent/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["analytics"])
        self.assertFalse(response.data["marketing"])


class AccountExportAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-export@example.com", phone="1234567890",
            password="safe-password",
        )
        self.other_user = User.objects.create_user(
            name="John Doe", email="john-export@example.com", phone="0987654321",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_authentication(self):
        response = APIClient().get("/api/auth/account/export/")
        self.assertEqual(response.status_code, 401)

    def test_export_includes_profile_and_is_downloadable(self):
        response = self.client.get("/api/auth/account/export/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response.data["profile"]["email"], self.user.email)

    def test_export_includes_resumes_and_versions(self):
        Resume.objects.create(
            user=self.user,
            file=SimpleUploadedFile("resume.pdf", b"resume"),
            original_filename="resume.pdf",
            status="completed",
            parsed_data={"skills": ["Python"]},
        )

        response = self.client.get("/api/auth/account/export/")

        self.assertEqual(len(response.data["resumes"]), 1)
        self.assertEqual(response.data["resumes"][0]["parsed_data"], {"skills": ["Python"]})

    def test_export_includes_job_curations(self):
        JobResumeCuration.objects.create(
            user=self.user, job_title="Backend Engineer", job_description="A" * 40,
            status="completed", analysis_data={"required_skills": ["Python"]},
        )

        response = self.client.get("/api/auth/account/export/")

        self.assertEqual(len(response.data["job_curations"]), 1)
        self.assertEqual(response.data["job_curations"][0]["job_title"], "Backend Engineer")

    def test_export_never_includes_another_users_data(self):
        Resume.objects.create(
            user=self.other_user,
            file=SimpleUploadedFile("resume.pdf", b"resume"),
            original_filename="not-mine.pdf",
            status="completed",
        )

        response = self.client.get("/api/auth/account/export/")

        self.assertEqual(response.data["resumes"], [])

    def test_export_includes_ai_usage_summary_not_raw_log(self):
        AIUsageLog.objects.create(user=self.user, action=AIUsageLog.ACTION_JOB_ANALYSIS, provider="groq")
        AIUsageLog.objects.create(user=self.user, action=AIUsageLog.ACTION_JOB_ANALYSIS, provider="groq")

        response = self.client.get("/api/auth/account/export/")

        self.assertEqual(response.data["ai_usage_summary"]["total_calls"], 2)
        self.assertEqual(response.data["ai_usage_summary"]["by_action"]["job_analysis"], 2)


class AccountDeletionAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Jane Doe", email="jane-delete@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_authentication(self):
        response = APIClient().post("/api/auth/account/delete/", {"password": "safe-password"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_wrong_password_is_rejected_and_account_survives(self):
        response = self.client.post(
            "/api/auth/account/delete/", {"password": "wrong-password"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(id=self.user.id).exists())

    def test_correct_password_permanently_deletes_the_account(self):
        response = self.client.post(
            "/api/auth/account/delete/", {"password": "safe-password"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(id=self.user.id).exists())

    def test_deletion_cascades_resumes_and_removes_the_file(self):
        resume = Resume.objects.create(
            user=self.user,
            file=SimpleUploadedFile("resume.pdf", b"resume content"),
            original_filename="resume.pdf",
            status="completed",
        )
        file_name = resume.file.name

        with patch("authentication.services.account_deletion_service.default_storage") as mock_storage:
            response = self.client.post(
                "/api/auth/account/delete/", {"password": "safe-password"}, format="json"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Resume.objects.filter(id=resume.id).count(), 0)
        mock_storage.delete.assert_called_once_with(file_name)

    def test_deletion_cascades_job_curations_and_subscription(self):
        JobResumeCuration.objects.create(
            user=self.user, job_title="Engineer", job_description="A" * 40, status="completed",
        )
        Subscription.objects.create(user=self.user, plan_id="free", status="none")

        self.client.post("/api/auth/account/delete/", {"password": "safe-password"}, format="json")

        self.assertEqual(JobResumeCuration.objects.filter(user_id=self.user.id).count(), 0)
        self.assertEqual(Subscription.objects.filter(user_id=self.user.id).count(), 0)

    def test_deletion_removes_matching_newsletter_subscription(self):
        NewsletterSubscriber.objects.create(email=self.user.email)

        self.client.post("/api/auth/account/delete/", {"password": "safe-password"}, format="json")

        self.assertEqual(NewsletterSubscriber.objects.filter(email=self.user.email).count(), 0)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_deletion_cancels_an_active_stripe_subscription(self):
        Subscription.objects.create(
            user=self.user, plan_id="pro", status="active",
            stripe_customer_id="cus_123", stripe_subscription_id="sub_123",
        )

        with patch("billing.services.stripe_service.stripe.Subscription.cancel") as mock_cancel:
            mock_cancel.return_value = MagicMock()
            response = self.client.post(
                "/api/auth/account/delete/", {"password": "safe-password"}, format="json"
            )

        self.assertEqual(response.status_code, 200)
        mock_cancel.assert_called_once_with("sub_123")

    def test_deletion_succeeds_even_if_stripe_cancellation_fails(self):
        Subscription.objects.create(
            user=self.user, plan_id="pro", status="active",
            stripe_customer_id="cus_123", stripe_subscription_id="sub_123",
        )
        # STRIPE_SECRET_KEY unset in this test — cancellation will fail with
        # StripeNotConfiguredError, which must not block the deletion itself.
        response = self.client.post(
            "/api/auth/account/delete/", {"password": "safe-password"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(id=self.user.id).exists())

    def test_another_users_data_is_untouched(self):
        other_user = User.objects.create_user(
            name="John Doe", email="john-delete@example.com", phone="0987654321",
            password="safe-password",
        )
        Resume.objects.create(
            user=other_user,
            file=SimpleUploadedFile("resume.pdf", b"resume"),
            original_filename="resume.pdf",
            status="completed",
        )

        self.client.post("/api/auth/account/delete/", {"password": "safe-password"}, format="json")

        self.assertTrue(User.objects.filter(id=other_user.id).exists())
        self.assertEqual(Resume.objects.filter(user=other_user).count(), 1)
