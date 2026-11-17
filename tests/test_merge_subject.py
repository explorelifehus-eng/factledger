#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests για `merge-subject` και `audit --check`.

Το merge-subject υπάρχει επειδή το `add` σε νέο subject ΔΕΝ κλείνει την πηγή:
μια μεταγραφή με δύο χειροκίνητα βήματα ανά fact αφήνει διπλές αλήθειες μόλις
ξεχαστεί το ένα σκέλος. Εδώ ελέγχεται ότι (α) το dry-run δεν γράφει, (β) το
apply κάνει add+close ατομικά, (γ) μια σύγκρουση τιμών μπλοκάρει τα πάντα, και
(δ) το `audit --check` δίνει rc≠0 ώστε να μπορεί να το φυλάει cron.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factledger import Ledger, LedgerError  # noqa: E402

CLI = str(ROOT / "factledger.py")


@pytest.fixture()
def led(tmp_path):
    l = Ledger(str(tmp_path / "facts.tsv"))
    l.add("silktales", "order.old", "status", "requested", "test", "stated", "2026-11-01")
    l.add("silktales", "order.old", "price", "12", "test", "stated", "2026-11-02")
    return l


def active(l, subject):
    return {r["predicate"]: r["value"] for r in l.active(subject=subject)}


def test_dry_run_writes_nothing(led):
    before = list(led.rows)
    m = led.merge_subject("order.old", "order.new")
    assert m["status"] == "dry-run"
    assert len(m["plan"]) == 2
    assert led.rows == before
    assert active(led, "order.old") == {"status": "requested", "price": "12"}


def test_apply_moves_and_closes(led):
    m = led.merge_subject("order.old", "order.new", apply=True)
    assert m["status"] == "applied"
    assert len(m["added"]) == 2 and len(m["closed"]) == 2
    assert active(led, "order.old") == {}                      # πηγή έκλεισε
    assert active(led, "order.new") == {"status": "requested", "price": "12"}
    # η μεταγραφή ξεκινά σήμερα (το `check` απαιτεί διάδοχο με valid_from = valid_to της παλιάς)·
    # η αρχική ημερομηνία διατηρείται στο source
    assert {r["valid_from"] for r in led.active(subject="order.new")} == {__import__("factledger").today()}
    assert any("ίσχυε από 2026-11-01" in r["source"]
               for r in led.rows if r["subject"] == "order.new")
    old = {r["id"]: r for r in led.rows if r["id"] in m["closed"]}
    assert all(r["superseded_by"] in m["added"] for r in old.values())
    assert all("merged:" in r["source"] for r in old.values())
    assert led.check() == []


def test_value_conflict_blocks_everything(tmp_path):
    l = Ledger(str(tmp_path / "facts.tsv"))
    l.add("silktales", "order.old", "status", "requested", "test", "stated", "2026-11-01")
    l.add("silktales", "order.new", "status", "shipped", "test", "stated", "2026-11-05")
    before = list(l.rows)
    m = l.merge_subject("order.old", "order.new", apply=True)
    assert m["status"] == "conflict"
    assert m["conflicts"][0]["kind"] == "value_conflict"
    assert l.rows == before                    # ούτε add ούτε close
    assert active(l, "order.old") == {"status": "requested"}
    assert active(l, "order.new") == {"status": "shipped"}


def test_redundant_is_closed_not_duplicated(tmp_path):
    l = Ledger(str(tmp_path / "facts.tsv"))
    l.add("silktales", "order.old", "status", "requested", "test", "stated", "2026-11-01")
    l.add("silktales", "order.new", "status", "requested", "test", "stated", "2026-11-03")
    m = l.merge_subject("order.old", "order.new", apply=True)
    assert m["status"] == "applied"
    assert m["added"] == [] and len(m["closed"]) == 1
    assert len([r for r in l.rows if r["subject"] == "order.new"]) == 1   # κανένα διπλό
    assert active(l, "order.old") == {}
    assert l.check() == []


def test_scope_filter(led):
    led.add("infra", "order.old", "note", "άλλο scope", "test", "stated", "2026-11-03")
    m = led.merge_subject("order.old", "order.new", scopes=["silktales"], apply=True)
    assert len(m["plan"]) == 2
    assert active(led, "order.old") == {"note": "άλλο scope"}   # άθικτο


def test_rejects_same_or_missing_subject(led):
    with pytest.raises(LedgerError):
        led.merge_subject("order.old", "order.old")
    with pytest.raises(LedgerError):
        led.merge_subject("δεν.υπάρχει", "order.new")


def test_cli_merge_then_audit_check(tmp_path):
    db = str(tmp_path / "facts.tsv")
    l = Ledger(db)
    l.add("silktales", "note.7b9137e5", "rule", "θαμμένο", "test", "stated", "2026-11-01")
    l.add("silktales", "order.old", "status", "requested", "test", "stated", "2026-11-01")

    # 1. dry-run: rc 0, τίποτα γραμμένο
    r = subprocess.run([sys.executable, CLI, "--db", db, "merge-subject",
                        "--from", "order.old", "--to", "order.new"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "DRY-RUN" in r.stdout
    assert Ledger(db).active(subject="order.old")

    # 2. audit --check: τα θαμμένα σπάνε το gate
    r = subprocess.run([sys.executable, CLI, "--db", db, "audit", "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1
    assert "CHECK FAIL" in r.stderr

    # 3. apply: μεταγραφή + κλείσιμο πηγής
    r = subprocess.run([sys.executable, CLI, "--db", db, "merge-subject",
                        "--from", "order.old", "--to", "order.new", "--apply"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "✓" in r.stdout
    l2 = Ledger(db)
    assert l2.active(subject="order.old") == []
    assert {x["predicate"] for x in l2.active(subject="order.new")} == {"status"}

    # 4. μόλις καθαρίσει το θαμμένο, το gate ανοίγει
    l2.merge_subject("note.7b9137e5", "silktales.ops", apply=True)
    r = subprocess.run([sys.executable, CLI, "--db", db, "audit", "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "CHECK OK" in r.stdout
