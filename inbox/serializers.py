from rest_framework import serializers

from inbox.models import InboundEmail

PREVIEW_CHARS = 400


class InboundEmailSerializer(serializers.ModelSerializer):
    curation_title = serializers.SerializerMethodField()
    locked = serializers.SerializerMethodField()
    locked_curation_title = serializers.SerializerMethodField()
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    preview = serializers.SerializerMethodField()
    can_undo = serializers.SerializerMethodField()

    class Meta:
        model = InboundEmail
        fields = [
            "id", "route", "from_name", "from_address", "subject", "preview", "sent_at", "received_at",
            "attachment_names", "authenticated", "curation", "curation_title", "match_method",
            "locked", "locked_curation_title", "category", "category_label", "category_confidence",
            "summary", "interview_at", "meeting_link", "suggested_status", "status_before",
            "status_applied_at", "status_applied_automatically", "status_undone_at", "can_undo",
            "forwarded_at", "state",
        ]
        read_only_fields = fields

    def get_curation_title(self, obj):
        return obj.curation.job_title if obj.curation_id else ""

    def get_locked(self, obj):
        return obj.locked_curation_id is not None and obj.curation_id is None

    def get_locked_curation_title(self, obj):
        return obj.locked_curation.job_title if obj.locked_curation_id else ""

    def get_preview(self, obj):
        text = " ".join((obj.text_body or "").split())
        return text[:PREVIEW_CHARS]

    def get_can_undo(self, obj):
        return bool(
            obj.status_applied_at
            and not obj.status_undone_at
            and obj.curation_id
            and obj.curation.application_status == obj.suggested_status
        )


class InboundEmailDetailSerializer(InboundEmailSerializer):
    class Meta(InboundEmailSerializer.Meta):
        fields = InboundEmailSerializer.Meta.fields + ["text_body"]
        read_only_fields = fields


class AssignSerializer(serializers.Serializer):
    curation_id = serializers.IntegerField(allow_null=True)
