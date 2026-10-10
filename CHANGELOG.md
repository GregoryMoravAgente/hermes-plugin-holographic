# Changelog

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
