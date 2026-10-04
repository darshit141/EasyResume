from authentication.services import auth_security


class ChangePasswordService:
    """
    Service for changing the authenticated user's password.
    """

    @staticmethod
    def change_password(user, current_password, new_password):
        # A signed-in session is not proof of the password: someone with a
        # stolen token could use this endpoint to guess it. So wrong entries
        # count against the same lockout as the sign-in form.
        locked_for = auth_security.lockout_seconds_remaining(user)
        if locked_for:
            return {
                "success": False,
                "locked": True,
                **auth_security.locked_out_payload(locked_for),
            }

        if not user.check_password(current_password):
            locked_for = auth_security.record_failed_login(user)
            if locked_for:
                return {
                    "success": False,
                    "locked": True,
                    **auth_security.locked_out_payload(locked_for),
                }
            return {
                "success": False,
                "message": "Current password is incorrect.",
            }

        user.set_password(new_password)
        user.save(update_fields=["password"])

        # Every other device is signed out: if the old password was
        # compromised, changing it must also end the intruder's session.
        # The caller gets fresh tokens so this device stays signed in.
        auth_security.revoke_all_sessions(user)
        tokens = auth_security.issue_tokens(user)

        return {
            "success": True,
            "message": "Password changed successfully.",
            "tokens": tokens,
        }
