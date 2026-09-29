"""Admin: change the pictures on the public site, slot by slot.

GET    admin/site-images/                 every slot, its default and what it shows now
PATCH  admin/site-images/<slot>/          {"source": "builtin"|"hidden", "builtin", "alt_text"}
POST   admin/site-images/<slot>/upload/   multipart "file" (+ optional "alt_text")
DELETE admin/site-images/<slot>/          back to the default
"""

from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import AdminAuditLog, SiteImage
from api.site_images import BUILTIN_LABELS, SLOTS, builtins_for, normalise_builtin
from api.utils.audit import record_admin_action
from api.utils.pictures import clean_alt, to_webp
from api.views.site_views import image_override


def slot_payload(slot, row):
    meta = SLOTS[slot]
    return {
        "slot": slot,
        "label": meta["label"],
        "page": meta["page"],
        "default": meta["default"],
        "builtins": [{"value": b, "label": BUILTIN_LABELS.get(b, b)} for b in builtins_for(slot)],
        "source": row.source if row else "default",
        "builtin": normalise_builtin(row.builtin) if row else "",
        "alt_text": row.alt_text if row else "",
        "current": image_override(row) if row else {"builtin": meta["default"]},
        "updated_at": row.updated_at if row else None,
    }


def get_slot(slot):
    """(row or None, error response or None)."""
    if slot not in SLOTS:
        return None, Response({"detail": "Unknown image slot."}, status=status.HTTP_404_NOT_FOUND)
    return SiteImage.objects.filter(slot=slot).first(), None


class AdminSiteImageListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        rows = {r.slot: r for r in SiteImage.objects.all()}
        return Response([slot_payload(slot, rows.get(slot)) for slot in SLOTS])


class AdminSiteImageDetailAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]
    parser_classes = [JSONParser]

    def patch(self, request, slot):
        row, error = get_slot(slot)
        if error:
            return error

        source = request.data.get("source")
        if source not in (SiteImage.SOURCE_BUILTIN, SiteImage.SOURCE_HIDDEN, None):
            return Response(
                {"detail": "source must be builtin or hidden. Upload a file to use your own picture."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if source is None:
            # Only the description is changing.
            if row is None:
                row = SiteImage(slot=slot, source=SiteImage.SOURCE_BUILTIN, builtin=SLOTS[slot]["default"])
        else:
            row = row or SiteImage(slot=slot)
            if source == SiteImage.SOURCE_BUILTIN:
                builtin = normalise_builtin(request.data.get("builtin"))
                if builtin not in builtins_for(slot):
                    return Response(
                        {"detail": "Pick one of the listed pictures for this slot."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                row.builtin = builtin
                # A bundled picture replaces any upload.
                row.image = None
                row.content_type = ""
                row.width = row.height = None
            row.source = source
        if "alt_text" in request.data:
            row.alt_text = clean_alt(request.data.get("alt_text"))
        row.updated_by = request.user
        row.save()
        record_admin_action(
            request, AdminAuditLog.ACTION_SITE_IMAGE_UPDATED, f"site_image:{slot}",
            detail={"source": row.source, "builtin": row.builtin},
        )
        return Response(slot_payload(slot, row))

    def delete(self, request, slot):
        row, error = get_slot(slot)
        if error:
            return error
        if row is not None:
            row.delete()
            record_admin_action(request, AdminAuditLog.ACTION_SITE_IMAGE_RESET, f"site_image:{slot}")
        return Response(slot_payload(slot, None))


class AdminSiteImageUploadAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, slot):
        row, error = get_slot(slot)
        if error:
            return error
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "Attach a picture to upload."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            data, width, height = to_webp(upload)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        row = row or SiteImage(slot=slot)
        row.source = SiteImage.SOURCE_CUSTOM
        row.builtin = ""
        row.image = data
        row.content_type = "image/webp"
        row.width, row.height = width, height
        if "alt_text" in request.data:
            row.alt_text = clean_alt(request.data.get("alt_text"))
        row.updated_by = request.user
        row.save()
        record_admin_action(
            request, AdminAuditLog.ACTION_SITE_IMAGE_UPDATED, f"site_image:{slot}",
            detail={"source": "custom", "width": width, "height": height, "bytes": len(data)},
        )
        return Response(slot_payload(slot, row), status=status.HTTP_201_CREATED)
