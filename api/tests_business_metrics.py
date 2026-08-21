from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from api.models import AnalyticsEvent
from authentication.models import User
from billing.models import AIUsageLog, Subscription
from billing.plans import get_price
from resumes.models import JobResumeCuration, Resume


class AdminBusinessMetricsAPITests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            name="Admin", email="admin-metrics@example.com", phone="1234567890",
            password="safe-password", is_staff=True,
        )
        self.client = APIClient()
        self.url = "/api/admin/business-metrics/"

    def test_requires_authentication(self):
        response = APIClient().get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_requires_staff(self):
        regular_user = User.objects.create_user(
            name="Jane Doe", email="jane-metrics@example.com", phone="0987654321",
            password="safe-password",
        )
        self.client.force_authenticate(user=regular_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_acquisition_counts_signups(self):
        User.objects.create_user(name="A", email="a@example.com", phone="1111111111", password="x")
        User.objects.create_user(name="B", email="b@example.com", phone="2222222222", password="x")
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        # staff_user + the two created here
        self.assertEqual(response.data["acquisition"]["total_signups"], 3)

    def test_activation_counts_users_with_a_completed_resume(self):
        user = User.objects.create_user(name="A", email="a-act@example.com", phone="1111111111", password="x")
        Resume.objects.create(
            user=user, file=SimpleUploadedFile("r.pdf", b"x"), original_filename="r.pdf", status="completed",
        )
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.data["activation"]["activated_users"], 1)

    def test_revenue_reports_mrr_per_currency_for_paying_subscribers(self):
        pro_price = get_price("pro", "EUR", "monthly")
        user = User.objects.create_user(name="A", email="a-rev@example.com", phone="1111111111", password="x")
        Subscription.objects.create(
            user=user, plan_id="pro", status="active", currency="EUR", billing_period="monthly",
        )
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.data["revenue"]["paying_subscriber_count"], 1)
        self.assertEqual(
            response.data["revenue"]["mrr_by_currency"]["EUR"], round(pro_price["amount_cents"] / 100, 2)
        )

    def test_annual_subscriber_contributes_one_twelfth_to_mrr(self):
        annual_price = get_price("pro", "EUR", "annual")
        user = User.objects.create_user(name="A", email="a-annual@example.com", phone="1111111111", password="x")
        Subscription.objects.create(
            user=user, plan_id="pro", status="active", currency="EUR", billing_period="annual",
        )
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)

        expected = round((annual_price["amount_cents"] / 12) / 100, 2)
        self.assertEqual(response.data["revenue"]["mrr_by_currency"]["EUR"], expected)

    def test_comp_subscription_with_no_currency_excluded_from_mrr(self):
        user = User.objects.create_user(name="A", email="a-comp@example.com", phone="1111111111", password="x")
        Subscription.objects.create(user=user, plan_id="pro", status="active")  # coupon grant, no currency

        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self.url)

        self.assertEqual(response.data["revenue"]["mrr_by_currency"], {})
        self.assertEqual(response.data["revenue"]["comp_subscriber_count"], 1)

    def test_ai_cost_is_null_when_nothing_has_been_costed(self):
        user = User.objects.create_user(name="A", email="a-aicost@example.com", phone="1111111111", password="x")
        AIUsageLog.objects.create(user=user, action=AIUsageLog.ACTION_JOB_ANALYSIS, provider="groq")
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.data["ai_cost"]["total_ai_calls"], 1)
        self.assertIsNone(response.data["ai_cost"]["total_ai_cost_usd"])

    def test_ai_cost_is_populated_once_tracked(self):
        user = User.objects.create_user(name="A", email="a-aicost2@example.com", phone="1111111111", password="x")
        AIUsageLog.objects.create(
            user=user, action=AIUsageLog.ACTION_JOB_ANALYSIS, provider="groq", cost_estimate_usd="0.0042"
        )
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)

        self.assertAlmostEqual(response.data["ai_cost"]["total_ai_cost_usd"], 0.0042)

    def test_funnel_reflects_real_product_usage(self):
        user = User.objects.create_user(name="A", email="a-funnel@example.com", phone="1111111111", password="x")
        Resume.objects.create(
            user=user, file=SimpleUploadedFile("r.pdf", b"x"), original_filename="r.pdf", status="completed",
        )
        JobResumeCuration.objects.create(
            user=user, job_title="Engineer", job_description="d" * 40, status="completed",
            tailored_resume_data={"summary": "x"},
        )
        AnalyticsEvent.objects.create(event_type=AnalyticsEvent.EVENT_LANDING_PAGE_VIEW)
        AnalyticsEvent.objects.create(event_type=AnalyticsEvent.EVENT_CHECKOUT_STARTED)
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)
        funnel = response.data["funnel"]

        self.assertEqual(funnel["landing_page_views"], 1)
        self.assertEqual(funnel["resume_uploads"], 1)
        self.assertEqual(funnel["job_analyses"], 1)
        self.assertEqual(funnel["tailored_resumes_generated"], 1)
        self.assertEqual(funnel["checkout_started"], 1)

    def test_customer_outcomes_reports_status_distribution_and_rates(self):
        user = User.objects.create_user(name="A", email="a-outcome@example.com", phone="1111111111", password="x")
        JobResumeCuration.objects.create(
            user=user, job_title="A", job_description="d" * 40, application_status="applied",
        )
        JobResumeCuration.objects.create(
            user=user, job_title="B", job_description="d" * 40, application_status="interviewing",
        )
        JobResumeCuration.objects.create(
            user=user, job_title="C", job_description="d" * 40, application_status="offer",
        )
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)
        outcomes = response.data["customer_outcomes"]

        self.assertEqual(outcomes["by_status"]["applied"], 1)
        self.assertEqual(outcomes["by_status"]["interviewing"], 1)
        self.assertEqual(outcomes["by_status"]["offer"], 1)
        # 3 applied-or-later, 2 reached interview-or-later
        self.assertAlmostEqual(outcomes["interview_rate"], 2 / 3, places=4)
        self.assertAlmostEqual(outcomes["offer_rate"], 1 / 3, places=4)

    def test_customer_outcomes_rates_are_null_with_no_applications(self):
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.get(self.url)

        self.assertIsNone(response.data["customer_outcomes"]["interview_rate"])
        self.assertIsNone(response.data["customer_outcomes"]["offer_rate"])
