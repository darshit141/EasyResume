class ATSScorer:
    """
    Utility class for calculating ATS score
    from structured resume data.
    """

    @staticmethod
    def calculate_score(parsed_data):
        score = 0

        # Contact Information
        if parsed_data.get("email") or parsed_data.get("phone"):
            score += 20

        # Professional Summary
        if parsed_data.get("summary"):
            score += 15

        # Skills
        if parsed_data.get("skills"):
            score += 20

        # Education
        if parsed_data.get("education"):
            score += 15

        # Experience
        if parsed_data.get("experience"):
            score += 20

        # Projects
        if parsed_data.get("projects"):
            score += 10

        return score