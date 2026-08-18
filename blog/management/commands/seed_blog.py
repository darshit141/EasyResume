"""Seeds the blog with starter posts.

These are real, accurate articles about how resume screening actually works
— written to be useful on their own, not filler. They exist so the blog
isn't empty on launch (an empty blog is also a common reason ad networks
reject a site). Edit or replace them freely in the Django admin.

Idempotent: re-running updates existing posts by slug instead of duplicating.
"""

from django.core.management.base import BaseCommand

from blog.models import Post

POSTS = [
    {
        "slug": "what-an-ats-actually-does",
        "title": "What an ATS Actually Does (and What It Doesn't)",
        "excerpt": (
            "Applicant tracking systems are widely misunderstood. Here's what they really do "
            "with your resume, and which popular advice is simply wrong."
        ),
        "tags": "ats, resume tips",
        "seo_description": (
            "A plain explanation of how applicant tracking systems parse and rank resumes, "
            "and which common ATS advice is outdated or false."
        ),
        "body": """\
Most advice about applicant tracking systems is either outdated or invented. This is what
these systems actually do.

## An ATS is a database, not a judge

An applicant tracking system is fundamentally a place to store and organise applications.
Its main job is to take your submission, extract structured fields from it (name, contact
details, work history, education, skills), and make that searchable for a recruiter.

The critical thing to understand: **in most companies, no software auto-rejects you.** A
recruiter searches and filters, and a human decides. The "75% of resumes are rejected by
robots" figure that circulates online has no credible source behind it.

## What actually goes wrong

Where candidates genuinely lose out is **parsing failure**. If the system can't read your
resume properly, your experience never makes it into the searchable database, so you never
appear in the recruiter's results. You aren't rejected — you're invisible.

Parsing tends to break on:

- Text placed inside images, logos, or headers and footers
- Multi-column layouts, where reading order gets scrambled
- Tables used for layout, which can interleave unrelated content
- Unusual section headings the parser doesn't recognise ("My Journey" instead of "Experience")
- Fonts embedded in ways that produce garbled character extraction

## Advice you can safely ignore

**"Use white text to hide keywords."** This is keyword stuffing. It is visible in the
extracted text, recruiters do notice, and it gets applications discarded. Don't.

**"Never use a PDF."** Modern parsers handle text-based PDFs well. A PDF exported from a
word processor is fine. A PDF that is a *scan* of a printed page is not — that's an image
with no extractable text.

**"You need exactly one page."** Length norms depend on seniority and field. Relevance
matters more than page count.

## What actually helps

1. Use conventional section headings: Experience, Education, Skills, Projects.
2. Keep a single-column layout for the body of the resume.
3. Put contact details in the document body, not in the page header.
4. Mirror the language of the job description where it is genuinely true of you. If the
   posting says "CI/CD" and you have that experience, write "CI/CD" rather than only a
   tool name.
5. Quantify outcomes. "Reduced page load time by 40%" is both more persuasive to a human
   and richer in searchable terms than "Responsible for performance."

## How IntelliResume fits in

Our ATS readiness score checks the things above that are objectively checkable: whether
your contact details, summary, skills, experience, education, projects, certifications and
achievements are present and parseable, and whether your experience entries lead with
action verbs and include quantified results.

It is our own heuristic. It is not affiliated with Workday, Greenhouse, Taleo or any other
vendor, and it cannot tell you how a specific employer's system will treat you. What it
can tell you is whether your resume has the structural problems that make software
misread it.
""",
    },
    {
        "slug": "tailoring-your-resume-without-lying",
        "title": "Tailoring Your Resume to a Job Without Lying",
        "excerpt": (
            "Tailoring means re-emphasising what's already true, not inventing experience. "
            "Here's where the line sits, and how to work right up to it."
        ),
        "tags": "resume tips, job search",
        "seo_description": (
            "How to tailor a resume to a specific job description honestly: reframing, "
            "reordering and vocabulary matching, without fabricating experience."
        ),
        "body": """\
"Tailor your resume for every application" is good advice that is rarely explained. Left
vague, it pushes people toward quietly inventing things. Here's the honest version.

## The line

Tailoring is **changing emphasis, ordering, and vocabulary**. It is not changing facts.

Legitimate:

- Moving the most relevant role or project to the top of its section
- Expanding the bullet points that match the job, and trimming those that don't
- Using the employer's term for something you have genuinely done
- Writing a summary aimed at this specific role

Not legitimate:

- Adding a skill you don't have because it's in the posting
- Inflating a job title
- Stretching employment dates
- Claiming ownership of work you supported but did not do

The second list isn't just an ethics problem. It's a practical one: it produces interviews
you cannot survive, and in many places it is grounds for dismissal after hiring.

## A worked example

Job posting asks for: *"experience improving reliability of production services, on-call
ownership, and working with observability tooling."*

Your existing bullet:

> Worked on the payments team maintaining backend services.

That's true but invisible to both a search and a reader. If, in reality, you were on the
on-call rotation and you cut the alert volume, the honest tailored version is:

> Owned on-call rotation for payments backend services; reduced weekly paging volume 60%
> by fixing alert thresholds and instrumenting request latency in Grafana.

Nothing was invented. The same work is described with the specificity the employer asked
for. This is what good tailoring looks like.

## Where the value really comes from

Most people's resumes are under-specified rather than dishonest. They describe
responsibilities instead of outcomes. Tailoring forces you to go back and ask "what
actually happened, and what changed because I was there?" — and that question improves the
resume for every application, not just this one.

## How we approach it

IntelliResume generates tailored versions strictly from your verified profile, and is
designed to reject generated content that can't be traced back to information you supplied
yourself. If the job wants something you haven't recorded, we tell you it's a gap — we
don't quietly write it in.

That's a deliberate constraint. A tool that invents plausible experience is not helping
you; it's writing a resume you'll have to defend in an interview.
""",
    },
    {
        "slug": "resume-mistakes-that-cost-interviews",
        "title": "Seven Resume Mistakes That Quietly Cost You Interviews",
        "excerpt": (
            "None of these are dramatic. All of them are common, and each one measurably "
            "reduces the number of replies you get."
        ),
        "tags": "resume tips, job search",
        "seo_description": (
            "Seven common resume mistakes — vague bullets, missing metrics, bad file names, "
            "layout problems — and exactly how to fix each one."
        ),
        "body": """\
These aren't exotic errors. They show up on most resumes, including from strong candidates.

## 1. Responsibilities instead of results

> Responsible for managing the company's social media accounts.

This tells a reader what you were assigned, not what you achieved. Compare:

> Grew company LinkedIn following from 2k to 18k in 14 months; inbound demo requests from
> social rose from ~3 to ~40 per month.

Same job. One of them gets a reply.

## 2. No numbers anywhere

If a resume contains no figures at all, every claim on it is unfalsifiable and reads as
generic. You don't need numbers on every line — but you need them somewhere. Percentages,
volumes, timeframes, team sizes, budgets, and before/after comparisons all work.

If you genuinely can't measure the outcome, describe the scope instead: "across 12 retail
sites", "for a 40-person engineering org".

## 3. A summary that says nothing

> Motivated self-starter and team player seeking a challenging role in a dynamic
> organisation.

This is true of everyone and describes no one. A useful summary states what you are, your
strongest relevant evidence, and what you're aiming at:

> Backend engineer, 6 years in payments. Built the settlement pipeline handling £40m/month
> at Acme. Looking for infrastructure work in fintech.

## 4. Contact details in the page header

Text in a document's header region is one of the most common things resume parsers drop
entirely. Losing your email address is an expensive way to find that out. Put contact
details in the body of the document.

## 5. Multi-column layouts

Designer-style two-column templates look sharp to a human and frequently scramble in
parsing, because reading order becomes ambiguous. Sidebars containing your skills are the
usual casualty. Keep the main content single-column.

## 6. The file name

`resume_final_v3_USE_THIS_ONE.pdf` gets seen by a human. So does `Untitled.pdf`. Use
`Firstname-Lastname-Resume.pdf`.

## 7. Unexplained gaps and ambiguous dates

Gaps are far less damaging than people fear — ambiguity is worse. "2021–2023" hides whether
you mean January 2021 to December 2023 or December 2021 to January 2023. Use months. If
there's a gap, a one-line explanation ("Career break — full-time caregiving") closes the
question a recruiter would otherwise have to guess at.

## Fixing them

Most of these take under an hour to correct across an entire resume, and unlike most job
search advice, they're concrete: you can check each one and know whether you've done it.
""",
    },
]


class Command(BaseCommand):
    help = "Create or update the starter blog posts."

    def handle(self, *args, **options):
        for data in POSTS:
            post, created = Post.objects.update_or_create(
                slug=data["slug"],
                defaults={**data, "status": Post.STATUS_PUBLISHED},
            )
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb}: {post.title}"))

        self.stdout.write(self.style.SUCCESS(f"\n{len(POSTS)} starter posts ready."))
