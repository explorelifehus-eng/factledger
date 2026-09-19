# factledger — practical usage guide

This guide covers how to actually use factledger day-to-day, the discipline it
requires, and how it fits alongside other memory tools. It is written from real
use, not from the spec.

## The core idea in one line

Separate **what the agent sees** (a compact compiled view) from **what is true**
(the full, sourced, append-only ledger).

## Daily workflow

### 1. Record a fact

```bash
python factledger.py add --scope silktales --subject product.70x70 --predicate cost \
    --value 22 --source "pricelist 2026-09" --confidence stated
```

`--source` is mandatory. This is the single most important discipline: **a fact
without a source is not a fact, it is a note.** If you cannot name where a value
came from, do not write it.

### 2. Read the current truth

```bash
python factledger.py show --subject product.70x70
```

This is what the agent should answer from. Because replacement is a function of
the key, `show` can never return a superseded value as current.

### 3. Answer "what held then?"

```bash
python factledger.py show --subject product.70x70 --as-of 2026-03-01
```

Use this for audits and retrospective questions. It is the reason the schema uses
time intervals instead of "last record".

### 4. See the audit trail

```bash
python factledger.py history --subject product.70x70
```

Nothing is ever deleted — only closed. Every mistake stays visible and reversible.

### 5. Fit memory into a prompt

```bash
python factledger.py compile --budget 600 --out MEMORY.md
```

This is the payoff. The compiled view stays stable as the ledger grows; the rest
stays reachable via `show`/`search`. The prompt does not bloat.

## The automation loop

Two stdlib tools close the cycle:

```bash
# extract durable facts from transcripts (SQLite) → ledger
python extract.py --config extract.json --apply

# generate memory files from the ledger
python sync_memory.py --config sync.json --apply
```

Run these on a schedule (e.g. a cron job every few hours) so the ledger stays fed
without depending on manual discipline. The safety mechanisms — verbatim-quote
requirement, secret-leak regex, structural filters, checkpoints, backups — are what
make unattended extraction safe.

## What it is NOT for

- **Free-text semantic search.** factledger is exact-key retrieval. For similarity
  over unstructured text, use a vector store / knowledge graph alongside it.
  **Ledger = truth, vector store = similarity.**
- **Entity dedup.** Synonyms are declared manually in `aliases.tsv`; factledger does
  not resolve them for you.
- **Automatic fact extraction out of the box.** The schema and CLI are provided; the
  extraction quality depends on how well `extract.py` is configured for your sources.

## The discipline it requires

The tool is structurally sound, but its value depends on two habits:

1. **Always provide a source.** Without this, the ledger degrades into a notes file.
2. **Feed it on a schedule.** The cron loop keeps the ledger alive without relying on
   memory. If you stop feeding it, it stops being useful.

## Related work

Several projects target the same problem — agent memory that stays small, sourced,
and non-contradictory — but with different approaches.

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

None of the big ones do exactly what factledger does: **structured truth with
replacement as a function of the key, no vector store, and no LLM in the read/write
path.** `memledger` is the closest in philosophy (append-only, provenance,
confidence) but is unproven. factledger stands apart for being zero-dependency,
offline, and making replacement structurally guaranteed rather than a model decision.
