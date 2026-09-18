"""Finds the job posting inside a web page, and only the job posting.

Used when a page carries no structured job data (schema.org JobPosting,
embedded app state, a known ATS API) — which is most company career sites.
The fetch used to fall back to *every* piece of visible text on the page, so
a pasted link came back with the site's menus, cookie banner, "similar jobs"
list, newsletter box and footer mixed into the description, and all of it was
then sent for AI analysis.

How it decides what the posting is, in order:

  1. Obvious boilerplate is removed first: navigation, headers, footers,
     sidebars, dialogs, forms and anything whose class, id or role says it is
     a menu, cookie notice, share bar, related-jobs list and so on.
  2. An element that names itself as the description (`job-description`,
     `jobDescriptionText`, `itemprop="description"`, ...) wins outright.
  3. Otherwise a readability-style score picks the container with the most
     paragraph-shaped, low-link, job-worded text, and merges in sibling
     sections of the same posting ("Requirements", "Benefits").
  4. The result must read like a job posting. A company homepage or a list
     of openings is reported as such instead of being returned as a
     "description".

Pure functions over a string — no network — so every rule is testable.
"""

import html
import re
from html.parser import HTMLParser

MAX_DESCRIPTION_CHARS = 20000
MIN_DESCRIPTION_CHARS = 200

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}

# Never part of a posting's text.
_DROP_TAGS = {
    "script", "style", "noscript", "svg", "template", "iframe", "canvas",
    "nav", "header", "footer", "aside", "form", "button", "select", "option",
    "dialog", "object", "video", "audio", "picture", "head", "title",
}

_DROP_ROLES = {
    "navigation", "banner", "contentinfo", "complementary", "dialog",
    "alertdialog", "search", "menu", "menubar", "toolbar", "tablist",
}

# class/id tokens that mark page furniture rather than content.
_BOILERPLATE_TOKENS = {
    "nav", "navbar", "navigation", "menu", "menubar", "breadcrumb", "breadcrumbs",
    "cookie", "cookies", "consent", "gdpr", "banner", "share", "sharing", "social",
    "related", "similar", "recommended", "recommendations", "footer", "sidebar",
    "modal", "popup", "overlay", "newsletter", "subscribe", "subscription", "signup",
    "login", "signin", "comment", "comments", "advert", "advertisement", "ads",
    "promo", "toolbar", "pagination", "pager", "skip", "masthead", "topbar",
    "offcanvas", "drawer", "widget", "carousel", "testimonials", "alert",
}

# Furniture that is never content, whatever else its class says
# ("cookie-content" is still a cookie banner).
_ALWAYS_DROP_TOKENS = {
    "cookie", "cookies", "consent", "gdpr", "newsletter", "modal", "popup",
    "breadcrumb", "breadcrumbs", "share", "sharing", "social",
}

# class/id tokens that mark the posting body itself.
_CONTENT_TOKENS = {"description", "jd", "details", "posting", "vacancy", "content", "body"}

_DESCRIPTION_HINT = re.compile(
    r"(job[-_ ]?(description|details|desc|body|content|posting|summary)"
    r"|description[-_ ]?(text|content|body|container)"
    r"|jobdescriptiontext|posting[-_ ]?(description|page)|vacancy[-_ ]?(description|details)"
    r"|jd[-_ ]?(container|content|body|details)"
    r"|position[-_ ]?(description|details)|role[-_ ]?(description|details))",
    re.IGNORECASE,
)

_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "li", "ul", "ol", "h1", "h2", "h3",
    "h4", "h5", "h6", "table", "tr", "td", "th", "dl", "dt", "dd", "blockquote",
    "pre", "br", "hr",
}
_PARAGRAPH_TAGS = {"p", "li", "pre", "td", "dd", "blockquote", "h2", "h3", "h4", "h5", "h6"}

