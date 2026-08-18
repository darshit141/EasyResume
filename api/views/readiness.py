"""Operational readiness without changing the existing liveness endpoint."""

import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from resumes.services.llm_service import LLMServiceError, get_llm_service

logger = logging.getLogger(__name__)


@api_view(["GET"])
def readiness_check(request):
    """Report whether the server's default-configured AI provider can currently serve AI work."""
    try:
        get_llm_service().check_model_available()
    except LLMServiceError as exc:
        logger.warning("LLM readiness check failed: %s", exc.code)
        return Response(
            {"status": "NOT_READY", "llm": exc.code},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"status": "READY", "llm": "AVAILABLE"})
