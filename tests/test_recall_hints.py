# -*- coding: utf-8 -*-
"""Tests για τα σήματα ανάκλησης του 1.3.0 — δύο τυφλά σημεία που κόστισαν απάντηση.

Πραγματικά περιστατικά (21/11/2026):
  1. `brief factledger` έδωσε 44 ενεργά και ΠΑΛΙΑ εικόνα, επειδή τα facts που
     έκλειναν το θέμα ζούσαν κάτω από άλλο subject (`hackathon.hermes_plugin`)
     και αναφέρουν το έργο μόνο στην ΤΙΜΗ. Με `--deep` = 70 ενεργά.
  2. Η εργασία `winners_check` έμεινε `requested` μετά την ανακοίνωση των νικητών
     (νέο fact στο ίδιο subject) και το tasks block την έδειχνε ως ζωντανή.

Τα tests κλειδώνουν:
  1. Το `brief` μετρά και δείχνει το «υπάρχει και αλλού» (τιμές), με τα θέματα.
  2. Το `brief` δεν διπλομετρά ό,τι ήδη βρήκε· με `--deep` δεν βγάζει υπόδειξη.
  3. Μια εργασία σημαίνεται όταν γράφτηκε νεότερο fact στο ΙΔΙΟ subject.
  4. Η επισήμανση ταξιδεύει στην compiled όψη (tasks block του sync).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import factledger as F  # noqa: E402
import sync_memory as S  # noqa: E402


def make(tmp_path, rows):
    p = tmp_path / "facts.tsv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(F.FIELDS) + "\n")
        for r in rows:
            f.write("\t".join(r.get(k, "") for k in F.FIELDS) + "\n")
    return F.Ledger(str(p))


def row(id_, subject, predicate, value, vfrom, scope="t", vto="", sup=""):
    return {"id": id_, "scope": scope, "subject": subject, "predicate": predicate,
            "value": value, "valid_from": vfrom, "valid_to": vto,
            "superseded_by": sup, "source": "test", "confidence": "stated"}


def test_brief_counts_value_only_matches_and_names_the_topics(tmp_path):
    led = make(tmp_path, [
        row("f1", "factledger.repo", "path", "C:/Users/ionma/factledger", "2026-11-06"),
        row("f2", "hackathon.hermes_plugin", "results",
            "το factledger δεν μπήκε στο top 10", "2026-11-20"),
    ])
    b = led.brief("factledger", ref="2026-11-21")
    assert {a["row"]["id"] for a in b["active"]} == {"f1"}
    assert b["related"]["facts"] == 1
    assert b["related"]["topics"] == ["hackathon"]


def test_brief_deep_pulls_the_value_only_fact_and_drops_the_hint(tmp_path):
    led = make(tmp_path, [
        row("f1", "factledger.repo", "path", "C:/Users/ionma/factledger", "2026-11-06"),
        row("f2", "hackathon.hermes_plugin", "results",
            "το factledger δεν μπήκε στο top 10", "2026-11-20"),
    ])
    b = led.brief("factledger", ref="2026-11-21", deep=True)
    assert {a["row"]["id"] for a in b["active"]} == {"f1", "f2"}
    assert b["related"] == {"facts": 0, "topics": [], "subjects": []}


def test_brief_does_not_double_count_rows_it_already_returned(tmp_path):
    led = make(tmp_path, [
        row("f1", "hackathon.catalog", "note", "το hackathon catalog 1.508 εγγραφές", "2026-11-13"),
    ])
    b = led.brief("hackathon", ref="2026-11-21")
    assert len(b["active"]) == 1
    assert b["related"]["facts"] == 0


def test_task_flagged_when_a_newer_fact_exists_on_the_same_subject(tmp_path):
    led = make(tmp_path, [
        row("f1", "hackathon.hermes_plugin", "task.winners_check",
            F.render_task("έλεγχος νικητών (email + X)", "requested", "agent", "",
                          "2026-11-19"), "2026-11-13"),
        row("f2", "hackathon.hermes_plugin", "results",
            "ΑΝΑΚΟΙΝΩΘΗΚΑΝ 20/11/2026 — top 10", "2026-11-20"),
    ])
    rows = led.tasks(ref="2026-11-21")
    assert len(rows) == 1
    assert rows[0]["newer"]["id"] == "f2"
    assert rows[0]["newer"]["valid_from"] == "2026-11-20"


def test_task_not_flagged_by_older_or_foreign_subjects(tmp_path):
    led = make(tmp_path, [
        row("f1", "car.beetle2003", "task.order",
            F.render_task("παραγγελία ανταλλακτικού", "requested", "agent", "",
                          "2026-11-18"), "2026-11-17"),
        row("f2", "car.beetle2003", "price", "41.43", "2026-11-10"),   # παλαιότερο
        row("f3", "car.beetle2003.parts", "note", "άλλο subject", "2026-11-20"),
    ])
    rows = led.tasks(ref="2026-11-21")
    assert rows[0]["newer"] is None


def test_closed_tasks_are_never_flagged(tmp_path):
    led = make(tmp_path, [
        row("f1", "hackathon.hermes_plugin", "task.winners_check",
            F.render_task("έλεγχος νικητών", "done", "agent", "f02913"), "2026-11-21"),
        row("f2", "hackathon.hermes_plugin", "results", "νικητές 20/11", "2026-11-20"),
    ])
    rows = led.tasks(include_done=True, ref="2026-11-21")
    assert rows[0]["newer"] is None


def test_brief_marks_a_superseded_task_inline(tmp_path):
    led = make(tmp_path, [
        row("f1", "hackathon.hermes_plugin", "task.winners_check",
            F.render_task("έλεγχος νικητών", "requested", "agent", "",
                          "2026-11-19"), "2026-11-13"),
        row("f2", "hackathon.hermes_plugin", "results", "νικητές 20/11", "2026-11-20"),
    ])
    b = led.brief("hackathon", ref="2026-11-21")
    task = next(a for a in b["active"] if a["row"]["id"] == "f1")
    assert task["newer"]["id"] == "f2"


def test_not_overdue_task_is_not_flagged_even_with_a_newer_fact(tmp_path):
    """Το σήμα κρατιέται σπάνιο: μόνο overdue + νεότερο fact. Αλλιώς «βρίσκει» πάντα."""
    led = make(tmp_path, [
        row("f1", "hackathon.registry", "task.winners_report",
            F.render_task("παρουσίαση νικητών", "requested", "agent", "",
                          "2027-01-24"), "2026-11-16"),
        row("f2", "hackathon.registry", "monitoring", "απόφαση: κανένας νέος έλεγχος",
            "2026-11-20"),
    ])
    rows = led.tasks(ref="2026-11-21")
    assert rows[0]["overdue"] is False
    assert rows[0]["newer"] is None


def test_compiled_view_carries_the_newer_fact_marker(tmp_path):
    led = make(tmp_path, [
        row("f1", "hackathon.hermes_plugin", "task.winners_check",
            F.render_task("έλεγχος νικητών", "requested", "agent", "", "2026-11-19"),
            "2026-11-13", scope="hackathon"),
        row("f2", "hackathon.hermes_plugin", "results", "νικητές 20/11",
            "2026-11-20", scope="hackathon"),
    ])
    target = {"path": str(tmp_path / "MEMORY.md"), "budget": 2200,
              "scopes": ["hackathon"], "quotas": {"hackathon": 800},
              "tasks": {"budget": 600, "max": 8}}
    text = S.render(led, target)
    line = next(l for l in text.splitlines() if "winners_check" in l)
    assert "⚠ νεότερο f2" in line
