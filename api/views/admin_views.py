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

DEFAULT_CONFIGS = [
    {
        "key": "LLM_PROVIDER",
        "category": "llm",
        "value_type": "string",
        "description": "Default LLM provider (ollama, groq, gemini)",
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
        "key": "GROQ_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Groq API Key",
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
        "key": "GEMINI_API_KEY",
        "category": "llm",
        "value_type": "string",
        "description": "Google Gemini API Key",
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
        "key": "MAX_RESUMES_PER_USER",
        "category": "limits",
        "value_type": "integer",
        "description": "Maximum number of resumes a user can upload",
    },
    {
        "key": "ALLOWED_JOB_URL_DOMAINS",
        "category": "limits",
        "value_type": "list",
        "description": "Comma-separated list of domains allowed for job URL parsing",
    },
]


def serialize_setting_value(value, value_type):
    if value_type == "list" and isinstance(value, list):
        return ",".join(value)
    if value_type == "boolean":
        return "true" if value else "false"
    return str(value) if value is not None else ""


class SystemConfigurationListUpdateAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        configs = {c.key: c for c in SystemConfiguration.objects.all()}
        data = []
        for default in DEFAULT_CONFIGS:
            key = default["key"]
            if key in configs:
                config = configs[key]
                data.append({
                    "key": key,
                    "value": config.value,
                    "description": config.description or default["description"],
                    "category": config.category or default["category"],
                    "value_type": config.value_type or default["value_type"],
                    "updated_at": config.updated_at,
                    "is_custom": True,
                })
            else:
                setting_val = getattr(settings, key, None)
                serialized_val = serialize_setting_value(setting_val, default["value_type"])
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
