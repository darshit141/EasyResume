"""Shared, safe error-response mapping for AI-backed resume views.

Keeps internal exception details (and prompts) out of API responses while
mapping each failure mode to a stable HTTP status and machine-readable code.
"""

from rest_framework import status
from rest_framework.response import Response

from resumes.services.llm_service import (
    LLMInvalidResponseError,
    LLMModelUnavailableError,
    LLMSchemaValidationError,
    LLMTimeoutError,
    LLMQuotaExceededError,
    LLMHighDemandError,
)
from resumes.services.cover_letter_ai_service import CoverLetterGroundingError
from resumes.services.job_application_ai_service import ApplicationEmailGroundingError
from resumes.services.resume_curation_ai_service import ResumeCurationGroundingError


def llm_error_response(exc):
    response_status = status.HTTP_503_SERVICE_UNAVAILABLE
    if isinstance(
        exc,
        (
            LLMInvalidResponseError,
            LLMSchemaValidationError,
            ResumeCurationGroundingError,
            # A grounding failure means the model returned unusable content,
            # not that the provider is down — 502, like the resume grounder.
            CoverLetterGroundingError,
            ApplicationEmailGroundingError,
        ),
    ):
        response_status = status.HTTP_502_BAD_GATEWAY
    elif isinstance(exc, (LLMModelUnavailableError, LLMTimeoutError, LLMQuotaExceededError, LLMHighDemandError)):
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE

    return Response(
        {
            "detail": str(exc),
            "code": getattr(exc, "code", "LLM_UNAVAILABLE"),
        },
        status=response_status,
    )
