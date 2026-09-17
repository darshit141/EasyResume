"""Indian GST for INR subscriptions: when it applies, how much, and how it splits.

Everything here is pure calculation — no database, no network — so every rule
can be tested exhaustively, and so an accountant reviewing the logic has one
short file to read rather than a trail through views and webhooks.

The model this implements, and the assumptions it rests on
-----------------------------------------------------------

IntelliResume is operated by an Indian private limited company. For an
Indian supplier of software-as-a-service:

  * GST is 18% (`GST_RATE_PERCENT`), charged only by a *registered*
    supplier. An unregistered supplier must not collect GST at all, which is
    why the whole thing sits behind `GST_ENABLED` and defaults off: turning
    it on is the moment the business has a GSTIN, not before.

  * INR prices are **inclusive** of GST. Indian consumers expect the
    advertised price to be the price they pay, and a GST-inclusive price
    means the amount charged never changes with the customer's state — only
    its breakdown does. The tax is therefore back-calculated out of the
    price, never added on top.

  * Place of supply decides the split. When it is the supplier's own state
    the supply is intra-state and the tax is half CGST and half SGST (or
    UTGST, in a union territory without its own legislature). Anywhere else
    it is inter-state and the whole amount is IGST. The total is 18% either
    way.

  * Place of supply is the customer's state on record. For a registered
    business customer it is the state encoded in their GSTIN, which cannot
    be contradicted by whatever they picked in a dropdown.

  * A registered business customer pays exactly the same amount as a
    consumer. Supplying their GSTIN changes the invoice, so they can claim
    the input tax credit, not the price. (Reverse charge applies to services
    *imported* from abroad; this is a domestic supply.)

  * EUR and GBP sales go through Stripe to customers outside India. Those
    are exports of services — zero-rated, normally supplied under a Letter
    of Undertaking — and nothing in this module is applied to them.

The SAC code, the invoice serial format and the decision to register are
questions for a chartered accountant, and are configuration here rather than
code for exactly that reason.
"""

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
import re

# The GST state codes, as used in the first two digits of every GSTIN and on
# every tax invoice's place-of-supply line.
#
# 25 (Daman and Diu) and 28 (the pre-2014 Andhra Pradesh) are real codes on
# older registrations, so they are accepted inside a GSTIN, but they are not
# offered as choices: Daman and Diu merged into 26 in 2020, and Andhra
# Pradesh registrations now use 37.
STATES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}

_LEGACY_STATE_CODES = {"25": "Daman and Diu", "28": "Andhra Pradesh (old)", "97": "Other Territory"}

# Union territories without a legislature levy UTGST rather than SGST on an
# intra-territory supply. Delhi, Puducherry and Jammu and Kashmir have
# legislatures, so they use SGST like a state.
UTGST_TERRITORIES = frozenset({"04", "26", "31", "35", "38"})

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

SUPPLY_INTRA_STATE = "intra_state"
SUPPLY_INTER_STATE = "inter_state"


class GSTError(ValueError):
    """Input that cannot produce a lawful tax calculation."""


def state_name(code):
    return STATES.get(code) or _LEGACY_STATE_CODES.get(code, "")


def is_valid_state_code(code):
    return code in STATES


