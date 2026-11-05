#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests για την ανθεκτικότητα της εξαγωγής — το πάγωμα του checkpoint.

Πραγματικό περιστατικό (crypto-trading, 23/10 → 04/11/2026): ένα μήνυμα 137.950
χαρακτήρων έγινε prompt ~61.000 tokens, το μοντέλο γύρισε **κενό content** με
finish_reason=stop, το `json.loads("")` πέταξε εξαίρεση, το `break` άφησε το
checkpoint ακίνητο — και η ΙΔΙΑ παρτίδα ξαναέσκαγε σε κάθε 6ωρο run. 191 μηνύματα
χρήστη (6,6M χαρακτήρες) έμειναν για 12 ημέρες χωρίς εξαγωγή, με τον cron να
δηλώνει «ok» γιατί η έξοδος ήταν 0.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extract as E  # noqa: E402


# ---------- τεμαχισμός μεγάλων μηνυμάτων ----------

def test_split_oversized_keeps_every_unit_within_budget():
    msg = {"id": 7, "session_id": "s1", "role": "user", "text": "x" * 250}
    units = E.split_oversized([msg], 100)
    assert len(units) == 3
    assert all(len(u["text"]) <= 100 for u in units)
    assert {u["id"] for u in units} == {7}            # ίδιο id → σωστό checkpoint
    assert [u["chunk"] for u in units] == ["1/3", "2/3", "3/3"]
    assert "".join(u["text"] for u in units) == "x" * 250   # τίποτα δεν χάνεται


def test_split_oversized_leaves_small_messages_untouched():
    msgs = [{"id": i, "session_id": "s", "role": "user", "text": "y" * 10} for i in (1, 2)]
    assert E.split_oversized(msgs, 100) == msgs


def test_no_batch_unit_exceeds_budget_after_split():
    """Ο κανόνας που έλειπε: ΚΑΜΙΑ μονάδα που στέλνεται στο μοντέλο δεν ξεπερνά το όριο."""
    msgs = [{"id": 1, "session_id": "s", "role": "user", "text": "z" * 5000},
            {"id": 2, "session_id": "s", "role": "user", "text": "k" * 40}]
    batches = E.batch(E.split_oversized(msgs, 1000), 1000)
    assert all(len(u["text"]) <= 1000 for b in batches for u in b)
    assert sum(len(b) for b in batches) >= 6
    assert batches[-1][-1]["id"] == 2


# ---------- αποκωδικοποίηση απάντησης μοντέλου ----------

def _api_body(content: str) -> str:
    return json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


def test_parse_llm_response_normal():
    assert E.parse_llm_response(_api_body('{"facts": []}')) == {"facts": []}


def test_parse_llm_response_strips_code_fence():
    assert E.parse_llm_response(_api_body('```json\n{"facts": []}\n```')) == {"facts": []}


def test_parse_llm_response_whitespace_content_raises_named_error():
    """Η ακριβής μορφή της βλάβης: 200 + κενό content από σκέτα κενά."""
    with pytest.raises(E.LLMError) as e:
        E.parse_llm_response(_api_body(" " * 542))
    assert "κενό content" in str(e.value)


def test_parse_llm_response_garbage_raises_named_error():
    with pytest.raises(E.LLMError):
        E.parse_llm_response("")
    with pytest.raises(E.LLMError):
        E.parse_llm_response(_api_body("δεν είναι json"))


# ---------- μηνύματα-φάκελοι της πλατφόρμας δεν είναι λόγια χρήστη ----------

def test_skill_wrapper_message_is_not_extracted(tmp_path):
    """Το σώμα skill (140k χαρ.) φτάνει ως «μήνυμα χρήστη» αλλά δεν είναι δικό του."""
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
               "role TEXT, content TEXT, timestamp REAL)")
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
               (1, "s", "user", '[IMPORTANT: The user has invoked the "trading-backtesting" '
                                'skill, indicating they want you to follow its instructions. '
                                + "x" * 5000, 1.0))
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
               (2, "s", "user", "το όριο του exit είναι 25%", 2.0))
    db.commit()
    c = {"state_db": str(tmp_path / "state.db")}
    ids = [m["id"] for m in E.fetch_messages(c, 0, None, 5)]
    assert ids == [2]


