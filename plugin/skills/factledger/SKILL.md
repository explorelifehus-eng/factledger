---
name: factledger
description: Use when the agent must record, look up, or audit a durable fact about the world (prices, statuses, decisions, capabilities) across sessions.
---

# Factledger

A typed-facts ledger with **structural truth**: for every `(scope, subject, predicate)`
exactly one row is active (empty `valid_to`). A new value **closes** the previous row
(`valid_to` + `superseded_by`) instead of overwriting it — so two contradictory versions
can never coexist as equal. History is the audit trail.

The ledger lives in a TSV file: human-readable, diffable, greppable. It supersedes but
never expires — a fact's `valid_from` says how fresh it is, and quoting a number without
its date (e.g. "τιμή Σεπτεμβρίου") is a correctness bug.

## When to write
Record anything durable the agent learns that should survive this session and across
sessions: prices, limits, statuses, decisions, capabilities, environment facts, rules.
Use the `factledger_add` tool. Facts whose value changes belong here. Never write
one-off task state, artifact lists, or datasets into the ledger.

## When to read
Before quoting a stored value, lifetime, status, or decision, look it up first —
a fact that is not in the compiled view may still be in the ledger. Use:
- `factledger_show` — the truth that holds today (or `as_of`).
- `factledger_search` — find by term across subject/value/predicate/source.
- `factledger_history` — what changed and when for a subject.
- `factledger_stale` — active facts older than N days (need re-check).
- `factledger_export` — structured JSON for reports/watchdogs.
- `factledger_check` — ledger integrity.

## Conventions
- `scope` is the domain namespace (`silktales`, `legal`, `hermes`, `market`, …); a new
  scope should not collide with existing ones.
- `subject` is the tracked entity, e.g. `competitor.x`, `case.y`, `mcp.z`.
- `source` must be verbatim where the fact comes from — a fact without a source is not
  written (the rule is explicit).
- Confidence: `stated` (first-party), `derived` (computed), `assumed` (guess).
- A value is superseded, never deleted; mistakes stay visible and reversible.
- For a compact view within a prompt budget, use `factledger_compile`.