# Words that show up in real postings, in the languages the product serves.
# Used both to steer scoring and to tell a posting from a homepage.
_JOB_SIGNALS = re.compile(
    r"\b("
    # English
    r"responsibilit\w*|requirements?|qualifications?|experience|skills?|"
    r"what you.?ll (do|bring|need)|about (the|this) (role|job|position)|about you|"
    r"we.?re looking for|we are looking for|you will|you.?ll|benefits|salary|"
    r"full[- ]time|part[- ]time|permanent|contract|remote|hybrid|on[- ]site|"
    r"job type|years of|degree|apply|candidate|we need|hiring|join (our|the) team|"
    r"role|position|"
    # Role nouns: a terse posting ("We need a backend engineer with Python")
    # may use none of the section words above.
    r"engineer|developer|designer|manager|analyst|specialist|consultant|"
    r"architect|scientist|administrator|coordinator|associate|intern(ship)?|"
    r"lead|director|officer|executive|technician|nurse|teacher|accountant|"
    # German
    r"aufgaben|anforderungen|qualifikation\w*|erfahrung|profil|wir bieten|"
    r"bewerbung|vollzeit|teilzeit|kenntnisse|"
    # French
    r"missions?|comp[ée]tences|exp[ée]rience|nous offrons|candidature|"
    r"temps plein|poste|"
    # Spanish
    r"requisitos|responsabilidades|experiencia|ofrecemos|jornada|"
    # Dutch
    r"functieomschrijving|vereisten|ervaring|wij bieten|vacature"
    r")\b",
    re.IGNORECASE,
)

# A link to one individual posting: a jobs-like path ending in a numeric id
# or a multi-word slug. Menu links such as /jobs/search or /careers/teams
# deliberately don't count.
_JOB_LINK = re.compile(
    r"/(?:jobs?|careers?|positions?|vacanc\w*|openings?|stellen\w*|emplois?)/"
    r"(?:[^/?#]+/)*?(?:\d{3,}|[a-z0-9]+(?:-[a-z0-9]+){2,})(?=[/?#]|$)",
    re.I,
)

# Lines that are UI, not posting.
_NOISE_LINE = re.compile(
    r"^(apply( now| for this job| here)?|save( job)?|share( this job)?|report( this)? job|"
    r"back to (jobs|search|results)|sign in|log in|register|print|copy link|"
    r"similar jobs|related jobs|view all jobs|see all jobs|accept( all)?( cookies)?|"
    r"decline|close|menu|skip to (main )?content|loading\.*)$",
    re.IGNORECASE,
)


class JobPageNotAPostingError(ValueError):
    """The page is readable but isn't a single job posting."""


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.text = None  # set for text nodes only


