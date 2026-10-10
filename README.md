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
| `auto_extract` | `false` | Store user sentences that look like preferences or decisions at session end |
| `mirror_memory_writes` | `false` | Also store every built-in memory `add` (MEMORY.md / USER.md) as a fact |
| `default_trust` | `0.5` | Trust score of a new fact |
| `min_trust_threshold` | `0.3` | Facts below this trust are not recalled |
| `temporal_decay_half_life` | `0` | Days; `0` turns recency decay off |
| `hrr_dim` | `1024` | HRR vector dimensions |
| `hrr_weight` | `0.3` | Weight of the HRR term in search ranking |

## Tools

| Tool | Description |
|------|-------------|
| `fact_store` | 9 actions: add, search, probe, related, reason, contradict, update, remove, list |
| `fact_feedback` | Rate a fact helpful or unhelpful (moves its trust score) |

- **search**: keyword (FTS5) candidates reranked by word overlap and HRR similarity, weighted by trust. Before each
  turn the provider runs it on the user's message and adds up to five facts to the context (`#id [trust] text`).
- **probe**: every fact linked to a named entity. **reason**: facts linked to all of several entities. Entities
  are linked when a fact is stored: capitalized names of two or more words in any cased script (`Alice Smith`,
  `Мария Иванова`), short quoted terms (`"Phoenix"`, `«Сбер Бизнес»`) and `X aka Y`. A one-word name is not
  linked; probing it falls back to keyword search.
- **related**: facts that share structure with an entity.
- **contradict**: candidate conflicts, i.e. pairs about the same subject that make a similar but not identical
  claim ("lives in Paris" / "lives in London"). It suggests; it never edits.

Keyword search matches whole words: there is no stemming, so `Masha` and `Mashas` are different words (the same
holds for inflected languages).

## Data and privacy

- Facts live in one SQLite file (`db_path`), in the Hermes home of the profile. Nothing is sent anywhere.
- With `auto_extract` on, user messages matching the preference/decision patterns are stored at session end.
- `retrieval_count` counts how often a fact was handed to the model; nothing else is tracked.
- Dependency: `numpy` (HRR algebra). Without it the provider still works with keyword search only.

## Development

Tests import Hermes core from a checkout, as an installed plugin does:

```bash
HERMES_AGENT_REPO=~/.hermes/hermes-agent PYTHONPATH=~/.hermes/hermes-agent python -m pytest -q
```

CI runs the suite on Linux, macOS and Windows against hermes-agent `main`, plus `hermes plugins validate`, the check
the plugin catalog runs at admission. Maintenance notes are in [HANDOFF.md](HANDOFF.md).

Original plugin by dusterbloom (hermes-agent PR #2351); its history is in hermes-agent
(`git log -- plugins/memory/holographic`). MIT licence.
