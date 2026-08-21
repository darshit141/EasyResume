"""Operational readiness: can this instance actually do the work it exists
to do — reach the database, reach Redis (the Celery broker AI jobs are
queued on), and reach the configured AI provider.

Unlike /api/health/ (liveness, fast, no external calls beyond the
database), this is allowed to be slower and more thorough — it's what a
load balancer or deploy pipeline checks before routing traffic to a new
instance, not what a process supervisor polls every few seconds.
"""

import logging

from django.conf import settings
from django.db import DatabaseError, connections
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from resumes.services.llm_service import LLMServiceError, get_llm_service

logger = logging.getLogger(__name__)


def _check_database():
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except DatabaseError:
        logger.warning("Readiness check: database is unreachable.")
        return False


def _check_redis():
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        return bool(client.ping())
    except Exception:
        logger.warning("Readiness check: Redis is unreachable.")
        return False


@api_view(["GET"])
def readiness_check(request):
    database_ok = _check_database()
    redis_ok = _check_redis()

    try:
        get_llm_service().check_model_available()
        llm_status = "AVAILABLE"
    except LLMServiceError as exc:
        llm_status = exc.code

    if not database_ok or not redis_ok or llm_status != "AVAILABLE":
        return Response(
            {
                "status": "NOT_READY",
                "database": "OK" if database_ok else "UNREACHABLE",
                "redis": "OK" if redis_ok else "UNREACHABLE",
                "llm": llm_status,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"status": "READY", "database": "OK", "redis": "OK", "llm": "AVAILABLE"})
