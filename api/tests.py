from unittest.mock import patch

from django.test import TestCase

from resumes.services.llm_service import LLMModelUnavailableError


class ReadinessCheckTests(TestCase):
    @patch("api.views.readiness.get_llm_service")
    def test_ready_when_the_configured_model_is_available(self, get_llm_service):
        response = self.client.get("/api/ready/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "READY", "llm": "AVAILABLE"})
        get_llm_service.return_value.check_model_available.assert_called_once()

    @patch("api.views.readiness.get_llm_service")
    def test_not_ready_when_the_configured_model_is_missing(self, get_llm_service):
        get_llm_service.return_value.check_model_available.side_effect = LLMModelUnavailableError()

        response = self.client.get("/api/ready/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "NOT_READY", "llm": "LLM_MODEL_UNAVAILABLE"},
        )
