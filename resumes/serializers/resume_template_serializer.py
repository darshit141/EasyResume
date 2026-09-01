from rest_framework import serializers

from resumes.resume_template import (
    ACCENT_PALETTE,
    DENSITIES,
    FONT_FAMILIES,
    HEADING_STYLES,
    NAME_ALIGNMENTS,
    SECTION_KEYS,
    TEMPLATE_PRESETS,
)


class TemplateAtsReportSerializer(serializers.Serializer):
    safe = serializers.BooleanField()
    warnings = serializers.ListField(child=serializers.CharField())


class ResolvedTemplateConfigSerializer(serializers.Serializer):
    """The fully-resolved layout config every renderer consumes. Read-only —
    it is always produced by resume_template.resolve_template, never accepted
    raw from a client."""

    template_id = serializers.CharField()
    section_order = serializers.ListField(child=serializers.CharField())
    hidden_sections = serializers.ListField(child=serializers.CharField())
    heading_style = serializers.CharField()
    accent_color = serializers.CharField()
    font_family = serializers.CharField()
    name_alignment = serializers.CharField()
    density = serializers.CharField()
    columns = serializers.IntegerField()


class ResumeTemplatePresetSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    config = ResolvedTemplateConfigSerializer()
    ats = TemplateAtsReportSerializer()


class TemplateOverridesSerializer(serializers.Serializer):
    """The subset of layout knobs a user may override on top of a preset.
    All optional; each is validated against its allowed values so a bad
    payload is a 400 here rather than being silently clamped later."""

    heading_style = serializers.ChoiceField(choices=HEADING_STYLES, required=False)
    accent_color = serializers.ChoiceField(choices=ACCENT_PALETTE, required=False)
    font_family = serializers.ChoiceField(choices=FONT_FAMILIES, required=False)
    name_alignment = serializers.ChoiceField(choices=NAME_ALIGNMENTS, required=False)
    density = serializers.ChoiceField(choices=DENSITIES, required=False)
    columns = serializers.ChoiceField(choices=[1, 2], required=False)
    section_order = serializers.ListField(
        child=serializers.ChoiceField(choices=SECTION_KEYS),
        required=False,
    )
    hidden_sections = serializers.ListField(
        child=serializers.ChoiceField(choices=SECTION_KEYS),
        required=False,
    )


class ResumeTemplatePreferenceSerializer(serializers.Serializer):
    """GET: the user's current choice, resolved config and ATS report.
    PUT: ``template_id`` (required) and optional ``overrides``."""

    template_id = serializers.ChoiceField(choices=list(TEMPLATE_PRESETS.keys()))
    overrides = TemplateOverridesSerializer(required=False)
    # Read-only echoes so a PUT response, like a GET, carries everything the
    # UI needs to re-render without a second request.
    config = ResolvedTemplateConfigSerializer(read_only=True)
    ats = TemplateAtsReportSerializer(read_only=True)
