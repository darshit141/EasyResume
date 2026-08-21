from django.urls import path

from ai_jobs.views import AIJobListAPIView, AIJobStatusAPIView

urlpatterns = [
    path("", AIJobListAPIView.as_view(), name="ai-job-list"),
    path("<int:job_id>/", AIJobStatusAPIView.as_view(), name="ai-job-status"),
]