def normalize_gstin(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def gstin_check_character(first_fourteen):
    """The 15th character of a GSTIN: a Luhn mod-36 check over the first 14.

    This is what catches a mistyped GSTIN before it is printed on an invoice
    a business then cannot claim credit against. It proves the number is
    well-formed, not that it is registered — only the GST portal can say
    that.
    """
    factor = 2
    total = 0
    for character in reversed(first_fourteen):
        value = factor * _GSTIN_ALPHABET.index(character)
        factor = 1 if factor == 2 else 2
        total += value // 36 + value % 36
    return _GSTIN_ALPHABET[(36 - total % 36) % 36]


def validate_gstin(value):
    """Return the normalised GSTIN, or raise GSTError saying what is wrong."""
    gstin = normalize_gstin(value)
    if len(gstin) != 15:
        raise GSTError("A GSTIN is 15 characters long.")
    if not GSTIN_PATTERN.match(gstin):
        raise GSTError("That isn't in the GSTIN format (for example 27AAPFU0939F1ZV).")
    if gstin[:2] not in STATES and gstin[:2] not in _LEGACY_STATE_CODES:
        raise GSTError("The first two digits of that GSTIN aren't a valid state code.")
    if gstin_check_character(gstin[:14]) != gstin[14]:
        raise GSTError("That GSTIN's check digit doesn't match — please check it for a typo.")
    return gstin


def gstin_state_code(gstin):
    return normalize_gstin(gstin)[:2]


def _paise(value):
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class GSTBreakdown:
    """One charge, taxed. Every amount is in paise and they always reconcile:
    taxable_value + cgst + sgst + igst == total, to the paisa."""

    applicable: bool
    total: int
    taxable_value: int
    rate_percent: str
    supply_type: str
    place_of_supply_code: str
    place_of_supply_name: str
    cgst: int
    sgst: int
    igst: int
    # "SGST" or "UTGST" — which name the state half carries on the invoice.
    state_tax_label: str
    # Set when GST was not charged, so an invoice can say why rather than
    # silently omitting the tax lines.
    reason: str = ""

    @property
    def total_tax(self):
        return self.cgst + self.sgst + self.igst

    def as_dict(self):
        data = asdict(self)
        data["total_tax"] = self.total_tax
        return data


def not_applicable(total, reason):
    """A charge on which no GST is levied, stated explicitly."""
    return GSTBreakdown(
        applicable=False,
        total=int(total),
        taxable_value=int(total),
        rate_percent="0",
        supply_type="",
        place_of_supply_code="",
        place_of_supply_name="",
        cgst=0,
        sgst=0,
        igst=0,
        state_tax_label="SGST",
        reason=reason,
    )


def place_of_supply(customer_state_code, customer_gstin=""):
    """Where a supply is made, for tax purposes.

    A registered customer's GSTIN is authoritative: it is the state the tax
    authority knows them by, and an invoice showing a different place of
    supply would deny them the credit they registered to claim.
    """
    if customer_gstin:
        return gstin_state_code(customer_gstin)
    if not is_valid_state_code(customer_state_code):
        raise GSTError("A valid Indian state is needed to work out GST.")
    return customer_state_code


def calculate_inclusive(total, rate_percent, supplier_state_code, customer_state_code, customer_gstin=""):
    """Break a GST-inclusive amount into taxable value and tax.

    `total` is what the customer pays, in paise. The taxable value is backed
    out of it (total × 100 / (100 + rate)), and whatever is left is the tax,
    so rounding can never make the parts disagree with the whole. On an
    intra-state supply the odd paisa, if there is one, goes to the central
    half — a convention, but a fixed one, so the same charge always produces
    the same invoice.
    """
    total = int(total)
    if total < 0:
        raise GSTError("A charge can't be negative.")

    rate = Decimal(str(rate_percent))
    if rate < 0:
        raise GSTError("A GST rate can't be negative.")
    if not is_valid_state_code(supplier_state_code):
        raise GSTError(
            "The supplier's GST state isn't configured, so the tax can't be split. "
            "Set GST_SELLER_STATE_CODE."
        )

    pos = place_of_supply(customer_state_code, customer_gstin)

    taxable_value = _paise(Decimal(total) * Decimal(100) / (Decimal(100) + rate))
    tax = total - taxable_value

    if pos == supplier_state_code:
        supply_type = SUPPLY_INTRA_STATE
        cgst = tax - tax // 2
        sgst = tax // 2
        igst = 0
    else:
        supply_type = SUPPLY_INTER_STATE
        cgst = sgst = 0
        igst = tax

    return GSTBreakdown(
        applicable=True,
        total=total,
        taxable_value=taxable_value,
        rate_percent=format(rate.normalize(), "f"),
        supply_type=supply_type,
        place_of_supply_code=pos,
        place_of_supply_name=state_name(pos),
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        state_tax_label="UTGST" if pos in UTGST_TERRITORIES else "SGST",
    )


def financial_year(moment):
    """The Indian financial year a date falls in, as "2026-27".

    Invoice serials restart every April, so this is part of every invoice
    number.
    """
    start = moment.year if moment.month >= 4 else moment.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def format_invoice_number(prefix, fy, sequence):
    """A GST invoice serial: unique within the financial year and at most 16
    characters, which is the statutory limit. Characters are restricted to
    letters, digits, '-' and '/', which is also the statutory set."""
    prefix = re.sub(r"[^A-Z0-9]", "", str(prefix or "").upper())[:4] or "INV"
    short_fy = fy.replace("-", "")[-4:]  # "2026-27" -> "2627"
    number = f"{prefix}/{short_fy}/{sequence:06d}"
    if len(number) > 16:
        raise GSTError(
            f"Invoice number {number} exceeds GST's 16-character limit — "
            "shorten GST_INVOICE_PREFIX."
        )
    return number


def rupees(paise):
    """Paise as a rupee string with Indian digit grouping: 1234567 -> '12,345.67'."""
    paise = int(paise)
    sign = "-" if paise < 0 else ""
    whole, fraction = divmod(abs(paise), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"{sign}{digits}.{fraction:02d}"
