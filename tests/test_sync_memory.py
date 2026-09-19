#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests για το sync_memory — κυρίως η διάκριση «γεγονός» vs «θόρυβος του sync»."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sync_memory import hand_edits, is_generated_noise, parse_generated, render  # noqa: E402
from factledger import Ledger  # noqa: E402

SEP = "\n§\n"


class TestGeneratedVsHanded(unittest.TestCase):
    def test_noise_lines_are_recognised(self):
        for line in ("… (+3 ακόμη, δες `factledger show`)",
                     "… (τα υπόλοιπα scopes εκτός budget — `factledger show`)"):
            self.assertTrue(is_generated_noise(line), line)

    def test_real_note_is_not_noise(self):
        self.assertFalse(is_generated_noise("- tax.status · entity = ιδιώτης"))

    def test_hand_edits_ignores_overflow_note_changes(self):
        """Το regression: όταν αλλάζει το N στο «+N ακόμη», ΔΕΝ είναι χειροκίνητη αλλαγή."""
        snapshot = SEP.join(["- a · p = 1", "… (+2 ακόμη, δες `factledger show`)"]) + "\n"
        existing = SEP.join(["- a · p = 1", "… (+5 ακόμη, δες `factledger show`)"]) + "\n"
        self.assertEqual(hand_edits(existing, snapshot), [])

    def test_hand_edits_still_catches_a_real_addition(self):
        snapshot = SEP.join(["- a · p = 1"]) + "\n"
        existing = SEP.join(["- a · p = 1", "- b · p = 2"]) + "\n"
        self.assertEqual(hand_edits(existing, snapshot), ["- b · p = 2"])

    def test_hand_edits_catches_free_text_note(self):
        snapshot = ""
        existing = "κάτι που έγραψε ο χρήστης με το χέρι"
        self.assertEqual(hand_edits(existing, snapshot), ["κάτι που έγραψε ο χρήστης με το χέρι"])

    def test_parse_generated_only_takes_dash_lines(self):
        text = SEP.join(["- a · p = 1", "… (+1 ακόμη)", "ελεύθερο κείμενο"])
        self.assertEqual(parse_generated(text), ["- a · p = 1"])


class TestRenderQuotas(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.led = Ledger(os.path.join(self.tmp, "f.tsv"))
        for i in range(12):
            self.led.add("sc", f"sub{i:02d}", "p", "v" * 20, "src")

    def test_order_decides_what_survives_the_budget(self):
        """Ό,τι ορίζει το `order` ως σημαντικό κόβεται ΤΕΛΕΥΤΑΙΟ."""
        out = render(self.led, {"scopes": ["sc"], "budget": 200, "order": ["sub11", "sub00"]})
        self.assertIn("sub11", out)
        self.assertIn("sub00", out)

    def test_budget_is_respected(self):
        out = render(self.led, {"scopes": ["sc"], "budget": 150})
        self.assertLess(len(out), 250)

    def test_quota_prevents_one_scope_from_eating_everything(self):
        self.led.add("other", "x", "p", "value", "src")
        out = render(self.led, {"scopes": ["sc", "other"], "budget": 400,
                                "quotas": {"sc": 150, "other": 200}})
        self.assertIn("x", out)   # το μικρό scope επιβιώνει χάρη στην ποσόστωση


if __name__ == "__main__":
    unittest.main(verbosity=2)
