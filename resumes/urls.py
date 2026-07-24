from django.urls import path

from resumes.views.resume_views import ResumeUploadAPIView

urlpatterns = [
    path(
        "upload/",
        ResumeUploadAPIView.as_view(),
        name="resume-upload",
    ),
]