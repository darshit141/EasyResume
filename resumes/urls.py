from django.urls import path

from resumes.views.resume_upload_view import ResumeUploadAPIView
from resumes.views.resume_view import ResumeViewAPIView
from resumes.views.resume_edit_view import ResumeEditAPIView
from resumes.views.resume_list_view import ResumeListAPIView
from resumes.views.resume_detail_view import ResumeDetailAPIView
from resumes.views.outcome_record_view import RecordOutcomeAPIView
from resumes.views.resume_version_view import ResumeVersionAPIView
from resumes.views.resume_version_detail_view import (
    ResumeVersionDetailAPIView,
)
from resumes.views.job_curation_view import (
    JobCurationCreateAPIView,
    JobCurationDetailAPIView,
    JobCurationListAPIView,
    JobGapAnalysisAPIView,
    JobUrlPreviewAPIView,
)
from resumes.views.resume_curation_view import (
    JobResumeCurationGenerateAPIView,
    TailoredResumeDocxExportAPIView,
    TailoredResumePdfExportAPIView,
)
from resumes.views.cover_letter_view import (
    ApplicationStatusAPIView,
    CoverLetterGenerateAPIView,
)
from resumes.views.missing_information_view import MissingInformationReviewAPIView
from resumes.views.resume_template_view import (
    ResumeTemplateListAPIView,
    ResumeTemplatePreferenceAPIView,
)
from resumes.views.tailored_resume_version_view import (
    TailoredResumeVersionDetailAPIView,
    TailoredResumeVersionListAPIView,
)
from resumes.views.autofill_profile_view import AutofillProfileAPIView
from resumes.views.job_application_view import (
    JobApplicationDetailAPIView,
    JobApplicationDraftAPIView,
    JobApplicationListAPIView,
    JobApplicationSendAPIView,
)
from resumes.views.guided_resume_view import (
    GuidedResumeAcceptAPIView,
    GuidedResumeAnswersAPIView,
    GuidedResumeDraftAPIView,
    GuidedResumeFinalizeAPIView,
    GuidedResumePolishAPIView,
    GuidedResumeSessionAPIView,
)

urlpatterns = [
    path(
        "autofill-profile/",
        AutofillProfileAPIView.as_view(),
        name="autofill-profile",
    ),
    path(
        "applications/",
        JobApplicationListAPIView.as_view(),
        name="job-application-list",
    ),
    path(
        "applications/<int:application_id>/",
        JobApplicationDetailAPIView.as_view(),
        name="job-application-detail",
    ),
    path(
        "applications/<int:application_id>/send/",
        JobApplicationSendAPIView.as_view(),
        name="job-application-send",
    ),
    path("guided/", GuidedResumeSessionAPIView.as_view(), name="guided-resume-session"),
    path("guided/answers/", GuidedResumeAnswersAPIView.as_view(), name="guided-resume-answers"),
    path("guided/draft/", GuidedResumeDraftAPIView.as_view(), name="guided-resume-draft"),
    path("guided/accept/", GuidedResumeAcceptAPIView.as_view(), name="guided-resume-accept"),
    path("guided/polish/", GuidedResumePolishAPIView.as_view(), name="guided-resume-polish"),
    path("guided/finalize/", GuidedResumeFinalizeAPIView.as_view(), name="guided-resume-finalize"),
    path(
        "outcomes/record/",
        RecordOutcomeAPIView.as_view(),
        name="record-application-outcome",
    ),
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
        "list/",
        ResumeListAPIView.as_view(),
        name="resume-list",
    ),
    path(
        "<int:resume_id>/",
        ResumeDetailAPIView.as_view(),
        name="resume-detail",
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
    path(
        "templates/",
        ResumeTemplateListAPIView.as_view(),
        name="resume-template-list",
    ),
    path(
        "template-preference/",
        ResumeTemplatePreferenceAPIView.as_view(),
        name="resume-template-preference",
    ),
    path(
        "job-curations/",
        JobCurationListAPIView.as_view(),
        name="job-curation-list",
    ),
    path(
        "job-curations/analyze/",
        JobCurationCreateAPIView.as_view(),
        name="job-curation-analyze",
    ),
    path(
        "job-curations/import-url/",
        JobUrlPreviewAPIView.as_view(),
        name="job-curation-import-url",
    ),
    path(
        "job-curations/<int:curation_id>/",
        JobCurationDetailAPIView.as_view(),
        name="job-curation-detail",
    ),
    path(
        "job-curations/<int:curation_id>/gap-analysis/",
        JobGapAnalysisAPIView.as_view(),
        name="job-curation-gap-analysis",
    ),
    path(
        "job-curations/<int:curation_id>/tailored-resume/",
        JobResumeCurationGenerateAPIView.as_view(),
        name="job-curation-tailored-resume",
    ),
    path(
        "job-curations/<int:curation_id>/tailored-resume/export/docx/",
        TailoredResumeDocxExportAPIView.as_view(),
        name="job-curation-tailored-resume-export-docx",
    ),
    path(
        "job-curations/<int:curation_id>/tailored-resume/export/pdf/",
        TailoredResumePdfExportAPIView.as_view(),
        name="job-curation-tailored-resume-export-pdf",
    ),
    path(
        "job-curations/<int:curation_id>/missing-information/",
        MissingInformationReviewAPIView.as_view(),
        name="job-curation-missing-information",
    ),
    path(
        "job-curations/<int:curation_id>/cover-letter/",
        CoverLetterGenerateAPIView.as_view(),
        name="job-curation-cover-letter",
    ),
    path(
        "job-curations/<int:curation_id>/application/",
        ApplicationStatusAPIView.as_view(),
        name="job-curation-application-status",
    ),
    path(
        "job-curations/<int:curation_id>/apply/draft/",
        JobApplicationDraftAPIView.as_view(),
        name="job-curation-apply-draft",
    ),
    path(
        "job-curations/<int:curation_id>/tailored-resume/versions/",
        TailoredResumeVersionListAPIView.as_view(),
        name="job-curation-tailored-resume-versions",
    ),
    path(
        "job-curations/<int:curation_id>/tailored-resume/versions/<int:version_id>/",
        TailoredResumeVersionDetailAPIView.as_view(),
        name="job-curation-tailored-resume-version-detail",
    ),
]
