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
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factledger import Ledger, LedgerError, norm  # noqa: E402

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|shpat_[a-f0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY|Bearer\s+[A-Za-z0-9._-]{20,}"
    r"|[A-Za-z0-9_]*(?:API_KEY|PASSWORD|SECRET_KEY)[A-Za-z0-9_]*\s*=\s*\S{8,}"
    # Γυμνό UUID (π.χ. Helius API key): 8-4-4-4-12 hex. Ένα UUID σε πεζογράφημα δεν
    # είναι ποτέ «απλό id» — ό,τι έχει αυτό το σχήμα είναι credential-grade token.
    r"|\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    # key/token σε URL ή assignment: ?api-key=…, api_key: … — το `=`/`:` μαζί με
    # όνομα κλειδιού αρκεί· δεν πιάνει σκέτο `key=value` (θα έπιανε ρυθμίσεις).
    r"|[A-Za-z0-9_-]*(?:api[-_]?key|access[-_]?token|auth[-_]?token|apikey|token)"
    r"[A-Za-z0-9_-]*[\s\"']*[=:][\s\"']*\S{8,}"
    # Γυμνό 32-hex token. Τα 40/64-hex (sha1/sha256 digests) ΕΞΑΙΡΟΥΝΤΑΙ σκόπιμα:
    # τα lookbehind/lookahead απαγορεύουν γειτονικό hex, άρα ένα 40άρι/64άρι hash
    # δεν ταιριάζει πουθενά μέσα του. Ένα 12-hex cron id δεν έχει 32 συνεχόμενα hex.
    r"|(?<![0-9a-fA-F])[0-9a-f]{32}(?![0-9a-fA-F]))", re.I)
SKIP_MARKERS = (
    "[OUT-OF-BAND USER MESSAGE", "[ASYNC DELEGATION BATCH", "[CONTEXT COMPACTION",
    "[Context from the interrupted", "--- Context Warnings ---", "─── Context",
    "<system", "<tool_result", "[IMPORTANT: Background process",
    # Το σώμα skill που εγχέει η πλατφόρμα φτάνει ως «μήνυμα χρήστη» 140k χαρακτήρων.
    # Δεν είναι δικά του λόγια: είναι γενικές οδηγίες (το prompt τις απαγορεύει ρητά
    # ως γεγονότα) και ήταν η αιτία που κόλλησε η εξαγωγή του crypto-trading.
    "[IMPORTANT: The user has invoked the",
    "✔ [default]", "Kanban t_", "[This response was interrupted",
)

# Φίλτρο μονιμότητας (config: transient_predicates / transient_signals). Λογική:
# μια «κατάσταση» είναι παροδική ΜΟΝΟ όταν το value κουβαλά σημείο-στον-χρόνο ή
# μετρητή προόδου. Έτσι ένα διαρκές «status = ΕΝΕΡΓΟ» ή ένα «schedule = 09:55»
# (άλλο predicate) επιβιώνουν, ενώ «status = … επόμενο στις 15:28» πέφτει.
TRANSIENT_PREDICATES = {"status", "progress", "state"}
TRANSIENT_SIGNALS = (
    r"\d{1,2}:\d{2}",
    r"\bεπόμεν\w*",
    r"\bσήμερα\b|\bτώρα\b|\bμόλις\b|\bαυτή τη στιγμή\b|\bσε εξέλιξη\b|\bτρέχει\b",
    r"\bολοκληρώθηκε\b|\bξεκίνησε\b|\bπάγωσε\b",
    r"\b\d+\s*(?:tests?|σφάλματα|αποτυχί\w+|πράσιν\w+)",
    r"\b\d+\s*(?:msgs?|μηνύματα|ευρήματα|αρχεία)\b",
)
TRANSIENT_RE = re.compile("|".join(TRANSIENT_SIGNALS), re.I)

