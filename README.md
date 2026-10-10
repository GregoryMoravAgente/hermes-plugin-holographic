# Holographic Memory Provider

Local memory provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent): a SQLite fact store with
FTS5 search, trust scoring, entity resolution and HRR (holographic reduced representation) retrieval. Everything
stays on your machine: no API key, no network, no model calls.

**Maintained by [itpartypattaya](https://github.com/itpartypattaya)** since October 2026. This is the provider that
shipped inside Hermes core as `plugins/memory/holographic/`; Nous Research
[handed it off](https://github.com/NousResearch/hermes-plugin-holographic/blob/main/HANDOFF.md) when the bundled
copy was scheduled to leave core (announced for October 15, 2026). Same provider name, same config, same database:
users who have `memory.provider: holographic` keep their facts. See [CHANGELOG.md](CHANGELOG.md) for what changed.

## Install

While your Hermes still bundles `holographic`, the bundled copy is used and this plugin stays dormant. After core
drops it, `hermes update` installs the catalog plugin named `holographic` for every profile that has it configured.
To install by hand:

```bash
hermes plugins install holographic          # from the plugin catalog
hermes config set memory.provider holographic
```

or `hermes memory setup` and pick `holographic`.

## Config

In `config.yaml` under `plugins.hermes-memory-store`:

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `$HERMES_HOME/memory_store.db` | SQLite database path |
| `auto_extract` | `false` | Store user sentences that state a preference or decision (English, Russian) at session end and before context compression, at trust 0.4 |
| `mirror_memory_writes` | `false` | Also store every built-in memory `add` (MEMORY.md / USER.md) as a fact |
| `default_trust` | `0.5` | Trust score of a new fact |
| `min_trust_threshold` | `0.3` | Facts below this trust are not recalled |
| `temporal_decay_half_life` | `0` | Days; `0` turns recency decay off |
| `hrr_dim` | `1024` | HRR vector dimensions |
| `hrr_weight` | `0.3` | Weight of the HRR term in search ranking |
| `prefetch_min_coverage` | `0.3` | Share of a message's informative words (rarer words weigh more) a fact must match to be recalled before the turn |
| `prefetch_repeat_turns` | `6` | A fact recalled before a turn is not injected again for this many turns of the session |
| `scope` | `profile` | `user`: a separate database per user (`holographic-users/<platform>-<user id>.db` next to `db_path`); in a shared chat the store follows the author of each turn, and with no user identity nothing is recalled or written |

## Tools

| Tool | Description |
|------|-------------|
| `fact_store` | add, search, probe, related, reason, contradict, update, remove, list, stats, export, review, entities, alias |
| `fact_feedback` | Rate a fact helpful or unhelpful (moves its trust score) |

- **search**: keyword (FTS5) candidates, any word form (English and Russian stems: `Маши` finds `Маша`, `deploys`
  finds `deploy`), reranked by word overlap and HRR similarity, weighted by trust.
- **Before each turn** the provider recalls up to five facts: search candidates that pass a relevance gate (the fact
  must cover enough of the message's informative words; "ok, thanks" recalls nothing) plus the top facts of entities
  the message names. A fact already injected in this session is not repeated for `prefetch_repeat_turns` turns.
  Facts are framed as data (`#id [trust] text`), one line each.
- **add** takes `entities` (who the fact is about; needed for one-word names such as a first name, so later facts
  mentioning it in any form link to it) and `supersedes` (the id of the fact it replaces). Its answer lists
  `similar` facts and `possible_conflicts` (same subject, a different claim); the agent decides. Text that is not a
  fact (JSON, tool or chat-role markup, instruction-like phrasing, invisible characters, long blobs, >1000
  characters) is refused with the reason.
- **stats** (what is stored and how much of it was ever recalled), **export** (Markdown view), **review**
  (clean-up candidates: low trust, never recalled for 60 days, near-duplicates).
- **entities** lists names with their aliases and fact counts; **alias** gives an entity a second name (or merges
  two entities). Spelling variants are one entity (`Hermes Agent` = `hermes-agent`), and `X aka Y` makes `Y` an alias.
- Thai, Chinese, Japanese and Korean (text without spaces between words, or with attached particles) are matched by
  character n-grams.
- **probe**: every fact linked to a named entity, from the entity index (name or alias, any letter case).
  **reason**: facts linked to *all* of several entities; when a name is not a known entity, keyword search that still
  requires every name. Entities are linked when a fact is stored: capitalized names of two to five words in any cased
  script (`Alice Smith`, `Мария Иванова`, `Jean-Luc Picard`), short quoted terms (`"Phoenix"`, `«Сбер Бизнес»`)
  and both sides of `X aka Y`; flags, `key=value`, paths, URLs and IPs are not names. A one-word name is not linked;
  probing it falls back to keyword search.
- **related**: facts structurally tied to an entity in their HRR vector, above a noise floor.
- Every recall action honours `min_trust` (default `min_trust_threshold`).
- **contradict**: candidate conflicts, i.e. pairs about the same subject that make a similar but not identical
  claim ("lives in Paris" / "lives in London"). It suggests; it never edits.

Stems are a search key, not a dictionary: an irregular form may still miss (`ponies` vs `pony`). Character n-grams
find shared fragments, not meaning: a Chinese question with no word in common with the fact will not find it.

## Data and privacy

- Facts live in one SQLite file (`db_path`), in the Hermes home of the profile, readable by its owner only (POSIX
  `0600`). Nothing is sent anywhere. A `db_path` outside the Hermes home is listed for `hermes backup`.
- Subagents, cron jobs and flush contexts read the store but never write to it (facts, feedback, counters,
  extraction).
- With `auto_extract` on, user messages matching the preference/decision patterns are stored at session end.
- `retrieval_count` counts how often a fact was handed to the model; nothing else is tracked.
- Dependencies: `numpy` (HRR algebra) and `snowballstemmer` (word forms; pure Python). Without numpy the provider
  works with keyword search only and says so in its prompt block; without snowballstemmer words compare exactly.

## Database and downgrades

Schema changes are additive: 0.4.0 adds a `meta` table (schema version, HRR dimension), an `entity_aliases` table and
an `entities.name_key` column; nothing older is dropped or rewritten, and the new column is nullable, so earlier
versions (including the copy bundled in Hermes core) keep reading and writing the file. When this version opens a
file an older one wrote to, it reconciles: missing keys are filled in, entities with the same key are merged (links,
aliases and names kept), aliases of entities the older version deleted are dropped, and vectors whose size does not
match `hrr_dim` are re-encoded. Only a writable agent does this; subagents, cron and flush contexts never migrate.

What is tested: this release, then 0.1.1, then this release on one database in CI (adds, edits, removals, aliases, a
keyless duplicate entity written by 0.1.1), plus the reconciliation cases above. What a downgrade does not keep:
aliases made after 0.4.0 are visible to 0.1.1 only through the legacy `entities.aliases` column; and with
`scope: user`, an older version knows nothing of per-user files, so it reads and writes the shared `db_path` again.
The per-user files stay untouched (no leak), but new facts would land in the shared store.

## Development

Tests import Hermes core from a checkout, as an installed plugin does:

```bash
HERMES_AGENT_REPO=~/.hermes/hermes-agent PYTHONPATH=~/.hermes/hermes-agent python -m pytest -q
```

CI runs the suite on Linux, macOS and Windows against hermes-agent `main`, plus `hermes plugins validate`, the check
the plugin catalog runs at admission. Maintenance notes are in [HANDOFF.md](HANDOFF.md).

Original plugin by dusterbloom (hermes-agent PR #2351); its history is in hermes-agent
(`git log -- plugins/memory/holographic`). MIT licence.
