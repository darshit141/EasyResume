"""Checking and re-encoding pictures an admin uploads for the public site.

Shared by the site picture slots (api.views.site_image_admin_views) and the
blog cover pictures (blog.views.cover_admin_view).
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# Refuse anything that would decode to more than this many pixels, so a small
# file can't expand into gigabytes of memory (a decompression bomb).
MAX_SOURCE_PIXELS = 40_000_000
MAX_EDGE = 2400
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def to_webp(upload):
    """Validate an uploaded picture and re-encode it as WebP.

    Re-encoding drops any metadata the file carried (camera, location) and
    anything that isn't pixels, so what's served is only ever an image.
    Returns (bytes, width, height) or raises ValueError with a message for
    the admin.
    """
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValueError("That file is over 8 MB. Use a smaller picture.")
    try:
        with Image.open(upload) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise ValueError("Upload a JPEG, PNG or WebP picture.")
            if probe.width * probe.height > MAX_SOURCE_PIXELS:
                raise ValueError("That picture is too large. Keep it under 40 megapixels.")
        upload.seek(0)
        with Image.open(upload) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened)
            image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            out = io.BytesIO()
            image.save(out, "WEBP", quality=85, method=6)
            return out.getvalue(), image.width, image.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("That file isn't a picture we can read. Upload a JPEG, PNG or WebP.")


def clean_alt(value):
    return str(value or "").strip()[:300]


def picture_response(data, content_type="image/webp"):
    """Serve stored picture bytes. Callers put a version in the URL, so the
    response can be cached for good."""
    from django.http import HttpResponse

    response = HttpResponse(bytes(data), content_type=content_type or "image/webp")
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    # The site and the API live on different origins.
    response["Cross-Origin-Resource-Policy"] = "cross-origin"
    response["X-Content-Type-Options"] = "nosniff"
    return response
