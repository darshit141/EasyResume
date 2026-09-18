"""Runtime GST configuration, and the one place that decides whether GST
applies to a given INR charge.

The arithmetic lives in billing.gst and never touches settings. This module
supplies it with the supplier's details as an operator has configured them
right now, through the admin Configuration tab, so registering for GST (or
correcting an address on the invoice) is a settings change rather than a
deploy.
"""

from dataclasses import dataclass

from django.conf import settings

from api.utils.config import get_config_value
from billing import gst


@dataclass(frozen=True)
class SupplierGST:
    enabled: bool
    rate_percent: str
    gstin: str
    state_code: str
    legal_name: str
    address: str
    sac_code: str
    invoice_prefix: str

    @property
    def registered(self):
        """GST may only be charged by a registered supplier. `enabled` is the
        operator's switch; a missing or malformed GSTIN overrules it, because
        printing a tax invoice without a valid GSTIN is not a tax invoice."""
        return self.enabled and bool(self.gstin) and gst.is_valid_state_code(self.state_code)


def _config(key):
    return get_config_value(key, getattr(settings, key, ""))


class GSTService:
    @staticmethod
    def supplier():
        gstin = gst.normalize_gstin(_config("GST_SELLER_GSTIN"))
        try:
            gstin = gst.validate_gstin(gstin) if gstin else ""
        except gst.GSTError:
            # Leaving it set would print an invalid number on every invoice.
            # Treating the supplier as unregistered is the safe failure: no
            # GST is collected until the number is corrected.
            gstin = ""

        state_code = str(_config("GST_SELLER_STATE_CODE") or "").strip()
        if gstin:
            # The GSTIN's own state wins over a separately typed code, which
            # could only ever disagree with it by mistake.
            state_code = gst.gstin_state_code(gstin)

        return SupplierGST(
            enabled=bool(_config("GST_ENABLED")),
            rate_percent=str(_config("GST_RATE_PERCENT") or "18").strip(),
            gstin=gstin,
            state_code=state_code,
            legal_name=str(_config("GST_SELLER_LEGAL_NAME") or "").strip(),
            address=str(_config("GST_SELLER_ADDRESS") or "").strip(),
            sac_code=str(_config("GST_SAC_CODE") or "").strip(),
            invoice_prefix=str(_config("GST_INVOICE_PREFIX") or "IR").strip(),
        )

    @staticmethod
    def breakdown(amount_paise, profile, supplier=None):
        """Tax an INR charge for this buyer, or say plainly why it isn't taxed."""
        supplier = supplier or GSTService.supplier()
        if not supplier.registered:
            return gst.not_applicable(
                amount_paise,
                "GST not charged: the supplier is not registered under GST.",
            )
        return gst.calculate_inclusive(
            amount_paise,
            supplier.rate_percent,
            supplier.state_code,
            getattr(profile, "state_code", ""),
            getattr(profile, "gstin", ""),
        )

    @staticmethod
    def price_tax_note(currency):
        """The one line under an INR price saying what it includes.

        Worded from the supplier's registration state, never from a literal:
        advertising "incl. 18% GST" while not registered would be claiming to
        collect a tax we are not permitted to collect.
        """
        if currency != "INR":
            return "Excludes VAT, calculated at checkout where applicable"
        supplier = GSTService.supplier()
        if supplier.registered:
            return f"Includes {supplier.rate_percent}% GST"
        return "Inclusive of all taxes"
