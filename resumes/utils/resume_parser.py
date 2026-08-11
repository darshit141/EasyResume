from resumes.services.resume_parser_service import ResumeParserService


class ResumeParser:
    """
    Backwards-compatible facade for the LLM-backed resume parser.

    Existing callers continue to use ``ResumeParser.parse(text)`` while the
    provider integration remains in the service layer.
    """

    @staticmethod
    def parse(text):
        return ResumeParserService().parse(text)
