from authentication.models import User


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

        user = User.objects.create_user(
            email=validated_data["email"],
            name=validated_data["name"],
            phone=validated_data["phone"],
            password=validated_data["password"],
        )

        return user