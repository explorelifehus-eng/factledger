#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Handlers για τα tools του plugin factledger.

Κάθε handler παίρνει (args: dict, **kwargs) και επιστρέφει ΕΝΑ JSON string —
πάντα, και σε σφάλμα. Ποτέ δεν raise. Αυτός είναι ο συμβόλαιο των tool
handlers στο Hermes plugin surface.
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path

# → repo root: το standalone factledger ζει ένα επίπεδο πάνω από το plugin/.
# Ο μόνος τρόπος να το βρει ο loader είναι sys.path — δεν κυκλοφορούμε αντίγραφο.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import factledger as fl  # noqa: E402

# Ο path του ledger. Default: plugin_data_dir (εγγυημένος επιβιώσιμος, ακολουθεί
# το ενεργό profile). Έλεγχος: FACTLEDGER_DB env ή ~/.factledger.
DB_ENV = os.environ.get("FACTLEDGER_DB")
if DB_ENV:
    _DB_PATH = DB_ENV
else:
    _DB_PATH = os.path.join(str(Path.home()), ".factledger", "facts.tsv")


def _ledger():
    return fl.Ledger(_DB_PATH)


def _ok(data) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": str(msg)}, ensure_ascii=False)


def _j2s(path: Path) -> str:
    return path.as_posix()


# ── tools ──────────────────────────────────────────────────────────────────
def add(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        res = led.add(
            scope=args.get("scope", ""), subject=args.get("subject", ""), predicate=args.get("predicate", ""),
            value=args.get("value", ""), source=args.get("source", ""),
            confidence=args.get("confidence", "stated"),
            valid_from=args.get("valid_from"),
        )
        return _ok(res)
    except Exception as e:  # noqa: BLE001 — συμβόλαιο: ποτέ raise
        return _err(e)


def show(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        rows = led.active(scope=args.get("scope"), subject=args.get("subject"),
                         as_of=args.get("as_of"))
        return _ok(rows)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def search(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        return _ok(led.search(args["term"], scope=args.get("scope")))
    except Exception as e:  # noqa: BLE001
        return _err(e)


def history(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        return _ok(led.history(args["subject"], scope=args.get("scope"),
                               predicate=args.get("predicate")))
    except Exception as e:  # noqa: BLE001
        return _err(e)


def compile(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        scopes = [s.strip() for s in args.get("scopes", "").split(",") if s.strip()] or None
        text = led.compile(scopes=scopes, budget=int(args.get("budget", 800)))
        return _ok({"view": text})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def stale(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        scopes = [s.strip() for s in args.get("scopes", "").split(",") if s.strip()] or None
        out = [{"row": r, "age_days": age} for r, age in led.stale(
            days=int(args.get("days", 30)), scopes=scopes)]
        return _ok(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def export(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        scopes = [s.strip() for s in args.get("scopes", "").split(",") if s.strip()] or None
        return _ok(led.export(scopes=scopes,
                              include_history=bool(args.get("include_history", False))))
    except Exception as e:  # noqa: BLE001
        return _err(e)


def check(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        problems = led.check()
        return _ok({"healthy": not problems, "problems": problems})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def stats(args: dict, **kwargs) -> str:
    try:
        led = _ledger()
        return _ok(led.stats())
    except Exception as e:  # noqa: BLE001
        return _err(e)


HANDLERS = {
    "factledger_add": add,
    "factledger_show": show,
    "factledger_search": search,
    "factledger_history": history,
    "factledger_compile": compile,
    "factledger_stale": stale,
    "factledger_export": export,
    "factledger_check": check,
    "factledger_stats": stats,
}
