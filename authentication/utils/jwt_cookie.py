from datetime import timedelta
from django.conf import settings

REFRESH_COOKIE_NAME = "refresh_token"


def set_refresh_token_cookie(response, refresh_token: str):
    """
    Sets the refresh token as an HttpOnly, SameSite=Lax, Secure cookie.
    
    This eliminates JavaScript exposure (mitigating XSS token exfiltration)
    while allowing the browser to automatically include the token on refresh calls.
    """
    if not refresh_token:
        return response

    lifetime = settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME", timedelta(days=7))
    max_age = int(lifetime.total_seconds())
    # In production (DEBUG=False), secure=True forces cookie transmission exclusively over HTTPS.
    # In local development (DEBUG=True), secure=False enables cookie persistence over plain HTTP.
    secure = not settings.DEBUG

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=max_age,
        httponly=True,
        samesite="Lax",
        secure=secure,
        path="/",
    )
    return response


def delete_refresh_token_cookie(response):
    """
    Clears the HttpOnly refresh token cookie on logout or account deletion.
    """
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path="/",
        samesite="Lax",
    )
    return response
