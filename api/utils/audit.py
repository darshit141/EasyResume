"""Writes AdminAuditLog rows for admin-panel actions.

The only thing that should ever create an AdminAuditLog row is
`record_admin_action` below - keeping every write behind one function is
what makes it possible to audit the audit trail itself (one place to check
that every admin-panel view that mutates state actually calls it).
"""

import logging

from api.throttles import client_ip  # noqa: F401  (re-exported for callers)

logger = logging.getLogger(__name__)


def mask_secret_for_audit(value):
    """The same last-four-characters convention billing.UserAIKey already
    shows admins for a stored provider key - enough to recognise which key
    changed, never enough to reconstruct it."""
    value = (value or "").strip()
    if not value:
        return ""
    return f"...{value[-4:]}" if len(value) > 4 else "...."


def record_admin_action(request, action, target, detail=None):
    """Write one AdminAuditLog row. Never raises: a logging failure must
    never block or roll back the admin action it is describing - it is
    reported and swallowed instead, the same defensive posture
    UserAIKeyService.mark_used already uses for a non-critical side effect.
    """
    from api.models import AdminAuditLog

    try:
        AdminAuditLog.objects.create(
            actor=getattr(request, "user", None) if getattr(request.user, "is_authenticated", False) else None,
            actor_email=getattr(request.user, "email", "") or "",
            action=action,
            target=target,
            detail=detail or {},
            ip_address=client_ip(request),
        )
    except Exception:  # pragma: no cover - defensive
        logger.error("Failed to record admin audit log: action=%s target=%s", action, target, exc_info=True)
