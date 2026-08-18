from rest_framework import status
from rest_framework.response import Response


def plan_restriction_response(exc):
    """Turn a plan restriction into an HTTP response the frontend can act on.

    402 Payment Required is used deliberately rather than 403: 403 means "you
    may never do this", while this is "you may do this once you pay", which is
    a different thing for both the user and the UI. The frontend keys off the
    `code` field to decide between showing a quota card and a feature-upgrade
    card, so both are always present.
    """
    return Response(exc.to_payload(), status=status.HTTP_402_PAYMENT_REQUIRED)
