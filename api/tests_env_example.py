"""`.env.example` must list every environment variable the code reads.

A server's `.env` is built by copying this file. A variable missing from it
is a variable nobody sets in production, and it falls back to whatever the
code defaults to - which is how `RESEND_API_KEY`, `FRONTEND_URL` and the Kira
settings went unset: they worked in development, and nothing said the template
was missing them. This test makes that drift fail CI instead of a deploy.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# os.getenv("NAME", ...), os.environ.get("NAME"), os.environ["NAME"], and the
# Stripe price ids billing/plans.py passes by name to its _price() helper.
_ENV_READ = re.compile(
    r"""os\.(?:getenv|environ\.get)\(\s*["']([A-Z][A-Z0-9_]+)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]"""
    r"""|["'](STRIPE_PRICE_ID_[A-Z0-9_]+)["']"""
)
_SKIP_DIRS = {"venv", ".venv", ".claude", "node_modules", "migrations", "__pycache__", "media", "scratch"}


def variables_read_by_code(root):
    found = {}
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if set(relative.parts) & _SKIP_DIRS or path.name.startswith("tests"):
            continue
        for match in _ENV_READ.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            name = next(group for group in match.groups() if group)
            found.setdefault(name, str(relative))
    return found


def variables_in_example(example):
    # A commented-out `# NAME=` line still documents the variable: optional
    # settings are listed that way so copying the file doesn't switch them on.
    return set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", example.read_text(encoding="utf-8"), re.M))


class EnvExampleCompletenessTests(SimpleTestCase):
    def test_every_variable_the_code_reads_is_in_env_example(self):
        root = Path(settings.BASE_DIR)
        documented = variables_in_example(root / ".env.example")
        missing = {
            name: where for name, where in variables_read_by_code(root).items() if name not in documented
        }

        self.assertEqual(
            missing,
            {},
            "Add these to .env.example (with their default, or commented out if optional): "
            + ", ".join(f"{name} (read in {where})" for name, where in sorted(missing.items())),
        )

    def test_the_scan_actually_finds_variables(self):
        """Guards the guard: a broken pattern would find nothing and pass."""
        found = variables_read_by_code(Path(settings.BASE_DIR))
        for name in ("SECRET_KEY", "GEMINI_API_KEY", "FREE_AI_CALLS_PER_MONTH", "STRIPE_PRICE_ID_PRO_EUR_MONTHLY"):
            self.assertIn(name, found)
