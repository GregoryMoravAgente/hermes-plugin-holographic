# Changelog

## 0.2.1 — 2026-10-11

Fixes from an independent review (Codex CLI) of 0.2.0 and of the provider contract. No schema change; a database
written by 0.2.1 still works with 0.1.1 and the bundled core copy (tested: 0.2.1 → 0.1.1 → 0.2.1 on one file).

### Fixed

- **reason** returned facts about only one of the entities when no fact linked them all. probe and reason now
  answer from the entity index (exact, any letter case, aliases included): reason intersects the entities' facts,
  and when a name is not a known entity it falls back to keyword search that still requires every name. probe of an
  unknown name no longer admits an unrelated fact through the HRR noise floor.
- **related** returned the top of the whole store for any entity. It now keeps only facts over a measured noise
  floor (0.2 at dim 1024: tied facts score ≥ 0.35, the best of 17k untied pairs 0.088); precision@5 0.92 → 1.00.
- **`min_trust`** was ignored by probe, related, reason and contradict, so facts rated unhelpful came back.
- **contradict** treated a claim that only adds detail ("likes apples" / "likes apples and bananas") as a conflict.
- **Writes were not atomic**: a failure after the INSERT left a fact without entities or vector. add, update,
  remove and feedback now run in one transaction each.
- `add` of an existing text answers `"status": "exists"` (was `"added"`); `update` to another fact's text answers
  `{"updated": false, "duplicate_of": id}` instead of an IntegrityError; `update` to empty text is refused.
- **Entity noise**: straight quotes pair only at word boundaries (`don't … it's` no longer yields "t … it"), `aka`
  takes the adjacent name only ("Alice Smith aka Bob Brown works here" no longer yields "Bob Brown works here"), a
  sentence-opening word is dropped ("So Apple"), and flags, `key=value`, paths, URLs and IPs are rejected. Names are
  matched with Unicode casefolding (SQLite `LIKE` folds ASCII only and treated `_`/`%` as wildcards). Entities left
  without facts are removed.
- **Search**: `pve-01` and `host:22` are searched as the phrases `pve 01` / `host 22` (the tokens the index holds;
  they used to collapse to `pve01`). A query with nothing searchable no longer sends the raw text to FTS5.
- `record_retrievals` counts integer ids only (`1.9` counted fact 1).
- A damaged or foreign-dimension HRR vector is skipped and logged instead of failing the query.
- Counter, trust and vector updates no longer re-index the FTS row (the update trigger now fires on content and
  tags only; older databases are migrated on open, and older versions keep working with it).
- Category banks were rebuilt from every vector of the category on each write (and logged an SNR warning per write
  past 256 facts) though nothing reads them since 0.2.0; a write now just drops its category's bank. A downgraded
  version finds no bank and uses the per-fact vectors.
- Reads take the store lock.

### Changed (provider contract)

- Subagent, cron and flush contexts (`agent_context`) read but never write: no facts, feedback, counters,
  extraction or mirroring.
- `initialize` opens the database of the `hermes_home` it is given and releases a previous handle when called again;
  `save_config` writes the profile it is given.
- `on_memory_write` takes the `metadata` argument; with `mirror_memory_writes` on, `replace` and `remove` follow the
  entry by `metadata["previous_content"]`.
- Re-running `hermes memory setup` offers the saved values as defaults instead of the factory ones (it used to switch
  `db_path` to a fresh file and turn `auto_extract` off).
- `backup_paths()` lists a database outside the Hermes home; the database and its WAL files are owner-only on POSIX.
- `limit` is clamped to 1–100; `fact_feedback` refuses actions other than helpful / unhelpful.

## 0.2.0 — 2026-10-10

First release under new maintenance (itpartypattaya). Measured on a synthetic store of 200 facts about 20 people
with random trust scores; before → after.

### Fixed

- **probe** returned the most trusted facts instead of the entity's facts (precision@5 0.02–0.04 → 1.00; a random
  pick scores ~0.06). It compared `unbind(fact, bind(entity, ROLE_ENTITY))` with the fact's content binding, but a
  fact vector is a *bundle* of bindings, so unbinding one leaves noise, not the content. probe now measures the
  fact's similarity to `bind(entity, ROLE_ENTITY)` directly and drops facts under a noise floor (4 sd of chance
  similarity), so trust only orders real matches. The category-bank shortcut had the same flaw and is gone.
- **reason** (facts linking several entities) found the right fact in the top 5 in 1 of 15 cases → 15 of 15, same fix.
- **contradict** flagged facts that share a subject and are *dissimilar*, i.e. different topics ("Carol King works
  at the bakery" / "Carol King plays guitar"), and missed real conflicts. It now pairs facts that share entities and
  make a similar, non-identical claim, by word overlap with the entity names left out ("lives in Paris" / "lives in
  London"). `content_similarity` in its output is now that word overlap. It no longer needs numpy.
- **search**: the HRR term compared the query with the whole fact vector instead of its content binding, so it was
  ~0.5 for every fact (a constant). It now compares `bind(query, ROLE_CONTENT)`: ~0.69 for facts on the query's
  topic vs ~0.51 for others.
- **`retrieval_count`** was declared and never written. It now counts facts handed to the model: prefetch and the
  search / probe / related / reason actions (not list or contradict). `updated_at` is not touched. Same fix as the
  unmerged hermes-agent#130930.
- **Entity names** were ASCII-only (`[A-Z][a-z]+`). Capitalized multi-word names in any cased script are linked now
  (`Мария Иванова`, `Jean-Luc Picard`), guillemets and curly quotes count as quotes, and a quoted span of more than
  three words is treated as a quotation, not a name. Related: unmerged hermes-agent#129521 and #116232.
- **FTS5 failures** were swallowed silently (an empty result looked like "no memories"); they are logged as warnings.
  Related: unmerged hermes-agent#127134.

### Changed

- **Built-in memory is no longer mirrored into the fact store by default.** Every `memory add` used to be copied as a
  fact; MEMORY.md / USER.md are already in the system prompt in full, so prefetch added the same sentence twice and
  the copy outlived the entry when it was reworded or removed. Set `mirror_memory_writes: true` for the old behaviour.
  Facts already mirrored stay in the store.
- Prefetched facts carry their id (`- #12 [0.5] …`) so the model can call `fact_feedback` on them. Related: unmerged
  hermes-agent#121513.
- The `fact_store` description no longer tells the model to "ALWAYS probe or reason first": it says to search first,
  probe a named entity, reason across several.

## 0.1.1 — 2026-10-02

Nous Research: standalone copy of the provider bundled in hermes-agent, synced with core, tests ported.
