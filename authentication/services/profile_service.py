class ProfileService:
    """
    Service for updating the authenticated user's profile.
    """

    @staticmethod
    def update_profile(user, validated_data):
        for field, value in validated_data.items():
            setattr(user, field, value)

        user.save(update_fields=list(validated_data.keys()))

        return {
            "success": True,
            "message": "Profile updated successfully.",
        }
