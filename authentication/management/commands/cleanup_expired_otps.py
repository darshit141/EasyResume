"""Deletes expired login/password-reset OTPs — data-minimization housekeeping
(COMMERCIAL_READINESS_AUDIT.md G9). Safe to run as often as you like; also
wired up as a daily Celery Beat task (see config/settings.py
CELERY_BEAT_SCHEDULE) so this doesn't depend on anyone remembering to run it.

    python manage.py cleanup_expired_otps
"""

from django.core.management.base import BaseCommand

from authentication.services.otp_cleanup_service import delete_expired_otps


class Command(BaseCommand):
    help = "Delete expired LoginOTP and PasswordResetOTP rows."

    def handle(self, *args, **options):
        result = delete_expired_otps()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {result['login_otps_deleted']} expired login OTP(s) and "
                f"{result['password_reset_otps_deleted']} expired password-reset OTP(s)."
            )
        )
