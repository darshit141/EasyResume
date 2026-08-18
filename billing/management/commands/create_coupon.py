"""Creates (or updates) a coupon from the command line — the same thing the
Django admin's Coupon page does, for scripting/automation instead of
clicking through a form. Equally available via /admin/billing/coupon/add/.

Examples:
    python manage.py create_coupon EARLYACCESS
    python manage.py create_coupon BETA-COHORT --uses 25 --days 30
    python manage.py create_coupon VIP --description "Comp account for reviewer"
"""

from django.core.management.base import BaseCommand, CommandError

from billing.models import Coupon


class Command(BaseCommand):
    help = "Create or update a coupon code that grants Pro access on redemption."

    def add_arguments(self, parser):
        parser.add_argument("code", help="The code users will enter, e.g. EARLYACCESS.")
        parser.add_argument(
            "--uses", type=int, default=1, help="How many different users may redeem it (default: 1)."
        )
        parser.add_argument(
            "--days", type=int, default=None, help="Pro access expires after this many days (default: permanent)."
        )
        parser.add_argument("--description", default="", help="Internal note, not shown to the redeemer.")

    def handle(self, *args, **options):
        code = options["code"].strip().upper()
        if not code:
            raise CommandError("Code must not be empty.")

        coupon, created = Coupon.objects.update_or_create(
            code=code,
            defaults={
                "max_redemptions": options["uses"],
                "duration_days": options["days"],
                "description": options["description"],
                "is_active": True,
            },
        )

        verb = "Created" if created else "Updated"
        expiry = f"{coupon.duration_days}-day trial" if coupon.duration_days else "permanent"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} coupon {coupon.code} — {coupon.max_redemptions} use(s), {expiry}.")
        )
