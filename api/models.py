from django.db import models


class SystemConfiguration(models.Model):
    """
    Dynamic system settings to override values in settings.py.
    """
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True)
    category = models.CharField(max_length=50)
    value_type = models.CharField(max_length=20)  # string, integer, float, boolean, list
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} = {self.value} ({self.value_type})"

    def parsed_value(self):
        val = self.value
        t = self.value_type
        if t == "integer":
            try:
                return int(val)
            except ValueError:
                return 0
        elif t == "float":
            try:
                return float(val)
            except ValueError:
                return 0.0
        elif t == "boolean":
            return val.strip().lower() in ("true", "yes", "1", "t", "y")
        elif t == "list":
            return [item.strip() for item in val.split(",") if item.strip()]
        return val


class AnalyticsEvent(models.Model):
    """Minimal first-party funnel/acquisition tracking — no third-party
    analytics account required to get real numbers (COMMERCIAL_READINESS_
    AUDIT.md §20/§21). Deliberately narrow: only the funnel steps that
    leave no natural trace elsewhere in the schema (a page view, a
    checkout click before Stripe is ever involved) are tracked here —
    everything else (resume uploaded, tailored resume generated, a
    subscription actually going active) is computed directly from the
    Resume/JobResumeCuration/Subscription tables it already lives in,
    rather than duplicated into a second, driftable copy.
    """

    EVENT_LANDING_PAGE_VIEW = "landing_page_view"
    EVENT_SIGNUP_COMPLETED = "signup_completed"
    EVENT_CHECKOUT_STARTED = "checkout_started"
    EVENT_CHOICES = [
        (EVENT_LANDING_PAGE_VIEW, "Landing page view"),
        (EVENT_SIGNUP_COMPLETED, "Signup completed"),
        (EVENT_CHECKOUT_STARTED, "Checkout started"),
    ]

    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    # Nullable: a landing-page view or an abandoned signup has no account
    # yet. session_id (a client-generated id persisted in localStorage) is
    # what lets an anonymous visitor's pre-signup steps still be counted
    # toward the funnel, without being personally identifying on its own.
    user = models.ForeignKey(
        "authentication.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_events",
    )
    session_id = models.CharField(max_length=64, blank=True)
    # Small, deliberately-restricted metadata (see the request serializer —
    # only UTM parameters are accepted) — this is a funnel counter, not a
    # general-purpose event-properties store.
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["session_id"]),
        ]

    def __str__(self):
        return f"{self.event_type} @ {self.created_at:%Y-%m-%d %H:%M}"


class B2BLead(models.Model):
    """A submission from the /for-career-coaches page: a coach or agency
    interested in a bulk/white-label arrangement rather than a single
    personal-use subscription. This is a sales pipeline, not analytics — no
    self-service product exists for these leads yet, so every one of them
    needs a human to follow up. Visible and manageable entirely through
    Django admin; deliberately no separate admin API/UI for this, since
    admin already covers search/filter/triage and a bespoke one would just
    be the same thing rebuilt."""

    TEAM_SIZE_CHOICES = [
        ("1", "Just me"),
        ("2-10", "2-10"),
        ("11-50", "11-50"),
        ("50+", "50+"),
    ]

    name = models.CharField(max_length=200)
    email = models.EmailField()
    company_name = models.CharField(max_length=200, blank=True)
    team_size = models.CharField(max_length=10, choices=TEAM_SIZE_CHOICES, blank=True)
    message = models.TextField(blank=True)
    contacted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} <{self.email}> @ {self.created_at:%Y-%m-%d}"

