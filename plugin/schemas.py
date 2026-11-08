#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Σχήματα (JSON Schema) για τα tools του plugin factledger.

Κάθε σχήμα είναι {name, description, parameters}: το parameters είναι το
JSON Schema object που καταναλώνει ο model — δηλαδή πώς καλείται το tool.
Οι περιγραφές είναι αγγλικές: είναι το κείμενο που διαβάζει το μοντέλο.
"""
from __future__ import annotations


def _params(props: dict, required: list[str] | None = None) -> dict:
    p = {"type": "object", "properties": props}
    if required:
        p["required"] = required
    return p


ADD_PROPS = {
    "scope": {"type": "string", "description": "Scope (namespace) of the fact — required."},
    "subject": {"type": "string", "description": "Subject of the fact (e.g. competitor.x · case.y)."},
    "predicate": {"type": "string", "description": "Property (e.g. price, status, decision)."},
    "value": {"type": "string", "description": "The fact's value."},
    "source": {"type": "string", "description": "Verbatim source (where it comes from) — required."},
    "confidence": {"type": "string", "enum": ["stated", "derived", "assumed"],
                   "description": "How certain the source is. Default: stated."},
    "valid_from": {"type": "string", "description": "ISO validity date (YYYY-MM-DD). Default: today."},
}

SHOW_PROPS = {
    "scope": {"type": "string", "description": "Restrict to a scope."},
    "subject": {"type": "string", "description": "Restrict to a subject."},
    "as_of": {"type": "string", "description": "ISO date for a historical view. Default: today."},
}

SEARCH_PROPS = {
    "term": {"type": "string", "description": "Search term (normalized: lowercase, NFKC)."},
    "scope": {"type": "string", "description": "Optional restriction to a scope."},
}

HISTORY_PROPS = {
    "subject": {"type": "string", "description": "The subject whose history to show (what changed when)."},
    "scope": {"type": "string", "description": "Optional restriction to a scope."},
    "predicate": {"type": "string", "description": "Optional restriction to a predicate."},
}

COMPILE_PROPS = {
    "scopes": {"type": "string", "description": "Comma-separated list of scopes."},
    "budget": {"type": "integer", "description": "Output character limit. Default: 800."},
}

STALE_PROPS = {
    "days": {"type": "integer", "description": "Active facts with valid_from older than N days. Default: 30."},
    "scopes": {"type": "string", "description": "Comma-separated list of scopes."},
}

EXPORT_PROPS = {
    "scopes": {"type": "string", "description": "Comma-separated list of scopes."},
    "include_history": {"type": "boolean",
                        "description": "If true, include closed (historical) rows. Default: false (active only)."},
}

STATS_PROPS: dict = {}


ADD = {"name": "factledger_add", "description": "Record or replace a fact in the ledger. A new value closes the previous one (valid_to + superseded_by) instead of deleting it.",
       "parameters": _params(ADD_PROPS, ["scope", "subject", "predicate", "value", "source"])}

SHOW = {"name": "factledger_show", "description": "The active truth that holds now (or as_of) for a scope/subject.",
        "parameters": _params(SHOW_PROPS)}

SEARCH = {"name": "factledger_search", "description": "Search across subject/value/predicate/source.",
          "parameters": _params(SEARCH_PROPS, ["term"])}

HISTORY = {"name": "factledger_history", "description": "What changed and when for a subject — audit trail with all previous values.",
           "parameters": _params(HISTORY_PROPS, ["subject"])}

COMPILE = {"name": "factledger_compile", "description": "Compact view within a budget (useful for prompt injection).",
           "parameters": _params(COMPILE_PROPS)}

STALE = {"name": "factledger_stale", "description": "Active facts older than N days — what needs re-checking.",
         "parameters": _params(STALE_PROPS)}

EXPORT = {"name": "factledger_export", "description": "Structured JSON export (for watchdogs/reports).",
          "parameters": _params(EXPORT_PROPS)}

STATS = {"name": "factledger_stats", "description": "Totals: active/closed rows, scopes.",
         "parameters": _params(STATS_PROPS)}


SCHEMAS = {
    "factledger_add": ADD,
    "factledger_show": SHOW,
    "factledger_search": SEARCH,
    "factledger_history": HISTORY,
    "factledger_compile": COMPILE,
    "factledger_stale": STALE,
    "factledger_export": EXPORT,
    "factledger_check": {"name": "factledger_check",
                         "description": "Ledger integrity check. Empty list = healthy.",
                         "parameters": _params({})},
    "factledger_stats": STATS,
}
