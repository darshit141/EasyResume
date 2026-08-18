class ChangePasswordService:
    """
    Service for changing the authenticated user's password.
    """

    @staticmethod
    def change_password(user, current_password, new_password):
        if not user.check_password(current_password):
            return {
                "success": False,
                "message": "Current password is incorrect.",
            }

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return {
            "success": True,
            "message": "Password changed successfully.",
        }
