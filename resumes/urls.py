from django.urls import path

from resumes.views.resume_upload_view import ResumeUploadAPIView
from resumes.views.resume_view import ResumeViewAPIView

urlpatterns = [
    path(
        "upload/",
        ResumeUploadAPIView.as_view(),
        name="resume-upload",
    ),
    path(
        "view/", 
        ResumeViewAPIView.as_view(), 
        name="resume-view"
    ),
]