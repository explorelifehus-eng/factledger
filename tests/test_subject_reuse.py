#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests για την ΕΠΑΝΑΧΡΗΣΗ ΘΕΜΑΤΩΝ: ο extractor δεν πρέπει να εφευρίσκει subjects
και ο sync δεν πρέπει να εισάγει τις δικές του γραμμές ως facts."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from extract import existing_topics, system_prompt  # noqa: E402
from sync_memory import is_generated_noise  # noqa: E402


class FakeLedger:
    def __init__(self, rows):
        self.rows = rows


class TestExistingTopics(unittest.TestCase):
    def rows(self):
        return [
            {"scope": "user", "subject": "car.beetle2003", "valid_from": "2026-11-16", "valid_to": ""},
            {"scope": "user", "subject": "car.beetle2003", "valid_from": "2026-11-10", "valid_to": ""},
            {"scope": "user", "subject": "style.tasks", "valid_from": "2026-11-14", "valid_to": ""},
            {"scope": "silktales", "subject": "product.70x70", "valid_from": "2026-11-12", "valid_to": ""},
            {"scope": "user", "subject": "κλειστό", "valid_from": "2026-11-15", "valid_to": "2026-11-16"},
        ]

    def test_dedupes_and_prefixes_scope(self):
        out = existing_topics(FakeLedger(self.rows()), ["user"])
        self.assertEqual(out.count("user/car.beetle2003"), 1)   # dedupe: ένα, όχι δύο
        self.assertIn("user/style.tasks", out)
        self.assertNotIn("silktales/", out)                     # scope filter
        self.assertNotIn("κλειστό", out)                        # κλειστή row δεν προσφέρεται

    def test_no_scope_filter_returns_all(self):
        out = existing_topics(FakeLedger(self.rows()), [])
        self.assertIn("silktales/product.70x70", out)

    def test_respects_char_budget(self):
        rows = [{"scope": "user", "subject": f"topic.{i:03d}xxxxxxxxxx", "valid_from": "2026-11-16",
                 "valid_to": ""} for i in range(500)]
        out = existing_topics(FakeLedger(rows), ["user"], max_chars=300)
        self.assertLessEqual(len(out), 300 + 2)
        self.assertGreater(out.count("/"), 0)

    def test_empty_ledger(self):
        self.assertEqual(existing_topics(FakeLedger([]), ["user"]), "")


class TestSystemPromptTopics(unittest.TestCase):
    def test_topics_injected_and_placeholder_gone(self):
        p = system_prompt(["user"], None, "user/car.beetle2003, user/style.tasks")
        self.assertIn("user/car.beetle2003", p)
        self.assertNotIn("{topics}", p)
        self.assertIn("ΕΠΑΝΑΧΡΗΣΗ ΘΕΜΑΤΟΣ", p)

    def test_without_topics_still_valid(self):
        p = system_prompt(["user"])
        self.assertNotIn("{topics}", p)
        self.assertIn("κανένα ακόμη", p)


class TestGeneratedNoise(unittest.TestCase):
    def test_generator_lines_are_noise(self):
        for s in ("… (+234 ακόμη στο user — `factledger show --scope user`)",
                  "ΠΑΡΑΓΟΜΕΝΟ από ledger — μη το γράφεις με το χέρι",
                  "— ανοιχτές εργασίες (όλα τα profiles) —",
                  "§"):
            self.assertTrue(is_generated_noise(s), s)

    def test_real_fact_is_not_noise(self):
        for s in ("- style.tasks · format = εργασίες με α/β/γ → πάντα πίνακας",
                  "car.beetle2003 · part = 1C0 959 831",
                  "— σκέψη του χρήστη που αρχίζει με παύλα"):
            self.assertFalse(is_generated_noise(s), s)


if __name__ == "__main__":
    unittest.main()
