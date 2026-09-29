"""Carry an admin's existing homepage-hero choice over to the new image slot."""

from django.db import migrations

LEGACY = {"hero-full-loop": "full-loop"}
ALLOWED = {"example", "hero-assembly", "hero-evolution", "hero-workspace", "full-loop"}


def forwards(apps, schema_editor):
    SystemConfiguration = apps.get_model("api", "SystemConfiguration")
    SiteImage = apps.get_model("api", "SiteImage")
    row = SystemConfiguration.objects.filter(key="HOMEPAGE_HERO_VISUAL").first()
    if row is None:
        return
    value = LEGACY.get(row.value.strip(), row.value.strip())
    if value in ALLOWED and value != "full-loop":
        SiteImage.objects.update_or_create(
            slot="home_hero", defaults={"source": "builtin", "builtin": value}
        )
    row.delete()


def backwards(apps, schema_editor):
    SystemConfiguration = apps.get_model("api", "SystemConfiguration")
    SiteImage = apps.get_model("api", "SiteImage")
    image = SiteImage.objects.filter(slot="home_hero", source="builtin").first()
    if image is None:
        return
    value = "hero-full-loop" if image.builtin == "full-loop" else image.builtin
    SystemConfiguration.objects.update_or_create(
        key="HOMEPAGE_HERO_VISUAL",
        defaults={"value": value, "value_type": "string", "category": "site", "description": ""},
    )


class Migration(migrations.Migration):
    dependencies = [("api", "0008_sign_in_audit_and_site_images")]
    operations = [migrations.RunPython(forwards, backwards)]
