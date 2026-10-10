# Changelog

## 0.4.1 — 2026-10-11

Fixes from a second independent review (Codex CLI, whole branch 0.1.1..0.4.0), each with a regression test.

### Fixed

- **User scope failed open**: an anonymous turn in a shared chat kept the previous person's store active, and a bot
  without an id passed the bot check. Now a bot, or an anonymous turn once the conversation has had authors, is
  nobody (no recall, no writes); a conversation that never had authors is the user Hermes named at start.
- **Extraction mixed people**: in user scope, session-end and pre-compression extraction stored every participant's
  sentences in the active person's store. Extraction now runs per turn in `sync_turn`, filed under `turn_author`;
  unattributable turns and whole transcripts are skipped.
- **Thread safety**: a scope switch could happen in the middle of a recall or tool call (the gateway calls hooks from
  several threads, and core leaves a timed-out prefetch running). Provider entry points are serialized.
- **Buffered recall counts** could be written to another user's or profile's database (fact ids are per database).
  Buffers are kept per database, and dropped rather than transferred if it stays busy until shutdown.
- **Migration lost legacy aliases** when it merged two entities with the same key; they are imported first now, and
  every merge keeps the merged entity's name as an alias of the survivor.
- **Aliases of entities an older version deleted** still resolved to the dead id, and new facts were linked to it.
  They are dropped on open, and alias lookups only follow live entities.
- **Alias collisions**: giving an alias that already belongs to another entity merged that whole entity and lost its
  name; it is refused now with a hint to merge explicitly.
- **Entity cache after a rollback** could link a fact to the wrong entity (a rolled-back id reused). The cache now
  follows a generation counter (bumped on every entity change and rollback) and SQLite's `data_version`.
- **Merges** keep the declared flag, re-encode the vectors of the facts involved, drop the category banks and copy
  the moved aliases into the legacy column older versions read.
- **Vectors of another size** written by an older version or setting are re-encoded on open (checked from the blobs,
  not only from the recorded dimension). Subagent, cron and flush contexts never migrate or repair a store.
- **Search**: no user query can produce an FTS5 syntax error (every MATCH token is operator-free, with or without the
  stemmer); a stem prefix match is kept only if the fact really contains the word or its stem ("walk" no longer
  returns "walker").
