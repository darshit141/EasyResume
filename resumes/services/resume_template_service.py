"""Reads and writes a user's resume-layout template preference, and resolves
the template that applies to a given tailored-resume version.

The preference is two columns on the user (``resume_template_id`` and
``resume_template_overrides``); this service is the only place that should
touch them, so the Pro-gate on custom overrides is enforced in exactly one
spot.
"""

from billing.services.entitlements_service import (
    EntitlementsService,
    FeatureRequiresProError,
)
from resumes.resume_template import (
    DEFAULT_TEMPLATE,
    DEFAULT_TEMPLATE_ID,
    TEMPLATE_PRESETS,
    resolve_template,
    template_ats_report,
)

# The override keys a user is allowed to set. `section_order` /
# `hidden_sections` are lists; the rest are scalars clamped by
# resolve_template.
OVERRIDE_KEYS = {
    "heading_style",
    "accent_color",
    "font_family",
    "name_alignment",
    "density",
    "columns",
    "section_order",
    "hidden_sections",
}


class ResumeTemplateService:
    @staticmethod
    def get_preference(user):
        """The user's saved choice plus its fully-resolved config and ATS
        report. Shape matches ResumeTemplatePreferenceSerializer."""
        template_id = user.resume_template_id or DEFAULT_TEMPLATE_ID
        overrides = user.resume_template_overrides or {}
        config = resolve_template(template_id, overrides)
        return {
            "template_id": config["template_id"],
            "overrides": {k: v for k, v in overrides.items() if k in OVERRIDE_KEYS},
            "config": config,
            "ats": template_ats_report(config),
        }

    @staticmethod
    def set_preference(user, template_id, overrides):
        """Persist a new choice. ``template_id`` must be a known preset.
        ``overrides`` may only be non-empty for a plan with the
        ``resume_template_customization`` entitlement — a Free user picks a
        preset and nothing more.

        Raises FeatureRequiresProError when a Free user sends overrides, and
        ValueError for an unknown preset id.
        """
        if template_id not in TEMPLATE_PRESETS:
            raise ValueError(
                f"Unknown resume template '{template_id}'. "
                f"Choose one of: {', '.join(sorted(TEMPLATE_PRESETS))}."
            )

        cleaned = {
            key: value
            for key, value in (overrides or {}).items()
            if key in OVERRIDE_KEYS and value is not None
        }

        if cleaned:
            # check_feature raises FeatureRequiresProError if not entitled.
            EntitlementsService.check_feature(user, "resume_template_customization")

        user.resume_template_id = template_id
        user.resume_template_overrides = cleaned
        user.save(update_fields=["resume_template_id", "resume_template_overrides"])
        return ResumeTemplateService.get_preference(user)

    @staticmethod
    def config_for_user(user):
        """Just the resolved config dict — for renderers and for snapshotting
        onto a new version at generation time."""
        return resolve_template(
            user.resume_template_id or DEFAULT_TEMPLATE_ID,
            user.resume_template_overrides or {},
        )

    @staticmethod
    def config_for_version(version, user=None):
        """The template a specific tailored-resume version should render
        with: its own snapshot if it has one, otherwise the user's current
        preference (covers versions generated before templates existed),
        otherwise the default."""
        if version is not None and getattr(version, "template_config", None):
            # Re-resolve so an old snapshot written under a since-changed
            # schema is still clamped to something a renderer can handle.
            snap = version.template_config
            return resolve_template(snap.get("template_id"), snap)
        if user is not None:
            return ResumeTemplateService.config_for_user(user)
        return dict(DEFAULT_TEMPLATE)
