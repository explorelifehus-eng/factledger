# -*- coding: utf-8 -*-
"""Tests για τις προσθήκες: stale / export / compile --order recency."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from factledger import Ledger  # noqa: E402


@pytest.fixture()
def led(tmp_path):
    db = tmp_path / "facts.tsv"
    ledger = Ledger(str(db))
    ledger.add("market", "a.price", "value", "10", "test", "stated", "2026-01-01")
    ledger.add("market", "b.price", "value", "20", "test", "stated", "2026-10-01")
    ledger.add("infra", "c.token", "ttl", "24h", "test", "stated", "2026-09-01")
    return ledger


def test_stale_returns_only_old_active_facts(led):
    rows = led.stale(30, ref="2026-10-30")
    names = {r["subject"] for r, _ in rows}
    assert names == {"a.price", "c.token"}          # b.price είναι 29 ημερών
    assert all(age >= 30 for _, age in rows)
    ages = [age for _, age in rows]
    assert ages == sorted(ages, reverse=True)       # πιο παλιά πρώτα


def test_stale_scope_filter(led):
    rows = led.stale(30, scopes=["infra"], ref="2026-10-31")
    assert [r["subject"] for r, _ in rows] == ["c.token"]


def test_stale_ignores_superseded(led):
    led.add("market", "a.price", "value", "11", "test", "stated", "2026-09-01")
    rows = led.stale(30, ref="2026-10-31")
    subjects = [r["subject"] for r, _ in rows]
    assert "a.price" in subjects
    assert all(r["value"] != "10" for r, _ in rows)  # η παλιά έκλεισε


def test_export_defaults_to_active_only(led):
    data = led.export()
    assert len(data) == 3
    assert set(data[0]) >= {"id", "scope", "subject", "predicate", "value",
                            "confidence", "valid_from", "valid_to"}
    json.dumps(data)  # serializable


def test_export_scope_and_history(led):
    led.add("market", "a.price", "value", "11", "test", "stated", "2026-09-01")
    assert len(led.export(scopes=["market"])) == 2
    assert len(led.export(scopes=["market"], include_history=True)) == 3


def test_compile_recency_puts_newest_first(led):
    text = led.compile(["market"], budget=800, order="recency")
    lines = [l for l in text.splitlines() if l.startswith("- ")]
    assert "b.price" in lines[0]


def test_compile_confidence_order_unchanged(led):
    text = led.compile(["market"], budget=800)
    assert text.splitlines()[0].startswith("- a.price")