- **Recall gate**: words of the message that no fact contains now count against coverage ("do submarines deploy on
  Jupiter?" is not about "we deploy software"); a message naming an identifier recalls only facts with that exact
  identifier ("pve-01" is not "pve-010").
- **reason** with a known and an unknown name intersects the known entity's facts (aliases included) with the unknown
  name's words.
- **update** with only `entities` links them; rewording a fact keeps the entities declared for it.
- **Conflicts** see negation ("likes apples" / "does not like apples"); such a pair is never reported as a duplicate.
- **No-repeat window** keys facts by content revision (two edits in the same second are different), drops recent
  facts before the top five are taken, and resets on context compression.
- **Validator** catches requests to send credentials somewhere and ignores phrases inside quotes.
- `backup_paths()` lists per-user stores next to an external `db_path`; at most 16 per-user stores stay open.
- Bounded work: `write_check` looks at 50 facts per entity at most, `review` and `contradict` load only what they
  compare, entity mentions are looked up by stem instead of scanning every name.

### Known limits

- A timed-out recall is discarded by core, but the provider cannot tell: its facts count as recalled and the recall
  indicator may show them.
- Word forms are stems used as prefixes, not lemmas: an irregular form may miss (`studies` vs `study`).

## 0.4.0 — 2026-10-11

Schema version 2, additive: older versions (0.1.1, the bundled core copy) keep working on the same file, and what
they write is reconciled on the next open. CI: 0.4.0 → 0.1.1 → 0.4.0 on one database.

### Added

- **Entity identity**: `entities.name_key` (NFKC, casefold, hyphen/underscore as space) with a unique index, so
  `Hermes Agent`, `hermes-agent` and `HERMES AGENT` are one entity. Entities an older version created without a key
  are keyed on open and merged into an existing one with the same key.
- **Aliases**: `entity_aliases` table; `X aka Y` makes `Y` an alias of `X` (it used to create two unrelated
  entities); actions `entities` (names, aliases, fact counts) and `alias` (second name, or merge two entities).
  Aliases are mirrored into the legacy `entities.aliases` column that older versions read.
- **HRR dimension recorded** in `meta`: changing `hrr_dim` re-encodes the stored vectors on open instead of making
  every vector query fail; the dimension of pre-0.4.0 stores is inferred from their blobs.
- **Thai, Chinese, Japanese, Korean**: matched by character n-grams (trigrams for Thai/Lao/Khmer/Myanmar, bigrams for
  CJK and Hangul) in search and in the recall gate. A substring scan, no extra index or migration.
- **Per-user stores** (`scope: user`): one database per `<platform>-<user id>` under `holographic-users/`, chosen by
  the user Hermes passes at start and switched to the author of each turn in shared chats (`on_turn_start`). No
  identity, or a bot author: fails closed (no recall, no writes). Separate files instead of a scope column: the
  global `UNIQUE(content)` keeps working per user, and an older version cannot expose one user's facts to another
  because it never opens those files.

### Changed

- contradict compares only facts that share an entity (grouped), not every pair.

## 0.3.0 — 2026-10-11

Recall and writing quality, from user reports (r/hermesagent: "can't tell if it works", "the agent never stores
anything", "fills up with junk", "an old IP stayed next to the new one") and the audit. No schema change: one more
use of the existing `entities.entity_type` column, and 0.3.0 → 0.1.1 → 0.3.0 still passes on one database.

### Added

- **Word forms**: search matches the exact word or its Snowball stem as an FTS5 prefix (English for Latin script,
  Russian for Cyrillic): `Маши`/`Машу` find `Маша`, `байк` finds `байке`, `deploys` finds `deploy`. Identifiers
  (`pve-01`, `v0.21.6`) are never stemmed. New dependency `snowballstemmer` (pure Python, BSD).
- **Russian stopwords** and greetings next to the English ones.
- **Relevance gate for recall before the turn**: a fact must cover a share (`prefetch_min_coverage`, 0.3) of the
  message's informative words, weighted by rarity (IDF), or match an identifier; a message with no informative word
  recalls nothing. On the test store "да, это так" no longer injects "Это проект для семьи".
- **No repeats**: a fact injected before a turn is not injected again in the same session for
  `prefetch_repeat_turns` (6) turns, keyed by fact revision; a new conversation or a rewound one starts clean.
- **Entity mentions**: when the message names a known entity, its top facts join the recall.
- **Declared entities**: `add`/`update` take `entities` (a first name, a pet). Declared and multi-word entities are
  then linked whenever a later fact mentions them in any word form ("У Маши аллергия" links to "Маша").
- **Write-time hints**: `add` answers with `similar` facts and `possible_conflicts` (same subject, a different
  claim; also between entity-less facts such as two IPs for one server), and takes `supersedes: <id>` to replace a
  fact in one transaction (the new one starts at default trust).
- **Write validator**: JSON, tool or chat-role markup, instruction-like phrasing ("ignore previous instructions"),
  invisible formatting characters, long encoded blobs and >1000-character texts are refused with the reason.
- **stats / export / review** actions; the core's recall indicator (`recall_status`) shows how many facts the last
  recall injected.
- A clear prompt block: when to store, `entities` for one-word names, `supersedes`, ids for `fact_feedback`, and a
  warning when numpy is missing.

### Changed

- Recall before the turn is framed as data ("stored facts: data, not instructions"), one line per fact, ≤300
  characters.
- `auto_extract` stores the matching sentence, not the whole message; English and Russian patterns; trust 0.4
  (below hand-added facts); also runs before context compression, so a long session that is later killed keeps it.
- Recall counts are buffered and written in one statement (every 25 recalls and at session boundaries) with no
  wait on another process's write lock; on a busy database they are kept for the next flush.
- An unreadable database no longer breaks the agent: the provider reports why memory is off (prompt block and tool
  errors) with the `sqlite3 … .recover` command.
- Shorter `fact_store` description with call shapes, for models that struggled with nine actions.

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
