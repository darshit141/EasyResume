"""The user's resume file, encoded so a browser extension can attach it.

Handed over as base64 inside JSON rather than as a normal file download,
because of the two hops it has to survive. The extension's service worker
is the only place that holds the auth token, so it must do the fetch; the
page is where the `<input type=file>` lives, and the only way across that
boundary is `chrome.scripting.executeScript`, whose arguments are
JSON-serialisable. A Blob or an ArrayBuffer does not survive that. Base64
does, and it is the reason this endpoint looks unusual.

Nothing is ever attached without the user asking for it. This endpoint is
called only when they tick the box, and the extension attaches only to a
file input that is empty.
"""

import base64
import os

# Attaching is a convenience; it is not worth streaming megabytes through a
# popup, and an ATS that accepts a resume larger than this is rare. Above
# the cap we say so plainly and the user attaches it themselves, which is
# the fallback the whole feature is designed to degrade into.
MAX_ATTACH_BYTES = 5 * 1024 * 1024

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf": "application/rtf",
    ".txt": "text/plain",
    ".odt": "application/vnd.oasis.opendocument.text",
}


def content_type_for(filename):
    _, extension = os.path.splitext(filename or "")
    return _CONTENT_TYPES.get(extension.lower(), "application/octet-stream")


class AutofillResumeFileService:
    @staticmethod
    def build(resume):
        """The file for `resume` as a JSON-safe payload. `resume` may be None.

        Every failure is reported as `available: false` with a `reason` the
        extension can show, rather than as an error: not having a file to
        attach is an ordinary state (the guided builder produces a resume
        with no source file at all), not a fault.
        """
        if resume is None or not resume.file:
            return {"available": False, "reason": "no_file"}

        filename = (resume.original_filename or os.path.basename(resume.file.name) or "resume").strip()

        try:
            size = resume.file.size
        except (OSError, ValueError):
            # The row survives but the blob does not - a restored database
            # pointed at empty storage, most often. Say it is unavailable
            # rather than crashing the fill the user is halfway through.
            return {"available": False, "reason": "missing_from_storage"}

        if size > MAX_ATTACH_BYTES:
            return {
                "available": False,
                "reason": "too_large",
                "filename": filename,
                "size": size,
                "max_size": MAX_ATTACH_BYTES,
            }

        try:
            with resume.file.open("rb") as handle:
                raw = handle.read()
        except (OSError, ValueError):
            return {"available": False, "reason": "missing_from_storage"}

        return {
            "available": True,
            "filename": filename,
            "content_type": content_type_for(filename),
            "size": len(raw),
            "data": base64.b64encode(raw).decode("ascii"),
        }