class _TreeBuilder(HTMLParser):
    """A forgiving DOM builder: real-world markup has unclosed and misnested
    tags, so an end tag closes the nearest open element of that name and is
    otherwise ignored."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if data and data.strip():
            node = _Node("#text", parent=self.stack[-1])
            node.text = data
            self.stack[-1].children.append(node)


def _parse(markup):
    builder = _TreeBuilder()
    try:
        builder.feed(markup)
        builder.close()
    except Exception:  # noqa: BLE001 - malformed markup keeps whatever parsed
        pass
    return builder.root


def _tokens(node):
    raw = f"{node.attrs.get('class', '')} {node.attrs.get('id', '')}"
    return {token for token in re.split(r"[^a-z0-9]+", raw.lower()) if token}


def _is_boilerplate(node):
    if node.tag in _DROP_TAGS:
        return True
    attrs = node.attrs
    if "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true":
        return True
    if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attrs.get("style", ""), re.I):
        return True
    if attrs.get("role", "").lower() in _DROP_ROLES:
        return True
    tokens = _tokens(node)
    if tokens & _ALWAYS_DROP_TOKENS:
        return True
    if tokens & _BOILERPLATE_TOKENS and not (tokens & _CONTENT_TOKENS):
        return True
    return False


def _prune(node):
    node.children = [child for child in node.children if child.tag == "#text" or not _is_boilerplate(child)]
    for child in node.children:
        if child.tag != "#text":
            _prune(child)


def _iter_elements(node):
    for child in node.children:
        if child.tag != "#text":
            yield child
            yield from _iter_elements(child)


def _text_len(node):
    if node.tag == "#text":
        return len(node.text.strip())
    return sum(_text_len(child) for child in node.children)


def _link_text_len(node):
    if node.tag == "#text":
        return 0
    if node.tag == "a":
        return _text_len(node)
    return sum(_link_text_len(child) for child in node.children)


def _link_density(node):
    total = _text_len(node)
    return (_link_text_len(node) / total) if total else 1.0


def _plain_text(node):
    if node.tag == "#text":
        return node.text
    return "".join(_plain_text(child) for child in node.children)


def _render(node, out):
    """Text with structure kept: blocks on their own lines, list items
    bulleted, so the posting still reads as a posting."""
    if node.tag == "#text":
        out.append(re.sub(r"\s+", " ", node.text))
        return
    if node.tag == "br":
        out.append("\n")
        return
    block = node.tag in _BLOCK_TAGS
    if block:
        out.append("\n")
    if node.tag == "li":
        out.append("- ")
    for child in node.children:
        _render(child, out)
    if block:
        out.append("\n")


def _clean_lines(text):
    lines = []
    seen_short = set()
    for raw in text.split("\n"):
        line = re.sub(r"[ \t ]+", " ", raw).strip()
        if not line or line == "-":
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _NOISE_LINE.match(line.lstrip("- ").strip()):
            continue
        # Sites repeat the title, location and button labels; a short line
        # seen before adds nothing.
        key = line.lower()
        if len(line) < 80:
            if key in seen_short:
                continue
            seen_short.add(key)
        lines.append(line)
    return "\n".join(lines).strip()


_LEAF_BLOCK_TAGS = {"div", "section", "article", "main", "span", "font", "center"}


def _is_paragraph_like(element):
    """A paragraph tag, or a block that holds text but no other blocks —
    plenty of sites write their postings as bare `<div>`s with `<br>`s."""
    if element.tag in _PARAGRAPH_TAGS:
        return True
    if element.tag not in _LEAF_BLOCK_TAGS:
        return False
    return not any(
        child.tag in _BLOCK_TAGS and child.tag != "br"
        for child in _iter_elements(element)
    )


def _score_candidates(root):
    scores = {}
    for element in _iter_elements(root):
        if not _is_paragraph_like(element):
            continue
        text = _plain_text(element).strip()
        if len(text) < 25:
            continue
        score = 1 + text.count(",") + min(len(text) / 100, 3)
        score += 2 * min(len(_JOB_SIGNALS.findall(text)), 5)
        parent = element.parent
        if parent is not None:
            scores[parent] = scores.get(parent, 0) + score
            if parent.parent is not None:
                scores[parent.parent] = scores.get(parent.parent, 0) + score / 2
    for node in list(scores):
        tokens = _tokens(node)
        if tokens & _CONTENT_TOKENS or _DESCRIPTION_HINT.search(" ".join(tokens)):
            scores[node] *= 1.5
        scores[node] *= 1 - min(_link_density(node), 0.9)
    return scores


def _hinted_container(root):
    best = None
    for element in _iter_elements(root):
        joined = f"{element.attrs.get('class', '')} {element.attrs.get('id', '')}"
        hinted = _DESCRIPTION_HINT.search(joined) or element.attrs.get("itemprop", "").lower() == "description"
        if not hinted:
            continue
        length = _text_len(element)
        if length >= MIN_DESCRIPTION_CHARS and (best is None or length > _text_len(best)):
            best = element
    return best


def _best_container(root):
    hinted = _hinted_container(root)
    if hinted is not None:
        return [hinted]

    scores = _score_candidates(root)
    if not scores:
        return []
    top = max(scores, key=scores.get)

    # The best-scoring element can be one fragment of the posting — a single
    # bullet list — when the posting is split into sections. While it is too
    # small to be a whole posting, step out to the container around it, as
    # long as that container is still content (few links) and not the whole
    # page.
    while True:
        parent = top.parent
        if parent is None or parent.tag in ("root", "html", "body"):
            break
        current, enclosing = _text_len(top), _text_len(parent)
        if current >= 500 or _link_density(parent) >= 0.3 or enclosing > max(1500, 3 * current):
            break
        top = parent
    top_score = scores.get(top, 0)

    # A posting often splits into sibling sections ("About the role",
    # "Requirements", "Benefits"); take the ones that score like content.
    parent = top.parent
    if parent is None:
        return [top]
    picked = []
    for sibling in parent.children:
        if sibling is top:
            picked.append(sibling)
            continue
        if sibling.tag == "#text":
            continue
        score = scores.get(sibling, 0)
        if score >= max(5, 0.2 * top_score) and _link_density(sibling) < 0.3:
            picked.append(sibling)
    return picked


def _job_links(node):
    links = set()
    for element in _iter_elements(node):
        if element.tag == "a":
            match = _JOB_LINK.search(element.attrs.get("href", ""))
            if match:
                links.add(match.group(0).lower())
    return links


def _looks_like_listing(page_links, containers):
    """A list of openings rather than one.

    `page_links` are counted before boilerplate is removed, because sites
    style their job lists with classes like "menu". The verdict is then
    judged on what was picked as the content: a real posting often sits
    beside a "similar jobs" list, and that must not count against it.
    """
    if len(page_links) < 6:
        return False
    content_links = set()
    for node in containers:
        content_links |= _job_links(node)
    if len(content_links) >= 6:
        return True
    body = sum(_text_len(node) for node in containers)
    density = sum(_link_text_len(node) for node in containers) / body if body else 1.0
    return density > 0.35


def _document_title(markup):
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    title = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
    # "Senior Engineer | Acme Careers" -> "Senior Engineer"
    first = re.split(r"\s+[|–—-]\s+", title)[0].strip()
    return (first if len(first) >= 3 else title)[:200]


def _first_text(node):
    for child in node.children:
        if child.tag == "#text":
            text = re.sub(r"\s+", " ", child.text).strip()
            if text:
                return text
        else:
            found = _first_text(child)
            if found:
                return found
    return ""


def _title(root, markup, containers):
    """The job title, not the site name and not a section heading.

    The document title is usually "Job title | Company" or
    "Job title, Company | Site", which makes it the best judge of which
    heading is the real one: a posting body often has its own <h1>
    ("About Acme"), and page headings often carry badges, the company and
    the location next to the title.
    """
    document_title = _document_title(markup)
    references = [t for t in (document_title, document_title.split(",")[0].strip()) if len(t) >= 3]

    headings = []
    for element in _iter_elements(root):
        if element.tag == "h1":
            text = re.sub(r"\s+", " ", _plain_text(element)).strip()
            if 3 <= len(text) <= 300:
                headings.append((text, _first_text(element)))

    for text, first in headings:
        lowered = text.lower()
        for reference in references:
            ref = reference.lower()
            if ref == lowered:
                return text[:200]
            if ref in lowered:
                # The heading carries extras around the title.
                return reference[:200]
            # A partial match must be a substantial share of the title, or a
            # logo heading ("Acme") would win against "Engineer, Acme".
            if lowered in ref and len(text) >= 8 and len(text) >= 0.5 * len(ref):
                return text[:200]
        if first and len(first) >= 8 and any(
            first.lower() in r.lower() and len(first) >= 0.5 * len(r) for r in references
        ):
            return first[:200]

    if document_title:
        return document_title

    for scope in containers + [root]:
        for element in _iter_elements(scope):
            if element.tag == "h1":
                text = re.sub(r"\s+", " ", _plain_text(element)).strip()
                if 3 <= len(text) <= 200:
                    return text
    return ""


def job_signal_count(text):
    return len(_JOB_SIGNALS.findall(text or ""))


def extract_job_content(markup):
    """Returns {"title", "description"} for the posting on this page.

    Returns None when too little text survives to be a posting (a login wall
    or a page that renders client-side). Raises JobPageNotAPostingError when
    the page is readable but is a homepage, a list of openings, or anything
    else that isn't one job.
    """
    root = _parse(markup)
    page_links = _job_links(root)
    _prune(root)

    containers = _best_container(root)
    parts = []
    for node in containers:
        _render(node, parts)
    description = _clean_lines("".join(parts))

    if len(description) < MIN_DESCRIPTION_CHARS:
        # No posting text but many links to individual jobs: a list of
        # openings, which deserves that answer rather than "unreadable".
        if len(page_links) >= 8:
            raise JobPageNotAPostingError("list of openings")
        return None

    if _looks_like_listing(page_links, containers):
        raise JobPageNotAPostingError("list of openings")
    if job_signal_count(description) < 2:
        raise JobPageNotAPostingError("no job wording")

    return {
        "title": _title(root, markup, containers),
        "description": description[:MAX_DESCRIPTION_CHARS],
    }


def clean_plain_text(text):
    """For providers that hand back text rather than markup: drop UI lines
    and repeats, keep the rest."""
    return _clean_lines(text or "")[:MAX_DESCRIPTION_CHARS]
