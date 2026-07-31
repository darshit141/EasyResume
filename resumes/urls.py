from django.urls import path

from resumes.views.resume_upload_view import ResumeUploadAPIView
from resumes.views.resume_view import ResumeViewAPIView
from resumes.views.resume_edit_view import ResumeEditAPIView
from resumes.views.resume_version_view import ResumeVersionAPIView
from resumes.views.resume_version_detail_view import (
    ResumeVersionDetailAPIView,
)

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
    path(
        "versions/",
        ResumeVersionAPIView.as_view(),
        name="resume-version-history",
    ),
     path(
        "versions/<int:version_id>/",
        ResumeVersionDetailAPIView.as_view(),
        name="resume-version-detail",
    ),
]