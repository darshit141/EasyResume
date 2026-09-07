from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.google_auth_serializer import GoogleSignInSerializer
from authentication.services.google_auth_service import (
    GoogleAuthError,
    GoogleAuthService,
)


@extend_schema(
    tags=["Authentication"],
    summary="Sign in with Google",
    description=(
        "Exchanges a Google ID token for an IntelliResume session.\n\n"
        "Returns exactly the same shape as a completed password login "
        "(`access`, `refresh`, `user`), so the client has one "
        "\"you are signed in\" contract regardless of how the user got "
        "there.\n\n"
        "No OTP step: Google has already authenticated the user, including "
        "whatever second factor is on their Google account. `created` says "
        "whether this call registered a new account or signed in an "
        "existing one.\n\n"
        "If an account already exists with the same **verified** Google "
        "email, the Google identity is linked to it rather than a duplicate "
        "being created, and any existing password keeps working."
    ),
    request=GoogleSignInSerializer,
    responses={
        status.HTTP_200_OK: OpenApiResponse(
            description="Signed in. Body carries access, refresh and user."
        ),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="No credential supplied."),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(
            description=(
                "The token failed verification, or the Google account's email "
                "is unverified. code: GOOGLE_TOKEN_INVALID | "
                "GOOGLE_EMAIL_MISSING | GOOGLE_EMAIL_UNVERIFIED."
            )
        ),
        status.HTTP_403_FORBIDDEN: OpenApiResponse(description="code: ACCOUNT_INACTIVE."),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
            description="Google sign-in isn't configured. code: GOOGLE_SIGN_IN_UNAVAILABLE."
        ),
    },
)
class GoogleSignInAPIView(APIView):
    # Same throttle bucket as password login: this is an unauthenticated
    # endpoint that resolves to a session, so it deserves the same
    # brute-force ceiling even though the credential is verified by Google.
    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = GoogleSignInSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = GoogleAuthService.sign_in(serializer.validated_data["credential"])
        except GoogleAuthError as exc:
            return Response(
                {"success": False, "message": exc.message, "code": exc.code},
                status=exc.status,
            )

        user = result["user"]
        return Response(
            {
                "success": True,
                "otp_required": False,
                "created": result["created"],
                "message": "Login successful.",
                "email": user.email,
                "access": result["access"],
                "refresh": result["refresh"],
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "phone": user.phone,
                    "is_staff": user.is_staff,
                },
            },
            status=status.HTTP_200_OK,
        )
