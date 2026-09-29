"""The image slots on the public site that an admin can change.

Each slot is one picture in one place. Its default is one of the bundled
illustrations the frontend ships (see intelliresume-react src/lib/scenes.js);
the admin can swap in another bundled one, upload their own, or hide it.
Names here must match the frontend's, which is what renders them.
"""

# The rendered scenes, usable in any slot.
SCENES = (
    "full-loop",
    "four-steps",
    "before-after",
    "facts-intact",
    "quiet-studio",
    "signup-panel",
    "guided-builder",
    "goals-to-role",
    "experience-to-resume",
    "pipeline-wide",
    "career-growth",
)

# Only the homepage hero can show these: the animated match card and the
# line drawings that follow the light/dark theme.
HERO_ONLY = ("example", "hero-assembly", "hero-evolution", "hero-workspace")

# The hero setting predates slots and stored the full-loop scene under this name.
LEGACY_ALIASES = {"hero-full-loop": "full-loop"}

BUILTIN_LABELS = {
    "full-loop": "Studio: profile + job into a matched resume",
    "four-steps": "Four steps: upload, add the job, analyse, finish",
    "before-after": "Before and after: a line rewritten",
    "facts-intact": "Your facts, sharpened",
    "quiet-studio": "Finished resume with checks and templates",
    "signup-panel": "Experience + job into a matched resume",
    "guided-builder": "Guided questions into a resume",
    "goals-to-role": "Goals matched to a target role",
    "experience-to-resume": "Notes into a structured resume",
    "pipeline-wide": "Wide pipeline: profile + job through analysis",
    "career-growth": "Career growth with a learning roadmap",
    "example": "Animated example match (interactive)",
    "hero-assembly": "Line drawing: assembly",
    "hero-evolution": "Line drawing: notes to tailored resume",
    "hero-workspace": "Line drawing: the resume editor",
}

SLOTS = {
    "home_hero": {"label": "Beside the headline", "page": "Home", "default": "full-loop", "extra": HERO_ONLY},
    "home_journey": {"label": "How your resume comes together", "page": "Home", "default": "experience-to-resume"},
    "home_start": {"label": "Start-building section", "page": "Home", "default": "guided-builder"},
    "home_cta": {"label": "Closing call-to-action background", "page": "Home", "default": "pipeline-wide"},
    "how_it_works_header": {"label": "Page header", "page": "How it works", "default": "four-steps"},
    "features_header": {"label": "Page header", "page": "Features", "default": "before-after"},
    "about_header": {"label": "Page header", "page": "About", "default": "career-growth"},
    "coaches_header": {"label": "Page header", "page": "For career coaches", "default": "facts-intact"},
    "upload_header": {"label": "Page header", "page": "Upload a resume", "default": "pipeline-wide"},
    "pricing_every_plan": {"label": "Every-plan section", "page": "Pricing", "default": "quiet-studio"},
    "login_showcase": {"label": "Side panel", "page": "Sign in", "default": "goals-to-role"},
    "signup_showcase": {"label": "Side panel", "page": "Sign up", "default": "signup-panel"},
}


def builtins_for(slot):
    return SCENES + SLOTS[slot].get("extra", ())


def normalise_builtin(name):
    name = (name or "").strip()
    return LEGACY_ALIASES.get(name, name)
