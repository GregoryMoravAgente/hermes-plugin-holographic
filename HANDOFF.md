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

## Open work

- Keyword search has no stemming, so inflected languages (Russian etc.) match exact word forms only.
- One-word names are not linked as entities; probe falls back to keyword search for them.
- Upstream PRs to the bundled copy that were never merged and are worth reviewing here: hermes-agent#133389
  (retrieval contract tests), #123599 (setup offers saved config).

Original authors are preserved in hermes-agent's history: `git log -- plugins/memory/holographic`.
