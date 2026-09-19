# factledger — typed facts with structural truth

Memory for agents/humans where **truth is enforced by structure, not by model judgment**.

For every `(scope, subject, predicate)` there is **exactly one active row**. A new value
**closes** the previous one (`valid_to` + `superseded_by`) — it does not delete it. This way
contradictory versions can never coexist as equals.

> **Docs:** practical usage guide → [`docs/usage.md`](docs/usage.md)

## Why you need this

Agent memory (e.g. Hermes) is **injected into every single turn**, so it has a hard size
budget — the more it grows, the more it crowds out the actual conversation. factledger
solves that by separating **what the agent sees** from **what is true**:

- **A compact compiled view** (`compile --budget`) fits the prompt and stays stable as the
  ledger grows — the rest stays queryable on demand via `show`/`search`, never lost.
- **One source of truth** for facts that must not drift: prices, access, rules, decisions.
  When a value changes, the old one is closed, not deleted — so the agent never answers
  from a stale or contradictory version.
- **Every fact is sourced and auditable.** Nothing is silently overwritten; the full
  history is one `history` call away, which makes mistakes visible and reversible.

In short: you get a memory that **stays small enough to fit the prompt** while **never
forgetting or contradicting itself** — without a vector store, without an LLM in the
read/write path, and with zero dependencies.

## Why it exists

mem0 (self-hosted, 65k★) was tested on the scenario "what is the **current** value?"
after a correction. Result: the **superseded** value came back **first** (score 0.79)
and the correct one third (0.77) — the UPDATE in automatic extraction only fired as an ADD.
The failure was not the embedding: it was that the "is it replaced?" decision was left to an LLM.

Here replacement is a **function of the key**, not a decision: the error cannot happen structurally.

## Usage

```bash
python factledger.py add --scope silktales --subject product.70x70 --predicate cost \
    --value 22 --source "pricelist 2026-09" --confidence stated

python factledger.py show --subject product.70x70          # current truth
python factledger.py show --subject product.70x70 --as-of 2026-03-01   # what held then
python factledger.py history --subject product.70x70       # what changed when and with which source
python factledger.py search kraken
python factledger.py compile --budget 600 --out MEMORY.md  # compact view for a prompt
python factledger.py check                                  # integrity check
python factledger.py stats
```

`--source` is **mandatory**: a fact without a source is not written.

## The loop: extract → ledger → memory files

Two tools close the cycle (both stdlib):

```bash
# 1. Extract durable facts from transcripts (SQLite) → ledger
python extract.py --config extract.json          # dry-run
python extract.py --config extract.json --apply  # write + checkpoint

# 2. Generate memory files from the ledger
python sync_memory.py --config sync.json         # dry-run
python sync_memory.py --config sync.json --apply # write with backups
```

**Why they are safe** (mechanisms, not hope):

| Risk | What stops it |
|---|---|
| LLM hallucination | Every fact requires a **verbatim quote** from the source; anything not found is rejected |
| Secret leakage | Regex over keys/tokens (value + quote) → rejection |
| "Facts" that are commands | Structural filter (predicates + value prefix) **and** few-shot; code overrides the prompt |
| Duplicates | Checkpoint at the last id + same value = "no change" |
| Lost manual change | Backup before every write + divergence detection from the snapshot → inserted as a fact |
| Prompt overflow | `budget` + per-scope quotas + explicit `order` of importance: the prompt stays stable as the ledger grows |

The two differences from vector-based memory systems: (a) replacement is a **key function**,
not a model decision; (b) nothing is ever deleted, so every mistake stays visible and reversible.

**Example agent integration** (the cycle: memory becomes a file the agent reads every turn,
but it is produced by the ledger):

```json
{
  "db": "facts.tsv",
  "targets": [
    { "path": "MEMORY.md", "scopes": ["project", "infra"],
      "order": ["rule.", "policy."], "quotas": { "project": 500, "infra": 300 },
      "budget": 900, "max_value": 90 }
  ]
}
```

