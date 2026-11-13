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
import re
import sys
import time
import unicodedata

FIELDS = ("id", "scope", "subject", "predicate", "value",
          "valid_from", "valid_to", "superseded_by", "source", "confidence")
# Subject που δεν ανακτάται με ΘΕΜΑ: ό,τι είναι ταυτότητα αντί για θέμα —
# `note.<hash>`, `session.<id>`, γυμνό hex/uuid. Ένα επίπεδο αλλά ΠΕΡΙΓΡΑΦΙΚΟ
# subject (`shipping`, `email`) βρίσκεται κανονικά με `brief <subject>`, άρα δεν
# είναι θαμμένο — μόνο ενημερωτικά «επίπεδο».
OPAQUE_SUBJECT_RE = re.compile(
    r"^note\.[0-9a-f]{6,}$"                     # note.7b9137e5
    r"|^session\.\d{6,}"                        # session.20261017_200602_7b8a4e
    r"|^[0-9a-f]{6,}$"                          # γυμνό hash (cron/b3dc21979c5e)
    r"|^[0-9a-f]{8}-[0-9a-f]{4}-",              # uuid
    re.I,
)


def opaque_subject(s: str) -> bool:
    """True όταν το subject είναι ταυτότητα, όχι θέμα (δεν ανακτάται με brief)."""
    return bool(OPAQUE_SUBJECT_RE.search(norm(s)))


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


# ── εργασίες (task.<ρήμα>): το lifecyle του αιτήματος ως fact ───────────────
TASK_STATES = ("requested", "approved", "done", "blocked", "cancelled")
TASK_DONE = ("done", "cancelled")
TASK_META_RE = re.compile(r"(?:^|\s·\s)(state|owner|evidence|due)=([^·]+)", re.I)
RULE_PREFIXES = ("rule", "rules", "law", "convention", "policy")
PROHIBIT_PREFIXES = ("prohibition", "forbid", "forbidden", "never", "ban")


def is_task(predicate: str) -> bool:
    return norm(predicate).startswith("task.")


def parse_task(value: str) -> dict:
    """Διαβάζει τιμή εργασίας: ελεύθερο κείμενο + `· state=/owner=/evidence=/due=`."""
    meta: dict[str, str] = {}
    text_parts: list[str] = []
    cur: str | None = None
    for part in str(value or "").split("·"):
        p = part.strip()
        if not p:
            continue
        m = re.match(r"(state|owner|evidence|due)\s*=\s*(.+)$", p, re.I)
        if m:
            cur = m.group(1).lower()
            meta[cur] = m.group(2).strip()
        elif cur:
            # Η τιμή ενός πεδίου μπορεί να περιέχει «·» (π.χ. δύο τηλέφωνα) —
            # ανήκει στο προηγούμενο πεδίο, όχι στο κείμενο της εργασίας.
            meta[cur] = f"{meta[cur]} · {p}".strip(" ·")
        else:
            text_parts.append(p)
    return {"text": " · ".join(text_parts), "state": meta.get("state", "requested").lower(),
            "owner": meta.get("owner", "agent"), "evidence": meta.get("evidence", ""),
            "due": meta.get("due", "")}


def render_task(text: str, state: str, owner: str = "agent",
                evidence: str = "", due: str = "") -> str:
    parts = [text.strip(), f"state={state}", f"owner={owner}"]
    if evidence:
        parts.append(f"evidence={evidence.strip()}")
    if due:
        parts.append(f"due={due.strip()}")
    return " · ".join(p for p in parts if p)


def fact_marker(row: dict) -> str:
    """Σήμανση είδους ώστε κανόνες/απαγορεύσεις να ξεχωρίζουν στο compile."""
    p = norm(row["predicate"])
    if p.startswith(PROHIBIT_PREFIXES):
        return "⛔"
    if p.startswith("correction"):
        return "✎"
    if p.startswith(RULE_PREFIXES):
        return "§"
    if is_task(p):
        return "✓" if parse_task(row["value"])["state"] in TASK_DONE else "▸"
    return ""


