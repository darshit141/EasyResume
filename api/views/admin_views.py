from django.conf import settings
from django.db import models
from django.db.models import Count
from rest_framework import status, permissions, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from authentication.models import User
from api.models import SystemConfiguration
from api.utils.secrets import encrypt_value, is_secret_key
from api.serializers.admin_serializers import AdminUserSerializer

# Every AI setting - provider, models, fallbacks and the provider API keys - is
# editable here and takes effect on the next request, so a discontinued model
# or a revoked token is fixed from the admin panel instead of by a deploy.
#
# API keys were once kept out of this list because the table stored them in
# plaintext, so a database dump exposed working credentials. They are back,
# encrypted at rest (api/utils/secrets.py), masked in every response, and
# never cleared by Reset to Defaults.
DEFAULT_CONFIGS = [
    {
        "key": "LLM_PROVIDER",
        "category": "llm",
        "value_type": "string",
        "description": "Default LLM provider (ollama, groq, gemini, kira)",
    },
    {
        "key": "LLM_FALLBACK_PROVIDER",
        "category": "llm",
        "value_type": "string",
        "description": "Provider to retry once with when the default provider fails. Empty = no fallback.",
    },
    {
        "key": "AI_PROVIDER_SELECTION_ENABLED",
        "category": "llm",
        "value_type": "boolean",
        "description": (
            "Let users pick the AI provider on each request. Off hides the picker everywhere "
            "and every request uses LLM_PROVIDER (and its fallback); a provider a client "
            "still sends is ignored."
        ),
    },
    {
        "key": "LLM_TIMEOUT",
        "category": "llm",
        "value_type": "integer",
        "description": "Timeout in seconds for LLM API calls",
    },
    {
        "key": "LLM_MAX_RETRIES",
        "category": "llm",
        "value_type": "integer",
        "description": "Maximum number of retries for LLM calls",
    },
    {
        "key": "LLM_TEMPERATURE",
        "category": "llm",
        "value_type": "float",
        "description": "Temperature setting for LLM generation",
    },
    {
        "key": "LLM_MAX_OUTPUT_TOKENS",
        "category": "llm",
        "value_type": "integer",
        "description": "Max tokens generated per LLM call",
    },
    {
        "key": "OLLAMA_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for local Ollama service",
    },
    {
        "key": "OLLAMA_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": "Ollama model name",
    },
    {
        "key": "OLLAMA_KEEP_ALIVE",
        "category": "llm",
        "value_type": "string",
        "description": "Model retention time in Ollama (e.g. 30m)",
    },
    {
        "key": "GROQ_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": "Groq model name",
    },
    {
        "key": "GROQ_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for Groq API",
    },
    {
        "key": "GROQ_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Groq API key. Stored encrypted; never shown again after saving.",
    },
    {
        "key": "GEMINI_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Google Gemini API key (aistudio.google.com/apikey). Stored encrypted; never shown again after saving.",
    },
    {
        "key": "GEMINI_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": (
            "First-choice Gemini model id. If it is discontinued it is skipped automatically. "
            "Leave empty to pick from the live model list. Run `python manage.py check_ai` "
            "to see which models the key is offered."
        ),
    },
    {
        "key": "GEMINI_FALLBACK_MODELS",
        "category": "llm",
        "value_type": "list",
        "description": "Comma-separated Gemini models to try, in order, when the first choice is overloaded, out of quota or discontinued.",
    },
    {
        "key": "GEMINI_MODEL_DISCOVERY",
        "category": "llm",
        "value_type": "boolean",
        "description": "Read Google's live model list to skip discontinued models and find further fallbacks automatically.",
    },
    {
        "key": "GEMINI_AUTO_FALLBACK_FAMILIES",
        "category": "llm",
        "value_type": "list",
        "description": "Automatic fallbacks must have one of these words in their id (e.g. flash). Empty turns automatic fallback off.",
    },
    {
        "key": "GEMINI_AUTO_EXCLUDE",
        "category": "llm",
        "value_type": "list",
        "description": "Automatic fallbacks must have none of these words in their id (e.g. image, tts, preview).",
    },
    {
        "key": "GEMINI_MAX_MODEL_ATTEMPTS",
        "category": "llm",
        "value_type": "integer",
        "description": "Most Gemini models a single request may try before failing.",
    },
    {
        "key": "GEMINI_MODEL_LIST_TTL",
        "category": "llm",
        "value_type": "integer",
        "description": "Seconds Google's model list is cached for (a new key always gets a fresh list).",
    },
    {
        "key": "GEMINI_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for Gemini API",
    },
    {
        "key": "KIRA_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": "Kira (kiraai.vn) chat model id, as listed in your Kira account. Text models only.",
    },
    {
        "key": "KIRA_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for the Kira API",
    },
    {
        "key": "KIRA_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Kira API key. Stored encrypted; never shown again after saving.",
    },
    {
        "key": "BYOK_ENABLED",
        "category": "llm",
        "value_type": "boolean",
        "description": (
            "Let users save their own Google Gemini API key. While a user's own key is "
            "active their AI requests use it, so the monthly AI-action limit — which only "
            "exists to cap our provider bill — stops applying to them. It unlocks no paid "
            "features. Switching this off immediately stops every saved key being used, "
            "and users can still delete theirs."
        ),
    },
    {
        "key": "GST_ENABLED",
        "category": "tax",
        "value_type": "boolean",
        "description": (
            "Charge Indian GST on rupee subscriptions. Turn on only once the business holds "
            "a GSTIN — an unregistered supplier must not collect GST. Prices stay the same "
            "either way (they include GST); this changes what the invoice shows."
        ),
    },
    {
        "key": "GST_RATE_PERCENT",
        "category": "tax",
        "value_type": "string",
        "description": "GST rate for the subscription, in percent (18 for software services).",
    },
    {
        "key": "GST_SELLER_GSTIN",
        "category": "tax",
        "value_type": "string",
        "description": "The business's 15-character GSTIN. An invalid one is ignored and no GST is charged until it is corrected.",
    },
    {
        "key": "GST_SELLER_STATE_CODE",
        "category": "tax",
        "value_type": "string",
        "description": "Two-digit GST state code of the business (e.g. 27 for Maharashtra). Read from the GSTIN when that is set.",
    },
    {
        "key": "GST_SELLER_LEGAL_NAME",
        "category": "tax",
        "value_type": "string",
        "description": "Legal name printed on invoices.",
    },
    {
        "key": "GST_SELLER_ADDRESS",
        "category": "tax",
        "value_type": "string",
        "description": "Registered address printed on invoices.",
    },
    {
        "key": "GST_SAC_CODE",
        "category": "tax",
        "value_type": "string",
        "description": "Services Accounting Code printed on tax invoices. Confirm the right code with your accountant.",
    },
    {
        "key": "GST_INVOICE_PREFIX",
        "category": "tax",
        "value_type": "string",
        "description": "Up to four letters starting every invoice number (PREFIX/2627/000001). Changing it mid-year starts a visibly different series.",
    },
    {
        "key": "MAX_RESUMES_PER_USER",
        "category": "limits",
        "value_type": "integer",
        "description": "Maximum number of resumes a user can upload",
    },
    {
        "key": "REWARDED_ADS_ENABLED",
        "category": "ads",
        "value_type": "boolean",
        "description": "Let free-tier users watch a rewarded video to earn a bonus credit.",
    },
    {
        "key": "REWARDED_AD_PROVIDER",
        "category": "ads",
        "value_type": "string",
        "description": "Which rewarded-ad network serves the video: admanager (Google Ad Manager) or partner (a third-party SDK configured via env).",
    },
    {
        "key": "REWARDED_AD_CREDITS_PER_VIEW",
        "category": "ads",
        "value_type": "integer",
        "description": "Bonus credits granted per completed rewarded ad. One credit = one extra AI action and one extra tailored resume.",
    },
    {
        "key": "REWARDED_AD_MAX_PER_DAY",
        "category": "ads",
        "value_type": "integer",
        "description": "Most rewarded ads one account may be credited for per day.",
    },
    {
        "key": "REWARDED_AD_CREDIT_TTL_DAYS",
        "category": "ads",
        "value_type": "integer",
        "description": "Days an earned credit stays usable before it expires.",
    },
    {
        "key": "REPLY_TRACKING_ENABLED",
        "category": "limits",
        "value_type": "boolean",
        "description": "Master switch for detecting employer replies to applications. Off means no plan gets it, whatever the per-plan settings say - use this if the Gmail security assessment lapses.",
    },
    {
        "key": "REPLY_TRACKING_FREE",
        "category": "limits",
        "value_type": "string",
        "description": "Reply tracking on the Free plan: none | alias | gmail. Anything unrecognised means none.",
    },
    {
        "key": "REPLY_TRACKING_PRO",
        "category": "limits",
        "value_type": "string",
        "description": "Reply tracking on Pro: alias (a Reply-To address we own - no mailbox access, misses portal applications), gmail (reads the user's inbox, needs the restricted scope + CASA assessment), or none.",
    },
    {
        "key": "REPLY_TRACKING_MAX",
        "category": "limits",
        "value_type": "string",
        "description": "Reply tracking on Max: none | alias | gmail. Set to alias while the Gmail CASA assessment is pending so Max still gets something real.",
    },
    {
        "key": "PRO_JOB_APPLICATIONS_PER_MONTH",
        "category": "limits",
        "value_type": "integer",
        "description": "Applications a Pro account may send per month. An abuse ceiling on our sending reputation before it is a plan feature.",
    },
    {
        "key": "MAX_JOB_APPLICATIONS_PER_MONTH",
        "category": "limits",
        "value_type": "integer",
        "description": "Applications a Max account may send per month.",
    },
    {
        "key": "DISPLAY_ADS_ENABLED",
        "category": "ads",
        "value_type": "boolean",
        "description": "Show banner ads to free-tier visitors on marketing and blog pages. Pro subscribers never see them.",
    },
    {
        "key": "DISPLAY_AD_PROVIDER",
        "category": "ads",
        "value_type": "string",
        "description": "Which network serves the banners: adsense (Google AdSense), admanager (Google Ad Manager), or none. Anything unrecognised serves no ads rather than guessing.",
    },
    {
        "key": "ADSENSE_CLIENT_ID",
        "category": "ads",
        "value_type": "string",
        "description": "AdSense publisher id (ca-pub-…), used when DISPLAY_AD_PROVIDER is adsense. Public — it appears in the page HTML.",
    },
    {
        "key": "GAM_NETWORK_CODE",
        "category": "ads",
        "value_type": "string",
        "description": "Google Ad Manager network code, used when DISPLAY_AD_PROVIDER is admanager. Also used by the rewarded-video provider.",
    },
    {
        "key": "DISPLAY_AD_UNIT_ARTICLE_TOP",
        "category": "ads",
        "value_type": "string",
        "description": "Ad id for the top-of-article placement: an AdSense slot id, or an Ad Manager ad unit path (/network/unit).",
    },
    {
        "key": "DISPLAY_AD_UNIT_ARTICLE_BOTTOM",
        "category": "ads",
        "value_type": "string",
        "description": "Ad id for the end-of-article placement.",
    },
    {
        "key": "DISPLAY_AD_UNIT_BLOG_INDEX",
        "category": "ads",
        "value_type": "string",
        "description": "Ad id for the blog index placement.",
    },
    {
        "key": "FREE_AI_CALLS_PER_MONTH",
        "category": "limits",
        "value_type": "integer",
        "description": "Blanket cap on AI-backed actions per month for a Free-plan user (resume parsing, job analysis, gap analysis, missing-information review, tailored resume generation combined). Pro is unlimited.",
    },
    {
        "key": "FREE_TAILORED_RESUMES_PER_MONTH",
        "category": "limits",
        "value_type": "integer",
        "description": "How many job-specific tailored resumes a Free-plan user can generate per month. Pro is unlimited.",
    },
    {
        "key": "ALLOWED_JOB_URL_DOMAINS",
        "category": "limits",
        "value_type": "list",
        "description": "Comma-separated list of domains allowed for job URL parsing",
    },
    {
        "key": "LOGIN_OTP_REQUIRED",
        "category": "security",
        "value_type": "boolean",
        "description": (
            "Require an emailed one-time code after the password when logging in. "
            "Enabled = two-factor login (recommended). Disabled = the password "
            "alone signs a user in, so a leaked or reused password is enough to "
            "take over an account. Takes effect on the next login attempt, no "
            "redeploy needed."
        ),
    },
]


