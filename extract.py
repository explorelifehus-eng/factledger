#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract — αυτόματη εξαγωγή διαρκών γεγονότων από transcripts → ledger.

Διαβάζει τα μηνύματα του χρήστη από μια βάση συνομιλιών (SQLite), ζητά από LLM
να βγάλει ΜΟΝΟ διαρκή γεγονότα σε αυστηρό JSON, τα ΕΠΑΛΗΘΕΥΕΙ και τα γράφει.

Χρήση:
    python extract.py --config extract.json                # dry-run
    python extract.py --config extract.json --apply

Οι τρεις μηχανισμοί που κάνουν την εξαγωγή ασφαλή:
 1. **Υποχρεωτικό quote**: κάθε γεγονός επιστρέφεται με αυτολεξεί απόσπασμα από την
    πηγή. Ό,τι δεν βρίσκεται στο κείμενο **απορρίπτεται** — οι παραισθήσεις δεν περνούν.
 2. **Καμία διαγραφή**: η εγγραφή γίνεται με supersede μέσω του ledger. Η ιστορία μένει.
 3. **Checkpoint**: καταγράφεται το τελευταίο id που επεξεργάστηκε· επανάληψη δεν
    δημιουργεί διπλά (ίδια τιμή → «χωρίς αλλαγή»).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factledger import Ledger, LedgerError, norm  # noqa: E402

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|shpat_[a-f0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY|Bearer\s+[A-Za-z0-9._-]{20,}"
    r"|[A-Za-z0-9_]*(?:API_KEY|PASSWORD|SECRET_KEY)[A-Za-z0-9_]*\s*=\s*\S{8,})", re.I)
SKIP_MARKERS = (
    "[OUT-OF-BAND USER MESSAGE", "[ASYNC DELEGATION BATCH", "[CONTEXT COMPACTION",
    "[Context from the interrupted", "--- Context Warnings ---", "─── Context",
    "<system", "<tool_result", "[IMPORTANT: Background process",
    "✔ [default]", "Kanban t_", "[This response was interrupted",
)
SYSTEM_PROMPT = """Είσαι εξαγωγέας ΔΙΑΡΚΩΝ γεγονότων από συνομιλίες χρήστη–βοηθού.
Επιστρέφεις ΑΥΣΤΗΡΑ ΕΝΑ JSON, χωρίς σχόλια, της μορφής:
{"facts":[{"scope":"...","subject":"...","predicate":"...","value":"...","confidence":"stated|derived|assumed","quote":"..."}]}

Τι είναι επιτρεπτό (διαρκές):
- αποφάσεις και προτιμήσεις του χρήστη, κανόνες που θέλει να τηρούνται
- ταυτότητες/στοιχεία που δεν αλλάζουν (ονόματα, λογαριασμοί, διαδρομές, διευθύνσεις)
- τιμές/κόστη/στόχοι/όρια/παράμετροι ρυθμίσεων, αρχιτεκτονικές αποφάσεις
- διορθώσεις: αν ο χρήστης διορθώνει κάτι που είχε ειπωθεί, ΚΡΑΤΑ ΜΟΝΟ τη νέα τιμή

Τι ΑΠΑΓΟΡΕΥΕΤΑΙ:
- παροδική κατάσταση («τρέχει», «περιμένω», «19/9 13:40», εκκρεμότητες μιας μέρας)
- **εντολές/αιτήματα για εργασία** («ξεκίνα», «διέγραψε», «ενσωμάτωσε», «υλοποίησε»): είναι
  task state, όχι γεγονός — ακόμη κι αν εκτελέστηκαν. Εξαίρεση μόνο αν εκφράζουν ΜΟΝΙΜΟ
  κανόνα ή προτίμηση («πάντα πίνακας», «ποτέ auto-post»)
- μυστικά: κλειδιά API, κωδικοί, tokens — ΠΟΤΕ
- γενικές γνώσεις, περιγραφές εργαλείων, ό,τι δεν δήλωσε ο χρήστης για τον εαυτό του/το έργο του
- περισσότερα από 12 γεγονότα: κράτα τα πιο σημαντικά. Αν δεν υπάρχει τίποτα διαρκές: {"facts":[]}

Δοκιμή μονιμότητας πριν γράψεις κάθε γεγονός: **«σε 3 μήνες, θα έχει νόημα αυτό χωρίς το
πλαίσιο της σημερινής συζήτησης;»** Αν όχι, μην το βάλεις.

Κανόνες πεδίων:
- scope: ένα από: {scopes}
- subject: πεζά με τελείες, π.χ. product.70x70, account.github, pricing.rule
- predicate: σύντομο πεζό, π.χ. cost, target, preference, status
- value: σύντομο και αυτοτελές (έως 200 χαρακτήρες)
- confidence: "stated" αν το είπε ρητά ο χρήστης, "derived" αν το συνήγαγες
- quote: ΑΥΤΟΛΕΞΕΙ απόσπασμα (έως 150 χαρακτήρες) από τα μηνύματα του χρήστη που
  αποδεικνύει το γεγονός. Αν δεν μπορείς να παραθέσεις αυτολεξεί, ΜΗΝ το βάλεις.

ΠΑΡΑΔΕΙΓΜΑΤΑ (μάθε τη διαφορά):
- «το κόστος του 70x70 είναι 22 ευρώ, διόρθωση από 15»
  ΣΩΣΤΟ → {"scope":"silktales","subject":"product.70x70","predicate":"cost","value":"22","confidence":"stated","quote":"το κόστος του 70x70 είναι 22 ευρώ"}
- «ενσωμάτωσε το Context7 και το Github»
  ΣΩΣΤΟ → {"facts":[]}  ← εντολή για εργασία: καμία διαρκής πληροφορία
  ΛΑΘΟΣ → value="Ενσωμάτωση Context7 και Github"
- «διέγραψε τον φάκελο mem0»
  ΣΩΣΤΟ → {"facts":[]}  ← μεμονωμένη ενέργεια που εκτελέστηκε
  ΛΑΘΟΣ → value="Διαγραφή του φακέλου mem0"
- «ξεκίνα την υλοποίηση»
  ΣΩΣΤΟ → {"facts":[]}  ← task state
  ΛΑΘΟΣ → value="Ο χρήστης ζήτησε να ξεκινήσει η υλοποίηση"
- «θέλω πάντα πίνακα όταν δίνω α/β/γ»
  ΣΩΣΤΟ → {"scope":"user","subject":"style.tasks","predicate":"format","value":"πίνακας, ίδια σειρά & labels","confidence":"stated","quote":"θέλω πάντα πίνακα"}
- «στο Github τα στοιχεία είναι GiaVaSwNi, email explorelifehus@gmail.com»
  ΣΩΣΤΟ → {"scope":"user","subject":"account.github","predicate":"username","value":"GiaVaSwNi","confidence":"stated","quote":"τα στοιχεία είναι GiaVaSwNi"}"""