# ── παρουσίαση: χρώμα μόνο σε tty, στοίχιση χωρίς εξαρτήσεις ────────────────
ANSI = {"dim": "\x1b[2m", "red": "\x1b[31m", "green": "\x1b[32m",
        "yellow": "\x1b[33m", "cyan": "\x1b[36m", "bold": "\x1b[1m",
        "reset": "\x1b[0m"}


def use_color() -> bool:
    """Ποτέ σε pipe/αρχείο· σεβασμός NO_COLOR· FACTLEDGER_COLOR=1 το επιβάλλει."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FACTLEDGER_COLOR") == "1":
        return True
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def paint(text: str, style: str) -> str:
    if not use_color() or style not in ANSI:
        return text
    return f"{ANSI[style]}{text}{ANSI['reset']}"


def dwidth(s: str) -> int:
    """Οπτικό πλάτος: οι τόνοι/συνδυαστικοί χαρακτήρες δεν πιάνουν στήλη."""
    w = 0
    for ch in str(s):
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, width: int, align: str = "left") -> str:
    gap = max(0, width - dwidth(s))
    return (" " * gap + s) if align == "right" else (s + " " * gap)


def human_age(days: int | None) -> str:
    if days is None:
        return "—"
    if days <= 0:
        return "σήμερα"
    if days < 7:
        return f"{days}ημ"
    if days < 60:
        return f"{days // 7}εβδ"
    if days < 730:
        return f"{days // 30}μ"
    return f"{days // 365}χ"


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
        def mark_group(r: dict) -> int:
            return {"⛔": 0, "§": 1, "✎": 2}.get(fact_marker(r), 3)

        if order == "recency":
            rows.sort(key=lambda r: (r["valid_from"] or "", r["scope"], r["subject"]), reverse=True)
        else:
            # Κανόνες/απαγορεύσεις πρώτα: αν κοπεί το budget, δεν χάνονται αυτά.
            rows.sort(key=lambda r: (mark_group(r), CONF_RANK.get(r["confidence"], 9),
                                     r["scope"], r["subject"]))
        lines = [header] if header else []
        used = len(header or "")
        for i, r in enumerate(rows):
            v = r["value"]
            if is_task(r["predicate"]):
                tk = parse_task(v)
                v = tk["text"] + (f" [{tk['state']}]" if tk["state"] != "requested" else "")
                if tk["due"]:
                    v += f" (due {tk['due']})"
            if max_value and len(v) > max_value:
                v = v[: max_value - 1].rstrip() + "…"
            m = fact_marker(r)
            line = f"- {m + ' ' if m else ''}{r['subject']} · {r['predicate']} = {v}"
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

    def close(self, fact_id: str, reason: str, ref: str | None = None) -> dict:
        """Κλείσιμο fact χωρίς αντικαταστάτη: έπαψε να ισχύει / δεν ήταν ποτέ κατάσταση.

        Το `add` κλείνει μόνο όταν υπάρχει νέα τιμή. Ένα log-τύπου fact («ολοκληρώθηκε»,
        «G1 exit 0») δεν έχει διάδοχο — και έτσι έμενε για πάντα ενεργό και σημασμένο
        από το `audit`. Ο λόγος γράφεται στο source, ώστε το κλείσιμο να είναι ελέγξιμο.
        """
        ref = ref or today()
        target = next((r for r in self.rows if r["id"] == fact_id), None)
        if target is None:
            raise LedgerError(f"δεν υπάρχει fact {fact_id}")
        if not self.covers(target, ref):
            raise LedgerError(f"{fact_id} δεν είναι ενεργό στις {ref}")
        if not reason.strip():
            raise LedgerError("χρειάζεται λόγος κλεισίματος")
        target["valid_to"] = ref
        target["superseded_by"] = ""
        target["source"] = f"{target['source']} · closed: {reason.strip()}"
        self.save()
        return target

    def confirm(self, fact_id: str, source: str, note: str = "",
                ref: str | None = None) -> dict:
        """Επαναβεβαίωση: η τιμή ισχύει ΑΚΟΜΑ — ίδια αλήθεια, νέα ημερομηνία.

        Χωρίς αυτό, ένα σωστό fact γερνάει στο `audit` για πάντα και ο θόρυβος
        κρύβει τα πραγματικά ληγμένα. Η επαναβεβαίωση κλείνει την παλιά γραμμή και
        γράφει ίδια τιμή με σημερινό `valid_from` — το ιστορικό δείχνει «ελέγχθηκε».
        """
        ref = ref or today()
        target = next((r for r in self.rows if r["id"] == fact_id), None)
        if target is None:
            raise LedgerError(f"δεν υπάρχει fact {fact_id}")
        if not self.covers(target, ref):
            raise LedgerError(f"{fact_id} δεν είναι ενεργό στις {ref}")
        src = source + (f" · {note}" if note else "")
        res = self.add(target["scope"], target["subject"], target["predicate"],
                       target["value"], src, target["confidence"], ref, allow_same=True)
        return {"confirmed": fact_id, **res}

    def audit(self, stale_days: int = 30, ref: str | None = None) -> dict:
        """Δομικός έλεγχος μνήμης — δεν κρίνει τιμές, κρίνει αν ανακτάται.

        Δύο αστοχίες που κάνουν τη μνήμη να «ξέρει αλλά να μη θυμάται»:
        1. facts κατάστασης (phase/status/pending…) που μένουν ενεργά χωρίς
           επανέλεγχο → διαβάζονται σαν σημερινή αλήθεια.
        2. facts θαμμένα κάτω από subject-ταυτότητα (note.<hash>, session.<id>,
           γυμνό hex/uuid) → δεν βρίσκονται με θέμα, μόνο με τύχη στη λέξη.

        Ένα επίπεδο αλλά περιγραφικό subject (`shipping`, `email`) ΔΕΝ είναι
        θαμμένο: `brief <subject>` το βρίσκει. Επιστρέφεται χωριστά ως
        `flat_subjects` (ενημερωτικό), ώστε ο αριθμός «θαμμένα» να σημαίνει
        μόνο ό,τι δεν ανακτάται.
        """
        ref = ref or today()
        today_dt = dt.date.fromisoformat(ref)
        state_words = ("phase", "status", "pending", "state", "progress", "next", "step")
        state_facts, fragmented, flat = [], [], []
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
            if opaque_subject(s):
                fragmented.append({"row": r, "age": age})
            elif age is not None and age >= stale_days and "." not in s:
                flat.append({"row": r, "age": age})
        state_facts.sort(key=lambda x: -x["age"])
        fragmented.sort(key=lambda x: -x["age"])
        flat.sort(key=lambda x: -x["age"])
        return {"ref": ref, "state_facts": state_facts, "fragmented": fragmented,
                "flat_subjects": flat,
                "counts": {"state_facts": len(state_facts), "fragmented": len(fragmented),
                           "flat_subjects": len(flat)}}

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

    # ── εργασίες: αίτημα που έχει κύκλο ζωής, όχι απλό fact ─────────────────
    def tasks(self, topic: str | None = None, include_done: bool = False,
              ref: str | None = None) -> list[dict]:
        """Ενεργές εργασίες `task.*` — προεπιλογή μόνο οι ανοιχτές.

        Η ηλικία μετριέται από το `valid_from` της τρέχουσας κατάστασης: μια
        εργασία που «ζητήθηκε» πριν 5 μέρες και δεν άλλαξε, φαίνεται 5 ημερών.
        """
        ref = ref or today()
        today_dt = dt.date.fromisoformat(ref)
        out = []
        for r in self.rows:
            if not is_task(r["predicate"]) or not self.covers(r, ref):
                continue
            t = parse_task(r["value"])
            if topic:
                hay = " | ".join((r["scope"], r["subject"], r["predicate"], t["text"]))
                if norm(topic) not in norm(hay):
                    continue
            if not include_done and t["state"] in TASK_DONE:
                continue
            try:
                age = (today_dt - dt.date.fromisoformat(r["valid_from"])).days
            except (ValueError, TypeError):
                age = None
            overdue = bool(t["due"]) and t["due"] < ref and t["state"] not in TASK_DONE
            out.append({"row": r, "task": t, "age": age, "overdue": overdue})
        out.sort(key=lambda x: (x["overdue"] is False, x["row"]["subject"], x["row"]["predicate"]))
        return out

    def task_set(self, scope: str, subject: str, verb: str, text: str,
                 owner: str = "agent", state: str = "requested", evidence: str = "",
                 due: str = "", source: str = "") -> dict:
        """Γράφει/ενημερώνει εργασία. `done` χωρίς απόδειξη απορρίπτεται."""
        st = norm(state)
        if st not in TASK_STATES:
            raise LedgerError(f"state must be one of {TASK_STATES}")
        pred = verb if norm(verb).startswith("task.") else "task." + norm(verb).replace(" ", "_")
        ev = str(evidence or "").strip()
        if st in TASK_DONE and not ev:
            raise LedgerError("done χωρίς --evidence: ένα «έγινε» χωρίς απόδειξη δεν καταγράφεται")
        value = render_task(text, st, owner, ev, due)
        src = source.strip() or f"task {st}"
        return self.add(scope, subject, pred, value, src)

    def task_done(self, fact_id: str, evidence: str, note: str = "") -> dict:
        """Κλείνει εργασία με απόδειξη (message-id, PR, path). Χωρίς απόδειξη: σφάλμα."""
        target = next((r for r in self.rows if r["id"] == fact_id), None)
        if target is None:
            raise LedgerError(f"δεν υπάρχει fact {fact_id}")
        if not self.covers(target, today()):
            raise LedgerError(f"{fact_id} δεν είναι ενεργό")
        if not is_task(target["predicate"]):
            raise LedgerError(f"{fact_id} δεν είναι εργασία (task.*)")
        ev = str(evidence or "").strip()
        if not ev:
            raise LedgerError("χρειάζεται --evidence <message-id|PR|path> — αλλιώς το «έγινε» δεν αποδεικνύεται")
        t = parse_task(target["value"])
        value = render_task(t["text"], "done", t["owner"], ev, t["due"])
        src = f"task done: {ev}" + (f" · {note}" if note.strip() else "")
        return self.add(target["scope"], target["subject"], target["predicate"],
                        value, src, target["confidence"], today(), allow_same=True)

    def check(self, deep: bool = False) -> list[str]:
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
            if r["valid_to"] and r["valid_from"] and r["valid_to"] < r["valid_from"]:
                problems.append(f"{r['id']}: valid_to {r['valid_to']} < valid_from {r['valid_from']}")
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
                elif r["valid_to"] and "· closed:" not in r["source"]:
                    problems.append(f"{r['id']}: κλειστή γραμμή χωρίς superseded_by")
        if deep:
            # Χειροκίνητες επεμβάσεις στο TSV: δεν πιάνεται από τους κανόνες
            # συνέπειας, αλλά χαλάει την αρίθμηση/το κλείσιμο της αλυσίδας.
            for r in self.rows:
                nxt = seen.get(r["superseded_by"]) if r["superseded_by"] else None
                if nxt and r["valid_to"] and nxt["valid_from"] and nxt["valid_from"] != r["valid_to"]:
                    problems.append(f"{r['id']}: κλείνει {r['valid_to']} αλλά η διάδοχος {nxt['id']} ξεκινά {nxt['valid_from']}")
            dupes: dict[tuple, list[str]] = {}
            for r in self.rows:
                k = self.key_of(r) + (norm(r["value"]), r["valid_from"])
                dupes.setdefault(k, []).append(r["id"])
            for k, ids in dupes.items():
                if len(ids) > 1:
                    problems.append(f"διπλή εγγραφή: {k[1]} · {k[2]} = {k[3][:40]} → {ids}")
            seq = [(r["id"], int(r["id"][1:])) for r in self.rows
                   if r["id"].startswith("f") and r["id"][1:].isdigit()]
            for (ia, a), (ib, b) in zip(seq, seq[1:]):
                if b < a:
                    problems.append(f"ids εκτός σειράς: {ia} πριν από {ib} (χειροκίνητη επέμβαση;)")
                    break
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
    s.add_argument("--source", action="store_true", help="δείξε και την προέλευση")

    h = sub.add_parser("history", help="τι άλλαξε πότε")
    h.add_argument("--subject", required=True)
    h.add_argument("--scope")
    h.add_argument("--predicate")
    h.add_argument("--full", action="store_true", help="πλήρης τιμή + source + superseded_by")

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
    br.add_argument("--handover", action="store_true", help="compact κάρτα έναρξης συνεδρίας")
    br.add_argument("--json", action="store_true", dest="as_json")

    tp = sub.add_parser("topics", help="ευρετήριο θεμάτων: ποια ζητήματα έχουν facts")
    tp.add_argument("--scopes", default="")
    tp.add_argument("--stale-days", type=int, default=30)
    tp.add_argument("--json", action="store_true", dest="as_json")

    ex = sub.add_parser("export", help="δομημένη εξαγωγή σε JSON")
    ex.add_argument("--scopes", default="")
    ex.add_argument("--all", action="store_true", dest="include_history")
    ex.add_argument("--format", choices=("json", "md", "csv"), default="json")
    ex.add_argument("--out", default="")

    ch = sub.add_parser("check", help="έλεγχος ακεραιότητας")
    ch.add_argument("--deep", action="store_true", help="και χειροκίνητες επεμβάσεις/αλυσίδες")
    au = sub.add_parser("audit", help="δομικός έλεγχος: facts που δεν ανακτώνται ή δεν ενημερώθηκαν")
    au.add_argument("--days", type=int, default=30)
    au.add_argument("--json", action="store_true", dest="as_json")

    cl = sub.add_parser("close", help="κλείσιμο fact χωρίς αντικαταστάτη (με λόγο)")
    cl.add_argument("id")
    cl.add_argument("--reason", required=True)

    cf = sub.add_parser("confirm", help="επαναβεβαίωση fact: ισχύει ακόμη, νέα ημερομηνία")
    cf.add_argument("id")
    cf.add_argument("--source", required=True)
    cf.add_argument("--note", default="")

    tk = sub.add_parser("tasks", help="εργασίες: τι ζητήθηκε, σε ποια κατάσταση, με ποια απόδειξη")
    tk.add_argument("topic", nargs="?", default=None)
    tk.add_argument("--scopes", default="")
    tk.add_argument("--all", action="store_true", dest="include_done")
    tk.add_argument("--json", action="store_true", dest="as_json")

    tt = sub.add_parser("task", help="καταγραφή ή ολοκλήρωση εργασίας")
    tsub = tt.add_subparsers(dest="task_cmd", required=True)
    ta = tsub.add_parser("add", help="νέα εργασία (state=requested)")
    ta.add_argument("--scope", required=True)
    ta.add_argument("--subject", required=True)
    ta.add_argument("--verb", required=True, help="π.χ. send_mail → task.send_mail")
    ta.add_argument("--text", required=True)
    ta.add_argument("--owner", default="agent")
    ta.add_argument("--state", default="requested")
    ta.add_argument("--evidence", default="")
    ta.add_argument("--due", default="")
    ta.add_argument("--source", default="")
    td = tsub.add_parser("done", help="ολοκλήρωση — απαιτεί --evidence")
    td.add_argument("id")
    td.add_argument("--evidence", default="")
    td.add_argument("--note", default="")

    sub.add_parser("stats", help="σύνολα")
    return p


def print_handover(led: "Ledger", b: dict, limit: int = 6) -> None:
    """Κάρτα έναρξης συνεδρίας: εργασίες, κατάσταση, πρόσφατες αλλαγές — σε 10 γραμμές."""
    c = b["counts"]
    print(paint(f"── {b['topic']} · {b['ref']} ──", "bold"))
    if not c["active"] and not c["changed"]:
        print("  κανένα fact — δοκίμασε `factledger topics`")
        return
    print(f"  ενεργά {c['active']} · παλιά {c['stale']} · αλλαγές {c['changed']}")
    tasks = led.tasks(b["topic"], include_done=False)
    if tasks:
        print(paint("  ▸ εργασίες", "cyan"))
        for t in tasks[:limit]:
            tk, row = t["task"], t["row"]
            line = (f"    {human_age(t['age']):>6}  {pad(tk['state'], 9)} "
                    f"{tk['text'][:64]}")
            if t["overdue"]:
                line += f"  ⚠ due {tk['due']}"
            if tk["evidence"]:
                line += f"  ↳ {tk['evidence'][:40]}"
            print(line)
    facts = [a for a in b["active"] if not is_task(a["row"]["predicate"])][:limit]
    if facts:
        print(paint("  ▸ κατάσταση", "cyan"))
        for a in facts:
            r = a["row"]
            print(f"    {human_age(a['age']):>6}  {fact_marker(r) or '·'} "
                  f"{r['predicate']} = {str(r['value'])[:88]}")
    if b["changed"]:
        print(paint("  ▸ άλλαξε πρόσφατα", "cyan"))
        for ch in b["changed"][:limit]:
            r = ch["row"]
            print(f"    {human_age(ch['days_ago']):>6}  ✗ {r['predicate']} = {str(r['value'])[:76]}")


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
        rows = led.active(args.scope, args.subject, args.as_of)
        if not rows:
            where = f" στις {args.as_of}" if args.as_of else ""
            print(f"κανένα ενεργό fact{where}")
        else:
            if args.as_of:
                print(paint(f"— ό,τι ίσχυε στις {args.as_of} —", "dim"))
            w1 = max(dwidth(r["id"]) for r in rows)
            w2 = max(dwidth(f"{r['scope']}/{r['subject']}") for r in rows)
            w3 = max(dwidth(r["predicate"]) for r in rows)
            for r in rows:
                mark = fact_marker(r)
                head = (f"{paint(pad(r['id'], w1), 'dim')}  "
                        f"{pad(r['scope'] + '/' + r['subject'], w2)} · "
                        f"{paint(pad(r['predicate'], w3), 'cyan')}")
                val = (mark + " " if mark else "") + r["value"]
                print(f"{head} = {val}  [{r['confidence']}] {paint(r['valid_from'], 'dim')}")
                if args.source:
                    print(f"{' ' * 2}↳ {r['source']}")
    elif args.cmd == "history":
        rows = led.history(args.subject, args.scope, args.predicate)
        if not rows:
            print(f"κανένα ιστορικό για «{args.subject}»")
        for r in rows:
            end = r["valid_to"] or "τώρα"
            arrow = "→" if r["valid_to"] else "•"
            print(f"{paint(r['id'], 'dim')}  {r['valid_from']} {arrow} {end}  "
                  f"{paint(r['predicate'], 'cyan')} = {r['value'] if args.full else r['value'][:120]}")
            if args.full:
                print(f"   ↳ source: {r['source']}")
                if r["superseded_by"]:
                    print(f"   ↳ διαδέχθηκε: {r['superseded_by']}")
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
        if args.handover:
            print_handover(led, b)
        elif args.as_json:
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
                    print(f"{mark} [{r['id']}] {r['scope']}/{r['subject']} · {r['predicate']} = {r['value']}{tail}")
                if b["changed"]:
                    print(f"— άλλαξε πρόσφατα ({args.recent} ημ.):")
                    for ch in b["changed"]:
                        r = ch["row"]
                        print(f"  ✗ [{r['id']}] {r['subject']} · {r['predicate']} = {r['value']}"
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
        if args.format == "md":
            cols = ("id", "scope", "subject", "predicate", "value", "valid_from", "valid_to")
            out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            for x in data:
                out.append("| " + " | ".join(str(x.get(c, "")).replace("|", "/")
                                             .replace("\n", " ") for c in cols) + " |")
            text = "\n".join(out)
        elif args.format == "csv":
            import io
            buf = io.StringIO()
            cols = list(data[0].keys()) if data else ["id"]
            w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            for x in data:
                w.writerow({c: x.get(c, "") for c in cols})
            text = buf.getvalue().rstrip("\n")
        else:
            text = json.dumps(data, ensure_ascii=False, indent=1)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print(f"γράφτηκε: {args.out} ({len(data)} facts)")
        else:
            print(text)
    elif args.cmd == "close":
        try:
            r = led.close(args.id, args.reason)
        except LedgerError as e:
            print(f"ΣΦΑΛΜΑ: {e}", file=sys.stderr)
            return 2
        print(f"⊘ {r['id']} έκλεισε {r['valid_to']} — {args.reason}")
    elif args.cmd == "confirm":
        try:
            r = led.confirm(args.id, args.source, args.note)
        except LedgerError as e:
            print(f"ΣΦΑΛΜΑ: {e}", file=sys.stderr)
            return 2
        print(f"✓ {args.id} → {r['id']} (επαναβεβαιώθηκε {today()})")
    elif args.cmd == "audit":
        a = led.audit(args.days)
        if args.as_json:
            print(json.dumps(a, ensure_ascii=False, indent=1, default=str))
        else:
            c = a["counts"]
            print(f"audit · {a['ref']} — {c['state_facts']} facts κατάστασης χωρίς επανέλεγχο"
                  f" · {c['fragmented']} θαμμένα κάτω από subject-ταυτότητα"
                  f" · {c['flat_subjects']} επίπεδα subjects (ανακτώνται με brief <subject>)")
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
    elif args.cmd == "tasks":
        scopes = [x for x in (args.scopes or "").split(",") if x.strip()]
        rows = led.tasks(args.topic, args.include_done)
        if scopes:
            want = {norm(s) for s in scopes}
            rows = [t for t in rows if norm(t["row"]["scope"]) in want]
        if args.as_json:
            print(json.dumps([{"id": t["row"]["id"], "scope": t["row"]["scope"],
                               "subject": t["row"]["subject"],
                               "predicate": t["row"]["predicate"], **t["task"],
                               "age": t["age"], "overdue": t["overdue"]} for t in rows],
                             ensure_ascii=False, indent=1))
        elif not rows:
            print("καμία εργασία" if args.include_done else "καμία ανοιχτή εργασία ✔")
        else:
            subj_w = max(dwidth(t["row"]["subject"]) for t in rows)
            label = "όλες" if args.include_done else "ανοιχτές"
            head = f"εργασίες · {label} {len(rows)}"
            if args.topic:
                head += f" · «{args.topic}»"
            print(paint(head, "bold"))
            for t in rows:
                r0, tk = t["row"], t["task"]
                icon = {"requested": "▸", "approved": "✔", "blocked": "⛔",
                        "done": "✓", "cancelled": "⊘"}.get(tk["state"], "▸")
                style = {"blocked": "red", "done": "green", "cancelled": "dim",
                         "approved": "green"}.get(tk["state"], "yellow")
                line = (f"{paint(icon, style)} {pad(r0['subject'], subj_w)}  "
                        f"{pad(tk['state'], 9)} {human_age(t['age']):>6}  {tk['text'][:64]}")
                if t["overdue"]:
                    line += paint(f"  ⚠ due {tk['due']}", "red")
                print(line)
                tail = []
                if tk["owner"] and tk["owner"] != "agent":
                    tail.append(f"owner {tk['owner']}")
                if tk["evidence"]:
                    tail.append(tk["evidence"])
                if tail:
                    print(f"    ↳ {' · '.join(tail)}")
    elif args.cmd == "task":
        try:
            if args.task_cmd == "add":
                r = led.task_set(args.scope, args.subject, args.verb, args.text,
                                 args.owner, args.state, args.evidence, args.due, args.source)
                pred = norm(args.verb)
                pred = pred if pred.startswith("task.") else "task." + pred.replace(" ", "_")
                print(f"+ {r['id']} {pred} · {norm(args.state)}")
            else:
                r = led.task_done(args.id, args.evidence, args.note)
                print(paint(f"✓ {args.id} → {r['id']} (done · {args.evidence})", "green"))
        except LedgerError as e:
            print(f"ΣΦΑΛΜΑ: {e}", file=sys.stderr)
            return 2
    elif args.cmd == "check":
        probs = led.check(getattr(args, "deep", False))
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
