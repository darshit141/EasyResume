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
    def parse(text, provider=None, user=None):
        if provider == "standard":
            # No AI call, so no key of anyone's is involved.
            result = StandardResumeParserService().parse(text)
            return ResumeParserService._validate(result)
        return ResumeParserService(provider=provider, user=user).parse(text)
