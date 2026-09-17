"""The GST arithmetic, tested on its own.

Every rule here ends up printed on a legal document, so the tests pin the
exact numbers rather than the general shape: a paisa out is a wrong invoice.
"""

from datetime import datetime

from django.test import SimpleTestCase

from billing import gst

# The GST portal's own worked example, and three other well-formed GSTINs.
VALID_GSTINS = ["27AAPFU0939F1ZV", "07AAGFF2194N1Z1", "33AAACH7409R1Z8", "24AAACC1206D1ZM"]


class GSTINValidationTests(SimpleTestCase):
    def test_well_formed_gstins_pass(self):
        for gstin in VALID_GSTINS:
            with self.subTest(gstin=gstin):
                self.assertEqual(gst.validate_gstin(gstin), gstin)

    def test_input_is_normalised_before_checking(self):
        self.assertEqual(gst.validate_gstin(" 27aapfu0939f1zv "), "27AAPFU0939F1ZV")
        self.assertEqual(gst.validate_gstin("27 AAPFU 0939F 1ZV"), "27AAPFU0939F1ZV")

    def test_a_single_mistyped_character_is_caught(self):
        """The whole point of the check digit: one wrong character in a
        GSTIN printed on an invoice denies the buyer their tax credit."""
        with self.assertRaisesMessage(gst.GSTError, "check digit"):
            gst.validate_gstin("27AAPFU0939F1ZW")
        with self.assertRaisesMessage(gst.GSTError, "check digit"):
            gst.validate_gstin("27AAPFU0938F1ZV")

    def test_wrong_length(self):
        with self.assertRaisesMessage(gst.GSTError, "15 characters"):
            gst.validate_gstin("27AAPFU0939F1Z")

    def test_wrong_shape(self):
        with self.assertRaisesMessage(gst.GSTError, "format"):
            gst.validate_gstin("2AAAPFU0939F1ZV")

    def test_unknown_state_code(self):
        # Shape is valid; "99" is not a state. Checksum recomputed so this
        # fails on the state and not on the check digit.
        body = "99AAPFU0939F1Z"
        with self.assertRaisesMessage(gst.GSTError, "state code"):
            gst.validate_gstin(body + gst.gstin_check_character(body))

    def test_legacy_state_codes_are_still_accepted_inside_a_gstin(self):
        body = "28AAPFU0939F1Z"
        gstin = body + gst.gstin_check_character(body)
        self.assertEqual(gst.validate_gstin(gstin), gstin)
        self.assertFalse(gst.is_valid_state_code("28"), "but not offered as a choice")


