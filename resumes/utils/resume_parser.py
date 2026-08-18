from resumes.services.resume_parser_service import ResumeParserService
from resumes.services.standard_resume_parser_service import StandardResumeParserService


class ResumeParser:
    """
    Backwards-compatible facade for the resume parser.

    Existing callers continue to use ``ResumeParser.parse(text)`` while the
    provider integration remains in the service layer. ``provider="standard"``
    routes to the deterministic, non-AI parser instead of an LLM provider.
    """

    @staticmethod
    def parse(text, provider=None):
        if provider == "standard":
            result = StandardResumeParserService().parse(text)
            return ResumeParserService._validate(result)
        return ResumeParserService(provider=provider).parse(text)
