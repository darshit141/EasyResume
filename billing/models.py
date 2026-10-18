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

    # Which processor this subscription is billed through. Blank for a
    # free account or a coupon grant. It decides what "manage billing" means
    # for this user: Stripe has a hosted customer portal, Razorpay does not,
    # so an INR subscriber cancels through our own endpoint instead.
    PROCESSOR_CHOICES = [("stripe", "Stripe"), ("razorpay", "Razorpay")]
    payment_processor = models.CharField(max_length=20, choices=PROCESSOR_CHOICES, blank=True)
    razorpay_subscription_id = models.CharField(max_length=64, blank=True)
    razorpay_customer_id = models.CharField(max_length=64, blank=True)

    # When the newest processor event applied to this row was *created* by
    # the processor. Razorpay states plainly that webhooks can arrive out of
    # order; without this, a late "charged" landing after "cancelled" would
    # quietly reactivate a subscription the customer had ended.
    processor_event_at = models.DateTimeField(null=True, blank=True)

    # The customer has cancelled, but keeps access until current_period_end.
    # Without it the settings page would keep offering "Cancel" to someone
    # who already did, and could not tell them when access actually ends.
    cancel_at_period_end = models.BooleanField(default=False)

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
            ),
            # Same reasoning: the Razorpay webhook finds the local row by
            # this id, so it must identify at most one.
            models.UniqueConstraint(
                fields=["razorpay_subscription_id"],
                condition=~models.Q(razorpay_subscription_id=""),
                name="unique_non_blank_razorpay_subscription_id",
            ),
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


class PlanPrice(models.Model):
    """An editable price for one (plan, currency, billing period).

    Prices used to live only in `billing.plans` as literals with env-var
    overrides, which meant changing one needed a code edit or at minimum an
    env change and a restart. This table takes precedence over those, so a
    price can be corrected from the admin without a deploy. The literals
    stay as the fallback: an empty table must leave the product working
    exactly as it did before.

    `stripe_price_id` sits beside the amount on purpose. Stripe, not this
    row, decides what a customer is actually charged - the amount here is
    only what we *display*. Storing them together makes it obvious that
    changing one without the other means showing a price we won't charge,
    which is misleading pricing, not a cosmetic bug.
    """

    plan_id = models.CharField(max_length=50)
    currency = models.CharField(max_length=3)
    billing_period = models.CharField(max_length=10)

    amount_cents = models.PositiveIntegerField(
        help_text="Displayed amount, in minor units. Must match what the Stripe price below actually charges."
    )
    stripe_price_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Leave blank to make this combination unpurchasable rather than charge the wrong amount.",
    )
    # INR is charged through Razorpay, which bills against a Plan rather
    # than a Stripe Price. Same rule as above: blank means "not for sale".
    razorpay_plan_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="INR only. The Razorpay Plan that charges exactly this amount, GST included.",
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Turn off to fall back to the built-in catalog price for this combination.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("plan_id", "currency", "billing_period")
        ordering = ["plan_id", "currency", "billing_period"]

    def __str__(self):
        return f"{self.plan_id} {self.currency} {self.billing_period} = {self.amount_cents}"


