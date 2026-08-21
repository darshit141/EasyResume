"""Liveness only: is the process up and able to reach its database at all.

Deliberately minimal and fast — this is what the container orchestrator's
healthcheck/restart policy uses, so it must never do anything slow (no LLM
call, no Redis ping — that's /api/ready/'s job). A healthy *process* with a
dead database is still not a service that can do anything useful, so this
was previously wrong to report unconditional 200 (COMMERCIAL_READINESS_
AUDIT.md D8) — it now actually touches the database.
"""

import logging

from django.db import DatabaseError, connections
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET"])
def health_check(request):
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("Health check: database is unreachable.")
        return Response(
            {"status": "DOWN", "database": "UNREACHABLE"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"status": "OK", "database": "OK"})
