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
SEP_LEN = len(SEP)  # ο διαχωριστής μετράει στο budget: αλλιώς το αρχείο βγαίνει μεγαλύτερο απ' όσο δηλώνει


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if "db" not in cfg or "targets" not in cfg:
        raise SystemExit("το config χρειάζεται 'db' και 'targets'")
    return cfg


def parse_generated(text: str) -> list[str]:
    """Γραμμές που ΠΑΡΑΓΑΓΑΜΕ εμείς (αναγνωρίζονται από το πρόθεμα '- ').

    Οι σημειώσεις υπερχείλισης («… (+N ακόμη …)») ΔΕΝ είναι γεγονότα: όταν αλλάζει
    το N αλλάζουν και αυτές, οπότε θα φαίνονταν «χειροκίνητες αλλαγές» και θα
    εισάγονταν ως ψευδο-γεγονότα. Εξαιρούνται ρητά.
    """
    out = []
    for chunk in text.split("§"):
        line = chunk.strip()
        if line.startswith("- "):
            out.append(line)
    return out


def is_generated_noise(line: str) -> bool:
    """Σημείωση/επικεφαλίδα του ίδιου του sync (όχι περιεχόμενο του χρήστη).

    Οι τρεις τελευταίες μορφές είναι το ίδιο πράγμα με το `… (+N ακόμη)`: γραμμές
    που ΠΑΡΑΓΕΙ ο generator. Χωρίς αυτές, το επόμενο sync τις εισάγει ως facts
    `handnote/note.<hash>` — έτσι γεννήθηκαν 18 «θαμμένα» subjects (17/11).
    """
    s = line.strip()
    if s.startswith("…") or s.startswith("...") or "ακόμη, δες `factledger show`" in s:
        return True
    if s.startswith("ΠΑΡΑΓΟΜΕΝΟ από"):
        return True
    if len(s) > 4 and s.startswith("—") and s.endswith("—"):
        return True          # «— ανοιχτές εργασίες (όλα τα profiles) —»
    return s in ("§", "---")


def looks_like_fact_line(line: str) -> bool:
    """Γραμμή με τη ΜΟΡΦΗ της compiled όψης: `- <subject> · <predicate> = <value>`.

    Μια τέτοια γραμμή ΔΕΝ εισάγεται ποτέ ως handnote. Όταν το snapshot λείπει ή
    έχει μείνει πίσω, η ίδια μας η όψη ξαναγράφεται ως `note.<hash>` κάτω από
    subject-ταυτότητα — δηλαδή «θαμμένη» αλήθεια που δεν ανακτάται (17/11: 18
    subjects, 21/11: 5). Ό,τι χειροκίνητο κρατούσε πραγματικό νόημα γράφεται ως
    πρόζα ή ως typed fact με `factledger add` — όχι ως γραμμή της όψης.
    """
    s = line.strip()
    return s.startswith("- ") and " · " in s and " = " in s


def hand_edits(existing: str, snapshot: str) -> list[str]:
    """Γραμμές στο υπάρχον αρχείο που ΔΕΝ υπάρχουν στην τελευταία παραγωγή.

    Χωρίς baseline ΔΕΝ γίνεται ανίχνευση: με κενό snapshot κάθε γραμμή της όψης
    φαίνεται «χειροκίνητη» και το store γεμίζει με τα δικά μας facts ως handnotes.
    """
    if not (snapshot or "").strip():
        return []
    old = set(parse_generated(snapshot))
    new = [line for line in parse_generated(existing)
           if line not in old and not looks_like_fact_line(line)]
    if new:
        return new
    return [c.strip() for c in existing.split("§")
            if c.strip() and not c.strip().startswith("- ") and not is_generated_noise(c)
            and c.strip() not in (snapshot or "").split("§")]


def view_subject(target: str) -> str:
    """Ανακτήθηκε subject για χειροκίνητες γραμμές: `view.<profile>`.

    Το `note.<hash>` ήταν subject-ταυτότητα — το `audit` το έλεγε «θαμμένο» και
    το `brief` δεν το έβρισκε ποτέ, άρα η γνώση του χρήστη χανόταν σιωπηλά.
    """
    d = os.path.dirname(os.path.abspath(target))
    if os.path.basename(d).lower() == "memories":
        name = os.path.basename(os.path.dirname(d))
        if name.lower() == "hermes":
            name = "default"
        return f"view.{name}"
    return "view.unknown"


