# factledger

A Hermes plugin that gives the agent a **typed-facts ledger with structural
truth** — a durable store for what the agent actually knows, where a new value
supersedes the old one in a first-class audit trail instead of overwriting it.

The ledger is a human-readable, diffable, greppable TSV file. For every
`(scope, subject, predicate)` exactly **one** row is active (empty `valid_to`);
writing a new value closes the previous row (`valid_to` + `superseded_by`)
rather than deleting it, so two contradictory versions can never coexist as
equals, and history is the audit trail. Supersedes, never expires — a fact's
`valid_from` says how fresh it is.

## Why you need this

Hermes memory is injected into every turn, so it has a hard character budget.
That budget is a feature — a compact prompt is a cheap prompt — but it means a
fact can fall out of the view while still being true. Factledger keeps the
**full** ledger queryable on demand: record a fact once, then `show`/`search`
it whenever a price, status, limit, decision or capability matters, without
paying to keep it in every prompt. It is the difference between "memory that
fits the prompt" and "memory that outlives the prompt."

## Tools

| Tool | Purpose |
|------|---------|
| `factledger_add` | Record or replace a fact (supersedes the previous one). |
| `factledger_show` | The active truth for a scope/subject, today or `as_of`. |
| `factledger_search` | Find by term across subject/value/predicate/source. |
| `factledger_history` | What changed and when for a subject. |
| `factledger_compile` | Compact view within a prompt-character budget. |
| `factledger_stale` | Active facts older than N days (need re-check). |
| `factledger_export` | Structured JSON export for reports/watchdogs. |
| `factledger_check` | Ledger integrity check. |
| `factledger_stats` | Totals: active/closed rows, scopes. |

## Install

```bash
hermes plugins install factledger
```

Every tool returns a JSON string (always, including on error — never raises).

## Data location

The ledger lives at `<hermes home>/plugin-data/factledger/facts.tsv` by default
— inside the active profile's Hermes home, so each profile keeps its own
ledger. To point it anywhere else (e.g. inside an Obsidian vault), set the
`db_path` setting: Plugins hub → factledger → settings, or
`plugins.entries.factledger.settings.db_path` in `config.yaml`. The atomic
replace includes a bounded retry for transient filesystem locks.

## Conventions

- **scope** = the domain namespace (`silktales`, `legal`, `market`, …).
- **subject** = the tracked entity (`competitor.x`, `case.y`, `mcp.z`).
- **source** is mandatory and should be verbatim — a fact without a source is
  not written (the rule is explicit).
- **confidence**: `stated` (first-party), `derived` (computed), `assumed`.
- A value is **superseded, never deleted** — mistakes stay visible and
  reversible.

## License

MIT
