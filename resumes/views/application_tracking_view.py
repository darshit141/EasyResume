from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.models import JobResumeCuration
from resumes.serializers.application_tracking_serializer import (
    AutofillTrackRequestSerializer,
    TrackedApplicationSerializer,
)
from resumes.services.application_tracking_service import ApplicationTrackingService


@extend_schema(
    tags=["Application tracking"],
    summary="Record that the extension filled an application form",
    description=(
        "Called by the browser extension the moment it fills a form, so an "
        "application made on someone else's careers site still appears in "
        "the tracker.\n\n"
        "The row is created with `status: saved` — the job is recorded, but "
        "no AI analysis is run, because none was asked for and it would "
        "spend a credit.\n\n"
        "`application_status` stays `saved`: filling a form is not applying, "
        "and plenty of forms are abandoned half-done. It moves to `applied` "
        "only when the extension reports a real submit event (see "
        "`.../submitted/`) or the user confirms it.\n\n"
        "Filling the same posting again updates the existing row rather than "
        "adding a second one; tracking links are compared with campaign "
        "parameters stripped, so the same job reached from an email and from "
        "a search does not become two applications."
    ),
    request=AutofillTrackRequestSerializer,
    responses={
        status.HTTP_200_OK: TrackedApplicationSerializer,
        status.HTTP_201_CREATED: TrackedApplicationSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="A job title is required."),
    },
)
class AutofillTrackAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AutofillTrackRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            curation, created = ApplicationTrackingService.record_autofill(
                request.user,
                job_url=data["job_url"],
                job_title=data["job_title"],
                job_description=data["job_description"],
                company=data["company"],
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            TrackedApplicationSerializer(curation).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Application tracking"],
    summary="Record that the filled form was actually submitted",
    description=(
        "Called when the extension observes a real submit event on a form it "
        "filled. This is the only automatic route to `application_status: "
        "applied` — the system never infers that an application was sent "
        "from the fact that it filled one in.\n\n"
        "`submit_detected_at` is what we observed and is kept separate from "
        "`applied_at`, which is the user's own record, so a wrong detection "
        "can be corrected without erasing the evidence.\n\n"
        "Idempotent, and it never moves an application backwards: a curation "
        "already at `interviewing` or `rejected` keeps that status."
    ),
    request=None,
    responses={
        status.HTTP_200_OK: TrackedApplicationSerializer,
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No such application for this user."),
    },
)
class AutofillSubmittedAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, curation_id):
        curation = JobResumeCuration.objects.filter(id=curation_id, user=request.user).first()
        if curation is None:
            raise Http404("Application not found.")

        ApplicationTrackingService.record_submission(curation)
        return Response(TrackedApplicationSerializer(curation).data)


@extend_schema(
    tags=["Application tracking"],
    summary="The user answered 'no, I didn't apply'",
    description=(
        "Takes this job out of the list the extension asks about, without "
        "claiming anything happened. Nothing is deleted and no status "
        "changes — a job someone opened and decided against is still worth "
        "having in their tracker, and recording 'not applied' as an outcome "
        "would be inventing one."
    ),
    request=None,
    responses={
        status.HTTP_200_OK: TrackedApplicationSerializer,
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No such application for this user."),
    },
)
class AutofillDismissAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, curation_id):
        curation = JobResumeCuration.objects.filter(id=curation_id, user=request.user).first()
        if curation is None:
            raise Http404("Application not found.")

        ApplicationTrackingService.dismiss_prompt(curation)
        return Response(TrackedApplicationSerializer(curation).data)


@extend_schema(
    tags=["Application tracking"],
    summary="Fills we never saw submitted — the ones worth asking about",
    description=(
        "Applications the extension filled in but never observed being "
        "submitted, newest first, from the last 14 days.\n\n"
        "Anything we detected, and anything the user has already filed "
        "themselves, is left out. Asking someone to confirm what the system "
        "already knows is the quickest way to make them stop answering, and "
        "the answers are only worth collecting while they can still remember "
        "the tab.\n\n"
        "Confirm one with `PATCH /api/resumes/job-curations/{id}/application/`."
    ),
    responses={status.HTTP_200_OK: TrackedApplicationSerializer(many=True)},
)
class PendingApplicationsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        pending = ApplicationTrackingService.pending_confirmations(request.user)
        return Response(TrackedApplicationSerializer(pending, many=True).data)