class PricingOffer(models.Model):
    """A time-boxed promotional price - a festival sale, a launch discount.

    Deliberately carries its own `stripe_price_id` rather than a percentage
    off. A discount that only exists in our display would show one number
    and charge another, which is misleading pricing rather than a bug to
    fix later. So an offer is a real, separate Stripe Price you create for
    the campaign, and **an offer with no Stripe price never activates** -
    the same rule the base prices already follow.

    Offers are per (plan, currency, period), the same grain as the price
    they replace, because each one needs its own Stripe Price. Group them
    with `campaign` so one festival reads as a single thing in the admin.
    """

    campaign = models.CharField(
        max_length=80,
        help_text='Groups the rows of one sale, e.g. "Diwali 2026". Internal.',
    )
    label = models.CharField(
        max_length=60,
        help_text='Shown to customers on the pricing card, e.g. "Diwali Offer".',
    )
    badge = models.CharField(
        max_length=24,
        blank=True,
        help_text='Short flash on the card, e.g. "33% OFF". Optional.',
    )

    plan_id = models.CharField(max_length=50)
    currency = models.CharField(max_length=3)
    billing_period = models.CharField(max_length=10)

    amount_cents = models.PositiveIntegerField(help_text="The promotional amount, in minor units.")
    stripe_price_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="The Stripe Price for the discounted amount. Blank means this offer stays dormant - we never advertise a discount we cannot actually charge.",
    )

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(
        default=True, help_text="Turn off to end a sale early without deleting its record."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [
            models.Index(fields=["plan_id", "currency", "billing_period", "starts_at", "ends_at"]),
        ]

    def __str__(self):
        return f"{self.campaign}: {self.plan_id} {self.currency} {self.billing_period}"

    def is_live(self, now=None):
        """Whether this offer should be applied right now.

        A missing Stripe price makes it dormant however correct the dates
        are - showing a sale we cannot charge is worse than showing none.
        """
        from django.utils import timezone

        now = now or timezone.now()
        return bool(
            self.is_active
            and self.stripe_price_id
            and self.starts_at <= now <= self.ends_at
        )


class UserAIKey(models.Model):
    """A provider API key the user brought themselves.

    Entirely optional, and never the normal path: IntelliResume's own AI
    infrastructure serves every request by default, and this exists only so
    that someone who has exhausted their included allowance has a third
    option beside "wait until next month" and "upgrade". When their own key
    is in use they are paying the provider directly, so the monthly AI-call
    ceiling — which exists purely to cap *our* spend — no longer applies to
    them.

    What it deliberately does NOT do is unlock paid features. Word export,
    version history and unlimited tailored resumes are product value, not
    compute cost, and a personal API key buys none of it. Conflating the
    two would turn a cost-control valve into a way to get Pro for free.

    Storage rules, all of which the tests assert:

      - The key is encrypted at rest under its own key-derivation domain
        (`api.utils.secrets.DOMAIN_USER_PROVIDER_KEY`), so a database dump
        is not a list of working credentials and a SystemConfiguration
        ciphertext cannot be replayed as somebody's personal key.
      - The plaintext is never returned by any endpoint, never logged, and
        never included in an error message. `last_four` exists so the UI can
        say *which* key is saved without being able to use it.
      - A key is validated against the live provider before it is stored, so
        a typo is reported immediately rather than at the moment the user
        next needs the feature to work.
    """

    # Only providers that are genuinely self-serve for an individual — a
    # Google AI Studio key takes about a minute to create and has a free
    # tier. Kept as a list rather than a single hardcoded "gemini" so
    # adding one later is a one-line change, but deliberately not "any
    # supported provider": Ollama is a local server with no key at all, and
    # offering it here would be nonsense.
    PROVIDER_GEMINI = "gemini"
    PROVIDER_DAHL = "dahl"
    PROVIDER_CHOICES = [
        (PROVIDER_GEMINI, "Google Gemini"),
        (PROVIDER_DAHL, "Dahl Global"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_keys",
    )
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)

    # Ciphertext, always. Never read this directly — go through
    # UserAIKeyService, which decrypts under the right domain and handles a
    # key that can no longer be decrypted.
    encrypted_key = models.TextField()
    last_four = models.CharField(max_length=4, blank=True)

    # Off rather than deleted when the user pauses it, so turning it back on
    # doesn't mean finding the key again.
    is_active = models.BooleanField(default=True)

    # When the provider last confirmed the key works, and what it said if it
    # didn't. A key that was valid on the day it was saved can be revoked in
    # the provider's console at any time, and the user needs to be told that
    # here rather than discovering it as a failed resume generation.
    last_validated_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=40, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "provider"], name="unique_user_ai_provider_key"),
        ]
        ordering = ["provider"]

    def __str__(self):
        # Never the key, not even partially beyond the last four — this
        # string ends up in admin lists and log lines.
        return f"{self.user_id}: {self.provider} (•••{self.last_four})"


