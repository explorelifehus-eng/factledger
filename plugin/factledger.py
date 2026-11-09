#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""factledger — typed facts with structural truth.

Κανόνας αλήθειας: για κάθε (scope, subject, predicate) υπάρχει ΑΚΡΙΒΩΣ ΜΙΑ
ενεργή γραμμή (valid_to κενό). Νέα τιμή ΚΛΕΙΝΕΙ την προηγούμενη — δεν τη σβήνει.
Έτσι δεν μπορούν να συνυπάρξουν αντιφατικές εκδοχές ως ισότιμες.

Μόνο stdlib. Τα δεδομένα είναι TSV: διαβάζεται από άνθρωπο, γίνεται diff, grep.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import unicodedata

FIELDS = ("id", "scope", "subject", "predicate", "value",
          "valid_from", "valid_to", "superseded_by", "source", "confidence")
CONFIDENCE = ("stated", "derived", "assumed")
CONF_RANK = {"stated": 0, "derived": 1, "assumed": 2}


def today() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def norm(s) -> str:
    """Κανονικοποίηση κλειδιού: NFKC, πεζά, συμπτυγμένα κενά. Το dedup στηρίζεται εδώ."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).strip().lower()
    return " ".join(s.split())


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, path: str, aliases_path: str | None = None):
        self.path = path
        self.aliases = self._load_aliases(aliases_path)
        self.rows: list[dict] = []
        self._load()

    # ── I/O ────────────────────────────────────────────────────────────────
    def _load_aliases(self, p: str | None) -> dict:
        out = {}
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "\t" not in line:
                        continue
                    canon, alts = line.split("\t", 1)
                    for a in alts.split(","):
                        if a.strip():
                            out[norm(a)] = canon.strip()
        return out

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8", newline="") as f:
            rdr = csv.DictReader(f, delimiter="\t")
            missing = [c for c in FIELDS if c not in (rdr.fieldnames or [])]
            if missing:
                raise LedgerError(f"missing columns in {self.path}: {missing}")
            for r in rdr:
                self.rows.append({k: (r.get(k) or "") for k in FIELDS})

    def save(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t")
            w.writeheader()
            for r in self.rows:
                w.writerow(r)
        # Το ledger ζει μέσα σε Obsidian vault: AV/indexer κρατούν στιγμιαίο lock στο
        # rename και το os.replace σκάει με WinError 5 στη μέση μιας παρτίδας που έχει
        # ήδη πληρωθεί σε tokens. Bounded retry — το σφάλμα είναι παροδικό, όχι λογικό.
        for attempt in range(6):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.25 * (attempt + 1))

    # ── πυρήνας ────────────────────────────────────────────────────────────
    def _next_id(self) -> str:
        n = 0
        for r in self.rows:
            if r["id"].startswith("f") and r["id"][1:].isdigit():
                n = max(n, int(r["id"][1:]))
        return f"f{n + 1:05d}"

    def _canon_predicate(self, predicate: str) -> str:
        p = norm(predicate)
        return self.aliases.get(p, p)

    def key_of(self, row: dict) -> tuple:
        return (norm(row["scope"]), norm(row["subject"]), norm(row["predicate"]))

    def add(self, scope: str, subject: str, predicate: str, value: str,
            source: str, confidence: str = "stated", valid_from: str | None = None,
            allow_same: bool = False) -> dict:
        if not source or not str(source).strip():
            raise LedgerError("a fact is not written without a source — the rule is explicit")
        if norm(confidence) not in CONFIDENCE:
            raise LedgerError(f"confidence must be one of {CONFIDENCE}")
        if not norm(scope) or not norm(subject) or not norm(predicate):
            raise LedgerError("scope/subject/predicate cannot be empty")
        predicate = self._canon_predicate(predicate)
        vfrom = valid_from or today()
        k = (norm(scope), norm(subject), predicate)

        active = [r for r in self.rows if r["valid_to"] == "" and self.key_of(r) == k]
        for old in active:
            if not allow_same and norm(old["value"]) == norm(value):
                return {"status": "unchanged", "id": old["id"], "row": old}
            old["valid_to"] = vfrom
        new = {
            "id": self._next_id(), "scope": norm(scope), "subject": norm(subject),
            "predicate": predicate, "value": str(value).strip(),
            "valid_from": vfrom, "valid_to": "", "superseded_by": "",
            "source": str(source).strip(), "confidence": norm(confidence),
        }
        for old in active:
            old["superseded_by"] = new["id"]
        self.rows.append(new)
        self.save()
        return {"status": "closed" if active else "added",
                "closed": [r["id"] for r in active], "id": new["id"], "row": new}

    def covers(self, row: dict, ref: str) -> bool:
        """Ισχύει η γραμμή την ημερομηνία ref;"""
        if row["valid_from"] and row["valid_from"] > ref:
            return False
        return not (row["valid_to"] and row["valid_to"] <= ref)

    def active(self, scope: str | None = None, subject: str | None = None,
               as_of: str | None = None) -> list[dict]:
        """Η αλήθεια που ισχύει τη δεδομένη (ή τη σημερινή) ημερομηνία.

        Μελλοντικά προγραμματισμένη αλλαγή ΔΕΝ κλείνει την τρέχουσα αλήθεια πριν
        έρθει η ώρα της: το as_of=None σημαίνει «σήμερα», όχι «ό,τι είναι ανοιχτό».
        """
        ref = as_of or today()
        out = []
        for r in self.rows:
            if scope and norm(r["scope"]) != norm(scope):
                continue
            if subject and norm(r["subject"]) != norm(subject):
                continue
            if self.covers(r, ref):
                out.append(r)
        return sorted(out, key=lambda r: (r["scope"], r["subject"], r["predicate"]))

    def history(self, subject: str, scope: str | None = None,
                predicate: str | None = None) -> list[dict]:
        out = []
        for r in self.rows:
            if norm(r["subject"]) != norm(subject):
                continue
            if scope and norm(r["scope"]) != norm(scope):
                continue
            if predicate and norm(r["predicate"]) != self._canon_predicate(predicate):
                continue
            out.append(r)
        return sorted(out, key=lambda r: (r["valid_from"] or "", r["id"]))

    def search(self, term: str, scope: str | None = None) -> list[dict]:
        t = norm(term)
        if not t:
            return []
        hits = [r for r in self.rows
                if t in norm(r["subject"]) or t in norm(r["value"])
                or t in norm(r["predicate"]) or t in norm(r["source"])]
        if scope:
            hits = [r for r in hits if norm(r["scope"]) == norm(scope)]
        return sorted(hits, key=lambda r: (r["valid_to"] != "", r["subject"]))

    def compile(self, scopes: list[str] | None = None, budget: int = 800,
                header: str | None = None, max_value: int = 0,
                order: str = "confidence") -> str:
        """Συμπαγής όψη για έγχυση σε prompt. Δεν ξεπερνά ποτέ το budget.

        max_value > 0 → κόβει τις μακριές τιμές· η πλήρης μένει στο ledger.
        order="recency" → τα πρόσφατα πρώτα, ώστε να επιβιώνουν στο budget.
        """
        ref = today()
        rows = [r for r in self.rows if self.covers(r, ref)]
        if scopes:
            want = {norm(s) for s in scopes}
            rows = [r for r in rows if norm(r["scope"]) in want]
        if order == "recency":
            rows.sort(key=lambda r: (r["valid_from"] or "", r["scope"], r["subject"]), reverse=True)
        else:
            rows.sort(key=lambda r: (CONF_RANK.get(r["confidence"], 9), r["scope"], r["subject"]))
        lines = [header] if header else []
        used = len(header or "")
        for i, r in enumerate(rows):
            v = r["value"]
            if max_value and len(v) > max_value:
                v = v[: max_value - 1].rstrip() + "…"
            line = f"- {r['subject']} · {r['predicate']} = {v}"
            if used + len(line) + 1 > budget:
                lines.append(f"… (+{len(rows) - i} ακόμη, δες `factledger show`)")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def stale(self, days: int = 30, scopes: list[str] | None = None,
              ref: str | None = None) -> list[tuple[dict, int]]:
        """Ενεργά facts των οποίων η valid_from είναι παλαιότερη από `days`.

        Επιστρέφει [(row, ηλικία_σε_μέρες)] από τα πιο παλιά πρώτα.
        Δεν αλλάζει τίποτα· δείχνει τι θέλει επανέλεγχο.
        """
        ref = ref or today()
        today_dt = dt.date.fromisoformat(ref)
        want = {norm(s) for s in scopes} if scopes else None
        out: list[tuple[dict, int]] = []
        for r in self.rows:
            if not self.covers(r, ref):
                continue
            if want and norm(r["scope"]) not in want:
                continue
            try:
                age = (today_dt - dt.date.fromisoformat(r["valid_from"])).days
            except (ValueError, TypeError):
                continue
            if age >= days:
                out.append((r, age))
        out.sort(key=lambda x: (-x[1], x[0]["scope"], x[0]["subject"]))
        return out

    def brief(self, topic: str, scopes: list[str] | None = None, max_age: int = 14,
              recent_days: int = 30, ref: str | None = None,
              deep: bool = False) -> dict:
        """Ό,τι ξέρουμε για ένα ΘΕΜΑ: ενεργά facts με ηλικία + πρόσφατες αλλαγές.

        Το `search` ψάχνει λέξη· το `brief` ψάχνει θέμα και επιστρέφει μαζί την
        τρέχουσα αλήθεια και τι έκλεισε πρόσφατα — δηλαδή ό,τι θα θυμόταν άνθρωπος
        που συζητούσε το ίδιο ζήτημα. Χωρίς αυτό, ένα ξεχασμένο «pending» κάτω από
        άσχετο subject διαβάζεται σαν σημερινή αλήθεια.
        """
        ref = ref or today()
        today_dt = dt.date.fromisoformat(ref)
        t = norm(topic)
        want = {norm(s) for s in scopes} if scopes else None
        hit: list[dict] = []
        for r in self.rows:
            if want and norm(r["scope"]) not in want:
                continue
            hay = " | ".join((norm(r["scope"]), norm(r["subject"]), norm(r["predicate"])))
            if deep:
                hay += " | " + norm(r["value"])
            if t and t in hay:
                hit.append(r)

        def age_of(row: dict, field: str) -> int | None:
            try:
                return (today_dt - dt.date.fromisoformat(row[field])).days
            except (ValueError, TypeError):
                return None

        active, changed = [], []
        for r in hit:
            if self.covers(r, ref):
                a = age_of(r, "valid_from")
                active.append({"row": r, "age": a, "stale": a is not None and a >= max_age})
            elif r["valid_to"]:
                d = age_of(r, "valid_to")
                if d is not None and 0 <= d <= recent_days:
                    changed.append({"row": r, "days_ago": d})
        active.sort(key=lambda x: (x["row"]["valid_from"] or "", x["row"]["id"]), reverse=True)
        changed.sort(key=lambda x: (x["row"]["valid_to"], x["row"]["id"]), reverse=True)
        return {
            "topic": topic, "ref": ref, "active": active, "changed": changed,
            "counts": {"active": len(active),
                       "stale": sum(1 for a in active if a["stale"]),
                       "changed": len(changed)},
        }

    def topics(self, scopes: list[str] | None = None, ref: str | None = None,
               stale_days: int = 30) -> list[dict]:
        """Ευρετήριο θεμάτων: το prefix του subject (=θέμα) με πλήθος και φρεσκάδα.

        Υπάρχει για να μη μαντεύει κανείς λέξεις-κλειδιά. Το θέμα είναι το πρώτο
        τμήμα του subject (`hackathon.hermes_plugin` → `hackathon`), γι' αυτό τα
        facts ενός ζητήματος γράφονται κάτω από το ίδιο prefix.
        """
        ref = ref or today()
        today_dt = dt.date.fromisoformat(ref)
        want = {norm(s) for s in scopes} if scopes else None
        groups: dict[str, dict] = {}
        for r in self.rows:
            if not self.covers(r, ref):
                continue
            if want and norm(r["scope"]) not in want:
                continue
            s = norm(r["subject"])
            key = s.split(".")[0] if "." in s else s
            g = groups.setdefault(key, {"topic": key, "facts": 0, "scopes": set(),
                                        "subjects": set(), "newest": "", "stale": 0})
            g["facts"] += 1
            g["scopes"].add(r["scope"])
            g["subjects"].add(r["subject"])
            if (r["valid_from"] or "") > g["newest"]:
                g["newest"] = r["valid_from"]
            try:
                if (today_dt - dt.date.fromisoformat(r["valid_from"])).days >= stale_days:
                    g["stale"] += 1
            except (ValueError, TypeError):
                pass
        out = []
        for g in groups.values():
            out.append({**g, "scopes": sorted(g["scopes"]),
                        "subjects": sorted(g["subjects"])})
        out.sort(key=lambda g: (-g["facts"], g["topic"]))
        return out

    def audit(self, stale_days: int = 30, ref: str | None = None) -> dict:
        """Δομικός έλεγχος μνήμης — δεν κρίνει τιμές, κρίνει αν ανακτάται.

        Δύο αστοχίες που κάνουν τη μνήμη να «ξέρει αλλά να μη θυμάται»:
        1. facts κατάστασης (phase/status/pending…) που μένουν ενεργά χωρίς
           επανέλεγχο → διαβάζονται σαν σημερινή αλήθεια.
        2. facts θαμμένα κάτω από μοναδικό subject (note.<hash>) → δεν βρίσκονται
           με θέμα, μόνο με τύχη στη λέξη.
        """
        ref = ref or today()
        today_dt = dt.date.fromisoformat(ref)
        state_words = ("phase", "status", "pending", "state", "progress", "next", "step")
        state_facts, fragmented = [], []
        for r in self.rows:
            if not self.covers(r, ref):
                continue
            try:
                age = (today_dt - dt.date.fromisoformat(r["valid_from"])).days
            except (ValueError, TypeError):
                age = None
            p = norm(r["predicate"])
            if any(w in p for w in state_words) and age is not None and age >= stale_days:
                state_facts.append({"row": r, "age": age})
            s = norm(r["subject"])
            if s.startswith("note.") or (age is not None and age >= stale_days
                                         and "." not in s):
                fragmented.append({"row": r, "age": age})
        state_facts.sort(key=lambda x: -x["age"])
        fragmented.sort(key=lambda x: -x["age"])
        return {"ref": ref, "state_facts": state_facts, "fragmented": fragmented,
                "counts": {"state_facts": len(state_facts), "fragmented": len(fragmented)}}

    def export(self, scopes: list[str] | None = None,
               include_history: bool = False) -> list[dict]:
        """Δομημένη εξαγωγή για άλλα εργαλεία (watchdog, reports)."""
        want = {norm(s) for s in scopes} if scopes else None
        rows = self.rows if include_history else [r for r in self.rows if r["valid_to"] == ""]
        return [
            {
                "id": r["id"], "scope": r["scope"], "subject": r["subject"],
                "predicate": r["predicate"], "value": r["value"],
                "confidence": r["confidence"], "source": r["source"],
                "valid_from": r["valid_from"], "valid_to": r["valid_to"],
                "superseded_by": r["superseded_by"],
            }
            for r in rows if not want or norm(r["scope"]) in want
        ]

    def check(self) -> list[str]:
        """Έλεγχος ακεραιότητας. Κενή λίστα = υγιές."""
        problems, seen = [], {}
        by_key: dict[tuple, list[dict]] = {}
        for r in self.rows:
            if r["id"] in seen:
                problems.append(f"διπλό id: {r['id']}")
            seen[r["id"]] = r
            by_key.setdefault(self.key_of(r), []).append(r)
            for f in ("scope", "subject", "predicate", "value"):
                if not r[f]:
                    problems.append(f"{r['id']}: κενό πεδίο '{f}'")
            if r["confidence"] not in CONFIDENCE:
                problems.append(f"{r['id']}: άγνωστο confidence '{r['confidence']}'")
            for f in ("valid_from", "valid_to"):
                v = r[f]
                if v:
                    try:
                        dt.date.fromisoformat(v)
                    except ValueError:
                        problems.append(f"{r['id']}: μη ISO ημερομηνία στο {f}: {v}")
        ref = today()
        for k, rows in by_key.items():
            todayrows = [r for r in rows if self.covers(r, ref)]
            if len(todayrows) > 1:
                problems.append(f"αντιφατική αλήθεια: {len(todayrows)} γραμμές καλύπτουν το {ref} για {k} → {[r['id'] for r in todayrows]}")
            ordered = sorted(rows, key=lambda r: (r["valid_from"] or "", r["id"]))
            for prev, nxt in zip(ordered, ordered[1:]):
                if prev["valid_to"] == "":
                    problems.append(f"επικάλυψη διαστημάτων για {k}: η {prev['id']} μένει ανοιχτή ενώ υπάρχει η {nxt['id']}")
                elif prev["valid_to"] > nxt["valid_from"]:
                    problems.append(f"επικάλυψη διαστημάτων για {k}: {prev['id']} κλείνει {prev['valid_to']} > έναρξη {nxt['id']} ({nxt['valid_from']})")
            for r in rows:
                s = r["superseded_by"]
                if s:
                    if s not in seen:
                        problems.append(f"{r['id']}: superseded_by δείχνει σε ανύπαρκτο {s}")
                    elif seen[s]["valid_from"] and r["valid_to"] and seen[s]["valid_from"] < r["valid_to"]:
                        problems.append(f"{r['id']}: η αντικαταστάτρια {s} ξεκινά πριν κλείσει η παλιά")
                elif r["valid_to"]:
                    problems.append(f"{r['id']}: κλειστή γραμμή χωρίς superseded_by")
        return problems

    def stats(self) -> dict:
        act = [r for r in self.rows if r["valid_to"] == ""]
        return {"σύνολο": len(self.rows), "ενεργές": len(act),
                "κλειστές": len(self.rows) - len(act),
                "scopes": sorted({r["scope"] for r in self.rows})}


# ── CLI ────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factledger", description="Typed facts με δομική αλήθεια")
    p.add_argument("--db", default=os.environ.get("FACTLEDGER_DB", "facts.tsv"))
    p.add_argument("--aliases", default=os.environ.get("FACTLEDGER_ALIASES", "aliases.tsv"))
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="προσθήκη/αντικατάσταση γεγονότος")
    a.add_argument("--scope", required=True)
    a.add_argument("--subject", required=True)
    a.add_argument("--predicate", required=True)
    a.add_argument("--value", required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--confidence", default="stated")
    a.add_argument("--from", dest="valid_from", default=None)

    s = sub.add_parser("show", help="ενεργή αλήθεια")
    s.add_argument("--scope")
    s.add_argument("--subject")
    s.add_argument("--as-of")

    h = sub.add_parser("history", help="τι άλλαξε πότε")
    h.add_argument("--subject", required=True)
    h.add_argument("--scope")
    h.add_argument("--predicate")

    q = sub.add_parser("search", help="αναζήτηση")
    q.add_argument("term")
    q.add_argument("--scope")

    c = sub.add_parser("compile", help="συμπαγής όψη για prompt")
    c.add_argument("--scopes", default="")
    c.add_argument("--budget", type=int, default=800)
    c.add_argument("--header", default="")
    c.add_argument("--out", default="")
    c.add_argument("--order", choices=("confidence", "recency"), default="confidence")

    st = sub.add_parser("stale", help="ενεργά facts παλαιότερα από N ημέρες")
    st.add_argument("--days", type=int, default=30)
    st.add_argument("--scopes", default="")

    br = sub.add_parser("brief", help="ό,τι ξέρουμε για ένα ΘΕΜΑ — ενεργά + πρόσφατες αλλαγές")
    br.add_argument("topic")
    br.add_argument("--scopes", default="")
    br.add_argument("--max-age", type=int, default=14)
    br.add_argument("--recent", type=int, default=30)
    br.add_argument("--deep", action="store_true")
    br.add_argument("--json", action="store_true", dest="as_json")

    tp = sub.add_parser("topics", help="ευρετήριο θεμάτων: ποια ζητήματα έχουν facts")
    tp.add_argument("--scopes", default="")
    tp.add_argument("--stale-days", type=int, default=30)
    tp.add_argument("--json", action="store_true", dest="as_json")

    ex = sub.add_parser("export", help="δομημένη εξαγωγή σε JSON")
    ex.add_argument("--scopes", default="")
    ex.add_argument("--all", action="store_true", dest="include_history")
    ex.add_argument("--out", default="")

    sub.add_parser("check", help="έλεγχος ακεραιότητας")
    au = sub.add_parser("audit", help="δομικός έλεγχος: facts που δεν ανακτώνται ή δεν ενημερώθηκαν")
    au.add_argument("--days", type=int, default=30)
    au.add_argument("--json", action="store_true", dest="as_json")
    sub.add_parser("stats", help="σύνολα")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        led = Ledger(args.db, args.aliases)
    except LedgerError as e:
        print(f"ΣΦΑΛΜΑ: {e}", file=sys.stderr)
        return 2

    if args.cmd == "add":
        try:
            r = led.add(args.scope, args.subject, args.predicate, args.value,
                        args.source, args.confidence, args.valid_from)
        except LedgerError as e:
            print(f"ΣΦΑΛΜΑ: {e}", file=sys.stderr)
            return 2
        if r["status"] == "unchanged":
            print(f"= χωρίς αλλαγή ({r['id']})")
        else:
            closed = ", ".join(r["closed"]) or "—"
            print(f"+ {r['id']} (έκλεισε: {closed})")
    elif args.cmd == "show":
        for r in led.active(args.scope, args.subject, args.as_of):
            print(f"{r['id']}  {r['scope']}/{r['subject']} · {r['predicate']} = {r['value']}  [{r['confidence']}] {r['valid_from']}")
    elif args.cmd == "history":
        for r in led.history(args.subject, args.scope, args.predicate):
            end = r["valid_to"] or "τώρα"
            print(f"{r['id']}  {r['valid_from']} → {end}  {r['predicate']} = {r['value']}  ({r['source']})")
    elif args.cmd == "search":
        for r in led.search(args.term, args.scope):
            flag = "" if r["valid_to"] == "" else "  [ιστορικό]"
            print(f"{r['id']}  {r['subject']} · {r['predicate']} = {r['value']}{flag}")
    elif args.cmd == "compile":
        scopes = [x for x in (args.scopes or "").split(",") if x.strip()]
        text = led.compile(scopes or None, args.budget, args.header or None,
                           order=args.order)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print(f"γράφτηκε: {args.out} ({len(text)} χαρ.)")
        else:
            print(text)
    elif args.cmd == "stale":
        scopes = [x for x in (args.scopes or "").split(",") if x.strip()]
        rows = led.stale(args.days, scopes or None)
        if not rows:
            print(f"κανένα ενεργό fact παλαιότερο από {args.days} ημέρες ✔")
        for r, age in rows:
            print(f"{r['id']}  {r['scope']}/{r['subject']} · {r['predicate']} = "
                  f"{r['value']}  [{r['confidence']}] από {r['valid_from']} ({age} ημ.)")
    elif args.cmd == "brief":
        scopes = [x for x in (args.scopes or "").split(",") if x.strip()]
        b = led.brief(args.topic, scopes or None, args.max_age, args.recent,
                      deep=args.deep)
        if args.as_json:
            print(json.dumps(b, ensure_ascii=False, indent=1, default=str))
        else:
            c = b["counts"]
            if not c["active"] and not c["changed"]:
                print(f"κανένα fact για «{args.topic}» — δοκίμασε `factledger topics`")
            else:
                print(f"brief «{b['topic']}» · {b['ref']} — {c['active']} ενεργά"
                      f" ({c['stale']} παλιά) · {c['changed']} αλλαγές σε {args.recent} ημ.")
                for a in b["active"]:
                    r, age = a["row"], a["age"]
                    mark = "⚠" if a["stale"] else "■"
                    tail = f"  [{age}ημ]" if age is not None else ""
                    if a["stale"]:
                        tail += " — παλιό, επαλήθευσε"
                    print(f"{mark} {r['scope']}/{r['subject']} · {r['predicate']} = {r['value']}{tail}")
                if b["changed"]:
                    print(f"— άλλαξε πρόσφατα ({args.recent} ημ.):")
                    for ch in b["changed"]:
                        r = ch["row"]
                        print(f"  ✗ {r['subject']} · {r['predicate']} = {r['value']}"
                              f"  (έκλεισε {r['valid_to']}, {ch['days_ago']}ημ → {r['superseded_by'] or '—'})")
    elif args.cmd == "topics":
        scopes = [x for x in (args.scopes or "").split(",") if x.strip()]
        rows = led.topics(scopes or None, stale_days=args.stale_days)
        if args.as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        elif not rows:
            print("κανένα ενεργό fact")
        else:
            for g in rows:
                warn = f"  ⚠{g['stale']} παλιά" if g["stale"] else ""
                print(f"{g['topic']:24} {g['facts']:4} facts  {len(g['subjects']):3} subjects"
                      f"  τελευταίο {g['newest'] or '—'}{warn}   [{', '.join(g['scopes'])}]")
    elif args.cmd == "export":
        scopes = [x for x in (args.scopes or "").split(",") if x.strip()]
        data = led.export(scopes or None, args.include_history)
        text = json.dumps(data, ensure_ascii=False, indent=1)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print(f"γράφτηκε: {args.out} ({len(data)} facts)")
        else:
            print(text)
    elif args.cmd == "audit":
        a = led.audit(args.days)
        if args.as_json:
            print(json.dumps(a, ensure_ascii=False, indent=1, default=str))
        else:
            c = a["counts"]
            print(f"audit · {a['ref']} — {c['state_facts']} facts κατάστασης χωρίς επανέλεγχο"
                  f" · {c['fragmented']} θαμμένα κάτω από μοναδικό subject")
            if a["state_facts"]:
                print("\nΚΑΤΑΣΤΑΣΗ (ενεργά, αλλά μπορεί να μην ισχύουν):")
                for x in a["state_facts"]:
                    r = x["row"]
                    print(f"  ⚠ {r['scope']}/{r['subject']} · {r['predicate']} = {r['value'][:110]}  ({x['age']}ημ)")
            if a["fragmented"]:
                print("\nΔΕΝ ΑΝΑΚΤΑΤΑΙ ΜΕ ΘΕΜΑ (γράψ' το κάτω από το θέμα του):")
                for x in a["fragmented"][:15]:
                    r = x["row"]
                    print(f"  · {r['scope']}/{r['subject']} · {r['predicate']} = {r['value'][:90]}  ({x['age']}ημ)")
                if len(a["fragmented"]) > 15:
                    print(f"  … (+{len(a['fragmented']) - 15} ακόμη)")
    elif args.cmd == "check":
        probs = led.check()
        if probs:
            print("ΠΡΟΒΛΗΜΑΤΑ:")
            for x in probs:
                print(" -", x)
            return 1
        print("υγιές ✔")
    elif args.cmd == "stats":
        st = led.stats()
        print(f"σύνολο {st['σύνολο']} · ενεργές {st['ενεργές']} · κλειστές {st['κλειστές']}")
        print("scopes:", ", ".join(st["scopes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
