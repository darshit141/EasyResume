"""Seed Dahl Global configuration and Gemini fallback cascade settings into SystemConfiguration.

Ensures DAHL_MODEL, DAHL_FALLBACK_MODELS, DAHL_BASE_URL, DAHL_API_KEY,
and enriched GEMINI_FALLBACK_MODELS are present in SystemConfiguration so
they can be managed dynamically from the admin Configuration tab without redeploying.
"""

from django.conf import settings
from django.db import migrations

from api.utils.secrets import encrypt_value, is_secret_key

SEEDED_SETTINGS = (
    ("DAHL_MODEL", "string"),
    ("DAHL_FALLBACK_MODELS", "list"),
    ("DAHL_BASE_URL", "string"),
    ("DAHL_API_KEY", "string"),
    ("GEMINI_FALLBACK_MODELS", "list"),
    ("GEMINI_MAX_MODEL_ATTEMPTS", "integer"),
    ("GEMINI_AUTO_FALLBACK_FAMILIES", "list"),
)


def seed_from_settings(SystemConfiguration):
    for key, value_type in SEEDED_SETTINGS:
        raw = getattr(settings, key, "")
        value = ",".join(raw) if isinstance(raw, (list, tuple)) else str(raw or "").strip()
        if not value:
            continue
        existing = SystemConfiguration.objects.filter(key=key).first()
        if existing:
            # If fallback models was previously saved as empty, enrich with default
            if key == "GEMINI_FALLBACK_MODELS" and not (existing.value or "").strip():
                existing.value = value
                existing.save(update_fields=["value"])
            continue
        SystemConfiguration.objects.create(
            key=key,
            value=encrypt_value(value) if is_secret_key(key) else value,
            value_type=value_type,
            category="llm",
            description="",
        )


def forwards(apps, schema_editor):
    SystemConfiguration = apps.get_model("api", "SystemConfiguration")
    if not getattr(settings, "RUNNING_TESTS", False):
        seed_from_settings(SystemConfiguration)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0010_blog_audit_actions"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
