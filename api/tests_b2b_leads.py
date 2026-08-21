from django.test import TestCase
from rest_framework.test import APIClient

from api.models import B2BLead


class B2BLeadAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/leads/b2b/"

    def test_anonymous_visitor_can_submit_a_lead(self):
        response = self.client.post(
            self.url,
            {
                "name": "Jamie Lead",
                "email": "jamie@coachingco.example",
                "company_name": "Coaching Co",
                "team_size": "2-10",
                "message": "Interested in a bulk plan for our clients.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        lead = B2BLead.objects.get()
        self.assertEqual(lead.name, "Jamie Lead")
        self.assertEqual(lead.email, "jamie@coachingco.example")
        self.assertEqual(lead.company_name, "Coaching Co")
        self.assertEqual(lead.team_size, "2-10")
        self.assertFalse(lead.contacted)

    def test_company_name_team_size_and_message_are_optional(self):
        response = self.client.post(
            self.url, {"name": "Solo Coach", "email": "solo@example.com"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        lead = B2BLead.objects.get()
        self.assertEqual(lead.company_name, "")
        self.assertEqual(lead.team_size, "")
        self.assertEqual(lead.message, "")

    def test_missing_name_is_rejected(self):
        response = self.client.post(self.url, {"email": "no-name@example.com"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(B2BLead.objects.count(), 0)

    def test_missing_email_is_rejected(self):
        response = self.client.post(self.url, {"name": "No Email"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(B2BLead.objects.count(), 0)

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            self.url, {"name": "Bad Email", "email": "not-an-email"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(B2BLead.objects.count(), 0)

    def test_invalid_team_size_is_rejected(self):
        response = self.client.post(
            self.url,
            {"name": "Bad Team Size", "email": "bad-team@example.com", "team_size": "9999"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(B2BLead.objects.count(), 0)