class BillingProfile(models.Model):
    """What an Indian tax invoice needs to know about the buyer.

    Only INR checkout asks for this. `state_code` is required there because
    it decides the place of supply, and so whether the invoice shows CGST
    and SGST or IGST. The GSTIN fields are optional: a business that
    supplies them gets an invoice it can claim input tax credit against,
    and pays exactly what a consumer pays.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_profile",
    )
    country = models.CharField(max_length=2, default="IN")
    state_code = models.CharField(max_length=2, blank=True)
    gstin = models.CharField(max_length=15, blank=True)
    legal_name = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id}: {self.state_code or '--'} {self.gstin or '(consumer)'}"


class RazorpayWebhookEvent(models.Model):
    """One row per Razorpay webhook delivery, keyed by the
    `x-razorpay-event-id` header — the Razorpay twin of StripeWebhookEvent,
    and there for the same two reasons: duplicate deliveries are normal and
    must not re-run business logic, and it is the audit trail of what
    Razorpay told us and whether we applied it."""

    event_id = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["event_type", "created_at"])]

    def __str__(self):
        return f"{self.event_type} ({self.event_id})"


class InvoiceSequence(models.Model):
    """The next invoice serial for one financial year.

    GST requires invoice numbers to be consecutive and unique within a
    financial year. A counter row locked with SELECT ... FOR UPDATE is the
    simplest thing that guarantees that under concurrent webhooks; deriving
    the next number from MAX() would hand two simultaneous charges the same
    serial.
    """

    financial_year = models.CharField(max_length=7, unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.financial_year}: {self.last_number}"


class GSTInvoice(models.Model):
    """A tax invoice (or, before GST registration, a plain invoice) for one
    successful INR charge.

    Deliberately NOT deleted with the account. Section 36 of the CGST Act
    requires invoices to be kept for 72 months after the annual return is
    due, and GDPR Article 17(3)(b) exempts data held to meet a legal
    obligation. So the user link is SET_NULL, and every buyer detail the
    invoice prints is copied onto it at issue time — an invoice must say
    what it said on the day it was issued, however the profile changes
    later or whether the account still exists at all.
    """

    # A "tax invoice" carries GST and can only be issued by a registered
    # supplier. Before registration the business issues a plain invoice
    # with no tax lines — not a "bill of supply", which is a document for
    # registered suppliers of exempt goods and services.
    KIND_TAX_INVOICE = "tax_invoice"
    KIND_INVOICE = "invoice"
    KIND_CHOICES = [(KIND_TAX_INVOICE, "Tax invoice"), (KIND_INVOICE, "Invoice")]

    number = models.CharField(max_length=16, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    issued_at = models.DateTimeField()
    financial_year = models.CharField(max_length=7)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gst_invoices",
    )

    # The charge this invoices. Unique, so a redelivered webhook can never
    # issue a second invoice for the same money.
    razorpay_payment_id = models.CharField(max_length=64, unique=True)
    razorpay_subscription_id = models.CharField(max_length=64, blank=True)

    plan_id = models.CharField(max_length=50)
    billing_period = models.CharField(max_length=10)
    description = models.CharField(max_length=200)
    currency = models.CharField(max_length=3, default="INR")

    # Supplier, as configured on the day of issue.
    seller_legal_name = models.CharField(max_length=200)
    seller_gstin = models.CharField(max_length=15, blank=True)
    seller_address = models.TextField(blank=True)
    seller_state_code = models.CharField(max_length=2, blank=True)
    sac_code = models.CharField(max_length=8, blank=True)

    # Buyer, as recorded on the day of issue.
    buyer_name = models.CharField(max_length=200)
    buyer_email = models.EmailField()
    buyer_gstin = models.CharField(max_length=15, blank=True)
    buyer_address = models.TextField(blank=True)
    place_of_supply_code = models.CharField(max_length=2, blank=True)
    place_of_supply_name = models.CharField(max_length=60, blank=True)

    # Amounts in paise. taxable_value + cgst + sgst + igst == total, always.
    supply_type = models.CharField(max_length=20, blank=True)
    rate_percent = models.CharField(max_length=6, default="0")
    taxable_value = models.PositiveIntegerField()
    cgst = models.PositiveIntegerField(default=0)
    sgst = models.PositiveIntegerField(default=0)
    igst = models.PositiveIntegerField(default=0)
    state_tax_label = models.CharField(max_length=5, default="SGST")
    total = models.PositiveIntegerField()
    note = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at"]
        indexes = [models.Index(fields=["user", "issued_at"])]

    def __str__(self):
        return f"{self.number} ({self.total / 100:.2f} INR)"