# Πηγή εξαγωγής (config: `roles`). Το prompt ΠΡΕΠΕΙ να ξέρει ποια πλευρά διαβάζει,
# αλλιώς το confidence είναι ψευδές: «stated» σημαίνει «το είπε ο χρήστης», και μια
# τεχνική αναφορά του βοηθού ΔΕΝ είναι δήλωση του χρήστη.
ROLE_LABEL = {
    "user": ("ΜΗΝΥΜΑΤΑ ΧΡΗΣΤΗ", "ο χρήστης", "του χρήστη",
             '"stated" αν το είπε ρητά ο χρήστης, "derived" αν το συνήγαγες'),
    "assistant": ("ΜΗΝΥΜΑΤΑ ΒΟΗΘΟΥ", "ο βοηθός", "του βοηθού",
                  '"stated" αν ο βοηθός το ανέφερε ρητά ως τεχνικό γεγονός ή απόφαση '
                  '(τεχνική αναφορά — ΟΧΙ δήλωση του χρήστη), "derived" αν το συνήγαγες'),
}


def role_labels(roles: list[str] | None) -> tuple[str, str, str, str]:
    return ROLE_LABEL.get((roles or ["user"])[0], ROLE_LABEL["user"])


def system_prompt(scopes: list[str], roles: list[str] | None = None) -> str:
    msgs_label, who_nom, who_gen, stated_rule = role_labels(roles)
    return (SYSTEM_PROMPT.replace("{scopes}", ", ".join(scopes))
            .replace("{msgs_label}", msgs_label)
            .replace("{who_nom}", who_nom)
            .replace("{who_gen}", who_gen)
            .replace("{stated_rule}", stated_rule))
