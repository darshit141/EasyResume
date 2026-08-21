from django.conf import settings

from authentication.models import PolicyAcceptance, User


class RegisterService:
    """
    Service for user registration.
    """

    @staticmethod
    def register_user(validated_data):
        """
        Create and return a new user.
        """

        validated_data.pop("confirm_password")
        # Already validated true by RegisterSerializer.validate_accepted_terms
        # before this ever runs — recorded as the moment of consent.
        validated_data.pop("accepted_terms", None)

        user = User.objects.create_user(
            email=validated_data["email"],
            name=validated_data["name"],
            # NULL, not "" — phone is a unique column, and two users both
            # leaving it blank must not collide on an empty-string value.
            phone=validated_data["phone"] or None,
            password=validated_data["password"],
        )

        PolicyAcceptance.objects.bulk_create([
            PolicyAcceptance(
                user=user,
                policy_type=PolicyAcceptance.POLICY_TERMS,
                version=settings.CURRENT_TERMS_OF_SERVICE_VERSION,
            ),
            PolicyAcceptance(
                user=user,
                policy_type=PolicyAcceptance.POLICY_PRIVACY,
                version=settings.CURRENT_PRIVACY_POLICY_VERSION,
            ),
        ])

        return user
