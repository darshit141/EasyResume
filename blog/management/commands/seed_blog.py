"""Seeds the blog with starter posts.

These are real, accurate articles about how resume screening actually works
— written to be useful on their own, not filler. They exist so the blog
isn't empty on launch (an empty blog is also a common reason ad networks
reject a site). Edit or replace them freely in the Django admin.

Idempotent: re-running updates existing posts by slug instead of duplicating.
"""

from django.core.management.base import BaseCommand

from blog.models import Post
from blog.seed_posts import POSTS as ARTICLES
from blog.seed_posts.extensions import EXTENSIONS

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

It is our own heuristic. It is not affiliated with any applicant tracking system vendor,
and it cannot tell you how a specific employer's system will treat you. What it
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
        "slug": "cover-letter-that-isnt-a-summary",
        "title": "How to Write a Cover Letter That Isn't Just a Summary of Your Resume",
        "excerpt": (
            "The reason most cover letters get skipped isn't that nobody reads them. It's "
            "that most of them repeat the resume in paragraph form, which gives the reader "
            "no reason to keep going."
        ),
        "tags": "cover letters, job search",
        "seo_description": (
            "Why most cover letters restate the resume and get skipped, and a concrete "
            "structure for writing one that actually adds information a recruiter needs."
        ),
        "body": """\
Whether cover letters "matter" is the wrong question — it depends entirely on what's in them.
A letter that repeats the resume in sentence form adds nothing and gets skipped in about
the time it takes to notice that's what it is. A letter that does something the resume
structurally can't gets read.

## What a resume can't do, and a letter can

A resume is a list. It's optimised for scanning, which means it has no room for connecting
tissue: why you left, why this role specifically, why now. A cover letter's entire job is
to supply the connecting tissue a bullet-point format has no space for.

That means a cover letter should never contain a sentence that's just a resume line with
"I" in front of it. "I have five years of experience in backend development" is already on
the resume. Writing it again in prose is not a cover letter — it's a worse version of the
document the reader already has open in another tab.

## What to actually put in it

**Why this role, specifically.** Not "I am excited about this opportunity" — that's true of
every application. What about *this* posting or *this* company connects to something real
about your background or interests? If you can't answer that in one honest sentence, that's
worth noticing before you apply, not after.

**The one thing the resume doesn't explain.** A career change, a gap, a move from a
different industry, why you're leaving a role after eight months. The resume shows the
fact; the letter is the only place to give it context. Not an excuse — context. "I spent
two years in operations before moving into engineering, which is why I debug production
incidents faster than most people at my level" reframes a fact the resume states flatly.

**One piece of evidence the job description specifically asked for.** Read the posting for
what it actually emphasises, then pick the one thing from your background that answers it
most directly — not the accomplishment you're proudest of, the one that's most relevant to
what this employer said they need.

## What to leave out

- Restating your job titles and dates — the resume already has these
- "I am a hard worker" or "I am passionate about X" — unfalsifiable, ignored
- Anything about salary or availability unless the posting explicitly asked
- More than four paragraphs. If it doesn't fit in four, it's doing the resume's job as well
  as its own, and both jobs suffer

## Length and structure

Three to four short paragraphs. Open by naming the role. One paragraph on why this role or
company. One paragraph connecting a specific piece of your evidence to what they asked for.
Close with a plain, direct sign-off — no "looking forward to hearing from you soon!"

## How IntelliResume fits in

Our cover letter generator works from the same verified resume data as the rest of the
product — it can't invent an accomplishment that isn't already in your profile, because it
has no other source to draw from. What it's good at is the part people find hardest under
time pressure: pulling out the one piece of your real evidence that actually answers what a
specific posting asked for, instead of restating your resume in longer sentences.
""",
    },
    {
        "slug": "what-a-skills-gap-actually-means",
        "title": "What a \"Skills Gap\" Actually Means Before You Apply",
        "excerpt": (
            "Missing one requirement on a job posting isn't a verdict. Here's how to read a "
            "gap analysis honestly — what to add to your resume, what to actually learn, "
            "and what's just not a match."
        ),
        "tags": "job search, resume tips",
        "seo_description": (
            "How to interpret a gap between your resume and a job description honestly: "
            "what's a documentation problem, what's a real skills gap, and what to do about each."
        ),
        "body": """\
Seeing "missing: Kubernetes" next to a job posting feels like a verdict. It usually isn't
one. A gap between your resume and a job description is one of three different things, and
they call for three different responses — treating all of them the same way is where people
go wrong in both directions, either over-applying to roles they're not close to, or
under-applying to roles they're actually qualified for.

## Three different kinds of gap

**You have it, but it isn't written down.** This is the most common case, and it's a
documentation problem, not a skills problem. You've used the tool, run the process, or
solved the kind of issue the posting describes, but your resume doesn't say so in those
words — because you wrote it for a different role, or because you didn't think it was
worth mentioning. The fix is adding a true, specific line to your resume, not learning
anything new.

**You have something adjacent, not the exact thing.** The posting asks for a specific tool
or framework; you have solid experience with a comparable one. This is real, but it's a
framing gap, not a knowledge gap — a resume that says "5 years with PostgreSQL; comfortable
picking up MySQL" is honest and often enough. Claiming the exact tool you haven't used is
where "tailoring" turns into fabrication.

**You genuinely don't have it.** No adjacent experience, no reasonable reframing. This is
the one category where the honest options are: learn it before applying, accept the
posting is a stretch and apply anyway if enough else lines up, or skip it. All three are
legitimate calls — what isn't legitimate is resolving the gap by writing a resume that
implies otherwise.

## Why this distinction matters more than the gap itself

An interview is where an invented qualification gets tested directly, by someone who
actually knows the subject. A documentation gap costs you nothing once it's fixed. A framed
gap survives scrutiny because it was honest to begin with. A fabricated gap is the one that
turns an interview into the moment you get caught, which is a worse outcome than simply not
getting the interview.

## Reading a match percentage correctly

A single number that says you're an "80% match" invites treating requirements as
interchangeable — but a posting's required skills and its preferred ones are not the same
weight, and a job description almost always lists more than any one candidate will actually
have. A gap analysis is only useful if it tells you *which* three requirements are load-bearing and
which one nice-to-have you can safely not have, not just how many boxes are checked.

## How IntelliResume fits in

Our gap analysis compares a job's stated requirements against your verified resume data —
never the other way around, and it never invents a skill you haven't demonstrated. Every
item comes back as one of the categories above: something you have that isn't written down
yet, or something you genuinely don't have. What you do with that is still your call; the
tool's job is making sure the call is based on an accurate picture, not a guess.
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


def _extended(data):
    """The starter post with its extra section inserted before the closing heading."""
    extra = EXTENSIONS.get(data["slug"])
    if not extra:
        return data
    marker, section = extra
    body = data["body"]
    at = body.rfind(marker) if marker else -1
    section = section.strip("\n") + "\n\n"
    if at != -1:
        body = body[:at] + section + body[at:]
    else:
        body = body.rstrip("\n") + "\n\n" + section
    return {**data, "body": body}


ALL_POSTS = [*(_extended(post) for post in POSTS), *ARTICLES]


class Command(BaseCommand):
    help = "Create or update the starter and long-form blog posts."

    def handle(self, *args, **options):
        for data in ALL_POSTS:
            post, created = Post.objects.update_or_create(
                slug=data["slug"],
                defaults={**data, "status": Post.STATUS_PUBLISHED},
            )
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb}: {post.title}"))

        self.stdout.write(self.style.SUCCESS(f"\n{len(ALL_POSTS)} posts ready."))
