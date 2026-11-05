#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Σχήματα (JSON Schema) για τα tools του plugin factledger.

Κάθε σχήμα είναι {name, description, parameters}: το parameters είναι το
JSON Schema object που καταναλώνει ο model - δηλαδή πώς καλείται το tool.
"""
from __future__ import annotations

_S = {
    "type": "object",
    "properties": {
        "scope": {"type": "string", "description": "Προαιρετικός περιορισμός σε ένα scope (π.χ. silktales)."},
    },
}


def _params(props: dict, required: list[str] | None = None) -> dict:
    p = {"type": "object", "properties": props}
    if required:
        p["required"] = required
    return p


ADD_PROPS = {
    "scope": {"type": "string", "description": "Scope (χώρος ονομάτων) του γεγονότος — υποχρεωτικό."},
    "subject": {"type": "string", "description": "Θέμα του γεγονότος (π.χ. competitor.x · case.y)."},
    "predicate": {"type": "string", "description": "Ιδιότητα (π.χ. price, status, decision)."},
    "value": {"type": "string", "description": "Η τιμή του γεγονότος."},
    "source": {"type": "string", "description": "Αυτολεξεί πηγή (από πού προκύπτει) — υποχρεωτικό."},
    "confidence": {"type": "string", "enum": ["stated", "derived", "assumed"],
                   "description": "Πόσο βέβαιη είναι η πηγή. Default: stated."},
    "valid_from": {"type": "string", "description": "Ημερομηνία ισχύος ISO (YYYY-MM-DD). Default: σήμερα."},
}

SHOW_PROPS = {
    "scope": {"type": "string", "description": "Περιορισμός σε scope."},
    "subject": {"type": "string", "description": "Περιορισμός σε subject."},
    "as_of": {"type": "string", "description": "Ημερομηνία ISO για ιστορική άποψη. Default: σήμερα."},
}

SEARCH_PROPS = {
    "term": {"type": "string", "description": "Όρος αναζήτησης (normalized: πεζά, NFKC)."},
    "scope": {"type": "string", "description": "Προαιρετικός περιορισμός σε scope."},
}

HISTORY_PROPS = {
    "subject": {"type": "string", "description": "Το θέμα του οποίου θέλουμε το ιστορικό (τί άλλαξε πότε)."},
    "scope": {"type": "string", "description": "Προαιρετικός περιορισμός σε scope."},
    "predicate": {"type": "string", "description": "Προαιρετικός περιορισμός σε predicate."},
}

COMPILE_PROPS = {
    "scopes": {"type": "string", "description": "Κόμμα-χωρισμένη λίστα scopes."},
    "budget": {"type": "integer", "description": "Όριο χαρακτήρων εξόδου. Default: 800."},
}

STALE_PROPS = {
    "days": {"type": "integer", "description": "Ενεργά facts με valid_from παλαιότερη από N ημέρες. Default: 30."},
    "scopes": {"type": "string", "description": "Κόμμα-χωρισμένη λίστα scopes."},
}

EXPORT_PROPS = {
    "scopes": {"type": "string", "description": "Κόμμα-χωρισμένη λίστα scopes."},
    "include_history": {"type": "boolean",
                        "description": "Αν true φέρνει και τις κλειστές (ιστορικές) γραμμές. Default: false (ενεργές μόνο)."},
}

STATS_PROPS: dict = {}


ADD = {"name": "factledger_add", "description": "Προσθήκη ή αντικατάσταση γεγονότος στο ledger. Νέα τιμή κλείνει την παλιά (valid_to + superseded_by) αντί να τη σβήνει.",
       "parameters": _params(ADD_PROPS, ["scope", "subject", "predicate", "value", "source"])}

SHOW = {"name": "factledger_show", "description": "Η ενεργή αλήθεια που ισχύει τώρα (ή as_of) για το scope/subject.",
        "parameters": _params(SHOW_PROPS)}

SEARCH = {"name": "factledger_search", "description": "Αναζήτηση σε subject/value/predicate/source.",
          "parameters": _params(SEARCH_PROPS, ["term"])}

HISTORY = {"name": "factledger_history", "description": "Τι άλλαξε και πότε για ένα subject — audit trail με όλες τις προηγούμενες τιμές.",
           "parameters": _params(HISTORY_PROPS, ["subject"])}

COMPILE = {"name": "factledger_compile", "description": "Συμπαγής όψη εντός budget (χρήσιμη για έγχυση σε prompt).",
           "parameters": _params(COMPILE_PROPS)}

STALE = {"name": "factledger_stale", "description": "Ενεργά facts παλαιότερα από N ημέρες — τι θέλει επανέλεγχο.",
         "parameters": _params(STALE_PROPS)}

EXPORT = {"name": "factledger_export", "description": "Δομημένη εξαγωγή σε JSON (για watchdog/reports).",
          "parameters": _params(EXPORT_PROPS)}

STATS = {"name": "factledger_stats", "description": "Σύνολα: ενεργές/κλειστές γραμμές, scopes.",
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
                         "description": "Έλεγχος ακεραιότητας του ledger. Κενή λίστα = υγιές.",
                         "parameters": _params({})},
    "factledger_stats": STATS,
}
