"""Request correlation ID — one id per HTTP request, present in every log
line that request produces and echoed back in the response header, so a
support ticket ("my request failed at 14:32") can be traced through the
logs instead of guessed at.
"""

import contextvars
import logging
import uuid

REQUEST_ID_HEADER = "X-Request-ID"

_current_request_id = contextvars.ContextVar("current_request_id", default="")


def get_current_request_id():
    return _current_request_id.get()


class RequestIDMiddleware:
    """Reads an inbound X-Request-ID (set by a load balancer/proxy in front
    of this app, if any) or generates a fresh one, makes it available to
    the logging filter below for the duration of the request, and echoes
    it back on the response so a client can quote it when reporting an
    issue."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex
        request.request_id = request_id
        token = _current_request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _current_request_id.reset(token)
        response[REQUEST_ID_HEADER] = request_id
        return response


class RequestIDLogFilter(logging.Filter):
    """Attaches the current request's id to every LogRecord as
    `request_id`, so it can be included in the log format string. Outside
    a request (a management command, a Celery task not tied to a request)
    this is simply an empty string."""

    def filter(self, record):
        record.request_id = get_current_request_id() or "-"
        return True
