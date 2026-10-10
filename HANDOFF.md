# Maintenance notes — hermes-plugin-holographic

**Status: maintained by [itpartypattaya](https://github.com/itpartypattaya)** (since 2026-10-10). This repository
started as Nous Research's standalone copy of the `holographic` memory provider bundled in
`NousResearch/hermes-agent` under `plugins/memory/holographic/`, published so someone could take it over
([original handoff note](https://github.com/NousResearch/hermes-plugin-holographic/blob/main/HANDOFF.md),
[takeover request](https://github.com/NousResearch/hermes-plugin-holographic/issues/1)).

## Catalog and migration

The plugin is submitted to the Hermes plugin catalog as `plugin-catalog/holographic.yaml` under the name
`holographic`. That exact name matters: once core drops its bundled copy, `hermes update` (and agent start) install
the catalog plugin of the same name for users with `memory.provider: holographic`
(`hermes_cli/memory_provider_migration.py`, `_LEFT_CORE`), keeping their config and data.

Releases are tags `vX.Y.Z` matching `version` in `plugin.yaml` and `pyproject.toml`; the catalog entry pins the
tag's commit by `sha:` and `version:`. A new release reaches users only through a SHA-bump PR to hermes-agent.

## Differences from the in-tree copy

- Self-imports are relative so the package loads from `~/.hermes/plugins/holographic/` under the loader's synthetic
  namespace.
- `pyproject.toml` is the dependency authority (no `tools.lazy_deps` calls: they pinned an old version in the
  lazy-deps registry and downgraded newer installs, hermes-agent#86992).
- Behaviour changes since 0.2.0 are listed in [CHANGELOG.md](CHANGELOG.md).

While a Hermes install still carries the bundled `plugins/memory/holographic`, that copy wins on name and this plugin
is dormant; it takes over once core drops the bundled directory.

## Tests

`tests/` holds the in-tree tests (`tests/plugins/memory/test_holographic*.py` in hermes-agent) with the import path
switched to the package `tests/conftest.py` loads from this repo, plus `test_holographic_v020.py`. Run locally with
`HERMES_AGENT_REPO=~/.hermes/hermes-agent PYTHONPATH=~/.hermes/hermes-agent python -m pytest -q`. The checkout
directory name must be a valid Python identifier (CI uses `plugin`): pytest imports the repository root as a package.
CI runs the suite on Linux, macOS and Windows against hermes-agent `main`, and `hermes plugins validate
--install-deps`, the catalog's admission check.

## Languages

`text.py` holds everything per language: function words (`STOP`), stemmer choice (`_STEMMERS`, `_DEFAULT_LANG`,
`_SCRIPT_LANGS`), greetings, negators, claim words, folding (`_FOLD`). To add a language: its function words (they
also decide which Latin language a text is), its stemmer, greetings and negators; then a main set and an unseen set in
`tests/test_holographic_languages.py` style, measured before the change. Arabic, Indic scripts and Indonesian go
through the word scan in `retrieval.py` (`_texts`, `_scan_candidates`), not FTS5; keep that in mind for any script
whose words FTS5 splits or whose inflection is a prefix. The README "Languages" section states how every number was
obtained; keep it that way.

## Open work

- Unsegmented scripts use a substring scan of character n-grams; an FTS5 trigram index would scale further but needs
  a sync story for older versions writing the file (no triggers: they would fail on SQLite builds without trigram).
  Not needed at this store's size; revisit if stores grow past ~50k facts.
- Paraphrase recall (no shared words) needs embeddings; deliberately out of scope (no network, no model).
- The word scan reads every row on each scan; past ~20k facts a persisted key index (with a reconciliation story for
  older versions writing the file) would be the next step. Host names in Spanish/French/Portuguese/Indonesian
  questions are not enforced as identifiers (compound words win there).
- Upstream PRs to the bundled copy that were never merged and are worth reviewing here: hermes-agent#133389
  (retrieval contract tests), #123599 (setup offers saved config).

Original authors are preserved in hermes-agent's history: `git log -- plugins/memory/holographic`.
