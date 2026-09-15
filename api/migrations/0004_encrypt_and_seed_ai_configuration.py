"""Make the database the source of truth for AI configuration.

Two idempotent steps:

1. Encrypt any provider API key still stored as plain text. Keys used to be
   saved in this table unencrypted; they are now encrypted at rest.
2. Copy the AI settings the server is running with right now (from .env) into
   the table wherever no row exists yet. From then on the admin Configuration
   tab is where models, fallbacks and keys change - live, with no deploy - and
   .env is only a first-boot fallback. Copying first is what keeps production
   working through the switch instead of starting from blank settings.

Seeding is skipped under `manage.py test`: the test database must not pick up
a developer's real keys and provider choice from their local .env, which would
override test settings and let tests make real network calls.
"""

from django.conf import settings
from django.db import migrations

from api.utils.secrets import encrypt_value, is_encrypted, is_secret_key

SEEDED_SETTINGS = (
    ("LLM_PROVIDER", "string"),
    ("LLM_FALLBACK_PROVIDER", "string"),
    ("GEMINI_MODEL", "string"),
    ("GEMINI_FALLBACK_MODELS", "list"),
    ("GROQ_MODEL", "string"),
    ("KIRA_MODEL", "string"),
    ("OLLAMA_MODEL", "string"),
    ("GEMINI_API_KEY", "string"),
    ("GROQ_API_KEY", "string"),
    ("KIRA_API_KEY", "string"),
)


def encrypt_plaintext_secrets(SystemConfiguration):
    for row in SystemConfiguration.objects.all():
        if is_secret_key(row.key) and row.value and not is_encrypted(row.value):
            row.value = encrypt_value(row.value.strip())
            row.save(update_fields=["value"])


def seed_from_settings(SystemConfiguration):
    for key, value_type in SEEDED_SETTINGS:
        value = getattr(settings, key, "")
        if isinstance(value, (list, tuple)):
            value = ",".join(str(item).strip() for item in value if str(item).strip())
        value = str(value or "").strip()
        if not value or SystemConfiguration.objects.filter(key=key).exists():
            continue
        SystemConfiguration.objects.create(
            key=key,
            value=encrypt_value(value) if is_secret_key(key) else value,
            value_type=value_type,
            category="llm",
            # Left empty so the admin tab shows the setting's own description.
            description="",
        )


def forwards(apps, schema_editor):
    SystemConfiguration = apps.get_model("api", "SystemConfiguration")
    encrypt_plaintext_secrets(SystemConfiguration)
    if not getattr(settings, "RUNNING_TESTS", False):
        seed_from_settings(SystemConfiguration)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0003_b2blead"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
