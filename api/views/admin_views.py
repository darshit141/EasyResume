from django.conf import settings
from django.db import models
from django.db.models import Count
from rest_framework import status, permissions, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from authentication.models import User
from api.models import SystemConfiguration
from api.serializers.admin_serializers import AdminUserSerializer

# GROQ_API_KEY / GEMINI_API_KEY are deliberately NOT in this list. The
# masking below (is_sensitive_key) only hides a value in the API response —
# it does nothing to protect what's actually sitting in the database, so
# routing live provider keys through SystemConfiguration meant a DB leak
# (backup, dump, SQL injection) exposed real credentials in plaintext.
# Every other genuine secret in this app (STRIPE_SECRET_KEY, SECRET_KEY, DB
# credentials) is env-var-only for the same reason; these two now match.
# Rotating a key requires an env change + redeploy instead of a UI click —
# a deliberate trade against live plaintext key storage.
DEFAULT_CONFIGS = [
    {
        "key": "LLM_PROVIDER",
        "category": "llm",
        "value_type": "string",
        "description": "Default LLM provider (ollama, groq, gemini, kira)",
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
        "key": "GEMINI_MODEL",
        "category": "llm",
        "value_type": "string",
        "description": "Google Gemini model name",
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
        "description": (
            "Kira (kiraai.vn) chat model id: kira-mini-1.0 (free, very high "
            "limits), kira-3.5-flash, kira-3.5-pro, kira-2.5-flash, "
            "kira-2.5-pro"
        ),
    },
    {
        "key": "KIRA_BASE_URL",
        "category": "llm",
        "value_type": "string",
        "description": "Base URL for the Kira API",
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
    return key.endswith("_API_KEY") or "SECRET" in key


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
            value_str = str(value) if value is not None else ""
            SystemConfiguration.objects.update_or_create(
                key=key,
                defaults={
                    "value": value_str.strip(),
                    "description": meta["description"],
                    "category": meta["category"],
                    "value_type": meta["value_type"],
                }
            )
            
        return self.get(request)


class SystemConfigurationResetAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        SystemConfiguration.objects.all().delete()
        return Response({"detail": "All configurations reset to defaults."}, status=status.HTTP_200_OK)


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
