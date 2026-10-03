"""Extra sections for the five original starter posts.

The starter posts in ``seed_blog.py`` are accurate but short. Each entry here
is a section that is inserted before the post's closing heading (or appended
if it has none) so the posts reach a useful length without being rewritten.
Keys are post slugs; values are (closing_heading_or_None, markdown).
"""

EXTENSIONS = {
    "what-an-ats-actually-does": (
        "## How IntelliResume fits in",
        r"""
## Checking your own resume the way a parser would

You cannot see inside a recruiting system, but you can run a simple test that tells you most of what matters. Save your resume as a PDF, then open it and select all the text with your cursor. Copy it into a plain text editor and read what comes out.

If the text appears in a sensible order, with your name, contact details, job titles, and dates in the right places, a parser will probably read it well. If the order is scrambled, columns are interleaved, words are broken into pieces, or whole sections are missing, a parser will have the same trouble, and you have found the fix: simplify the layout.

A few other quick checks help:

- **Try a different file.** If an application accepts both a PDF and a word-processor document, either is usually fine, as long as the text is real text and not an image of text. Never submit a scanned page.
- **Read your headings.** If a section is called something unusual, rename it to a standard heading. Recruiters searching a database also rely on predictable structure.
- **Look at your dates.** Use one consistent format, such as "Mar 2022 to Jun 2024," so the system can calculate your tenure.
- **Spell out acronyms once.** Writing "Search Engine Optimisation (SEO)" lets both forms be matched by whoever searches.

## What recruiters actually search for

Because humans are doing the searching, the words they type are the words they expect to find: a job title, a skill, a tool, a location, a qualification. Take a moment to look at how the postings you are interested in describe the work, and make sure your resume uses the same terms where they truly apply to you.

This is not keyword stuffing. It is describing your real experience in the vocabulary of the field. If you managed a team of support agents and the postings call that role "customer support," do not call it "client happiness operations." The same principle applies to your skills section: list the tools and methods you can actually use, named the way employers name them.

## What to take from all this

The practical conclusion is reassuring. You do not need to trick a system, and you cannot. What you need is a clean, readable document that a parser can extract accurately and a person can understand quickly, filled with specific, honest evidence for the job at hand. The same qualities that please a human reader, clarity, structure, relevance, and honesty, are the ones that make a resume work well in any database.
""",
    ),
    "tailoring-your-resume-without-lying": (
        "## How we approach it",
        r"""
## A repeatable process for tailoring

Tailoring a resume does not need to take an hour for each application. With a good master document, you can do it in fifteen to twenty minutes. Here is a process you can repeat.

**Step 1: Read the posting and mark the priorities.** Highlight the five or six things the employer clearly cares about most. These are usually the first responsibilities listed, anything repeated, and anything described as essential.

**Step 2: Search your master record for matching evidence.** For each priority, find the experiences, projects, and results in your own history that relate to it. If you have more than one, choose the strongest.

**Step 3: Reorder, do not rewrite.** Move the most relevant roles and bullets to the top of each section. Within a role, put the bullet that best matches the posting first. Cut or shorten the bullets that matter least for this job.

**Step 4: Adjust the language, not the facts.** If your bullet says "onboarded new clients" and the posting says "customer onboarding," using the employer's term is honest, because it describes the same work. If the posting says "managed a P&L" and you never did, do not add it.

**Step 5: Update the summary.** Rewrite two sentences at the top so they point at this role, using real strengths you can support below.

**Step 6: Read it as the employer.** Does the page answer the question "why should we talk to this person for this job?" If not, the most relevant evidence is still buried.

## Stretching versus framing

It helps to know where legitimate emphasis ends and misrepresentation begins. These distinctions are worth keeping in mind.

- **Framing** means choosing which true things to highlight. A person who spent ten percent of a job coordinating a small project can mention that experience for a project coordinator role, as long as the description is proportionate.
- **Stretching** means describing something larger than it was: calling a one-week trial "managing a programme," or a team contribution "leading the project." It may feel harmless, but a single interview question can expose it.
- **Fabricating** means claiming something that never happened, such as a qualification, a tool you never used, or a job title you never held. This can lead to a withdrawn offer, or dismissal if it is discovered after you are hired.

A good test: could you comfortably talk about this line for five minutes in an interview, including details someone else could check? If so, it is fair. If you hope no one asks, remove it.

## The long-term benefit of honesty

Honest tailoring has a practical advantage. When your resume accurately reflects what you can do, the jobs you are invited to interview for are jobs you can actually do well. You spend less time in interviews that go nowhere, and you start new roles with realistic expectations on both sides. Some roles will fit less perfectly, and that is acceptable. A smaller number of well-matched applications usually produces better results than a large number of inflated ones, and it leaves you free of the worry of being found out.
""",
    ),
    "cover-letter-that-isnt-a-summary": (
        "## How IntelliResume fits in",
        r"""
## A structure you can reuse

Many people struggle with cover letters because they begin with a blank page. A simple structure removes the problem. You can fill in each part with specifics for each role in under half an hour.

**Opening (two or three sentences).** Name the role and give one specific reason you are drawn to it or to the organisation. Avoid starting with "I am writing to apply for..." and stating the obvious. Start with something that shows you have read about the work: "Your recent expansion into regional delivery caught my attention, because I spent three years reducing late deliveries for a regional carrier."

**Middle (one or two short paragraphs).** Choose two or three requirements from the posting and show how you have met them, with one concrete story or result for each. This is the section where the letter earns its keep. Keep each example tight: what the problem was, what you did, and what happened.

**Closing (two or three sentences).** Say what you would like to do next, thank the reader, and keep the tone confident rather than pleading. "I would welcome the chance to talk about how this experience could help your team, and I am available for a conversation at your convenience."

## Examples of stronger and weaker sentences

Compare these pairs.

**Weak:** "I am a hard-working and passionate person who would be a great asset to your company."
**Stronger:** "In my last role I rebuilt the intake process for a twelve-person team, which cut the time to respond to new enquiries from two days to four hours."

**Weak:** "I believe my skills match your requirements."
**Stronger:** "You are looking for someone to coordinate suppliers across three countries. For the past four years I have managed twenty suppliers in five time zones, so the day-to-day of that work is familiar to me."

**Weak:** "I would love the opportunity to learn and grow at your company."
**Stronger:** "I am especially interested in the way your team combines analysis with customer work, which is the part of my current role that I find most rewarding."

The stronger versions are specific and verifiable, and they sound like a person, not a template.

## Common problems to check before sending

- **The wrong company name.** If you reuse letters, this error is more common than you would think. Check it twice.
- **Generic praise.** "Your company is a leader in its field" says nothing. If you cannot name a specific thing you admire, either find one or omit the compliment.
- **A page of text.** Aim for about 250 to 350 words, which will fit comfortably on a single page.
- **Duplicating the resume.** If a sentence is already in your resume, replace it with context the resume cannot give: why you made a decision, how you approached a problem, or what you learned.
- **Typos and tone.** Read it aloud once. Where you stumble, the sentence is probably too complicated.

## When a cover letter is not needed

If an application does not request one and gives no place to upload it, a strong resume and a short note in the email body are usually enough. When a letter is optional, writing a good one can set you apart, but a weak, generic one does not help. Put in the time only when you have something specific to say.
""",
    ),
    "what-a-skills-gap-actually-means": (
        "## How IntelliResume fits in",
        r"""
## Deciding what to do about each kind of gap

Understanding the three types of gap is useful, but the real value comes from deciding what to do next. A short routine helps you turn a list of missing items into a plan.

**Step 1: Sort each gap.** For every requirement you do not clearly meet, decide whether it is a gap in your resume (you have the skill but did not show it), a gap in your experience (you have not done it), or a gap you can close quickly.

**Step 2: Fix the ones that are only presentation.** If you have the skill but it is not on the page, add it with an example. This is the cheapest and fastest improvement, and it is often the largest. Many "gaps" disappear once you describe your work in more detail.

**Step 3: Look for adjacent evidence for the real gaps.** You may not have used a particular tool, but you may have used a close equivalent. You may not have managed people, but you may have mentored someone or led a project team. State the adjacent experience honestly and let the employer decide how much it counts.

**Step 4: Decide whether to close the gap before applying.** A short course or a small project can turn "no experience" into "recent, practical experience." If the missing requirement is central to the role and cannot be learned quickly, it may be sensible to look for roles one step closer.

**Step 5: Prepare to talk about it.** If you apply with a gap, expect a question about it. A confident answer such as "I have not used that tool, but I have used a similar one for three years and picked up new tools quickly, as when I learned X in my first month" is far better than hoping it will not come up.

## An example

Suppose a posting for a marketing analyst asks for experience with a specific analytics platform, SQL, and presenting to senior stakeholders. You have used a different analytics platform for four years, you write simple SQL queries but have never listed it, and you have presented to your own team but not to executives.

- The analytics platform is a **transfer** gap: you can say that you have used an equivalent for four years and can learn the new one quickly.
- SQL is a **presentation** gap: add it to your skills and give an example of a query you wrote and what it achieved.
- Presenting to executives is a **partial** gap: describe the largest audience you have addressed and the form of the presentation, and say you are keen to do more.

After this exercise, a profile that looked like three missing items looks like a strong match with one honest area to grow, and your application says so.

## Be wary of the number

A match percentage can be a helpful prompt, but it is a blunt instrument. It cannot tell the difference between a central requirement and a nice-to-have, and it cannot weigh the quality of your evidence. Use it to start asking questions, not to decide whether to apply. Treat any single number as a prompt to look at the underlying details, which is where the real decision lies.
""",
    ),
    "resume-mistakes-that-cost-interviews": (
        "## Fixing them",
        r"""
## A few more that are easy to miss

The seven mistakes above are the most common, but several others quietly cost candidates attention, especially when they appear together.

**Inconsistent formatting.** Mixed date formats, bullets that change style, different fonts, and uneven spacing all suggest carelessness, and carelessness is hard to square with claims of attention to detail. Pick one style and apply it everywhere.

**Tense that wanders.** Use the present tense for your current role and the past tense for earlier ones. A sudden switch mid-bullet reads as an error.

**Long paragraphs instead of bullets.** Big blocks of text are hard to scan, and recruiters rarely read them. Break them into short bullets of one or two lines.

**Irrelevant personal details.** Age, marital status, a photo in many markets, and a full home address are usually unnecessary and, in some places, can create bias. A city and a phone number are enough.

**An unprofessional email address.** Use a simple address based on your name. It is a small detail, but it is one of the first things a recruiter sees.

**Tiny fonts and cramped margins.** If you need nine-point type to fit everything, you have too much on the page. Cut content instead.

**Typos.** A single spelling error can create a bad impression, and a basic spell-check will not catch the wrong word spelled correctly. Read the resume aloud, then ask someone else to read it.

## Fix the biggest problem first

With this many possible mistakes, it is tempting to polish everything at once. Resist that. Start with the problem a reader would notice first: a weak top third. The top of the first page is read before anything else, so its summary, its first role, and its first two bullets decide whether the rest is read at all. Make those lines specific and relevant, then work down the page. Formatting and proofreading come last, because there is no point polishing a sentence you may well delete.

Keep a record of what you changed and which version you sent where. If you notice that a particular version gets more replies, you will know what to keep, and you will stop repeating the changes that made no difference.

## A final proofreading routine

Before you send any resume, spend ten minutes on a structured check:

1. **Read it aloud** from start to finish. Your ear catches errors that your eye has learned to ignore.
2. **Read it backwards,** one line at a time, to catch spelling errors without being distracted by the meaning.
3. **Check every date and number** against your records.
4. **Check the details that must be perfect:** your name, phone number, email address, and the employer names.
5. **Open the saved file** (not just the editor) to confirm that fonts, spacing, and page breaks look as intended.
6. **Ask someone else** to look at it for thirty seconds and tell you what stands out.

Resumes are usually rejected for a few specific reasons, most of them avoidable. A short, honest, well-organised document with clear results is the best defence against all of them.
""",
    ),
}
