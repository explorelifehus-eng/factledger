#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Handlers για τα tools του plugin factledger.

Κάθε handler παίρνει (args: dict, **kwargs) και επιστρέφει ΕΝΑ JSON string —
πάντα, και σε σφάλμα. Ποτέ δεν raise. Αυτός είναι ο συμβόλαιο των tool
handlers στο Hermes plugin surface.
"""
from __future__ import annotations

import json
from pathlib import Path

# Το standalone factledger.py κουβαλιέται ΜΕΣΑ στο plugin (catalog: subdir=plugin),
# γιατί η εγκατάσταση παίρνει ΜΟΝΟ τα περιεχόμενα του plugin/. Το αντίγραφο
# παράγεται από scripts/sync_plugin_copy.py και φυλάσσεται από tests/test_plugin_packaging.py.
_CTX = None  # το ctx του plugin — ορίζεται από το register() στο __init__.py


def set_ctx(ctx) -> None:
    """Αποθηκεύει το plugin ctx, ώστε τα tools να διαβάζουν config στο call time."""
    global _CTX
    _CTX = ctx


def _load_standalone():
    """Φόρτωσε το συσκευασμένο `factledger.py` ΜΟΝΟ από δίπλα στο tools.py.

    Φορτώνεται με μοναδικό module name — ΟΧΙ με `import factledger`, που θα
    επέστρεφε το ίδιο το plugin package (χωρίς κλάση Ledger) αντί για το
    standalone module. Ψάχνει μόνο το bundled αντίγραφο: ό,τι άλλο (FACTLEDGER_PY
    ή το γονικό dir) θα έτρεχε μη-ελεγμένο κώδικα μετά την εγκατάσταση.
    """
    import importlib.util as _ilu

    path = Path(__file__).resolve().parent / "factledger.py"
    if not path.is_file():
        raise ImportError(f"factledger.py not found next to tools.py ({path})")
    spec = _ilu.spec_from_file_location("_factledger_standalone", str(path))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fl = _load_standalone()  # noqa: E402


def _db_path() -> Path:
    """Η διαδρομή του ledger, ΛΥΜΕΝΗ ΣΤΟ CALL TIME.

    Πρώτα το `db_path` από το config_schema (plugins.entries.factledger.settings),
    αλλιώς το per-profile plugin data dir — ΠΟΤΕ το κοινό ~/.factledger, που θα
    μοιραζόταν facts ανάμεσα σε profiles. Ο import είναι lazy ώστε το probe
    εγκατάστασης (που φορτώνει το tools.py μόνο του) να μην απαιτεί το Hermes.
    """
    if _CTX is not None:
        custom = _CTX.get_config("db_path")
        if custom:
            return Path(str(custom))
    from plugins.plugin_storage import plugin_data_dir
    return plugin_data_dir("factledger") / "facts.tsv"


def _ledger():
    return fl.Ledger(str(_db_path()))


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
