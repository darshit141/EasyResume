"""Issues and renders invoices for INR charges.

Issuing is the part with legal weight: the number must be consecutive
within the financial year, the same payment must never be invoiced twice,
and what the invoice says must be fixed at the moment of issue. Rendering is
just a faithful picture of the stored row — it never recalculates anything,
so a later change of GST rate or address cannot silently alter an invoice
that has already been issued.
"""

import io
import logging
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from billing import gst
from billing.models import BillingProfile, GSTInvoice, InvoiceSequence
from billing.plans import get_plan
from billing.services.gst_service import GSTService

logger = logging.getLogger(__name__)

# GST dates are Indian dates whatever the server's time zone is. A payment at
# 01:30 IST on 1 April belongs to the new financial year even though it is
# still 31 March in UTC.
IST = ZoneInfo("Asia/Kolkata")


class GSTInvoiceService:
    @staticmethod
    def issue(user, payment_id, razorpay_subscription_id, amount_paise, plan_id, billing_period, issued_at=None):
        from billing.services.razorpay_sync_service import next_invoice_number

        issued_at = issued_at or timezone.now()
        supplier = GSTService.supplier()
        profile = BillingProfile.objects.filter(user=user).first() if user else None

        try:
            breakdown = GSTService.breakdown(amount_paise, profile, supplier)
        except gst.GSTError:
            # No usable place of supply on record. The IGST Act's fallback
            # for a recipient whose address isn't available is the
            # supplier's own location, which makes it an intra-state supply.
            logger.warning(
                "No valid billing state for user_id=%s; using the supplier's state as place of supply.",
                getattr(user, "id", None),
            )
            breakdown = gst.calculate_inclusive(
                amount_paise, supplier.rate_percent, supplier.state_code, supplier.state_code
            )

        plan = get_plan(plan_id) or {}
        period_label = "annual" if billing_period == "annual" else "monthly"
        description = f"IntelliResume {plan.get('name', plan_id).strip()} subscription ({period_label})"

        buyer_gstin = (profile.gstin if profile else "") if breakdown.applicable else ""
        financial_year = gst.financial_year(issued_at.astimezone(IST))

        with transaction.atomic():
            # Taking the sequence lock *before* the duplicate check means two
            # deliveries of the same payment are serialised, so the second
            # finds the first's invoice instead of burning a serial number
            # and leaving a gap in the series.
            InvoiceSequence.objects.select_for_update().get_or_create(financial_year=financial_year)
            existing = GSTInvoice.objects.filter(razorpay_payment_id=payment_id).first()
            if existing is not None:
                return existing
            number = next_invoice_number(supplier.invoice_prefix, financial_year)

            invoice = GSTInvoice.objects.create(
                number=number,
                kind=GSTInvoice.KIND_TAX_INVOICE if breakdown.applicable else GSTInvoice.KIND_INVOICE,
                issued_at=issued_at,
                financial_year=financial_year,
                user=user,
                razorpay_payment_id=payment_id,
                razorpay_subscription_id=razorpay_subscription_id or "",
                plan_id=plan_id or "",
                billing_period=billing_period or "",
                description=description[:200],
                seller_legal_name=supplier.legal_name or "IntelliResume",
                seller_gstin=supplier.gstin if breakdown.applicable else "",
                seller_address=supplier.address,
                seller_state_code=supplier.state_code,
                sac_code=supplier.sac_code if breakdown.applicable else "",
                buyer_name=((profile.legal_name if profile and profile.legal_name else "") or getattr(user, "name", "") or "")[:200],
                buyer_email=getattr(user, "email", "") or "",
                buyer_gstin=buyer_gstin,
                buyer_address=(profile.address if profile else "") or "",
                place_of_supply_code=breakdown.place_of_supply_code,
                place_of_supply_name=breakdown.place_of_supply_name,
                supply_type=breakdown.supply_type,
                rate_percent=breakdown.rate_percent,
                taxable_value=breakdown.taxable_value,
                cgst=breakdown.cgst,
                sgst=breakdown.sgst,
                igst=breakdown.igst,
                state_tax_label=breakdown.state_tax_label,
                total=breakdown.total,
                note=breakdown.reason[:300],
            )

        logger.info("Issued %s %s for payment %s.", invoice.get_kind_display(), invoice.number, payment_id)
        return invoice

    @staticmethod
    def summary(invoice):
        return {
            # The number contains slashes, so URLs address an invoice by id.
            "id": invoice.id,
            "number": invoice.number,
            "kind": invoice.kind,
            "kind_label": invoice.get_kind_display(),
            "issued_at": invoice.issued_at,
            "description": invoice.description,
            "currency": invoice.currency,
            "total": invoice.total,
            "total_display": f"₹{gst.rupees(invoice.total)}",
            "tax_total": invoice.cgst + invoice.sgst + invoice.igst,
        }

    @staticmethod
    def render_pdf(invoice):
        """A one-page A4 invoice carrying every particular the stored row
        holds. Text-based, like the resume PDFs, so it can be searched and
        read by an accountant's tools."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
            topMargin=16 * mm, bottomMargin=16 * mm,
            title=f"Invoice {invoice.number}", author=invoice.seller_legal_name,
        )
        # Helvetica has no rupee glyph, so amounts are written "Rs." and the
        # currency is stated in the header rather than risk an empty box.
        body = ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=12)
        small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
        heading = ParagraphStyle("heading", parent=body, fontName="Helvetica-Bold", fontSize=15, leading=19)
        label = ParagraphStyle("label", parent=body, fontName="Helvetica-Bold")

        def para(text, style=body):
            return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)

        def money(paise):
            return f"Rs. {gst.rupees(paise)}"

        title = "TAX INVOICE" if invoice.kind == GSTInvoice.KIND_TAX_INVOICE else "INVOICE"
        story = [para(title, heading), Spacer(1, 4)]

        seller_lines = [invoice.seller_legal_name, invoice.seller_address]
        if invoice.seller_gstin:
            seller_lines.append(f"GSTIN: {invoice.seller_gstin}")
        if invoice.seller_state_code:
            seller_lines.append(
                f"State: {gst.state_name(invoice.seller_state_code)} ({invoice.seller_state_code})"
            )

        buyer_lines = [invoice.buyer_name, invoice.buyer_email, invoice.buyer_address]
        if invoice.buyer_gstin:
            buyer_lines.append(f"GSTIN: {invoice.buyer_gstin}")
        if invoice.place_of_supply_code:
            buyer_lines.append(
                f"Place of supply: {invoice.place_of_supply_name} ({invoice.place_of_supply_code})"
            )

        meta = Table(
            [
                [para("Invoice no.", label), para(invoice.number), para("Date", label),
                 para(invoice.issued_at.astimezone(IST).strftime("%d %b %Y"))],
                [para("Currency", label), para("Indian Rupee (INR)"), para("Payment ref.", label),
                 para(invoice.razorpay_payment_id)],
            ],
            colWidths=[24 * mm, 56 * mm, 26 * mm, 68 * mm],
        )
        meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story += [meta, Spacer(1, 8)]

        parties = Table(
            [
                [para("Supplier", label), para("Billed to", label)],
                [para("\n".join(line for line in seller_lines if line)),
                 para("\n".join(line for line in buyer_lines if line))],
            ],
            colWidths=[87 * mm, 87 * mm],
        )
        parties.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#999999")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story += [parties, Spacer(1, 10)]

        header = ["Description", "SAC", "Taxable value"]
        row = [para(invoice.description), para(invoice.sac_code or "-"), para(money(invoice.taxable_value))]
        lines = Table([[para(h, label) for h in header], row], colWidths=[112 * mm, 22 * mm, 40 * mm])
        lines.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#999999")),
            ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.HexColor("#DDDDDD")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ]))
        story += [lines, Spacer(1, 6)]

        totals = [["Taxable value", money(invoice.taxable_value)]]
        if invoice.kind == GSTInvoice.KIND_TAX_INVOICE:
            half = _half_rate(invoice.rate_percent)
            if invoice.igst:
                totals.append([f"IGST @ {invoice.rate_percent}%", money(invoice.igst)])
            else:
                totals.append([f"CGST @ {half}%", money(invoice.cgst)])
                totals.append([f"{invoice.state_tax_label} @ {half}%", money(invoice.sgst)])
        totals.append(["Total", money(invoice.total)])

        totals_table = Table(
            [[para(k, label if k == "Total" else body), para(v, label if k == "Total" else body)] for k, v in totals],
            colWidths=[134 * mm, 40 * mm],
        )
        totals_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.HexColor("#999999")),
        ]))
        story += [totals_table, Spacer(1, 12)]

        if invoice.kind == GSTInvoice.KIND_TAX_INVOICE:
            story.append(para("Tax is not payable on reverse charge basis.", small))
            story.append(para("The amount charged includes GST.", small))
        elif invoice.note:
            story.append(para(invoice.note, small))
        story.append(Spacer(1, 4))
        story.append(para("This is a computer-generated invoice and does not require a signature.", small))

        doc.build(story)
        return buffer.getvalue()


def _half_rate(rate_percent):
    from decimal import Decimal

    half = Decimal(str(rate_percent or "0")) / 2
    return format(half.normalize(), "f")
