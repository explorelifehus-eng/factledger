# -*- coding: utf-8 -*-
"""Tests για την 1.2.0 — παρουσίαση, εργασίες (task.*), σημάνσεις, export, deep check.

Το περιστατικό που κλειδώνουν: στις 11/11 στάλθηκε mail προς dealer από άλλη
συνεδρία και δεν ανακλήθηκε (φαινόταν «δεν στάλθηκε»). Η ρίζα δεν ήταν το λάθος
mailbox — ήταν ότι μια **ενέργεια** δεν είχε καταγραφεί ως εργασία με κατάσταση
και απόδειξη. Εδώ τα tests απαιτούν: (α) `done` χωρίς απόδειξη να απορρίπτεται,
(β) οι κανόνες/απαγορεύσεις να μη χάνονται στο budget, (γ) η ανάγνωση σε
ημερομηνία του παρελθόντος να δίνει την τότε αλήθεια.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import factledger as F  # noqa: E402


def make(tmp_path, rows=()):
    p = tmp_path / "facts.tsv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(F.FIELDS) + "\n")
        for r in rows:
            f.write("\t".join(r.get(k, "") for k in F.FIELDS) + "\n")
    return F.Ledger(str(p))


def row(id_, subject, predicate, value, vfrom, vto="", sup="", source="test",
        scope="t", conf="stated"):
    return {"id": id_, "scope": scope, "subject": subject, "predicate": predicate,
            "value": value, "valid_from": vfrom, "valid_to": vto,
            "superseded_by": sup, "source": source, "confidence": conf}


# ── παρουσίαση ─────────────────────────────────────────────────────────────
def test_human_age_buckets():
    assert F.human_age(None) == "—"
    assert F.human_age(0) == "σήμερα"
    assert F.human_age(3) == "3ημ"
    assert F.human_age(20) == "2εβδ"
    assert F.human_age(120) == "4μ"
    assert F.human_age(900) == "2χ"


def test_pad_ignores_combining_marks():
    # «ά» = α + τόνος: δύο code points, ένα οπτικό πλάτος
    assert F.dwidth("ά") == 1
    assert F.dwidth("ΑΒ") == 2
    assert F.pad("ά", 3) == "ά  "


def test_color_is_off_without_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FACTLEDGER_COLOR", raising=False)
    monkeypatch.setattr(F.sys.stdout, "isatty", lambda: False, raising=False)
    assert F.paint("x", "red") == "x"
    monkeypatch.setenv("FACTLEDGER_COLOR", "1")
    assert F.paint("x", "red").startswith("\x1b[31m")


# ── εργασίες ───────────────────────────────────────────────────────────────
def test_parse_and_render_task_roundtrip():
    v = F.render_task("στείλε mail", "done", owner="agent",
                      evidence="msg 1a1d58f8", due="2026-11-18")
    t = F.parse_task(v)
    assert t == {"text": "στείλε mail", "state": "done", "owner": "agent",
                 "evidence": "msg 1a1d58f8", "due": "2026-11-18"}


def test_task_add_then_list_open(tmp_path):
    led = make(tmp_path)
    led.task_set("user", "car.beetle", "send_mail", "στείλε mail στον dealer",
                 due="2026-11-18", source="μήνυμα χρήστη")
    open_tasks = led.tasks()
    assert len(open_tasks) == 1
    assert open_tasks[0]["task"]["state"] == "requested"
    assert open_tasks[0]["task"]["due"] == "2026-11-18"


def test_task_done_without_evidence_is_rejected(tmp_path):
    led = make(tmp_path)
    r = led.task_set("user", "car.beetle", "send_mail", "στείλε mail")
    try:
        led.task_done(r["id"], "")
    except F.LedgerError as e:
        assert "evidence" in str(e)
    else:
        raise AssertionError("done χωρίς απόδειξη δεν πρέπει να περνά")
    assert led.tasks()[0]["task"]["state"] == "requested"


def test_task_done_keeps_history_and_closes(tmp_path):
    led = make(tmp_path)
    r = led.task_set("user", "car.beetle", "send_mail", "στείλε mail")
    res = led.task_done(r["id"], "msg 1a1d58f8")
    assert res["status"] == "closed"
    done = led.tasks(include_done=True)
    assert done[0]["task"]["state"] == "done"
    assert done[0]["task"]["evidence"] == "msg 1a1d58f8"
    assert led.tasks() == []  # προεπιλογή: μόνο ανοιχτές
    hist = led.history("car.beetle")
    assert [h["id"] for h in hist] == [r["id"], res["id"]]


def test_task_set_rejects_done_without_evidence(tmp_path):
    led = make(tmp_path)
    try:
        led.task_set("user", "x.y", "publish", "δημοσίευσε", state="done")
    except F.LedgerError:
        pass
    else:
        raise AssertionError("state=done χωρίς evidence έπρεπε να απορριφθεί")


def test_task_blocked_has_owner(tmp_path):
    led = make(tmp_path)
    led.task_set("user", "car.beetle", "dealer_reply", "απάντηση αντιπροσωπείας",
                 owner="third-party", state="blocked", due="2026-11-18",
                 evidence="τηλ. 23210 90755")
    t = led.tasks()[0]["task"]
    assert t["owner"] == "third-party" and t["state"] == "blocked"


# ── compile: σημάνσεις & προτεραιότητα ─────────────────────────────────────
def test_compile_marks_kinds_and_puts_rules_first(tmp_path):
    led = make(tmp_path, [
        row("f1", "market.price", "value", "140", "2026-11-01"),
        row("f2", "rules.outreach", "prohibition.copy", "όχι αντιγραφές", "2026-11-01"),
        row("f3", "rules.all", "rule.everything", "όλες οι αλλαγές καθολικές", "2026-11-01"),
        row("f4", "hackathon.mail", "correction.sent", "στάλθηκε 11/11", "2026-11-01"),
    ])
    out = led.compile(budget=2000)
    lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert lines[0].startswith("- ⛔") and lines[1].startswith("- §")
    assert any(ln.startswith("- ✎") for ln in lines)
    # τα απλά facts μένουν χωρίς σήμανση
    assert any(ln.startswith("- market.price") for ln in lines)


def test_compile_renders_task_compactly(tmp_path):
    led = make(tmp_path)
    led.task_set("user", "car.beetle", "send_mail", "στείλε mail στον dealer",
                 due="2026-11-18")
    out = led.compile(budget=800)
    assert "▸ car.beetle · task.send_mail = στείλε mail στον dealer (due 2026-11-18)" in out
    assert "evidence=" not in out  # η απόδειξη μένει στο store, όχι στο prompt


# ── check --deep ───────────────────────────────────────────────────────────
def test_deep_check_finds_backwards_interval(tmp_path):
    led = make(tmp_path, [row("f1", "a.b", "p", "v", "2026-11-10", "2026-11-05", "")])
    assert any("valid_to" in p for p in led.check())
    assert any("valid_to" in p for p in led.check(deep=True))


def test_deep_check_finds_out_of_sequence_ids(tmp_path):
    led = make(tmp_path, [
        row("f00005", "a.b", "p", "v", "2026-11-01"),
        row("f00003", "a.c", "p", "v", "2026-11-01"),
    ])
    assert not any("εκτός σειράς" in p for p in led.check())
    assert any("εκτός σειράς" in p for p in led.check(deep=True))


def test_deep_check_flags_hand_edited_chain(tmp_path):
    led = make(tmp_path, [
        row("f1", "a.b", "p", "old", "2026-11-01", "2026-11-03", "f2"),
        row("f2", "a.b", "p", "new", "2026-11-05"),
    ])
    assert any("ξεκινά" in p for p in led.check(deep=True))


# ── as-of: η αλήθεια του παρελθόντος ───────────────────────────────────────
def test_active_as_of_returns_past_truth(tmp_path):
    led = make(tmp_path, [
        row("f1", "a.b", "p", "old", "2026-11-01", "2026-11-10", "f2"),
        row("f2", "a.b", "p", "new", "2026-11-10"),
    ])
    assert [r["value"] for r in led.active(as_of="2026-11-05")] == ["old"]
    assert [r["value"] for r in led.active(as_of="2026-11-11")] == ["new"]


# ── CLI: export & tasks ────────────────────────────────────────────────────
def test_cli_export_md_and_csv(tmp_path, capsys):
    led = make(tmp_path)
    led.task_set("user", "car.beetle", "send_mail", "στείλε mail")
    db = str(tmp_path / "facts.tsv")
    assert F.main(["--db", db, "export", "--format", "md"]) == 0
    md = capsys.readouterr().out
    assert md.splitlines()[0].startswith("| id | scope |")
    assert "task.send_mail" in md
    assert F.main(["--db", db, "export", "--format", "csv"]) == 0
    csv_out = capsys.readouterr().out
    assert csv_out.splitlines()[0].startswith("id,scope,subject,predicate")


def test_cli_tasks_json(tmp_path, capsys):
    led = make(tmp_path)
    led.task_set("user", "car.beetle", "send_mail", "στείλε mail", due="2026-11-18")
    db = str(tmp_path / "facts.tsv")
    assert F.main(["--db", db, "tasks", "--json"]) == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data[0]["subject"] == "car.beetle"
    assert data[0]["state"] == "requested"
    assert data[0]["due"] == "2026-11-18"


def test_cli_task_done_requires_evidence(tmp_path, capsys):
    led = make(tmp_path)
    r = led.task_set("user", "car.beetle", "send_mail", "στείλε mail")
    db = str(tmp_path / "facts.tsv")
    assert F.main(["--db", db, "task", "done", r["id"]]) == 2
    assert "evidence" in capsys.readouterr().err
    assert F.main(["--db", db, "task", "done", r["id"], "--evidence", "msg-1"]) == 0
    assert "done" in capsys.readouterr().out


def test_cli_brief_handover_shows_tasks(tmp_path, capsys):
    led = make(tmp_path)
    led.task_set("user", "car.beetle", "send_mail", "στείλε mail στον dealer",
                 due="2026-11-18")
    led.add("user", "car.beetle", "vin", "WVWZZZ9CZ3M600610", "test")
    db = str(tmp_path / "facts.tsv")
    assert F.main(["--db", db, "brief", "car.beetle", "--handover"]) == 0
    out = capsys.readouterr().out
    assert "car.beetle" in out and "εργασίες" in out and "send_mail" not in out
    assert "στείλε mail στον dealer" in out


def test_parse_task_keeps_dots_inside_evidence():
    v = F.render_task("απάντηση dealer", "blocked", owner="third-party",
                      evidence="τηλ. 23210 90755 · salesvw@poreia-ae.gr")
    t = F.parse_task(v)
    assert t["text"] == "απάντηση dealer"
    assert t["evidence"] == "τηλ. 23210 90755 · salesvw@poreia-ae.gr"
    assert t["state"] == "blocked"