# ---------- συμπεριφορά του κύκλου: καμία παγωμένη θέση ----------

def _make_cfg(tmp_path, n_msgs=3, resume_from=None):
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
               "role TEXT, content TEXT, timestamp REAL)")
    for i in range(1, n_msgs + 1):
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                   (i, "s1", "user", f"τιμή {i}: το όριο είναι {i}0 ευρώ", 1790000000 + i))
    db.commit()
    db.close()
    cp = tmp_path / "state.json"
    if resume_from is not None:
        cp.write_text(json.dumps({"last_message_id": resume_from}), encoding="utf-8")
    cfg = {"state_db": str(tmp_path / "state.db"), "checkpoint": str(cp),
           "db": str(tmp_path / "facts.tsv"), "scopes": ["crypto"], "scope_fallback": "crypto",
           "roles": ["user"], "min_user_chars": 5, "max_chars": 18000,
           "api": {"base_url": "http://localhost/x", "model": "m", "key_env": "K", "max_tokens": 10}}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


def _fact(i):
    return {"scope": "crypto", "subject": f"limit.{i}", "predicate": "value",
            "value": str(i * 10), "confidence": "stated", "quote": f"το όριο είναι {i}0 ευρώ"}


def test_main_advances_checkpoint_when_llm_fails(tmp_path, monkeypatch):
    """Αποτυχία = παράλειψη με καταγραφή, ΟΧΙ αιώνιο πάγωμα."""
    cfg = _make_cfg(tmp_path, 3)

    def boom(*a, **k):
        raise E.LLMError("κενό content από το μοντέλο")

    monkeypatch.setattr(E, "call_llm", boom)
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    rc = E.main(["--config", str(tmp_path / "cfg.json"), "--apply"])
    st = json.loads(open(cfg["checkpoint"], encoding="utf-8").read())
    assert st["last_message_id"] == 3          # η θέση ΠΡΟΧΩΡΑ
    assert st["skipped"] == [[1, 3]]           # καταγεγραμμένη παράλειψη
    assert rc == 1                             # ορατό στον cron
    assert not os.path.exists(cfg["db"]) or open(cfg["db"], encoding="utf-8").read().count("\n") == 0


def test_main_writes_facts_and_exits_zero_on_healthy_run(tmp_path, monkeypatch):
    cfg = _make_cfg(tmp_path, 3)
    monkeypatch.setattr(E, "call_llm", lambda *a, **k: {"facts": [_fact(2)]})
    rc = E.main(["--config", str(tmp_path / "cfg.json"), "--apply"])
    st = json.loads(open(cfg["checkpoint"], encoding="utf-8").read())
    assert rc == 0 and st["last_message_id"] == 3 and st["applied"] >= 1
    assert st["skipped"] == []
    assert "limit.2" in open(cfg["db"], encoding="utf-8").read()


def test_main_resumes_from_an_advanced_checkpoint(tmp_path, monkeypatch):
    """Checkpoint γραμμένο ανά παρτίδα: ο επόμενος κύκλος ξεκινά από εκεί που έμεινε."""
    cfg = _make_cfg(tmp_path, 3, resume_from=2)
    seen = []

    def capture(cfg_, env, scopes, text, roles=None):
        seen.append(text)
        return {"facts": []}

    monkeypatch.setattr(E, "call_llm", capture)
    rc = E.main(["--config", str(tmp_path / "cfg.json"), "--apply"])
    assert rc == 0
    assert seen and "#1" not in seen[0] and "#2" not in seen[0]   # δεν ξαναϋποβάλλονται
    st = json.loads(open(cfg["checkpoint"], encoding="utf-8").read())
    assert st["last_message_id"] == 3


def test_main_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    cfg = _make_cfg(tmp_path, 2)
    monkeypatch.setattr(E, "call_llm", lambda *a, **k: {"facts": [_fact(1)]})
    rc = E.main(["--config", str(tmp_path / "cfg.json")])
    assert rc == 0
    assert not os.path.exists(cfg["checkpoint"])   # dry-run: το state δεν γράφεται
    assert not os.path.exists(cfg["db"])
