# -*- coding: utf-8 -*-
"""Ο generator δεν ξαναδιαβάζει τη δική του όψη ως ανθρώπινη γνώση.

Πραγματικό περιστατικό (21/11/2026): sync με snapshot που είχε μείνει πίσω
εισήγαγε 5 γραμμές της ίδιας της MEMORY.md ως `handnote/note.<hash>` — θαμμένα
subjects που δεν ανακτώνται με `brief`. Το ίδιο είχε γίνει 17/11 με 18 subjects.

Δύο φράγματα, και τα δύο κώδικας:
  1. Χωρίς snapshot δεν γίνεται ανίχνευση χειροκίνητων αλλαγών (χωρίς baseline
     όλα μοιάζουν χειροκίνητα).
  2. Γραμμή με τη ΜΟΡΦΗ fact (`- subject · predicate = value`) δεν εισάγεται ποτέ,
     ό,τι κι αν λέει το snapshot — drift δεν παράγει facts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sync_memory as S  # noqa: E402

VIEW = (
    "ΠΑΡΑΓΟΜΕΝΟ από ledger — μη το γράφεις με το χέρι\n"
    "§\n"
    "— ανοιχτές εργασίες (όλα τα profiles) —\n"
    "§\n"
    "- ▸ car.beetle2003 · order = παραγγελία ανταλλακτικού [requested]\n"
    "§\n"
    "- infra.telegram · topology = gateway μέσω multiplexer\n"
    "§\n"
    "… (+96 ακόμη στο infra — `factledger show --scope infra`)\n"
)


def test_no_snapshot_means_no_hand_edit_detection():
    assert S.hand_edits(VIEW, "") == []
    assert S.hand_edits(VIEW, "   \n") == []


def test_stale_snapshot_does_not_turn_view_lines_into_facts():
    stale = VIEW.replace("§\n- infra.telegram · topology = gateway μέσω multiplexer\n", "")
    assert S.hand_edits(VIEW, stale) == []


def test_fact_shaped_line_is_never_imported_even_without_snapshot():
    line = "- style.links · expectation = σκέτο link → τι είναι + fact-check"
    assert S.looks_like_fact_line(line) is True
    assert S.looks_like_fact_line("Κανονική σημείωση του χρήστη για το έργο") is False


def test_handnote_subject_is_retrievable_not_an_identity_hash():
    assert S.view_subject("C:/x/hermes/profiles/it-section/memories/MEMORY.md") == "view.it-section"
    assert S.view_subject("C:/x/hermes/memories/MEMORY.md") == "view.default"


def test_real_operator_prose_is_still_captured():
    existing = "ΠΑΡΑΓΟΜΕΝΟ από ledger — μη το γράψεις με το χέρι\n§\nΘέλω το backup στις 03:00, όχι 02:00.\n"
    got = S.hand_edits(existing, "ΠΑΡΑΓΟΜΕΝΟ από ledger — μη το γράψεις με το χέρι\n")
    assert got == ["Θέλω το backup στις 03:00, όχι 02:00."]
