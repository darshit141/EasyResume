import re


class ResumeParser:
    """
    Utility to convert extracted resume text into structured JSON.
    """

    @staticmethod
    def parse(text):
        return {
            "name": ResumeParser.extract_name(text),
            "email": ResumeParser.extract_email(text),
            "phone": ResumeParser.extract_phone(text),
            "skills": ResumeParser.extract_skills(text),
            "education": ResumeParser.extract_education(text),
            "experience": ResumeParser.extract_experience(text),
            "projects": ResumeParser.extract_projects(text),
        }

    @staticmethod
    def extract_name(text):
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        for line in lines[:5]:
            if (
                "@" not in line
                and not re.search(r"\d", line)
                and len(line.split()) <= 4
            ):
                return line

        return ""

    @staticmethod
    def extract_email(text):
        email = re.search(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            text,
        )
        return email.group() if email else ""

    @staticmethod
    def extract_phone(text):
        phone = re.search(
            r"\+?\d[\d\s\-()]{8,}\d",
            text,
        )
        return phone.group() if phone else ""

    @staticmethod
    def extract_skills(text):
        return ResumeParser.extract_section(
            text,
            [
                "technical skills",
                "skills",
                "key skills",
            ],
        )

    @staticmethod
    def extract_education(text):
        return ResumeParser.extract_section(
            text,
            [
                "education",
                "academic qualification",
                "qualifications",
            ],
        )

    @staticmethod
    def extract_experience(text):
        return ResumeParser.extract_section(
            text,
            [
                "professional experience",
                "work experience",
                "experience",
                "employment",
            ],
        )

    @staticmethod
    def extract_projects(text):
        return ResumeParser.extract_section(
            text,
            [
                "academic projects",
                "personal projects",
                "projects",
                "project",
            ],
        )

    @staticmethod
    def extract_section(text, headings):
        lines = text.splitlines()

        collecting = False
        section = []

        stop_headings = {
            "professional summary",
            "summary",
            "technical skills",
            "skills",
            "education",
            "professional experience",
            "work experience",
            "experience",
            "academic projects",
            "projects",
            "certifications",
            "languages",
            "references",
            "interests",
            "achievements",
            "objective",
            "profile",
        }

        for line in lines:
            current = line.strip()

            if not current:
                continue

            normalized = current.lower().strip().rstrip(":")

            # Start collecting only when the full heading matches
            if normalized in headings:
                collecting = True
                continue

            if collecting:
                # Stop only when another full heading is found
                if normalized in stop_headings:
                    break

                section.append(current)

        return section