## Schema

| column | meaning |
|---|---|
| `id` | `f00001`… stable |
| `scope` | `user` / `profile` / `project` / `session` — the "per whom" of retrieval |
| `subject` | the object (`product.70x70`, `kraken`) |
| `predicate` | the property (`cost`, `access`) — passes through the alias map |
| `value` | the value |
| `valid_from` / `valid_to` | validity interval; empty `valid_to` = open |
| `superseded_by` | which row replaced it |
| `source` | where it came from (mandatory) |
| `confidence` | `stated` > `derived` > `assumed` (order in compile) |

Data: **a single TSV**, human-readable, grep-able, with clean `git diff`.
Optional `aliases.tsv` (`canonical<TAB>alias1,alias2`) for synonymous predicates.

## Design choices

- **Time intervals, not "last record"** — `show --as-of` answers what held then
  (necessary for audits/retrospective queries).
- **Scheduled change** does not close the current truth before its time.
- **`compile` with a character budget** — fits in a prompt without bloating it; the rest
  stays reachable via `show`/`search`.
- **Zero dependencies** (stdlib), zero network, zero LLM on read/write; an LLM is needed
  only if you want automatic fact extraction from transcripts (outside the core).
- **Mistakes stay visible**: nothing is deleted, only closed — free audit trail.

## What it is NOT

- It does not do semantic/vector search over free text — that is what a separate system
  (e.g. GraphRAG) is for. **Ledger = truth, vector store = similarity.**
- It does not extract facts from conversations on its own (only the schema/CLI is provided to do so).
- It does not solve entity dedup outside keys: synonyms are declared in `aliases.tsv`.

## Related work

Several projects target the same problem — agent memory that stays small, sourced, and
non-contradictory — but with different approaches:

**Closest in philosophy (structured ledger, not vector):**

| Project | What it does |
|---|---|
| [`selfradiance/memledger`](https://github.com/selfradiance/memledger) | Append-only CLI ledger for structured agent memory claims with provenance, confidence, contestability, and immutable history — the closest match |
| [`hooyao/claude-code-memory`](https://github.com/hooyao/claude-code-memory) | Event-sourced memory engine for Claude Code — auditable, structured, with relationship graphs |
| [`Yeseh/cortex`](https://github.com/Yeseh/cortex) | Structured agentic memory system |
| [`Jdawgboo/agent-memory-inspector`](https://github.com/Jdawgboo/agent-memory-inspector) | Inspects structured agent memories for staleness, duplication, and missing provenance |

**The big/well-known ones (vector + knowledge graph, different approach):**

| Project | What it does |
|---|---|
| [`mem0ai/mem0`](https://github.com/mem0ai/mem0) | Vector-based memory layer; keeps a superseded value as an equal, which is exactly the failure this project was built to avoid |
| [`topoteretes/cognee`](https://github.com/topoteretes/cognee) | AI memory platform with a self-hosted knowledge graph |
| [`getzep/graphiti`](https://github.com/getzep/graphiti) | Real-time temporal knowledge graphs for agents |
| [`letta-ai/letta`](https://github.com/letta-ai/letta) | Stateful agents with advanced memory (MemGPT) |
| [`getzep/zep`](https://github.com/getzep/zep) | Long-term memory layer |

None of the big ones do exactly what factledger does: **structured truth with replacement
as a function of the key, no vector store, and no LLM in the read/write path.** `memledger`
is the closest in philosophy (append-only, provenance, confidence) but is unproven.
factledger stands apart for being zero-dependency, offline, and making replacement
structurally guaranteed rather than a model decision.

## Tests

```bash
python -m unittest discover -s tests -v
```

The critical one: `test_supersede_closes_previous` — the new value closes the old one,
one active truth, full history.

## License

MIT — see `LICENSE`.
