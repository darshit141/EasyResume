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

    # Which price the subscriber is actually paying — set from the Stripe
    # Checkout session's metadata (see StripeService.create_checkout_session)
    # when checkout.session.completed arrives. Without this, MRR cannot be
    # computed at all: a "pro" plan_id alone doesn't say whether someone is
    # on the EUR or GBP price, or monthly or annual (COMMERCIAL_READINESS_
    # AUDIT.md §45). Blank for a coupon-granted comp subscription, which by
    # definition isn't paying anything and shouldn't count toward MRR.
    currency = models.CharField(max_length=3, blank=True)
    billing_period = models.CharField(max_length=10, blank=True)

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

    class Meta:
        constraints = [
            # A blank stripe_customer_id means "no Stripe customer yet" for
            # every free-tier user, so uniqueness can't apply to blank
            # values — only once a real Stripe customer id is set must it
            # be unique, which is what `.filter(stripe_customer_id=...)
            # .first()` in SubscriptionSyncService relies on actually
            # returning at most one row.
            models.UniqueConstraint(
                fields=["stripe_customer_id"],
                condition=~models.Q(stripe_customer_id=""),
                name="unique_non_blank_stripe_customer_id",
            )
        ]

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

    # --- Cost/observability fields (COMMERCIAL_READINESS_AUDIT.md P0-11) ---
    # All nullable/blank: existing rows and any call site that hasn't been
    # updated to pass them keep working unchanged. Populated by
    # ai_jobs.tasks.run_ai_job for every AI operation that runs through the
    # background job queue; older, still-synchronous call sites leave these
    # blank until they're migrated onto the same queue.
    job = models.ForeignKey(
        "ai_jobs.AIJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_logs",
    )
    model_name = models.CharField(max_length=100, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    cost_estimate_usd = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self):
        return f"{self.user.email} - {self.action} ({self.provider})"


class CreditGrant(models.Model):
    """A bonus allowance a free-tier user earned rather than paid for.

    While a grant is unexpired it lifts *both* monthly free ceilings — the
    blanket ``ai_calls_per_month`` and the marketed ``tailored_resumes_per_
    month`` — by ``amount`` (see EntitlementsService.bonus_credits). There is
    deliberately no separate "spend" record: a grant simply stops counting
    once ``expires_at`` passes, which keeps the accounting identical to how
    the base monthly quotas already work (a rolling count against a limit,
    not a decrementing wallet) and means a credit that was never used just
    lapses instead of lingering forever.

    ``reason`` records how it was earned. ``external_id`` is the ad network's
    own transaction id for a ``rewarded_ad`` grant and is what makes the
    server-to-server reward callback idempotent — the same transaction
    delivered twice (ad networks retry) creates one grant, not two.
    """

    REASON_REWARDED_AD = "rewarded_ad"
    REASON_REFERRAL = "referral"
    REASON_PROFILE_COMPLETION = "profile_completion"
    REASON_DAILY_STREAK = "daily_streak"
    REASON_ADMIN_GRANT = "admin_grant"
    REASON_CHOICES = [
        (REASON_REWARDED_AD, "Rewarded ad"),
        (REASON_REFERRAL, "Referral"),
        (REASON_PROFILE_COMPLETION, "Profile completion"),
        (REASON_DAILY_STREAK, "Daily streak"),
        (REASON_ADMIN_GRANT, "Admin grant"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="credit_grants",
    )
    amount = models.PositiveIntegerField()
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    # The ad provider that issued a rewarded-ad grant ("admanager" /
    # "partner"); blank for every other reason.
    provider = models.CharField(max_length=20, blank=True)
    # The provider's transaction id, unique per (reason, external_id) so a
    # retried reward callback is a no-op. Blank for reasons that have no
    # external transaction (referral, streak, admin grant).
    external_id = models.CharField(max_length=200, blank=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-granted_at"]
        indexes = [
            models.Index(fields=["user", "expires_at"]),
        ]
        constraints = [
            # Idempotency for reward callbacks: a provider transaction id
            # can back at most one grant. The partial condition keeps the
            # many blank-external_id rows (referral / streak / admin) from
            # colliding with each other.
            models.UniqueConstraint(
                fields=["reason", "external_id"],
                condition=~models.Q(external_id=""),
                name="unique_reason_external_id",
            ),
        ]

    def __str__(self):
        return f"{self.user.email} +{self.amount} ({self.reason})"


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


class StripeWebhookEvent(models.Model):
    """One row per Stripe webhook delivery, keyed by Stripe's own event id.

    This is what makes webhook processing idempotent: Stripe redelivers
    events (on timeout, on manual replay, sometimes just because), and
    without this, a redelivered `checkout.session.completed` or
    `invoice.paid` would reprocess business logic that already ran —
    harmless for some events, but a real correctness risk for others. It
    also doubles as the webhook audit trail the audit calls for: what
    Stripe sent, when, and whether it was applied successfully.
    """

    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["event_type", "created_at"])]

    def __str__(self):
        return f"{self.event_type} ({self.stripe_event_id})"
