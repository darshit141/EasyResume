from django.test import TestCase
from rest_framework.test import APIClient

from api.models import AnalyticsEvent
from authentication.models import User


class TrackEventAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/analytics/track/"

    def test_anonymous_visitor_can_track_an_event(self):
        response = self.client.post(
            self.url, {"event_type": "landing_page_view", "session_id": "sess-123"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        event = AnalyticsEvent.objects.get()
        self.assertEqual(event.event_type, "landing_page_view")
        self.assertEqual(event.session_id, "sess-123")
        self.assertIsNone(event.user)

    def test_authenticated_user_is_recorded_on_the_event(self):
        user = User.objects.create_user(
            name="Jane Doe", email="jane-analytics@example.com", phone="1234567890",
            password="safe-password",
        )
        self.client.force_authenticate(user=user)

        response = self.client.post(self.url, {"event_type": "checkout_started"}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(AnalyticsEvent.objects.get().user, user)

    def test_invalid_event_type_is_rejected(self):
        response = self.client.post(self.url, {"event_type": "made_up_event"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(AnalyticsEvent.objects.count(), 0)

    def test_only_utm_metadata_keys_are_kept(self):
        response = self.client.post(
            self.url,
            {
                "event_type": "landing_page_view",
                "metadata": {
                    "utm_source": "google",
                    "utm_campaign": "launch",
                    "password": "should-not-be-stored",
                    "__proto__": "ignored",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        stored = AnalyticsEvent.objects.get().metadata
        self.assertEqual(stored, {"utm_source": "google", "utm_campaign": "launch"})
        self.assertNotIn("password", stored)

    def test_missing_event_type_is_rejected(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 400)
