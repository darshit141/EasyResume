"""Blog cover pictures: the bundled illustrations a post can use, and which
one a post gets when the admin hasn't picked one.

The illustrations themselves ship with the frontend (src/lib/illustrations.js);
these names must match its keys.
"""

# Each works both as the wide picture on the post and, through its card-size
# stand-in, on the blog index.
COVER_LABELS = {
    "match-bridge": "Resume linked to a job's requirements",
    "transform-factlock": "Rewrite that keeps the facts",
    "ats-overlay": "Tracking-system checks",
    "growth-versions": "Master resume and tailored versions",
    "builder-split": "Guided question to resume line",
    "match-ledger": "Requirements with evidence",
    "gap-columns": "Skills gap in three columns",
    "growth-steps": "Path to the target role",
    "apply-send": "Application ready to send",
    "track-board": "Application tracker board",
}
COVER_CHOICES = list(COVER_LABELS)

# The posts we wrote, then the first tag that has a picture.
POST_COVERS = {
    "what-an-ats-actually-does": "ats-overlay",
    "tailoring-your-resume-without-lying": "transform-factlock",
    "cover-letter-that-isnt-a-summary": "builder-split",
    "what-a-skills-gap-actually-means": "gap-columns",
    "resume-mistakes-that-cost-interviews": "match-ledger",
}
TAG_COVERS = {
    "ats": "ats-overlay",
    "cover letters": "builder-split",
    "resume tips": "growth-versions",
    "job search": "match-bridge",
    "skills gap": "gap-columns",
    "applications": "track-board",
}
FALLBACK_COVER = "match-bridge"


def automatic_cover(post):
    if post.slug in POST_COVERS:
        return POST_COVERS[post.slug]
    for tag in post.tag_list:
        if tag.lower() in TAG_COVERS:
            return TAG_COVERS[tag.lower()]
    return FALLBACK_COVER


def cover_payload(post):
    """What the public site shows for a post's cover.

    {"source": "auto" | "builtin", "builtin", "alt"}, or
    {"source": "custom", "src", "width", "height", "alt"}, or
    {"source": "hidden"}. `src` is relative to the API root.
    """
    source = post.cover_source or "auto"
    if source == post.COVER_HIDDEN:
        return {"source": "hidden"}
    if source == post.COVER_CUSTOM and post.cover_version:
        return {
            "source": "custom",
            "src": f"blog/posts/{post.slug}/cover/?v={post.cover_version}",
            "width": post.cover_width,
            "height": post.cover_height,
            "alt": post.cover_alt,
        }
    if source == post.COVER_BUILTIN and post.cover_builtin in COVER_LABELS:
        return {"source": "builtin", "builtin": post.cover_builtin, "alt": post.cover_alt}
    return {"source": "auto", "builtin": automatic_cover(post), "alt": post.cover_alt}
