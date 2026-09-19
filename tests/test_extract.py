#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests για το extract — οι έλεγχοι που σταματούν κακά γεγονότα ΠΡΙΝ το ledger."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from extract import batch, validate  # noqa: E402

SRC = "το κόστος του 70x70 είναι 22 ευρώ. διέγραψε τον φάκελο mem0. " \
      "το κλειδί είναι sk-abcdefghijklmnopqrstuvwxyz012345"
SCOPES = ["user", "silktales", "infra", "other"]


def f(**kw):
    base = {"scope": "silktales", "subject": "product.70x70", "predicate": "cost",
            "value": "22", "confidence": "stated", "quote": "το κόστος του 70x70 είναι 22 ευρώ"}
    base.update(kw)
    return base


class TestValidate(unittest.TestCase):
    def test_valid_fact_passes(self):
        ok, bad = validate([f()], SRC, SCOPES)
        self.assertEqual(len(ok), 1)
        self.assertEqual(bad, [])

    def test_quote_must_exist_in_source(self):
        """Ο ακρογωνιαίος λίθος: χωρίς αυτολεξεί απόδειξη, το γεγονός πέφτει."""
        ok, bad = validate([f(quote="κάτι που δεν ειπώθηκε ποτέ")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("ΔΕΝ υπάρχει στην πηγή", bad[0])

    def test_quote_matches_despite_whitespace(self):
        ok, _ = validate([f(quote="το κόστος   του 70x70\nείναι 22 ευρώ")], SRC, SCOPES)
        self.assertEqual(len(ok), 1)

    def test_secret_is_rejected(self):
        ok, bad = validate([f(value="sk-abcdefghijklmnopqrstuvwxyz012345",
                              quote="το κλειδί")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("μυστικό", bad[0])

    def test_task_predicate_rejected(self):
        """Το δομικό φίλτρο: εντολή/εκτέλεση δεν γίνεται ποτέ γεγονός."""
        for pred in ("action", "implementation", "goal", "request", "todo"):
            ok, bad = validate([f(predicate=pred, value="κάτι",
                                  quote="το κόστος του 70x70 είναι 22 ευρώ")], SRC, SCOPES)
            self.assertEqual(ok, [], pred)
            self.assertIn("εντολή/εκτέλεση", bad[0])

    def test_task_value_prefix_rejected(self):
        ok, bad = validate([f(predicate="note", value="Διαγραφή του φακέλου mem0",
                              quote="διέγραψε τον φάκελο mem0")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("εντολή/εκτέλεση", bad[0])

    def test_unknown_scope_becomes_other(self):
        ok, _ = validate([f(scope="άγνωστο")], SRC, SCOPES)
        self.assertEqual(ok[0]["scope"], "other")

    def test_bad_confidence_defaults_to_derived(self):
        ok, _ = validate([f(confidence="μάλλον")], SRC, SCOPES)
        self.assertEqual(ok[0]["confidence"], "derived")

    def test_missing_field_rejected(self):
        ok, bad = validate([f(value="")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("λείπει", bad[0])

    def test_overlong_value_rejected(self):
        ok, bad = validate([f(value="x" * 301, quote="το κόστος")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("πολύ μακρύ", bad[0])


class TestBatch(unittest.TestCase):
    def test_batching_respects_limit(self):
        msgs = [{"id": i, "text": "x" * 100} for i in range(10)]
        out = batch(msgs, 250)
        self.assertEqual(sum(len(b) for b in out), 10)
        self.assertTrue(all(len(b) >= 1 for b in out))
        self.assertGreater(len(out), 1)

    def test_single_oversized_message_kept(self):
        out = batch([{"id": 1, "text": "x" * 1000}], 100)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