SYSTEM_PROMPT = """Είσαι εξαγωγέας ΔΙΑΡΚΩΝ γεγονότων από συνομιλίες χρήστη–βοηθού.
Διαβάζεις {msgs_label} — μία πλευρά της συνομιλίας, δηλωμένη ρητά.
Επιστρέφεις ΑΥΣΤΗΡΑ ΕΝΑ JSON, χωρίς σχόλια, της μορφής:
{"facts":[{"scope":"...","subject":"...","predicate":"...","value":"...","confidence":"stated|derived|assumed","quote":"..."}]}

Τι είναι επιτρεπτό (διαρκές):
- αποφάσεις και προτιμήσεις του χρήστη, κανόνες που θέλει να τηρούνται
- ταυτότητες/στοιχεία που δεν αλλάζουν (ονόματα, λογαριασμοί, διαδρομές, διευθύνσεις)
- τιμές/κόστη/στόχοι/όρια/παράμετροι ρυθμίσεων, αρχιτεκτονικές αποφάσεις
- διορθώσεις: αν {who_nom} διορθώνει κάτι που είχε ειπωθεί, ΚΡΑΤΑ ΜΟΝΟ τη νέα τιμή

Τι ΑΠΑΓΟΡΕΥΕΤΑΙ:
- παροδική κατάσταση («τρέχει», «περιμένω», «19/9 13:40», εκκρεμότητες μιας μέρας)
- **εντολές/αιτήματα για εργασία** («ξεκίνα», «διέγραψε», «ενσωμάτωσε», «υλοποίησε»): είναι
  task state, όχι γεγονός — ακόμη κι αν εκτελέστηκαν. Εξαίρεση μόνο αν εκφράζουν ΜΟΝΙΜΟ
  κανόνα ή προτίμηση («πάντα πίνακας», «ποτέ auto-post»)
- μυστικά: κλειδιά API, κωδικοί, tokens — ΠΟΤΕ
- γενικές γνώσεις, περιγραφές εργαλείων, ό,τι δεν δήλωσε {who_nom} για τον εαυτό του/το έργο του
- **παροδική κατάσταση/πρόοδος**: «status = … σε εξέλιξη / επόμενο στις 15:28 /
  N tests πέρασαν». Διαρκές είναι το ΠΟΥ είναι τα πράγματα, το ΟΡΙΟ, ο ΚΑΝΟΝΑΣ, η
  ΑΠΟΦΑΣΗ — όχι το πόσο πάει σήμερα
- περισσότερα από 12 γεγονότα: κράτα τα πιο σημαντικά. Αν δεν υπάρχει τίποτα διαρκές: {"facts":[]}

Δοκιμή μονιμότητας πριν γράψεις κάθε γεγονός: **«σε 3 μήνες, θα έχει νόημα αυτό χωρίς το
πλαίσιο της σημερινής συζήτησης;»** Αν όχι, μην το βάλεις.

Κανόνες πεδίων:
- scope: ένα από: {scopes}
  ΤΑΞΙΝΟΜΙΑ (τα λάθη εδώ καίγονται ποσόστωση άλλου scope — μην τα μπερδεύεις):
  · ανταλλακτήριο / paper / CEX API / entries → cex · on-chain / AMM / Jupiter / Solana RPC → dex
  · πορτοφόλια, seeds, cold storage, κλειδιά → wallet · ροές / cron / loop / υποδομή → cron
  · λοιπά trading (θέση, έξοδος, στρατηγική) → crypto
  ΠΟΤΕ «user», «other» ή scope εκτός λίστας.
- subject: πεζά με τελείες, π.χ. product.70x70, account.github, pricing.rule.
  **Ειδικό ως προς το πλαίσιο**: αν η ίδια έννοια ισχύει ΑΛΛΙΩΣ σε άλλο πλαίσιο, βάλε το
  πλαίσιο ως τελευταίο τμήμα — trade.sl.cex ≠ trade.sl.dex, exit.tp.dex ≠ exit.tp.cex.
  Δύο διαφορετικές αλήθειες με κοινό κλειδί: η δεύτερη κλείνει την πρώτη και χάνεται αλήθεια.
- predicate: σύντομο πεζό, π.χ. cost, target, preference, status
- value: σύντομο και αυτοτελές (έως 200 χαρακτήρες)
- confidence: {stated_rule}
- quote: ΑΥΤΟΛΕΞΕΙ απόσπασμα (έως 150 χαρακτήρες) από τα μηνύματα {who_gen} που
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
    # `roles` (default ["user"]): ποια πλευρά της συνομιλίας είναι πηγή. Τα τεχνικά
    # ευρήματα (paths, cron ids, thresholds) γράφονται συχνά από τον βοηθό, άρα η
    # επιλογή ΜΕΤΡΙΕΤΑΙ, δεν υποτίθεται. Χωρίς το κλειδί: ίδια συμπεριφορά όπως πριν.
    roles = [str(r) for r in (cfg.get("roles") or ["user"])]
    ph = ",".join("?" * len(roles))
    c = sqlite3.connect(f"file:{cfg['state_db']}?mode=ro", uri=True)
    q = ("SELECT id, session_id, role, content, timestamp FROM messages "
         f"WHERE id > ? AND role IN ({ph}) ORDER BY id")
    rows = c.execute(q, (since_id, *roles)).fetchall()
    c.close()
    out = []
    for mid, sid, role, content, ts in rows:
        if not content:
            continue
        text = str(content)
        if len(text) < min_chars or any(m in text for m in SKIP_MARKERS):
            continue
        out.append({"id": mid, "session_id": sid, "role": role, "text": text, "ts": ts})
    if limit:
        out = out[:limit]
    return out


def split_oversized(messages: list[dict], max_chars: int) -> list[dict]:
    """Κόβει μήνυμα μεγαλύτερο από τον προϋπολογισμό της παρτίδας σε τεμάχια.

    Γιατί υπάρχει: το batching κρατά ΠΑΝΤΑ ολόκληρο το πρώτο μήνυμα μιας παρτίδας,
    οπότε ένα μήνυμα 138.000 χαρ. γίνεται ένα prompt ~61.000 tokens (≈2,3 χαρ/τοκεν).
    Στο όριο του πλαισίου το μοντέλο γυρίζει κενό content → `json.loads("")` →
    εξαίρεση → το checkpoint ΔΕΝ προχωράει → η ίδια παρτίδα ξανασκάει σε ΚΑΘΕ run
    και η εξαγωγή παγώνει για πάντα (μετρημένο: crypto-trading, 12 ημέρες).
    Τα τεμάχια κρατούν το ίδιο id, ώστε το checkpoint να δείχνει το σωστό μήνυμα.
    """
    out = []
    for m in messages:
        t = str(m.get("text") or "")
        if len(t) <= max_chars:
            out.append(m)
            continue
        n = (len(t) + max_chars - 1) // max_chars
        for i in range(n):
            piece = dict(m)
            piece["text"] = t[i * max_chars:(i + 1) * max_chars]
            piece["chunk"] = f"{i + 1}/{n}"
            out.append(piece)
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


class LLMError(RuntimeError):
    """Αποτυχία της κλήσης/αποκωδικοποίησης της απάντησης του μοντέλου.

    Ξεχωριστός τύπος ώστε ο caller να μπορεί να κάνει retry/παράλειψη χωρίς να
    μπερδεύει «το μοντέλο δεν γύρισε JSON» με «χάλασε το ledger».
    """


def parse_llm_response(raw: str) -> dict:
    """Εξωτερικό JSON → περιεχόμενο μοντέλου → JSON γεγονότων, με καθαρές αιτίες.

    Το κρίσιμο σημείο: **κενό content** (ή content από σκέτα κενά) δεν είναι
    «0 γεγονότα» — είναι αποτυχία, και πρέπει να ονομαστεί. Παρατηρήθηκε σε prompt
    ~61k tokens: το API απαντά 200 / finish_reason=stop με 542 χαρακτήρες κενών.
    """
    try:
        content = json.loads(raw)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"η απάντηση του API δεν είναι JSON γνωστής μορφής "
                       f"({type(e).__name__}): {raw[:200]!r}") from e
    if not (content or "").strip():
        raise LLMError("κενό content από το μοντέλο (πιθανότατα prompt στο όριο "
                       "του πλαισίου) — η παρτίδα θα ξαναδοκιμαστεί/παραλειφθεί")
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        return json.loads(content)
    except ValueError as e:
        raise LLMError(f"το content δεν είναι έγκυρο JSON: {content[:200]!r}") from e


def call_llm(cfg: dict, env: dict, scopes: list[str], text: str,
             roles: list[str] | None = None) -> dict:
    api = cfg["api"]
    key = env.get(api["key_env"], "") or os.environ.get(api["key_env"], "")
    if not key:
        raise SystemExit(f"λείπει το κλειδί {api['key_env']}")
    body = json.dumps({
        "model": api["model"],
        "messages": [
            {"role": "system", "content": system_prompt(scopes, roles)},
            {"role": "user", "content": f"{role_labels(roles)[0]}:\n" + text},
        ],
        "temperature": 0,
        "max_tokens": api.get("max_tokens", 3000),
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(api["base_url"], data=body,
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=api.get("timeout", 180)).read().decode("utf-8", "replace")
    return parse_llm_response(raw)


def validate(facts: list[dict], text: str, scopes: list[str],
             scope_fallback: str = "other",
             transient_predicates: set[str] | None = None,
             transient_signals: tuple[str, ...] | None = None,
             transient_strict: bool = False) -> tuple[list[dict], list[str]]:
    flat = norm(text)
    transient_preds = (TRANSIENT_PREDICATES if transient_predicates is None
                       else set(transient_predicates))
    transient_re = (TRANSIENT_RE if transient_signals is None
                    else re.compile("|".join(transient_signals), re.I))
    ok, rejected = [], []
    # Το φίλτρο καταστάσεων-εργασίας είναι επίτηδες ΕΔΩ και όχι μόνο στο prompt:
    # το prompt πείθει, ο κώδικας εγγυάται. Ό,τι μυρίζει εντολή/εκτέλεση απορρίπτεται
    # ανεξάρτητα από την κρίση του μοντέλου.
    task_predicates = {"action", "request", "implementation", "requirement",
                       "required", "task", "todo", "goal"}
    task_value = re.compile(
        r"^(Να |Διαγραφή |Ενσωμάτωση |Υλοποίηση |Ξεκίνα|Μετατροπή |Αλλαγή του |Διόρθωση του )")
    # Δεύτερο δίχτυ: το αίτημα μπορεί να είναι ΜΕΣΑ στο value («Δεν υπάρχει σύνδεση,
    # ζητήθηκε να φτιαχτεί») ή σε predicate που δεν είναι από τα task_predicates
    # («access = …» απάντηση σε ερώτηση). Μόνο ρητοί δείκτες-αίτημα, ώστε να μην
    # πέφτουν διαδικασίες («μετά την αλλαγή: ξανατρέξε …»).
    request_marker = re.compile(
        r"ζητήθηκε|να φτιαχτεί|να φτιαχτούν|να ενσωματωθεί|να υλοποιηθεί|να διαγραφεί|"
        r"\bφτιάξε\b|\bδιέγραψε\b|\bενσωμάτωσε\b|\bυλοποίησε\b|\bξεκίνα\b|\bπρόσθεσε\b",
        re.I)
    for f in facts:
        why = None
        for k in ("scope", "subject", "predicate", "value", "quote"):
            if not str(f.get(k, "")).strip():
                why = f"λείπει '{k}'"
        if why:
            rejected.append(f"{f} → {why}"); continue
        if (norm(f["predicate"]) in task_predicates
                or task_value.match(str(f["value"]).strip())
                or request_marker.search(str(f["value"]))):
            rejected.append(f"{f['subject']} · {f['predicate']} → εντολή/εκτέλεση, όχι διαρκές γεγονός")
            continue
        # Μονιμότητα: «κατάσταση» γίνεται παροδική μόνο όταν το value κουβαλά
        # σημείο-στον-χρόνο ή μετρητή προόδου — αλλιώς ένα διαρκές status επιβιώνει.
        # `transient_strict` (assistant-side): το predicate αρκεί, χωρίς σήμα — ο
        # βοηθός γράφει «status =» για το πού βρίσκεται ΤΩΡΑ η δουλειά, που είναι
        # ο ορισμός του παροδικού.
        if (norm(f["predicate"]) in transient_preds
                and (transient_strict or transient_re.search(str(f["value"])))):
            reason = ("predicate-only (strict)" if transient_strict
                      else "σημείο-στον-χρόνο/πρόοδος")
            rejected.append(f"{f['subject']} · {f['predicate']} = "
                            f"{str(f['value'])[:60]} → παροδική κατάσταση "
                            f"({reason}), όχι διαρκές γεγονός")
            continue
        if SECRET_RE.search(str(f["value"]) + " " + str(f["quote"])):
            rejected.append(f"{f['subject']} → φαίνεται μυστικό"); continue
        if norm(f["quote"]) not in flat:
            rejected.append(f"{f['subject']} → το quote ΔΕΝ υπάρχει στην πηγή"); continue
        if len(str(f["value"])) > 300 or len(str(f["subject"])) > 60 or len(str(f["predicate"])) > 60:
            why = "πολύ μακρύ πεδίο"
            rejected.append(f"{f['subject']} → {why}"); continue
        if f["scope"] not in scopes:
            # `scope_fallback`: πού πάει γεγονός με άγνωστο/απών scope. Default "other"
            # (συμπεριφορά πριν το knob)· ένα profile με δικά του scopes το θέτει στα
            # δικά του, ώστε να μην μπορεί να πέσει σε scope που διαβάζει άλλο target.
            f["scope"] = scope_fallback
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
    roles = cfg.get("roles") or ["user"]
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
    # Ένα μήνυμα μεγαλύτερο από τον προϋπολογισμό ΔΕΝ στέλνεται ολόκληρο: το prompt
    # φτάνει στο όριο του πλαισίου και το μοντέλο γυρίζει κενό (βλ. split_oversized).
    max_chars = int(cfg.get("max_chars", 18000))
    units = split_oversized(msgs, max_chars)
    if len(units) != len(msgs):
        print(f"τεμαχισμός μεγάλων μηνυμάτων: {len(msgs)} → {len(units)} μονάδες "
              f"(όριο {max_chars} χαρ./μονάδα)")

    led = Ledger(cfg["db"], cfg.get("aliases"))
    total = {"added": 0, "closed": 0, "unchanged": 0}
    all_applied, all_rejected, all_skipped = [], [], []
    processed_to = since

    def save_state(to_id: int) -> None:
        """Checkpoint ΜΕΤΑ από κάθε παρτίδα: διακοπή ≠ επανάληψη όλης της δουλειάς."""
        if not (a.apply and cp):
            return
        d = os.path.dirname(cp)
        if d:
            os.makedirs(d, exist_ok=True)
        json.dump({"last_message_id": to_id,
                   "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                   "applied": len(all_applied), "rejected": len(all_rejected),
                   "skipped": all_skipped},
                  open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    for i, b in enumerate(batch(units, max_chars), 1):
        text = "\n\n---\n\n".join(
            f"[{m['session_id']} #{m['id']}"
            f"{' τμ.' + m['chunk'] if m.get('chunk') else ''}] {m['text']}" for m in b)
        print(f"\nπαρτίδα {i}: {len(b)} μονάδες, {len(text)} χαρ.")
        r = None
        for attempt in (1, 2):
            try:
                r = call_llm(cfg, env, scopes, text, roles)
                break
            except Exception as e:
                print(f"  ΣΦΑΛΜΑ LLM (απόπειρα {attempt}/2): {e}")
                if attempt == 1:
                    time.sleep(3)
        if r is None:
            # Μία χαλασμένη παρτίδα ΔΕΝ επιτρέπεται να παγώσει τον κύκλο για πάντα:
            # η θέση προχωρά, τα μηνύματα καταγράφονται ως παραλειφθέντα στο state
            # και η έξοδος είναι μη-μηδενική (ο cron το δείχνει ως αποτυχία).
            lo, hi = b[0]["id"], b[-1]["id"]
            all_skipped.append([lo, hi])
            print(f"  ΠΑΡΑΛΕΙΨΗ παρτίδας {i} (ids {lo}–{hi}): {len(b)} μονάδες ΔΕΝ "
                  f"εξήχθησαν — η θέση προχωρά για να μη κολλήσει η εξαγωγή")
            processed_to = b[-1]["id"]
            save_state(processed_to)
            continue
        facts = r.get("facts") or []
        good, bad = validate(facts, text, scopes, cfg.get("scope_fallback", "other"),
                             cfg.get("transient_predicates"), cfg.get("transient_signals"),
                             bool(cfg.get("transient_strict", False)))
        print(f"  προτεινόμενα: {len(facts)} · αποδεκτά: {len(good)} · απορρίφθηκαν: {len(bad)}")
        for x in bad:
            print("    ✗", x[:160])
        for f in good:
            src = (f"{b[0]['role']} session {b[0]['session_id']} msg#{b[0]['id']} · "
                   f"«{str(f['quote'])[:110]}»")
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
        save_state(processed_to)

    if a.apply:
        print(f"\nγράφτηκαν: {total.get('added', 0)} νέα · {total.get('unchanged', 0)} χωρίς αλλαγή · "
              f"{total.get('closed', 0)} αντικαταστάσεις")
        if cp:
            save_state(processed_to)
            print(f"checkpoint: id {processed_to} → {cp}")
        if all_skipped:
            print(f"ΠΡΟΣΟΧΗ: {len(all_skipped)} παρτίδες παραλείφθηκαν "
                  f"(ids: {all_skipped}) — δεν εξήχθησαν, καταγράφηκαν στο state")
        for cmd in cfg.get("post_sync", []):
            print(f"\nεπόμενο βήμα: {' '.join(cmd)}")
            subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)), check=False)
    else:
        print("\n[dry-run] ΔΕΝ γράφτηκε τίποτα. Πρόσθεσε --apply.")
    # Μη-μηδενική έξοδος όταν κάτι παραλείφθηκε: ο cron δείχνει αποτυχία αντί για
    # σιωπηλό «ok» — το «applied 0» επί 12 ημέρες ήταν αόρατο ακριβώς γι' αυτόν τον λόγο.
    return 1 if all_skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
