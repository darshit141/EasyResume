from rest_framework import serializers

from ai_jobs.models import AIJob


class AIJobSerializer(serializers.ModelSerializer):
    """Status-polling representation. Never carries prompt/response content —
    only enough to know what happened and where to fetch the real result."""

    result = serializers.SerializerMethodField()

    class Meta:
        model = AIJob
        fields = [
            "id",
            "job_type",
            "status",
            "provider",
            "curation_id",
            "resume_id",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "result",
        ]
        read_only_fields = fields

    def get_result(self, job):
        """Where the frontend should fetch the actual output from, once the
        job is done. For most job types the AI output lives on the existing
        Resume/JobResumeCuration row and this just points at it; a few job
        types (see AIJob.output_data) have no standalone GET endpoint for
        their full response bundle, so that bundle is returned inline."""
        if job.status != AIJob.STATUS_COMPLETED:
            return None
        if job.output_data:
            return job.output_data
        if job.curation_id:
            return {"curation_id": job.curation_id}
        if job.resume_id:
            return {"resume_id": job.resume_id}
        return None
