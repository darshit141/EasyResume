"""Expired OTPs are transient auth artifacts, not data anyone has a reason
to keep once they've lapsed — deleting them is pure data-minimization
housekeeping with zero product risk (COMMERCIAL_READINESS_AUDIT.md G9).
Kept separate from the actual GDPR-erasure path (account_deletion_service)
since this runs on a schedule for every user, not on request for one.
"""

from django.utils import timezone

from authentication.models import LoginOTP, PasswordResetOTP


def delete_expired_otps():
    """Deletes every LoginOTP/PasswordResetOTP row past its own expires_at.
    Returns the counts deleted, for logging/observability."""
    now = timezone.now()
    login_deleted, _ = LoginOTP.objects.filter(expires_at__lt=now).delete()
    reset_deleted, _ = PasswordResetOTP.objects.filter(expires_at__lt=now).delete()
    return {"login_otps_deleted": login_deleted, "password_reset_otps_deleted": reset_deleted}
