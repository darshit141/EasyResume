"""Copy the OpenRouter settings the server is running with (from .env) into
SystemConfiguration wherever no row exists yet, same as 0004 did for the
other providers. From then on the admin Configuration tab is where
OPENROUTER_MODEL/BASE_URL/API_KEY change - live, with no deploy.

Seeding is skipped under `manage.py test`, for the same reason as 0004: the
test database must not pick up a developer's real key from their local .env.
"""

from django.conf import settings
from django.db import migrations

from api.utils.secrets import encrypt_value, is_secret_key

SEEDED_SETTINGS = (
    ("OPENROUTER_MODEL", "string"),
    ("OPENROUTER_BASE_URL", "string"),
    ("OPENROUTER_API_KEY", "string"),
)


def seed_from_settings(SystemConfiguration):
    for key, value_type in SEEDED_SETTINGS:
        value = str(getattr(settings, key, "") or "").strip()
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
    if not getattr(settings, "RUNNING_TESTS", False):
        seed_from_settings(SystemConfiguration)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0004_encrypt_and_seed_ai_configuration"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