def import_handnotes(led: Ledger, target: str, lines: list[str]) -> list[str]:
    """Χειροκίνητες γραμμές → γεγονότα. Idempotent: ίδια γραμμή → ίδιο κλειδί."""
    imported = []
    subject = view_subject(target)
    for line in lines:
        h = hashlib.sha1(line.encode("utf-8")).hexdigest()[:8]
        r = led.add(scope="handnote", subject=subject, predicate=f"note.{h}", value=line,
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
    used = len(header) + SEP_LEN if header else 0

    # Οι ανοιχτές εργασίες μπαίνουν ΠΡΙΝ από τα scopes και από ΟΛΑ τα scopes:
    # είναι το μόνο περιεχόμενο που πρέπει να ταξιδεύει σε κάθε profile, αλλιώς
    # ένα θέμα που ξεκίνησε στο default δεν συνεχίζεται από το it-section.
    tcfg = target.get("tasks")
    if tcfg:
        tbudget = min(int(tcfg.get("budget", 600)), max(0, budget - used))
        tmax = int(tcfg.get("max", 8))
        items = led.tasks()
        if items and tbudget > 80:
            block, spent = ["— ανοιχτές εργασίες (όλα τα profiles) —"], 0
            spent += len(block[0]) + SEP_LEN
            for t in items[:tmax]:
                tk, r0 = t["task"], t["row"]
                verb = r0["predicate"].split(".", 1)[-1]
                tail = tk["state"] + (f", due {tk['due']}" if tk["due"] else "")
                nw = t.get("newer")
                if nw:
                    tail += f", ⚠ νεότερο {nw['id']}"
                line = f"- ▸ {r0['subject']} · {verb} = {tk['text'][:58]} [{tail}]"
                if spent + len(line) + SEP_LEN > tbudget:
                    break
                block.append(line)
                spent += len(line) + SEP_LEN
            if len(block) > 1:
                lines.extend(block)
                used += spent

    carry = 0
    for idx, sc in enumerate(scopes or []):
        # Κρατάμε χώρο για την τελική σημείωση «λείπουν scopes», αλλιώς το αρχείο
        # τελειώνει σιωπηλά και ο αναγνώστης νομίζει ότι τα είδε όλα.
        reserve = 60 if idx < len(scopes) - 1 else 0
        allowed = min(int(quotas.get(sc, default_q)) + carry, budget - used - reserve)
        if allowed <= 60:
            note = "… (λείπουν τα υπόλοιπα scopes — `factledger show`)"
            if used + len(note) + SEP_LEN <= budget:
                lines.append(note)
            break
        rows = sorted(led.active(scope=sc), key=lambda r: rank(r["subject"]))
        sc_lines, spent, shown = [], 0, 0
        for r in rows:
            v = r["value"]
            if max_value and len(v) > max_value:
                v = v[: max_value - 1].rstrip() + "…"
            line = f"- {r['subject']} · {r['predicate']} = {v}"
            if spent + len(line) + SEP_LEN > allowed:
                break
            sc_lines.append(line)
            spent += len(line) + SEP_LEN
            shown += 1

        def note_for(n_left: int) -> str:
            return f"… (+{n_left} ακόμη στο {sc} — `factledger show --scope {sc}`)"

        def fits(note: str) -> bool:
            return (spent + len(note) + SEP_LEN <= allowed
                    and used + spent + len(note) + SEP_LEN <= budget)

        if shown < len(rows):
            # Η σημείωση υπερχείλισης προηγείται της τελευταίας γραμμής: χωρίς αυτήν
            # ο αναγνώστης διαβάζει το αρχείο σαν να είδε ΟΛΗ τη μνήμη. Θυσιάζουμε
            # γραμμές μέχρι να χωρέσει (μία τουλάχιστον παραμένει, αλλιώς το scope
            # θα έμοιαζε άδειο).
            while sc_lines and not fits(note_for(len(rows) - shown)):
                spent -= len(sc_lines.pop()) + SEP_LEN
                shown -= 1
            note = note_for(len(rows) - shown)
            if fits(note):
                sc_lines.append(note)
                spent += len(note) + SEP_LEN
        lines.extend(sc_lines)
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
