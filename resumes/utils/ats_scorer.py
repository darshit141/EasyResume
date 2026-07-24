class ATSScorer:
    """
    Utility class for calculating ATS score.
    """

    @staticmethod
    def calculate_score(text):
        score = 0

        text = text.lower()

        if "phone" in text or "email" in text:
            score += 20

        if "professional summary" in text:
            score += 15

        if "skills" in text:
            score += 20

        if "education" in text:
            score += 15

        if "experience" in text:
            score += 20

        if "projects" in text:
            score += 10

        return score