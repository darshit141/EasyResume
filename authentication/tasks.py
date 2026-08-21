import logging

from celery import shared_task

from authentication.services.otp_cleanup_service import delete_expired_otps

logger = logging.getLogger(__name__)


@shared_task
def cleanup_expired_otps_task():
    result = delete_expired_otps()
    logger.info(
        "Expired OTP cleanup: %s login OTP(s), %s password-reset OTP(s) deleted.",
        result["login_otps_deleted"],
        result["password_reset_otps_deleted"],
    )
    return result
