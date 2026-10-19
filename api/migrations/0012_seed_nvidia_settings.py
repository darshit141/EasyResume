"""Seed NVIDIA NIM configuration into SystemConfiguration.

Ensures NVIDIA_MODEL, NVIDIA_FALLBACK_MODELS, NVIDIA_BASE_URL, and NVIDIA_API_KEY
are present in SystemConfiguration so they can be managed dynamically from the admin
Configuration tab without redeploying.
"""

from django.conf import settings
from django.db import migrations

from api.utils.secrets import encrypt_value, is_secret_key

SEEDED_SETTINGS = (
    ("NVIDIA_MODEL", "string"),
    ("NVIDIA_FALLBACK_MODELS", "list"),
    ("NVIDIA_BASE_URL", "string"),
    ("NVIDIA_API_KEY", "string"),
)


def seed_from_settings(SystemConfiguration):
    for key, value_type in SEEDED_SETTINGS:
        raw = getattr(settings, key, "")
        value = ",".join(raw) if isinstance(raw, (list, tuple)) else str(raw or "").strip()
        if not value:
            continue
        existing = SystemConfiguration.objects.filter(key=key).first()
        if existing:
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
        ("api", "0011_seed_dahl_and_gemini_fallbacks"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
