# -*- coding: utf-8 -*-
"""audit: «θαμμένο» σημαίνει ΔΕΝ ανακτάται με θέμα — όχι «ένα fact ανά subject».

Ένα επίπεδο αλλά περιγραφικό subject (`shipping`, `email`) βρίσκεται κανονικά με
`brief <subject>`, άρα δεν είναι θαμμένο. Μόνο τα subject-ταυτότητες
(`note.<hash>`, `session.<id>`, γυμνό hex/uuid) δεν ανακτώνται.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factledger import FIELDS, Ledger, LedgerError, opaque_subject  # noqa: E402

REF = "2026-11-11"


@pytest.fixture()
def led(tmp_path):
    db = tmp_path / "facts.tsv"
    with open(db, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(FIELDS) + "\n")
    return Ledger(str(db))


def add(led, scope, subject, predicate, value, valid_from="2026-09-19"):
    return led.add(scope=scope, subject=subject, predicate=predicate, value=value,
                   source="test", valid_from=valid_from)


def test_note_hash_subject_is_fragmented(led):
    add(led, "handnote", "note.7b9137e5", "text", "κάτι")
    a = led.audit(14, ref=REF)
    assert a["counts"]["fragmented"] == 1
    assert a["counts"]["flat_subjects"] == 0


def test_session_subject_is_fragmented(led):
    add(led, "user", "session.20261017_200602_7b8a4e", "archive_status", "θα αρχειοθετηθεί")
    assert led.audit(14, ref=REF)["counts"]["fragmented"] == 1


def test_bare_hash_subject_is_fragmented(led):
    add(led, "cron", "b3dc21979c5e", "delivery", "deliver: local")
    assert led.audit(14, ref=REF)["counts"]["fragmented"] == 1


def test_flat_unique_descriptive_subject_is_NOT_fragmented(led):
    """Ο κανονικός συναγερμός που παραγόταν: `silktales/email` δεν είναι θαμμένο."""
    add(led, "silktales", "email", "contact", "info@silktales.gr")
    a = led.audit(14, ref=REF)
    assert a["counts"]["fragmented"] == 0
    assert a["counts"]["flat_subjects"] == 1


def test_flat_subject_with_several_facts_is_NOT_fragmented(led):
    add(led, "silktales", "shipping", "free_from", "€99")
    add(led, "silktales", "shipping", "carrier_cost", "ΕΛΤΑ €5,50")
    a = led.audit(14, ref=REF)
    assert a["counts"]["fragmented"] == 0
    assert a["counts"]["flat_subjects"] == 2


def test_dotted_subject_is_never_flat(led):
    add(led, "silktales", "shipping.free", "value", "€99")
    a = led.audit(14, ref=REF)
    assert a["counts"]["flat_subjects"] == 0


def test_fresh_flat_subject_not_counted(led):
    add(led, "silktales", "email", "contact", "info@silktales.gr", valid_from=REF)
    assert led.audit(14, ref=REF)["counts"]["flat_subjects"] == 0


def test_opaque_subject_helper():
    assert opaque_subject("note.7b9137e5")
    assert opaque_subject("session.20261017_200602_7b8a4e")
    assert opaque_subject("b3dc21979c5e")
    assert opaque_subject("da2e799e-8ed1-4180-8d0a-4d8df0732683")
    assert not opaque_subject("shipping")
    assert not opaque_subject("shipping.free")
    assert not opaque_subject("competitor.greekangels")
    assert not opaque_subject("case.silktales")
    # περιγραφικά subjects που ΞΕΚΙΝΟΥΝ με session./note. — δεν είναι θαμμένα
    assert not opaque_subject("session.archive")
    assert not opaque_subject("session.model")
    assert not opaque_subject("session.handoff")
    assert not opaque_subject("note.convention")
