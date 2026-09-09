"""Tests for the application-form autofill profile.

The rule these defend: a field we cannot source comes back **empty**, never
inferred. The extension leaves empty fields alone, so a blank costs the
user a moment's typing — while a guess gets submitted to an employer under
their name. Most of these are about refusing to fill something in.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import User
from resumes.models import Resume
from resumes.services.autofill_profile_service import AutofillProfileService

URL = "/api/resumes/autofill-profile/"

PARSED = {
    "personal_information": {
        "name": "Priya Sharma",
        "email": "priya.sharma@example.com",
        "phone": "+91 90000 11111",
    },
    "summary": "Data analyst. Portfolio at https://priya.dev and github.com/priyas",
    "skills": ["SQL", "Power BI", "Python"],
    "experience": [
        {"header": "Data Analyst Intern | BrightRetail Pvt Ltd | Jan 2025 - Jun 2025", "bullets": []}
    ],
    "education": [{"header": "B.Sc. Statistics | Pune University | 2021-2024", "bullets": []}],
}


class AutofillDerivationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Priya Sharma", email="account@example.com", phone="000", password="pw"
        )

    def build(self, parsed=None, resume=True):
        stub = type("R", (), {"parsed_data": parsed if parsed is not None else PARSED})()
        return AutofillProfileService.build(self.user, stub if resume else None)

    # -- the fields a form actually asks for -----------------------------

    def test_name_is_split_into_first_and_last(self):
        p = self.build()

        self.assertEqual(p["first_name"], "Priya")
        self.assertEqual(p["last_name"], "Sharma")

    def test_a_single_word_name_leaves_the_last_name_empty(self):
        p = self.build({"personal_information": {"name": "Prince"}})

        self.assertEqual(p["first_name"], "Prince")
        self.assertEqual(p["last_name"], "")

    def test_multi_word_surnames_are_kept_whole(self):
        p = self.build({"personal_information": {"name": "Ana Maria da Silva"}})

        self.assertEqual(p["first_name"], "Ana")
        self.assertEqual(p["last_name"], "Maria da Silva")

    def test_resume_contact_details_win_over_the_account(self):
        """The employer reads the resume; the two must agree."""
        p = self.build()

        self.assertEqual(p["email"], "priya.sharma@example.com")
        self.assertEqual(p["phone"], "+91 90000 11111")

    def test_account_details_are_the_fallback_with_no_resume(self):
        p = self.build(resume=False)

        self.assertEqual(p["email"], "account@example.com")
        self.assertFalse(p["has_resume"])

    # -- header parsing --------------------------------------------------

    def test_current_role_and_employer_come_from_the_latest_experience(self):
        p = self.build()

        self.assertEqual(p["current_title"], "Data Analyst Intern")
        self.assertEqual(p["current_company"], "BrightRetail Pvt Ltd")

    def test_dates_are_never_mistaken_for_the_employer(self):
        p = self.build()

        self.assertNotIn("2025", p["current_company"])
        self.assertNotIn("2024", p["institution"])

    def test_the_at_style_header_is_understood(self):
        p = self.build(
            {"experience": [{"header": "Backend Engineer at Acme Corp (2019-2022)"}]}
        )

        self.assertEqual(p["current_title"], "Backend Engineer")
        self.assertEqual(p["current_company"], "Acme Corp")

    def test_the_em_dash_style_header_is_understood(self):
        p = self.build({"experience": [{"header": "Nurse — St Mary's Hospital · 2020-Present"}]})

        self.assertEqual(p["current_title"], "Nurse")
        self.assertEqual(p["current_company"], "St Mary's Hospital")

    def test_an_unsplittable_header_yields_a_title_and_no_invented_employer(self):
        p = self.build({"experience": [{"header": "Freelance work"}]})

        self.assertEqual(p["current_title"], "Freelance work")
        self.assertEqual(p["current_company"], "")

    def test_education_is_split_the_same_way(self):
        p = self.build()

        self.assertEqual(p["degree"], "B.Sc. Statistics")
        self.assertEqual(p["institution"], "Pune University")

    # -- links -----------------------------------------------------------

    def test_github_and_portfolio_are_found_anywhere_in_the_resume(self):
        p = self.build()

        self.assertEqual(p["github_url"], "https://github.com/priyas")
        self.assertEqual(p["website_url"], "https://priya.dev")

    def test_the_portfolio_field_never_returns_linkedin_or_github(self):
        """Those have their own fields on nearly every application form."""
        p = self.build(
            {"summary": "See linkedin.com/in/someone and github.com/someone"}
        )

        self.assertEqual(p["website_url"], "")
        self.assertEqual(p["linkedin_url"], "https://linkedin.com/in/someone")

    # -- the refusals ----------------------------------------------------

    def test_a_missing_location_is_empty_not_guessed(self):
        p = self.build()

        self.assertEqual(p["location"], "")

    def test_missing_links_are_empty(self):
        p = self.build({"personal_information": {"name": "No Links"}})

        self.assertEqual(p["linkedin_url"], "")
        self.assertEqual(p["github_url"], "")
        self.assertEqual(p["website_url"], "")

    def test_an_empty_resume_produces_empty_fields_not_errors(self):
        p = self.build({})

        self.assertEqual(p["current_title"], "")
        self.assertEqual(p["institution"], "")
        self.assertEqual(p["skills"], [])


class AutofillAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            name="Priya Sharma", email="p@example.com", phone="1", password="pw"
        )
        self.client = APIClient()

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_returns_the_profile_for_the_signed_in_user(self):
        Resume.objects.create(
            user=self.user, original_filename="r.pdf", status="completed", parsed_data=PARSED
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["first_name"], "Priya")
        self.assertEqual(response.data["current_company"], "BrightRetail Pvt Ltd")
        self.assertTrue(response.data["has_resume"])

    def test_works_before_any_resume_exists(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["has_resume"])
        self.assertEqual(response.data["email"], "p@example.com")
