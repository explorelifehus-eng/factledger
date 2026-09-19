#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_memory — παράγει αρχεία μνήμης (MEMORY.md/USER.md) ΑΠΟ το ledger.

Χρήση:
    python sync_memory.py --config sync.json            # dry-run (δείχνει τι θα γράψει)
    python sync_memory.py --config sync.json --apply    # γράφει (με αντίγραφα)

Κανόνες ασφάλειας — όλοι ρητοί, όχι προαιρετικοί:
 1. Δεν γράφει τίποτα αν το ledger δεν περνά το `check` (αντιφατική αλήθεια = στοπ).
 2. Πριν από κάθε εγγραφή: αντίγραφο `<target>.bak-<timestamp>`.
 3. Αν το υπάρχον αρχείο ΔΙΑΦΕΡΕΙ από την τελευταία παραγωγή (χειροκίνητη αλλαγή),
    οι επιπλέον γραμμές εισάγονται ως γεγονότα και φυλάσσεται αντίγραφο —
    τίποτα δεν χάνεται σιωπηλά.
 4. Όριο χαρακτήρων ανά αρχείο: ό,τι δεν χωρά ΔΗΛΩΝΕΤΑΙ ως «+N ακόμη».
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factledger import Ledger, LedgerError  # noqa: E402

SEP = "\n§\n"


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if "db" not in cfg or "targets" not in cfg:
        raise SystemExit("το config χρειάζεται 'db' και 'targets'")
    return cfg


def parse_generated(text: str) -> list[str]:
    """Γραμμές που ΠΑΡΑΓΑΓΑΜΕ εμείς (αναγνωρίζονται από το πρόθεμα '- ')."""
    out = []
    for chunk in text.split("§"):
        line = chunk.strip()
        if line.startswith("- "):
            out.append(line)
    return out


def hand_edits(existing: str, snapshot: str) -> list[str]:
    """Γραμμές στο υπάρχον αρχείο που ΔΕΝ υπάρχουν στην τελευταία παραγωγή."""
    old = set(parse_generated(snapshot)) if snapshot else set()
    return [line for line in parse_generated(existing) if line not in old] or \
           [c.strip() for c in existing.split("§")
            if c.strip() and not c.strip().startswith("- ") and c.strip() not in (snapshot or "").split("§")]


def import_handnotes(led: Ledger, target: str, lines: list[str]) -> list[str]:
    """Χειροκίνητες γραμμές → γεγονότα. Idempotent: ίδια γραμμή → ίδιο κλειδί."""
    imported = []
    for line in lines:
        h = hashlib.sha1(line.encode("utf-8")).hexdigest()[:8]
        r = led.add(scope="handnote", subject=f"note.{h}", predicate="text", value=line,
                    source=f"{os.path.basename(target)} — χειροκίνητη προσθήκη",
                    confidence="stated")
        if r["status"] != "unchanged":
            imported.append(line)
    return imported


def render(led: Ledger, target: dict) -> str:
    """Ποσοστώσεις ανά scope + ρητή σειρά σημασίας (το «τι μετράει» είναι θέμα ΟΨΗΣ).

    Το config δίνει `order`: προθέματα subject με σειρά προτεραιότητας. Ό,τι δεν
    ταιριάζει πάει στο τέλος, αλφαβητικά. Έτσι το budget κόβει πάντα το λιγότερο
    σημαντικό — όχι ό,τι τύχει αλφαβητικά.
    """
    scopes = target.get("scopes") or []
    budget = int(target.get("budget", 1200))
    quotas = target.get("quotas") or {}
    default_q = int(target.get("scope_quota", 250))
    header = target.get("header") or None
    max_value = int(target.get("max_value", 0))
    order = [p.lower() for p in (target.get("order") or [])]

    def rank(subject: str) -> tuple:
        s = subject.lower()
        for i, p in enumerate(order):
            if s.startswith(p):
                return (i, s)
        return (len(order), s)

    lines = [header] if header else []
    used = len(header) if header else 0
    carry = 0
    for sc in scopes or []:
        allowed = min(int(quotas.get(sc, default_q)) + carry, budget - used)
        if allowed <= 60:
            lines.append("… (τα υπόλοιπα scopes εκτός budget — `factledger show`)")
            break
        rows = sorted(led.active(scope=sc), key=lambda r: rank(r["subject"]))
        spent, shown = 0, 0
        for r in rows:
            v = r["value"]
            if max_value and len(v) > max_value:
                v = v[: max_value - 1].rstrip() + "…"
            line = f"- {r['subject']} · {r['predicate']} = {v}"
            if spent + len(line) + 1 > allowed:
                break
            lines.append(line)
            spent += len(line) + 1
            shown += 1
        if shown < len(rows):
            note = f"… (+{len(rows) - shown} ακόμη, δες `factledger show`)"
            lines.append(note)
            spent += len(note) + 1
        used += spent
        carry = max(0, allowed - spent)
    if not scopes:
        lines = [ln for ln in led.compile(None, budget=budget, header=header,
                                           max_value=max_value).splitlines() if ln.strip()]
    return SEP.join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--apply", action="store_true", help="χωρίς αυτό: dry-run")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    led = Ledger(cfg["db"], cfg.get("aliases"))
    probs = led.check()
    if probs:
        print("ΣΤΟΠ — το ledger δεν είναι υγιές:")
        for p in probs:
            print("  -", p)
        return 2

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    rc = 0
    for t in cfg["targets"]:
        path = t["path"]
        snap_path = path + ".generated"
        existing = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        snapshot = open(snap_path, encoding="utf-8").read() if os.path.exists(snap_path) else ""

        # Χειροκίνητη αλλαγή = απόκλιση από ΤΕΚΜΗΡΙΩΜΕΝΗ βάση (snapshot). Στην 1η
        # εκτέλεση δεν υπάρχει βάση, άρα το υπάρχον περιεχόμενο ΔΕΝ είναι «χειροκίνητο».
        extra = (hand_edits(existing, snapshot)
                 if snapshot.strip() and existing.strip() != snapshot.strip() else [])
        new = render(led, t)
        chars = len(new)
        print(f"\n── {os.path.basename(path)} ──")
        print(f"   σήμερα: {len(existing)} χαρ. → νέα όψη: {chars} χαρ. "
              f"({'μείωση' if chars < len(existing) else 'αύξηση'} {abs(chars - len(existing))})")
        print(f"   scopes={t.get('scopes')} · budget={t.get('budget', 1200)} · γραμμές={new.count('§') + 1}")
        if extra:
            print(f"   ⚠ χειροκίνητες γραμμές που θα διασωθούν: {len(extra)}")
            for e in extra[:4]:
                print(f"      · {e[:90]}")

        if not a.apply:
            print("   [dry-run] ΔΕΝ γράφτηκε. Πλήρες περιεχόμενο:")
            print("      " + new.replace("\n", "\n      "))
            continue

        if existing and existing.strip() != snapshot.strip():
            shutil.copy2(path, f"{path}.bak-{stamp}")
            print(f"   αντίγραφο: {os.path.basename(path)}.bak-{stamp}")
            if extra:
                got = import_handnotes(led, path, extra)
                print(f"   εισήχθησαν ως γεγονότα (scope=handnote): {len(got)}")
                rc = 0
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        with open(snap_path, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"   ✓ γράφτηκε + snapshot (.generated)")

    if a.apply:
        st = led.stats()
        print(f"\nledger: {st['σύνολο']} γραμμές · ενεργές {st['ενεργές']} · check: "
              f"{'υγιές ✔' if not led.check() else 'ΠΡΟΒΛΗΜΑ'}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
