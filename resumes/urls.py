from django.urls import path

from resumes.views.resume_upload_view import ResumeUploadAPIView
from resumes.views.resume_view import ResumeViewAPIView
from resumes.views.resume_edit_view import ResumeEditAPIView

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
    path("edit/",
          ResumeEditAPIView.as_view(),
            name="resume-edit"
        ),
]