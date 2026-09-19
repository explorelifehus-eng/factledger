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
import os
import sys
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
                raise LedgerError(f"λείπουν στήλες στο {self.path}: {missing}")
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
        os.replace(tmp, self.path)

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
            raise LedgerError("χωρίς πηγή (source) δεν γράφεται γεγονός — ο κανόνας είναι ρητός")
        if norm(confidence) not in CONFIDENCE:
            raise LedgerError(f"confidence πρέπει ένα από {CONFIDENCE}")
        if not norm(scope) or not norm(subject) or not norm(predicate):
            raise LedgerError("scope/subject/predicate δεν μπορούν να είναι κενά")
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
                header: str | None = None) -> str:
        """Συμπαγής όψη για έγχυση σε prompt. Δεν ξεπερνά ποτέ το budget."""
        ref = today()
        rows = [r for r in self.rows if self.covers(r, ref)]
        if scopes:
            want = {norm(s) for s in scopes}
            rows = [r for r in rows if norm(r["scope"]) in want]
        rows.sort(key=lambda r: (CONF_RANK.get(r["confidence"], 9), r["scope"], r["subject"]))
        lines = [header] if header else []
        used = len(header or "")
        for i, r in enumerate(rows):
            line = f"- {r['subject']} · {r['predicate']} = {r['value']}"
            if used + len(line) + 1 > budget:
                lines.append(f"… (+{len(rows) - i} ακόμη, δες `factledger show`)")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

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

    sub.add_parser("check", help="έλεγχος ακεραιότητας")
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
        text = led.compile(scopes or None, args.budget, args.header or None)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print(f"γράφτηκε: {args.out} ({len(text)} χαρ.)")
        else:
            print(text)
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
