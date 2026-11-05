#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests για το extract — οι έλεγχοι που σταματούν κακά γεγονότα ΠΡΙΝ το ledger."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from extract import batch, fetch_messages, system_prompt, validate  # noqa: E402

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

    def test_request_inside_value_rejected(self):
        """Αίτημα ΜΕΣΑ στο value δεν γίνεται κατάσταση.

        Πραγματικό περιστατικό: «calendar φτιάξε σύνδεση» βγήκε ως
        mcp.google.calendar status = «Δεν υπάρχει σύνδεση, ζητήθηκε να φτιαχτεί»
        και την επόμενη μέρα ήταν λάθος (το ημερολόγιο είχε συνδεθεί).
        """
        src = "calendar φτιάξε σύνδεση"
        ok, bad = validate([f(predicate="status",
                              value="Δεν υπάρχει σύνδεση, ζητήθηκε να φτιαχτεί",
                              quote="calendar φτιάξε σύνδεση")], src, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("εντολή/εκτέλεση", bad[0])

    def test_imperative_inside_value_rejected(self):
        src = "διέγραψε τον φάκελο mem0"
        ok, bad = validate([f(predicate="note", value="να διαγραφεί ο φάκελος mem0",
                              quote="διέγραψε τον φάκελο mem0")], src, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("εντολή/εκτέλεση", bad[0])

    def test_durable_procedure_not_rejected(self):
        """Το φίλτρο δεν σκοτώνει ό,τι περιγράφει μόνιμη διαδικασία: το «να γίνει»
        δεν είναι δείκτης-αίτημα (γι' αυτό δεν μπήκε στη λίστα)."""
        src = "μετά από κάθε ανανέωση πρέπει να γίνει snapshot του baseline"
        ok, _ = validate([f(predicate="procedure",
                            value="Μετά από κάθε ανανέωση: να γίνει snapshot του baseline",
                            quote="πρέπει να γίνει snapshot του baseline")], src, SCOPES)
        self.assertEqual(len(ok), 1)

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


class TestScopeFallback(unittest.TestCase):
    """`scope_fallback` (νέο knob): default "other" = παλιά συμπεριφορά, αλλά ένα
    profile με δικά του scopes το ανακατευθύνει, ώστε γεγονός με άγνωστο scope να
    ΜΗΝ μπορεί να πέσει σε scope που διαβάζει target άλλου profile."""

    def test_default_is_other(self):
        ok, _ = validate([f(scope="άγνωστο")], SRC, SCOPES)
        self.assertEqual(ok[0]["scope"], "other")

    def test_fallback_can_be_redirected(self):
        ok, _ = validate([f(scope="άγνωστο")], SRC, SCOPES, "crypto")
        self.assertEqual(ok[0]["scope"], "crypto")

    def test_known_scope_untouched(self):
        ok, _ = validate([f(scope="silktales")], SRC, SCOPES, "crypto")
        self.assertEqual(ok[0]["scope"], "silktales")


class TestRoles(unittest.TestCase):
    """`roles` (νέο knob): default ["user"] = παλιά συμπεριφορά. Μετράμε αν αξίζει
    να εξάγονται και assistant-side facts (paths, cron ids, thresholds)."""

    def _db(self, rows):
        p = os.path.join(tempfile.mkdtemp(), "state.db")
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
                  "role TEXT, content TEXT, timestamp REAL)")
        c.executemany("INSERT INTO messages VALUES (?,?,?,?,?)", rows)
        c.commit()
        c.close()
        return p

    ROWS = [(1, "s", "user", "u" * 60, 0.0),
            (2, "s", "assistant", "a" * 60, 0.0),
            (3, "s", "tool", "t" * 60, 0.0),
            (4, "s", "assistant", "short", 0.0)]

    def test_default_roles_is_user_only(self):
        got = fetch_messages({"state_db": self._db(self.ROWS)}, 0, None, 25)
        self.assertEqual([m["id"] for m in got], [1])

    def test_roles_can_include_assistant(self):
        got = fetch_messages({"state_db": self._db(self.ROWS), "roles": ["user", "assistant"]},
                             0, None, 25)
        self.assertEqual([m["id"] for m in got], [1, 2])   # 4 κόπηκε από min_chars, 3 από role

    def test_roles_accepts_single_assistant(self):
        got = fetch_messages({"state_db": self._db(self.ROWS), "roles": ["assistant"]},
                             0, None, 25)
        self.assertEqual([m["id"] for m in got], [2])

    def test_since_id_and_limit_still_apply(self):
        got = fetch_messages({"state_db": self._db(self.ROWS), "roles": ["user", "assistant"]},
                             1, 1, 25)
        self.assertEqual([m["id"] for m in got], [2])


class TestSecrets(unittest.TestCase):
    """SECRET_RE μετά το περιστατικό Helius: πιάνει credential-grade tokens,
    ΔΕΝ πιάνει ids/hashes (χωρίς false positives)."""

    def test_helius_uuid_key_rejected(self):
        ok, bad = validate([f(value="677194af-520a-4097-8047-f3deea9187ed",
                              quote="677194af-520a-4097-8047-f3deea9187ed api key")],
                           SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("μυστικό", bad[0])

    def test_uuid_in_quote_rejected(self):
        """Αρκεί το quote να κουβαλά το κλειδί: η εγγραφή πέφτει."""
        ok, bad = validate([f(value="Helius endpoint ενεργό",
                              quote="key 0f8fad5b-d9cb-469f-a165-70867728950e")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("μυστικό", bad[0])

    def test_url_api_key_rejected(self):
        ok, bad = validate([f(value="https://mainnet.helius-rpc.com/?api-key=abc123def456",
                              quote="quoted")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("μυστικό", bad[0])

    def test_bare_32hex_rejected(self):
        ok, bad = validate([f(value="token " + "a1b2c3d4" * 4, quote="quoted")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("μυστικό", bad[0])

    def test_cron_id_12hex_not_flagged(self):
        ok, _ = validate([f(value="job 0025e43232df, κάθε 1 λεπτό",
                            quote="το κόστος του 70x70 είναι 22 ευρώ")], SRC, SCOPES)
        self.assertEqual(len(ok), 1)

    def test_solana_mint_not_flagged(self):
        ok, _ = validate([f(value="mint EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                            quote="το κόστος του 70x70 είναι 22 ευρώ")], SRC, SCOPES)
        self.assertEqual(len(ok), 1)

    def test_sha256_not_flagged(self):
        ok, _ = validate([f(value="digest " + "ab" * 32,
                            quote="το κόστος του 70x70 είναι 22 ευρώ")], SRC, SCOPES)
        self.assertEqual(len(ok), 1)

    def test_sha1_not_flagged(self):
        ok, _ = validate([f(value="commit " + "ab" * 20,
                            quote="το κόστος του 70x70 είναι 22 ευρώ")], SRC, SCOPES)
        self.assertEqual(len(ok), 1)


class TestPermanence(unittest.TestCase):
    """Φίλτρο μονιμότητας: κόβει παροδικά status/πρόοδο, κρατά διαρκή κατάσταση."""

    def test_durable_status_survives(self):
        ok, _ = validate([f(predicate="status", value="ΕΝΕΡΓΟ με gh token",
                            quote="το κόστος του 70x70 είναι 22 ευρώ")], SRC, SCOPES)
        self.assertEqual(len(ok), 1)

    def test_transient_status_with_clock_rejected(self):
        ok, bad = validate([f(predicate="status", value="διορθώθηκε· επόμενο στις 15:28",
                              quote="quoted")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("παροδική", bad[0])

    def test_transient_progress_count_rejected(self):
        ok, bad = validate([f(predicate="progress", value="267 tests passed",
                              quote="quoted")], SRC, SCOPES)
        self.assertEqual(ok, [])
        self.assertIn("παροδική", bad[0])

    def test_schedule_with_clock_survives(self):
        """Το ρολόι μόνο του δεν είναι παροδικό: ένα SCHEDULE είναι διαρκές."""
        ok, _ = validate([f(predicate="schedule", value="καθημερινά 09:55, σιωπή όταν OK",
                            quote="το κόστος του 70x70 είναι 22 ευρώ")], SRC, SCOPES)
        self.assertEqual(len(ok), 1)

    def test_filter_can_be_disabled(self):
        ok, _ = validate([f(predicate="status", value="επόμενο στις 15:28",
                            quote="το κόστος του 70x70 είναι 22 ευρώ")],
                         SRC, SCOPES, "other", set())
        self.assertEqual(len(ok), 1)


class TestPromptVariant(unittest.TestCase):
    """Το prompt ξέρει ποια πλευρά διαβάζει (roles) — αλλιώς το confidence ψεύδεται."""

    def test_default_is_user(self):
        p = system_prompt(SCOPES)
        self.assertIn("ΜΗΝΥΜΑΤΑ ΧΡΗΣΤΗ", p)
        self.assertNotIn("ΜΗΝΥΜΑΤΑ ΒΟΗΘΟΥ", p)

    def test_assistant_variant(self):
        p = system_prompt(SCOPES, ["assistant"])
        self.assertIn("ΜΗΝΥΜΑΤΑ ΒΟΗΘΟΥ", p)
        self.assertNotIn("ΜΗΝΥΜΑΤΑ ΧΡΗΣΤΗ", p)
        self.assertIn("τεχνική αναφορά", p)

    def test_no_leftover_placeholders(self):
        for roles in (["user"], ["assistant"]):
            p = system_prompt(SCOPES, roles)
            for ph in ("{scopes}", "{msgs_label}", "{who_nom}", "{who_gen}", "{stated_rule}"):
                self.assertNotIn(ph, p, roles)

    def test_role_labels_fallback(self):
        self.assertIn("ΜΗΝΥΜΑΤΑ ΧΡΗΣΤΗ", system_prompt(SCOPES, ["unknown-role"]))


class TestStrictPermanence(unittest.TestCase):
    """Task 2 (assistant-side): με transient_strict το predicate αρκεί — ΟΛΟ το
    status/progress/state πέφτει, όχι μόνο ό,τι έχει ρολόι/μετρητή."""

    def test_strict_cuts_durable_looking_status(self):
        ok, bad = validate([f(predicate="status", value="ΕΝΕΡΓΟ με gh token",
                              quote="το κόστος του 70x70 είναι 22 ευρώ")],
                           SRC, SCOPES, "other", None, None, True)
        self.assertEqual(ok, [])
        self.assertIn("predicate-only", bad[0])
        self.assertIn("ΕΝΕΡΓΟ", bad[0])          # το value μπαίνει στο διαγνωστικό

    def test_strict_leaves_other_predicates_alone(self):
        ok, _ = validate([f(predicate="cost", value="22",
                            quote="το κόστος του 70x70 είναι 22 ευρώ")],
                         SRC, SCOPES, "other", None, None, True)
        self.assertEqual(len(ok), 1)

    def test_non_strict_keeps_durable_status(self):
        ok, _ = validate([f(predicate="status", value="ΕΝΕΡΓΟ με gh token",
                            quote="το κόστος του 70x70 είναι 22 ευρώ")], SRC, SCOPES)
        self.assertEqual(len(ok), 1)             # user-side: δεν πειράζεται

    def test_strict_also_cuts_progress_and_state(self):
        for pred in ("progress", "state"):
            ok, bad = validate([f(predicate=pred, value="όλα πράσινα",
                                  quote="το κόστος του 70x70 είναι 22 ευρώ")],
                               SRC, SCOPES, "other", None, None, True)
            self.assertEqual(ok, [], pred)
            self.assertIn("predicate-only", bad[0])


class TestScopeLeak(unittest.TestCase):
    """Task 1: ΚΑΜΙΑ διαρροή — κάθε άγνωστο scope πέφτει στο crypto, ποτέ στα
    scopes που διαβάζει το δικό μου MEMORY.md/USER.md (user/other/hermes/…)."""
    CRYPTO = ["crypto", "cex", "dex", "kraken", "wallet", "cron"]
    DEFAULT_SCOPES = {"user", "other", "silktales", "hermes", "infra", "market", "handnote"}

    def test_unknown_scope_falls_back_to_crypto(self):
        for sc in ("solana", "user", "other", "hermes", "whatever"):
            ok, _ = validate([f(scope=sc, quote="το κόστος του 70x70 είναι 22 ευρώ")],
                             SRC, self.CRYPTO, "crypto")
            self.assertEqual(ok[0]["scope"], "crypto", sc)

    def test_no_scope_can_target_default_memory(self):
        for sc in self.CRYPTO:
            self.assertNotIn(sc, self.DEFAULT_SCOPES, sc)


class TestCryptoConfig(unittest.TestCase):
    """Task 1: φρουρά στη ρύθμιση — αν κάποιος ξαναβάλει άδειο/διαρρέον scope, σκάει."""

    CFG = os.environ.get("FACTLEDGER_CRYPTO_CONFIG",
                         r"C:\Users\ionma\HERMES VAULT\MEMORY\extract.crypto.json")

    @unittest.skipUnless(os.path.exists(CFG), "crypto config δεν υπάρχει εδώ")
    def test_crypto_config_no_leak_and_taxonomy(self):
        import json
        cfg = json.load(open(self.CFG, encoding="utf-8"))
        scopes = set(cfg["scopes"])
        self.assertFalse(scopes & TestScopeLeak.DEFAULT_SCOPES)
        self.assertIn(cfg.get("scope_fallback"), scopes)     # το fallback είναι δικό του
        self.assertNotIn("solana", scopes)                   # 0 facts → βγήκε
        self.assertIn("wallet", scopes)                      # 3 facts → μένει
        self.assertNotEqual(cfg.get("transient_strict"), True)  # user-side: όχι strict


class TestPromptDirectives(unittest.TestCase):
    """Task 1β/3: οι οδηγίες ταξινομίας και ειδικότητας subject φτάνουν στο prompt."""

    def test_taxonomy_block(self):
        p = system_prompt(SCOPES, ["user"])
        self.assertIn("ΤΑΞΙΝΟΜΙΑ", p)
        self.assertIn("ΠΟΤΕ «user», «other»", p)
        self.assertIn("AMM", p)
        self.assertIn("wallet", p)

    def test_subject_specificity_block(self):
        for roles in (["user"], ["assistant"]):
            p = system_prompt(SCOPES, roles)
            self.assertIn("Ειδικό ως προς το πλαίσιο", p, roles)
            self.assertIn("trade.sl.cex", p, roles)
            self.assertIn("exit.tp.dex", p, roles)


if __name__ == "__main__":
    unittest.main(verbosity=2)
