# -*- coding: utf-8 -*-
"""Tests για την ανάκληση με ΘΕΜΑ — `brief`, `topics`, `audit`.

Πραγματικό περιστατικό (9/11/2026): το demo video του hackathon είχε δημοσιευτεί
από τις 5/11, αλλά το fact `video.factledger.status · phase` έλεγε ακόμη
«phase2=pending» κάτω από subject που δεν ταίριαζε με το θέμα `hackathon`.
Κάθε ερώτηση τύπου «υπάρχει βίντεο;» απαντιόταν από το δίκτυο, όχι από τη μνήμη.

Τα tests κλειδώνουν τρεις συμπεριφορές:
  1. `brief` επιστρέφει το θέμα ολόκληρο (όχι λέξη-κλειδί) και ξεχωρίζει τα παλιά.
  2. `brief` δείχνει και τι έκλεισε πρόσφατα — η αλλαγή είναι μέρος της μνήμης.
  3. `audit` εντοπίζει facts κατάστασης που δεν επανελέγχθηκαν και facts θαμμένα
     κάτω από μοναδικό subject.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import factledger as F  # noqa: E402


def make(tmp_path, rows):
    p = tmp_path / "facts.tsv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(F.FIELDS) + "\n")
        for r in rows:
            f.write("\t".join(r.get(k, "") for k in F.FIELDS) + "\n")
    return F.Ledger(str(p))


def row(id_, subject, predicate, value, vfrom, vto="", sup=""):
    return {"id": id_, "scope": "t", "subject": subject, "predicate": predicate,
            "value": value, "valid_from": vfrom, "valid_to": vto,
            "superseded_by": sup, "source": "test", "confidence": "stated"}


def test_brief_returns_whole_topic_not_just_keyword(tmp_path):
    led = make(tmp_path, [
        row("f1", "hackathon.hermes_plugin", "submitted", "form OK", "2026-11-05"),
        row("f2", "hackathon.hermes_plugin", "demo_video", "84s on X", "2026-11-05"),
        row("f3", "shopify.catalog", "verdict", "admin only", "2026-11-06"),
    ])
    b = led.brief("hackathon", ref="2026-11-09")
    ids = {a["row"]["id"] for a in b["active"]}
    assert ids == {"f1", "f2"}          # και τα δύο facts του θέματος
    assert b["counts"]["active"] == 2
    assert "f3" not in ids


def test_brief_flags_stale_and_shows_recent_change(tmp_path):
    led = make(tmp_path, [
        row("f1", "video.demo", "phase", "pending", "2026-10-01", "2026-11-09", "f2"),
        row("f2", "video.demo", "phase", "done", "2026-11-09"),
        row("f3", "hackathon.x", "review", "old", "2026-09-01"),
    ])
    b = led.brief("video.demo", ref="2026-11-09", max_age=14)
    assert [a["row"]["value"] for a in b["active"]] == ["done"]     # μόνο η αλήθεια
    assert b["changed"][0]["row"]["value"] == "pending"             # η αλλαγή παραμένει ορατή
    assert b["changed"][0]["row"]["superseded_by"] == "f2"

    b2 = led.brief("hackathon", ref="2026-11-09", max_age=14)
    assert b2["active"][0]["stale"] is True                         # 69 ημερών → σημαία
    assert b2["counts"]["stale"] == 1


def test_topics_indexes_by_subject_prefix(tmp_path):
    led = make(tmp_path, [
        row("f1", "hackathon.hermes_plugin", "submitted", "ok", "2026-11-05"),
        row("f2", "hackathon.hermes_plugin", "demo", "84s", "2026-11-05"),
        row("f3", "shopify", "plan", "basic", "2026-11-01"),
    ])
    t = {g["topic"]: g for g in led.topics(ref="2026-11-09")}
    assert t["hackathon"]["facts"] == 2
    assert t["hackathon"]["subjects"] == ["hackathon.hermes_plugin"]
    assert t["shopify"]["facts"] == 1


def test_audit_finds_unreviewed_state_and_buried_facts(tmp_path):
    led = make(tmp_path, [
        row("f1", "cron.review", "status", "pending", "2026-09-01"),      # παλιό state
        row("f2", "note.ab12cd", "text", "x", "2026-11-08"),             # θαμμένο
        row("f3", "hackathon.hermes_plugin", "demo", "84s", "2026-11-05"),  # υγιές
    ])
    a = led.audit(stale_days=30, ref="2026-11-09")
    assert [x["row"]["id"] for x in a["state_facts"]] == ["f1"]
    assert {x["row"]["id"] for x in a["fragmented"]} == {"f2"}
    assert a["counts"]["state_facts"] == 1
    assert a["counts"]["fragmented"] == 1
    assert a["counts"]["flat_subjects"] == 0


def test_brief_empty_topic_is_silent_not_error(tmp_path):
    led = make(tmp_path, [row("f1", "a.b", "p", "v", "2026-11-05")])
    b = led.brief("δεν-υπάρχει", ref="2026-11-09")
    assert b["counts"] == {"active": 0, "stale": 0, "changed": 0}


def test_confirm_refreshes_date_without_losing_history(tmp_path):
    led = make(tmp_path, [row("f1", "hackathon.x", "submitted", "form OK", "2026-10-01")])
    r = led.confirm("f1", "re-checked", ref="2026-11-09")
    assert r["id"] != "f1"                      # νέα γραμμή, ίδια αλήθεια
    b = led.brief("hackathon", ref="2026-11-09", max_age=14)
    assert b["active"][0]["age"] == 0           # δεν γερνάει πια
    assert b["changed"][0]["row"]["id"] == "f1"  # η παλιά μένει ορατή ως αλλαγή
    assert led.check() == []                    # καμία δομική ζημιά


def test_confirm_rejects_unknown_or_inactive(tmp_path):
    led = make(tmp_path, [
        row("f1", "a.b", "p", "v", "2026-09-01", "2026-10-01", "f2"),
        row("f2", "a.b", "p", "v2", "2026-10-01"),
    ])
    for bad in ("f9", "f1"):
        try:
            led.confirm(bad, "x", ref="2026-11-09")
        except F.LedgerError:
            continue
        raise AssertionError(f"θα έπρεπε να απορρίψει το {bad}")


def test_close_retires_a_log_fact_without_successor(tmp_path):
    led = make(tmp_path, [
        row("f1", "pack.gates", "status", "G1 exit 0", "2026-09-20"),
        row("f2", "hackathon.x", "demo", "84s", "2026-11-05"),
    ])
    led.close("f1", "log-τύπου, όχι κατάσταση", ref="2026-11-09")
    assert [r["id"] for r in led.active(as_of="2026-11-09")] == ["f2"]
    assert led.check() == []                       # το κλείσιμο με λόγο είναι νόμιμο
    b = led.audit(stale_days=14, ref="2026-11-09")
    assert b["state_facts"] == []                  # δεν ξανασημαίνεται
    try:
        led.close("f1", "ξανά", ref="2026-11-09")
    except F.LedgerError:
        pass
    else:
        raise AssertionError("διπλό κλείσιμο θα έπρεπε να απορριφθεί")
