#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests για το factledger — τα κρίσιμα είναι η δομική αλήθεια, όχι το happy path."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from factledger import Ledger, LedgerError  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "facts.tsv")
        self.led = Ledger(self.db)


class TestStructuralTruth(Base):
    def test_supersede_closes_previous(self):
        """ΤΟ ΚΡΙΣΙΜΟ ΤΕΣΤ (αυτό που απέτυχε το mem0): η νέα τιμή κλείνει την παλιά."""
        self.led.add("silktales", "product.70x70", "cost", "15", "session 19/9", valid_from="2026-09-01")
        r = self.led.add("silktales", "product.70x70", "cost", "22", "χρήστης 19/9", valid_from="2026-09-19")
        self.assertEqual(r["status"], "closed")
        act = self.led.active(subject="product.70x70")
        self.assertEqual([x["value"] for x in act], ["22"])          # ΜΙΑ ενεργή αλήθεια
        hist = self.led.history("product.70x70")
        self.assertEqual([x["value"] for x in hist], ["15", "22"])   # ιστορικό διατηρείται
        self.assertEqual(hist[0]["valid_to"], "2026-09-19")          # η παλιά έκλεισε
        self.assertEqual(hist[0]["superseded_by"], hist[1]["id"])     # με σύνδεσμο στη νέα

    def test_same_value_is_noop(self):
        self.led.add("s", "x", "p", "1", "src")
        r = self.led.add("s", "x", "p", "1", "src")
        self.assertEqual(r["status"], "unchanged")
        self.assertEqual(len(self.led.rows), 1)

    def test_as_of_returns_historical_truth(self):
        self.led.add("s", "x", "p", "15", "src", valid_from="2026-01-01")
        self.led.add("s", "x", "p", "22", "src", valid_from="2026-06-01")
        self.assertEqual(self.led.active(subject="x", as_of="2026-03-01")[0]["value"], "15")
        self.assertEqual(self.led.active(subject="x", as_of="2026-07-01")[0]["value"], "22")

    def test_source_is_mandatory(self):
        with self.assertRaises(LedgerError):
            self.led.add("s", "x", "p", "1", "   ")

    def test_unknown_confidence_rejected(self):
        with self.assertRaises(LedgerError):
            self.led.add("s", "x", "p", "1", "src", confidence="maybe")


class TestScopesAndDedup(Base):
    def test_scopes_are_independent(self):
        self.led.add("silktales", "price.earrings", "retail", "20", "src")
        self.led.add("personal", "price.earrings", "retail", "999", "src")
        self.assertEqual(len(self.led.active()), 2)
        self.assertEqual(len(self.led.active(scope="silktales")), 1)

    def test_key_is_normalized(self):
        """Ίδιο κλειδί με άλλα κεφάλαια/κενά → ίδια εγγραφή, όχι διπλή."""
        self.led.add("Silk Tales", "Product.70x70", "Cost", "15", "src")
        self.led.add("  silk   tales ", "product.70x70", "cost", "22", "src")
        self.assertEqual(len(self.led.rows), 2)
        self.assertEqual(len(self.led.active()), 1)
        self.assertEqual(self.led.active()[0]["value"], "22")

    def test_alias_map_collapses_predicates(self):
        al = os.path.join(self.tmp, "aliases.tsv")
        with open(al, "w", encoding="utf-8") as f:
            f.write("retail_price\tτιμή πώλησης,τιμη ραφι\n")
        led = Ledger(self.db, al)
        led.add("s", "x", "τιμή πώλησης", "20", "src")
        led.add("s", "x", "retail_price", "28", "src")
        self.assertEqual(len(led.active()), 1)
        self.assertEqual(led.active()[0]["value"], "28")


class TestCompileAndCheck(Base):
    def test_compile_respects_budget(self):
        for i in range(50):
            self.led.add("s", f"subject{i:02d}", "p", "v", "src")
        out = self.led.compile(budget=300)
        self.assertLessEqual(len(out), 400)
        self.assertIn("ακόμη", out)

    def test_compile_filters_scope(self):
        self.led.add("a", "x", "p", "1", "src")
        self.led.add("b", "y", "p", "2", "src")
        out = self.led.compile(["a"])
        self.assertIn("x", out)
        self.assertNotIn("y", out)

    def test_check_clean(self):
        self.led.add("s", "x", "p", "1", "src")
        self.led.add("s", "x", "p", "2", "src", valid_from="2026-12-31")
        self.assertEqual(self.led.check(), [])

    def test_check_detects_two_active(self):
        self.led.add("s", "x", "p", "1", "src")
        self.led.add("s", "x", "p", "2", "src")
        self.led.rows[-1]["valid_to"] = ""          # χειροκίνητη διαφθορά
        self.led.rows[0]["valid_to"] = ""
        self.led.rows[0]["superseded_by"] = ""
        probs = self.led.check()
        self.assertTrue(any("αντιφατική αλήθεια" in p for p in probs))

    def test_check_detects_broken_supersede_link(self):
        self.led.add("s", "x", "p", "1", "src")
        self.led.add("s", "x", "p", "2", "src")
        self.led.rows[0]["superseded_by"] = "f99999"
        self.assertTrue(any("ανύπαρκτο" in p for p in self.led.check()))


class TestPersistence(Base):
    def test_roundtrip(self):
        self.led.add("s", "x", "p", "1", "src")
        self.led.add("s", "x", "p", "2", "src", valid_from="2026-12-31")
        again = Ledger(self.db)
        self.assertEqual(len(again.rows), 2)
        self.assertEqual(again.active(subject="x")[0]["value"], "1")   # προγραμματισμένη αλλαγή: ισχύει ακόμη το 1
        self.assertEqual(again.active(subject="x", as_of="2027-01-01")[0]["value"], "2")
        self.assertEqual(again.check(), [])

    def test_missing_columns_rejected(self):
        with open(self.db, "w", encoding="utf-8") as f:
            f.write("id\tscope\nf1\ts\n")
        with self.assertRaises(LedgerError):
            Ledger(self.db)


if __name__ == "__main__":
    unittest.main(verbosity=2)
