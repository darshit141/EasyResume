"""The interview the guided resume builder runs.

Config, not code: one list of sections, each with its questions. The
frontend renders questions by `type`; the backend
(`GuidedResumeService.assemble`) reads the collected answers back out by
`maps_to`. Branching is declarative — a question with `show_if` only
applies when a prior answer matches.

Keep this list the single source of truth for both ends. Adding a
question here is all it takes for it to appear in the flow.
"""

# Every situation the opening question offers. Drives `show_if` branching.
SITUATIONS = [
    {"value": "student", "label": "Still studying"},
    {"value": "recent_graduate", "label": "Recently graduated, looking for my first role"},
    {"value": "first_job", "label": "Have some work but never made a proper resume"},
    {"value": "career_changer", "label": "Changing to a different field"},
    {"value": "experienced", "label": "Experienced, just need a clean resume"},
]

# Sections that get an AI drafting step (answers -> {header, bullets}
# entries the user reviews). Everything else is assembled verbatim.
DRAFTED_SECTIONS = ("experience", "projects", "summary")

SECTIONS = [
    {
        "id": "target",
        "title": "What are you aiming for?",
        "subtitle": "This shapes the questions and the wording we use.",
        "kind": "form",
        "questions": [
            {
                "id": "target_role",
                "prompt": "Target role or job title",
                "help": "A best guess is fine — you can change it later.",
                "type": "text",
                "placeholder": "e.g. Junior Software Engineer, Data Analyst, Marketing Assistant",
                "required": True,
                "suggestions": [
                    "Software Engineer", "Data Analyst", "Marketing Assistant",
                    "Business Analyst", "Customer Support", "Sales Associate",
                    "Mechanical Engineer", "Accountant", "Graphic Designer",
                ],
            },
            {
                "id": "situation",
                "prompt": "Which best describes you right now?",
                "type": "single_choice",
                "required": True,
                "choices": SITUATIONS,
            },
        ],
    },
    {
        "id": "basics",
        "title": "The basics",
        "subtitle": "Contact details that go at the top of the resume.",
        "kind": "form",
        "questions": [
            {"id": "full_name", "prompt": "Full name", "type": "text", "required": True, "maps_to": "personal_information.name"},
            {"id": "email", "prompt": "Email", "type": "email", "required": True, "maps_to": "personal_information.email"},
            {"id": "phone", "prompt": "Phone", "type": "text", "required": False, "maps_to": "personal_information.phone"},
            {"id": "location", "prompt": "Location", "type": "text", "required": False, "placeholder": "City, Country"},
            {"id": "links", "prompt": "LinkedIn / portfolio / GitHub", "type": "text", "required": False, "placeholder": "One link per line", "multiline": True},
        ],
    },
    {
        "id": "experience",
        "title": "Work experience",
        "subtitle": "Jobs, internships, part-time work, volunteering — anything where you did real work. Skip if you have none yet.",
        "kind": "repeatable",
        "add_label": "Add a role",
        "optional": True,
        "item_questions": [
            {"id": "title", "prompt": "Your role / title", "type": "text", "required": True},
            {"id": "org", "prompt": "Company or organisation", "type": "text", "required": True},
            {"id": "employment_type", "prompt": "Type", "type": "single_choice", "required": False, "choices": [
                {"value": "full_time", "label": "Full-time"}, {"value": "part_time", "label": "Part-time"},
                {"value": "internship", "label": "Internship"}, {"value": "contract", "label": "Contract / freelance"},
                {"value": "volunteer", "label": "Volunteer"},
            ]},
            {"id": "dates", "prompt": "When", "type": "text", "required": False, "placeholder": "e.g. Jun 2023 – Aug 2023, or 2021 – Present"},
            {"id": "day_to_day", "prompt": "What did you do day to day?", "help": "Plain words are fine — \"I answered customer emails and updated the tracking sheet\".", "type": "textarea", "required": True},
            {"id": "achievements", "prompt": "Anything you improved, built, fixed, organised, or were trusted with?", "type": "textarea", "required": False},
            {"id": "metric", "prompt": "Any number attached to it?", "help": "Time saved, people helped, sales, error rate, volume, team size — a rough number is fine.", "type": "text", "required": False},
            {"id": "tools", "prompt": "Tools, software or methods you actually used", "type": "tags", "required": False},
        ],
    },
    {
        "id": "education",
        "title": "Education",
        "subtitle": "Degrees, diplomas, bootcamps, relevant courses.",
        "kind": "repeatable",
        "add_label": "Add education",
        "item_questions": [
            {"id": "qualification", "prompt": "Qualification", "type": "text", "required": True, "placeholder": "e.g. B.Sc. Computer Science"},
            {"id": "institution", "prompt": "Institution", "type": "text", "required": True},
            {"id": "dates", "prompt": "Years", "type": "text", "required": False, "placeholder": "e.g. 2020 – 2024"},
            {"id": "status", "prompt": "Status", "type": "single_choice", "required": False, "choices": [
                {"value": "completed", "label": "Completed"},
                {"value": "in_progress", "label": "In progress"},
            ]},
            {"id": "highlights", "prompt": "Grade, honours, relevant coursework or activities (optional)", "type": "textarea", "required": False},
        ],
    },
    {
        "id": "projects",
        "title": "Projects",
        "subtitle": "Coursework, personal builds, competitions, freelance pieces — proof of what you can do. Especially important if your work history is short.",
        "kind": "repeatable",
        "add_label": "Add a project",
        "optional": True,
        "item_questions": [
            {"id": "name", "prompt": "Project name", "type": "text", "required": True},
            {"id": "what", "prompt": "What is it and what did you build/do?", "type": "textarea", "required": True},
            {"id": "outcome", "prompt": "Result or what you learned", "type": "textarea", "required": False},
            {"id": "tools", "prompt": "Tools / tech used", "type": "tags", "required": False},
            {"id": "link", "prompt": "Link (optional)", "type": "text", "required": False},
        ],
    },
    {
        "id": "skills",
        "title": "Skills",
        "subtitle": "What you can actually do. We'll tidy the naming.",
        "kind": "form",
        "questions": [
            {"id": "skills", "prompt": "Skills", "help": "Add technical and non-technical: languages, tools, software, plus things like \"customer communication\" or \"data entry\".", "type": "tags", "required": True, "maps_to": "skills"},
            {"id": "languages", "prompt": "Spoken languages (optional)", "type": "tags", "required": False, "maps_to": "languages"},
            {"id": "certifications", "prompt": "Certifications or licences (optional)", "type": "tags", "required": False},
        ],
    },
    {
        "id": "summary",
        "title": "A short summary",
        "subtitle": "Two or three sentences at the top. Answer plainly and we'll write it.",
        "kind": "form",
        "questions": [
            {"id": "who_you_are", "prompt": "In one line, who are you professionally?", "type": "text", "required": True, "placeholder": "e.g. Final-year CS student focused on backend development"},
            {"id": "strengths", "prompt": "Your two or three biggest strengths for this role", "type": "textarea", "required": False},
            {"id": "goal", "prompt": "What kind of role or team are you hoping to join?", "type": "text", "required": False},
        ],
    },
]


def section_by_id(section_id):
    for section in SECTIONS:
        if section["id"] == section_id:
            return section
    return None


def flow_for(situation=None):
    """The section list to run. `situation` is reserved for future
    section-level branching; today every section is shown and individual
    questions self-gate via `show_if` on the client."""
    return SECTIONS
