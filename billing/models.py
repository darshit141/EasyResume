from django.conf import settings
from django.db import models
from django.utils import timezone


class Subscription(models.Model):
    """
    One row per user, tracking their Stripe subscription state. The plan
    catalog itself (name, price, limits) lives in billing.plans as plain
    Python data — these are placeholder prices, not final ones — rather than
    a database table, since nothing here needs to be admin-editable yet.
    """

    STATUS_CHOICES = [
        ("none", "No subscription"),
        ("active", "Active"),
        ("past_due", "Past due"),
        ("canceled", "Canceled"),
        ("incomplete", "Incomplete"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )

    plan_id = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="none",
    )

    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)

    current_period_end = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} - {self.plan_id or 'no plan'} ({self.status})"

    @property
    def is_active(self):
        # `current_period_end` is set by both real Stripe subscriptions and
        # time-limited coupon grants (see CouponService). Checking it here
        # makes expiry self-enforcing on every read, with no scheduled task
        # needed — a coupon-granted trial simply stops being active once its
        # date passes, the same moment anything next checks entitlements.
        # A permanent grant (current_period_end left null) never expires.
        if self.status != "active":
            return False
        if self.current_period_end is not None and self.current_period_end <= timezone.now():
            return False
        return True


class AIUsageLog(models.Model):
    """One row per successful AI-backed call, for per-user quota enforcement
    and for actually being able to see where AI spend is going.

    Only successful calls are logged (a provider timeout/error never reaches
    this) — quota tracks work actually done, not attempts, matching how the
    existing tailored-resume quota already behaves. `provider` is the
    resolved provider that served the call (never blank), so it's possible
    to see the real ollama/groq/gemini split even for requests that didn't
    explicitly choose one.
    """

    ACTION_RESUME_PARSE = "resume_parse"
    ACTION_JOB_ANALYSIS = "job_analysis"
    ACTION_GAP_ANALYSIS = "gap_analysis"
    ACTION_MISSING_INFORMATION_REVIEW = "missing_information_review"
    ACTION_TAILORED_RESUME_GENERATION = "tailored_resume_generation"
    ACTION_COVER_LETTER = "cover_letter_generation"
    ACTION_CHOICES = [
        (ACTION_RESUME_PARSE, "Resume parse"),
        (ACTION_JOB_ANALYSIS, "Job analysis"),
        (ACTION_GAP_ANALYSIS, "Gap analysis"),
        (ACTION_MISSING_INFORMATION_REVIEW, "Missing information review"),
        (ACTION_TAILORED_RESUME_GENERATION, "Tailored resume generation"),
        (ACTION_COVER_LETTER, "Cover letter generation"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_usage_logs",
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    provider = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self):
        return f"{self.user.email} - {self.action} ({self.provider})"


class Coupon(models.Model):
    """A code that grants Pro access without going through Stripe.

    Built for early access / trial distribution — hand a code to a beta
    tester or reviewer, they redeem it, they get Pro. `duration_days` makes
    it a time-limited trial when set; leave it null for a permanent grant
    (e.g. a comp account for an internal tester). `max_redemptions` is what
    makes this "configurable" rather than always strictly single-use: the
    default of 1 gives one code = one use, but the same mechanism supports
    minting one code for a whole beta cohort by raising the number.
    """

    code = models.CharField(max_length=40, unique=True)
    description = models.CharField(
        max_length=200, blank=True, help_text="Internal note — not shown to the redeemer."
    )
    duration_days = models.PositiveIntegerField(
        null=True, blank=True, help_text="Pro access lasts this many days. Leave blank for a permanent grant."
    )
    max_redemptions = models.PositiveIntegerField(
        default=1, help_text="How many different users may redeem this exact code. 1 = single-use."
    )
    is_active = models.BooleanField(
        default=True, help_text="Turn off to disable a code without deleting its redemption history."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    @property
    def redemptions_used(self):
        return self.redemptions.count()

    @property
    def redemptions_remaining(self):
        return max(0, self.max_redemptions - self.redemptions_used)


class CouponRedemption(models.Model):
    """One row per successful redemption — the audit trail of who used which
    code and when, and what actually enforces "used once" (or N times)."""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coupon_redemptions")
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A given user can redeem a given code at most once, independent of
        # how many total redemptions that code allows — prevents one person
        # from claiming a multi-use code's entire allowance solo.
        unique_together = ("coupon", "user")
        ordering = ["-redeemed_at"]

    def __str__(self):
        return f"{self.user.email} redeemed {self.coupon.code}"