def serialize_setting_value(value, value_type):
    if value_type == "list" and isinstance(value, list):
        return ",".join(value)
    if value_type == "boolean":
        return "true" if value else "false"
    return str(value) if value is not None else ""


def is_sensitive_key(key):
    return is_secret_key(key)


class SystemConfigurationListUpdateAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        configs = {c.key: c for c in SystemConfiguration.objects.all()}
        data = []
        for default in DEFAULT_CONFIGS:
            key = default["key"]
            if key in configs:
                config = configs[key]
                val = config.value
                if is_sensitive_key(key) and val:
                    val = "********"
                data.append({
                    "key": key,
                    "value": val,
                    "description": config.description or default["description"],
                    "category": config.category or default["category"],
                    "value_type": config.value_type or default["value_type"],
                    "updated_at": config.updated_at,
                    "is_custom": True,
                    "is_secret": is_sensitive_key(key),
                })
            else:
                setting_val = getattr(settings, key, None)
                serialized_val = serialize_setting_value(setting_val, default["value_type"])
                if is_sensitive_key(key) and serialized_val:
                    serialized_val = "********"
                data.append({
                    "key": key,
                    "value": serialized_val,
                    "description": default["description"],
                    "category": default["category"],
                    "value_type": default["value_type"],
                    "updated_at": None,
                    "is_custom": False,
                    "is_secret": is_sensitive_key(key),
                })
        return Response(data, status=status.HTTP_200_OK)

    def put(self, request):
        if not isinstance(request.data, list):
            return Response({"detail": "Expected a list of configurations."}, status=status.HTTP_400_BAD_REQUEST)
        
        errors = []
        # Pre-validate all configurations before writing to DB
        for item in request.data:
            key = item.get("key")
            value = item.get("value")
            
            meta = next((d for d in DEFAULT_CONFIGS if d["key"] == key), None)
            if not meta:
                errors.append({"key": key, "detail": f"Unknown configuration key: {key}"})
                continue
                
            # If sensitive and value is masked, skip validation
            if is_sensitive_key(key) and value == "********":
                continue
                
            value_str = str(value) if value is not None else ""
            value_type = meta["value_type"]
            
            if value_type == "integer":
                try:
                    int(value_str)
                except ValueError:
                    errors.append({"key": key, "detail": f"{key} must be an integer."})
                    continue
            elif value_type == "float":
                try:
                    float(value_str)
                except ValueError:
                    errors.append({"key": key, "detail": f"{key} must be a float."})
                    continue
            elif value_type == "boolean":
                if value_str.strip().lower() not in ("true", "false", "yes", "no", "1", "0", "t", "f", "y", "n"):
                    errors.append({"key": key, "detail": f"{key} must be a boolean."})
                    continue
                    
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
            
        # Write valid ones
        for item in request.data:
            key = item.get("key")
            value = item.get("value")
            
            # If sensitive and value is masked, do not modify/overwrite the configuration
            if is_sensitive_key(key) and value == "********":
                continue
                
            meta = next(d for d in DEFAULT_CONFIGS if d["key"] == key)
            value_str = (str(value) if value is not None else "").strip()
            if is_sensitive_key(key):
                if not value_str:
                    # Clearing a key removes the stored one; the server
                    # environment's key (if any) is used again.
                    SystemConfiguration.objects.filter(key=key).delete()
                    continue
                # Encrypted at rest: a database dump or backup must not be a
                # list of working provider credentials.
                value_str = encrypt_value(value_str)
            SystemConfiguration.objects.update_or_create(
                key=key,
                defaults={
                    "value": value_str,
                    "description": meta["description"],
                    "category": meta["category"],
                    "value_type": meta["value_type"],
                }
            )
            
        return self.get(request)


class SystemConfigurationResetAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        # Stored API keys are credentials, not tunables: wiping them would take
        # the AI down on a click meant to undo a bad setting.
        rows = SystemConfiguration.objects.all()
        secret_ids = [row.pk for row in rows if is_sensitive_key(row.key)]
        rows.exclude(pk__in=secret_ids).delete()
        return Response(
            {"detail": "Settings reset to defaults. Stored API keys were kept."},
            status=status.HTTP_200_OK,
        )


class AdminUserPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminUserListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        search_query = request.query_params.get("search", "").strip()
        queryset = User.objects.annotate(resume_count=Count("resumes")).order_by("-date_joined")
        
        if search_query:
            queryset = queryset.filter(
                models.Q(email__icontains=search_query) |
                models.Q(name__icontains=search_query) |
                models.Q(phone__icontains=search_query)
            )

        paginator = AdminUserPagination()
        paginated_queryset = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AdminUserSerializer(paginated_queryset, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminUserToggleActiveAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, user_id):
        if request.user.id == user_id:
            return Response(
                {"detail": "You cannot deactivate your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
            
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
            
        user.is_active = not user.is_active
        user.save()
        
        annotated_user = User.objects.annotate(resume_count=Count("resumes")).get(id=user_id)
        return Response(AdminUserSerializer(annotated_user).data, status=status.HTTP_200_OK)


class AdminUserToggleStaffAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, user_id):
        if request.user.id == user_id:
            return Response(
                {"detail": "You cannot remove your own admin status."},
                status=status.HTTP_400_BAD_REQUEST,
            )
            
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
            
        user.is_staff = not user.is_staff
        user.save()
        
        annotated_user = User.objects.annotate(resume_count=Count("resumes")).get(id=user_id)
        return Response(AdminUserSerializer(annotated_user).data, status=status.HTTP_200_OK)


class AdminStatsAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from resumes.models import Resume
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        staff_users = User.objects.filter(is_staff=True).count()
        total_resumes = Resume.objects.count()

        return Response({
            "total_users": total_users,
            "active_users": active_users,
            "staff_users": staff_users,
            "total_resumes": total_resumes,
        }, status=status.HTTP_200_OK)