def load_env(path: str) -> dict:
    env = {}
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_cfg(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("state_db", "db", "api"):
        if k not in cfg:
            raise SystemExit(f"το config χρειάζεται '{k}'")
    return cfg


def fetch_messages(cfg: dict, since_id: int, limit: int | None, min_chars: int) -> list[dict]:
    c = sqlite3.connect(f"file:{cfg['state_db']}?mode=ro", uri=True)
    q = "SELECT id, session_id, role, content, timestamp FROM messages WHERE id > ? AND role='user' ORDER BY id"
    rows = c.execute(q, (since_id,)).fetchall()
    c.close()
    out = []
    for mid, sid, role, content, ts in rows:
        if not content:
            continue
        text = str(content)
        if len(text) < min_chars or any(m in text for m in SKIP_MARKERS):
            continue
        out.append({"id": mid, "session_id": sid, "text": text, "ts": ts})
    if limit:
        out = out[:limit]
    return out


def batch(messages: list[dict], max_chars: int) -> list[list[dict]]:
    batches, cur, size = [], [], 0
    for m in messages:
        if size + len(m["text"]) > max_chars and cur:
            batches.append(cur)
            cur, size = [], 0
        cur.append(m)
        size += len(m["text"])
    if cur:
        batches.append(cur)
    return batches


def call_llm(cfg: dict, env: dict, scopes: list[str], text: str) -> dict:
    api = cfg["api"]
    key = env.get(api["key_env"], "") or os.environ.get(api["key_env"], "")
    if not key:
        raise SystemExit(f"λείπει το κλειδί {api['key_env']}")
    body = json.dumps({
        "model": api["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.replace("{scopes}", ", ".join(scopes))},
            {"role": "user", "content": "ΜΗΝΥΜΑΤΑ ΧΡΗΣΤΗ:\n" + text},
        ],
        "temperature": 0,
        "max_tokens": api.get("max_tokens", 3000),
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(api["base_url"], data=body,
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=api.get("timeout", 180)).read().decode("utf-8", "replace")
    content = json.loads(raw)["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    return json.loads(content)


def validate(facts: list[dict], text: str, scopes: list[str]) -> tuple[list[dict], list[str]]:
    flat = norm(text)
    ok, rejected = [], []
    # Το φίλτρο καταστάσεων-εργασίας είναι επίτηδες ΕΔΩ και όχι μόνο στο prompt:
    # το prompt πείθει, ο κώδικας εγγυάται. Ό,τι μυρίζει εντολή/εκτέλεση απορρίπτεται
    # ανεξάρτητα από την κρίση του μοντέλου.
    task_predicates = {"action", "request", "implementation", "requirement",
                       "required", "task", "todo", "goal"}
    task_value = re.compile(
        r"^(Να |Διαγραφή |Ενσωμάτωση |Υλοποίηση |Ξεκίνα|Μετατροπή |Αλλαγή του |Διόρθωση του )")
    for f in facts:
        why = None
        for k in ("scope", "subject", "predicate", "value", "quote"):
            if not str(f.get(k, "")).strip():
                why = f"λείπει '{k}'"
        if why:
            rejected.append(f"{f} → {why}"); continue
        if (norm(f["predicate"]) in task_predicates
                or task_value.match(str(f["value"]).strip())):
            rejected.append(f"{f['subject']} · {f['predicate']} → εντολή/εκτέλεση, όχι διαρκές γεγονός")
            continue
        if SECRET_RE.search(str(f["value"]) + " " + str(f["quote"])):
            rejected.append(f"{f['subject']} → φαίνεται μυστικό"); continue
        if norm(f["quote"]) not in flat:
            rejected.append(f"{f['subject']} → το quote ΔΕΝ υπάρχει στην πηγή"); continue
        if len(str(f["value"])) > 300 or len(str(f["subject"])) > 60 or len(str(f["predicate"])) > 60:
            why = "πολύ μακρύ πεδίο"
            rejected.append(f"{f['subject']} → {why}"); continue
        if f["scope"] not in scopes:
            f["scope"] = "other"
        if f.get("confidence") not in ("stated", "derived", "assumed"):
            f["confidence"] = "derived"
        ok.append(f)
    return ok, rejected


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--since-id", type=int, default=None)
    a = ap.parse_args(argv)

    cfg = load_cfg(a.config)
    env = load_env(cfg.get("env_file", ""))
    scopes = cfg.get("scopes", ["user", "silktales", "hermes", "infra", "market", "other"])
    state = {}
    cp = cfg.get("checkpoint")
    if cp and os.path.exists(cp):
        state = json.load(open(cp, encoding="utf-8"))
    since = a.since_id if a.since_id is not None else int(state.get("last_message_id", 0))

    msgs = fetch_messages(cfg, since, a.limit, int(cfg.get("min_user_chars", 25)))
    print(f"μηνύματα προς επεξεργασία: {len(msgs)} (από id > {since})")
    if not msgs:
        print("τίποτα νέο — έξοδος")
        return 0

    led = Ledger(cfg["db"], cfg.get("aliases"))
    total = {"added": 0, "closed": 0, "unchanged": 0}
    all_applied, all_rejected = [], []
    processed_to = since
    for i, b in enumerate(batch(msgs, int(cfg.get("max_chars", 18000))), 1):
        text = "\n\n---\n\n".join(f"[{m['session_id']} #{m['id']}] {m['text']}" for m in b)
        print(f"\nπαρτίδα {i}: {len(b)} μηνύματα, {len(text)} χαρ.")
        try:
            r = call_llm(cfg, env, scopes, text)
        except Exception as e:
            print(f"  ΣΦΑΛΜΑ LLM: {e} — σταματώ εδώ (checkpoint δεν προχωρά)")
            break
        facts = r.get("facts") or []
        good, bad = validate(facts, text, scopes)
        print(f"  προτεινόμενα: {len(facts)} · αποδεκτά: {len(good)} · απορρίφθηκαν: {len(bad)}")
        for x in bad:
            print("    ✗", x[:160])
        for f in good:
            src = f"session {b[0]['session_id']} msg#{b[0]['id']} · «{str(f['quote'])[:110]}»"
            print(f"    · [{f['scope']}] {f['subject']} · {f['predicate']} = {f['value']}  ({f['confidence']})")
            if a.apply:
                try:
                    res = led.add(f["scope"], f["subject"], f["predicate"], str(f["value"]),
                                  src, f["confidence"])
                    total[res["status"]] = total.get(res["status"], 0) + 1
                    if res["status"] == "closed":
                        total["closed"] += 0
                    all_applied.append(f)
                except LedgerError as e:
                    print("       ΣΦΑΛΜΑ εγγραφής:", e)
        all_rejected += bad
        processed_to = b[-1]["id"]

    if a.apply:
        print(f"\nγράφτηκαν: {total.get('added', 0)} νέα · {total.get('unchanged', 0)} χωρίς αλλαγή · "
              f"{total.get('closed', 0)} αντικαταστάσεις")
        if cp:
            os.makedirs(os.path.dirname(cp), exist_ok=True)
            json.dump({"last_message_id": processed_to,
                       "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                       "applied": len(all_applied), "rejected": len(all_rejected)},
                      open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"checkpoint: id {processed_to} → {cp}")
        for cmd in cfg.get("post_sync", []):
            print(f"\nεπόμενο βήμα: {' '.join(cmd)}")
            subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)), check=False)
    else:
        print("\n[dry-run] ΔΕΝ γράφτηκε τίποτα. Πρόσθεσε --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
