"""LLM-backed resume parsing used after the existing file text extraction."""

from resumes.prompts import RESUME_JSON_SCHEMA, build_resume_extraction_prompt
from resumes.resume_content import ENTRY_SECTIONS, normalize_entry_list
from resumes.services.llm_service import LLMSchemaValidationError, get_llm_service


class ResumeParserService:
    """Validates untrusted LLM output before it enters business logic or storage."""

    personal_information_fields = ("name", "email", "phone")
    scalar_fields = ("name", "email", "phone", "summary")
    # Sections whose entries are {header, bullets} objects.
    entry_fields = ENTRY_SECTIONS
    # Sections that stay flat string lists.
    string_list_fields = ("skills", "achievements", "languages")
    collection_fields = entry_fields + string_list_fields

    def __init__(self, llm_service=None, provider=None, user=None):
        """``user`` is the account the work is being done for. It is used for
        one thing: if they have saved their own provider API key, the client
        built here uses it instead of the server's (see
        billing.services.user_ai_key_service). Left None — a management
        command, a health check — the server's own credentials are used, which
        is the behaviour this had before BYOK existed."""
        self.user = user
        self.llm_service = llm_service or get_llm_service(provider, user=user)

    def parse(self, text):
        result = self.llm_service.generate_structured(
            build_resume_extraction_prompt(text),
            RESUME_JSON_SCHEMA,
        )
        return self._validate(result)

    @classmethod
    def _validate(cls, result):
        if not isinstance(result, dict):
            raise LLMSchemaValidationError()

        expected = {"personal_information", *cls.scalar_fields, *cls.collection_fields}
        if set(result) != expected:
            raise LLMSchemaValidationError()

        validated = {}
        personal_information = result["personal_information"]
        if not isinstance(personal_information, dict) or set(personal_information) != set(cls.personal_information_fields):
            raise LLMSchemaValidationError()
        if any(
            value is not None and not isinstance(value, str)
            for value in personal_information.values()
        ):
            raise LLMSchemaValidationError()
        validated["personal_information"] = personal_information

        for field in cls.scalar_fields:
            value = result[field]
            if value is not None and not isinstance(value, str):
                raise LLMSchemaValidationError()
            validated[field] = value

        for field in cls.string_list_fields:
            value = result[field]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise LLMSchemaValidationError()
            validated[field] = [item.strip() for item in value if item.strip()]

        for field in cls.entry_fields:
            value = result[field]
            if not isinstance(value, list):
                raise LLMSchemaValidationError()
            # Every item must be something an entry can be built from — a
            # string (tolerated) or a {header, bullets} object. Anything
            # else is the model ignoring the schema, which is a real fault.
            if not all(isinstance(item, (str, dict)) for item in value):
                raise LLMSchemaValidationError()
            for item in value:
                if isinstance(item, dict) and not ({"header", "bullets"} & set(item)):
                    raise LLMSchemaValidationError()
            validated[field] = normalize_entry_list(value)

        return validated