class InclusiveCalculationTests(SimpleTestCase):
    def test_intra_state_splits_into_cgst_and_sgst(self):
        result = gst.calculate_inclusive(49900, "18", "27", "27")

        self.assertTrue(result.applicable)
        self.assertEqual(result.supply_type, gst.SUPPLY_INTRA_STATE)
        self.assertEqual(result.taxable_value, 42288)
        self.assertEqual(result.cgst, 3806)
        self.assertEqual(result.sgst, 3806)
        self.assertEqual(result.igst, 0)
        self.assertEqual(result.total, 49900)
        self.assertEqual(result.state_tax_label, "SGST")

    def test_inter_state_is_all_igst(self):
        result = gst.calculate_inclusive(49900, "18", "27", "29")

        self.assertEqual(result.supply_type, gst.SUPPLY_INTER_STATE)
        self.assertEqual(result.taxable_value, 42288)
        self.assertEqual((result.cgst, result.sgst, result.igst), (0, 0, 7612))
        self.assertEqual(result.place_of_supply_name, "Karnataka")

    def test_the_customer_pays_the_same_wherever_they_are(self):
        """Inclusive pricing means the split changes, never the amount."""
        intra = gst.calculate_inclusive(399900, "18", "27", "27")
        inter = gst.calculate_inclusive(399900, "18", "27", "07")

        self.assertEqual(intra.total, inter.total)
        self.assertEqual(intra.total_tax, inter.total_tax)

    def test_an_odd_paisa_of_tax_goes_to_the_central_half(self):
        result = gst.calculate_inclusive(100, "18", "27", "27")

        self.assertEqual(result.taxable_value, 85)
        self.assertEqual((result.cgst, result.sgst), (8, 7))

    def test_parts_always_reconcile_to_the_total(self):
        for amount in list(range(0, 3000, 7)) + [49900, 99900, 399900, 799900, 1499999]:
            for customer in ("27", "07"):
                with self.subTest(amount=amount, customer=customer):
                    result = gst.calculate_inclusive(amount, "18", "27", customer)
                    self.assertEqual(
                        result.taxable_value + result.cgst + result.sgst + result.igst, amount
                    )

    def test_a_gstin_decides_the_place_of_supply(self):
        """A business registered in Delhi is billed as Delhi, even if the
        dropdown said Maharashtra."""
        result = gst.calculate_inclusive(49900, "18", "27", "27", customer_gstin="07AAGFF2194N1Z1")

        self.assertEqual(result.place_of_supply_code, "07")
        self.assertEqual(result.supply_type, gst.SUPPLY_INTER_STATE)

    def test_union_territory_without_a_legislature_uses_utgst(self):
        result = gst.calculate_inclusive(49900, "18", "04", "04")

        self.assertEqual(result.state_tax_label, "UTGST")

    def test_delhi_has_a_legislature_so_it_is_sgst(self):
        result = gst.calculate_inclusive(49900, "18", "07", "07")

        self.assertEqual(result.state_tax_label, "SGST")

    def test_an_unconfigured_supplier_state_is_an_error_not_a_guess(self):
        with self.assertRaisesMessage(gst.GSTError, "GST_SELLER_STATE_CODE"):
            gst.calculate_inclusive(49900, "18", "", "27")

    def test_a_customer_with_no_state_and_no_gstin_is_an_error(self):
        with self.assertRaises(gst.GSTError):
            gst.calculate_inclusive(49900, "18", "27", "")

    def test_negative_amounts_are_refused(self):
        with self.assertRaises(gst.GSTError):
            gst.calculate_inclusive(-1, "18", "27", "27")

    def test_not_applicable_says_why_and_charges_nothing_extra(self):
        result = gst.not_applicable(49900, "Supplier not registered.")

        self.assertFalse(result.applicable)
        self.assertEqual(result.total, 49900)
        self.assertEqual(result.taxable_value, 49900)
        self.assertEqual(result.total_tax, 0)
        self.assertEqual(result.reason, "Supplier not registered.")


class InvoiceNumberingTests(SimpleTestCase):
    def test_financial_year_runs_april_to_march(self):
        self.assertEqual(gst.financial_year(datetime(2026, 4, 1)), "2026-27")
        self.assertEqual(gst.financial_year(datetime(2027, 3, 31)), "2026-27")
        self.assertEqual(gst.financial_year(datetime(2026, 3, 31)), "2025-26")

    def test_number_format_is_within_the_statutory_sixteen_characters(self):
        number = gst.format_invoice_number("IR", "2026-27", 1)

        self.assertEqual(number, "IR/2627/000001")
        self.assertLessEqual(len(number), 16)

    def test_a_long_prefix_is_trimmed_to_stay_within_the_limit(self):
        number = gst.format_invoice_number("intelliresume", "2026-27", 999999)

        self.assertEqual(number, "INTE/2627/999999")
        self.assertLessEqual(len(number), 16)

    def test_only_permitted_characters_are_used(self):
        number = gst.format_invoice_number("i r!", "2026-27", 5)

        self.assertRegex(number, r"^[A-Z0-9/-]+$")


class RupeeFormattingTests(SimpleTestCase):
    def test_indian_digit_grouping(self):
        self.assertEqual(gst.rupees(49900), "499.00")
        self.assertEqual(gst.rupees(399900), "3,999.00")
        self.assertEqual(gst.rupees(1234567), "12,345.67")
        self.assertEqual(gst.rupees(12345678900), "12,34,56,789.00")
        self.assertEqual(gst.rupees(0), "0.00")
