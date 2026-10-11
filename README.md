> **Moved: this repo is archived.** The holographic memory provider is now maintained by
> [@itpartypattaya](https://github.com/itpartypattaya) at
> **[itpartypattaya/hermes-plugin-holographic](https://github.com/itpartypattaya/hermes-plugin-holographic)**,
> and that's the copy listed in the Hermes plugin catalog as `holographic`.
>
> - **Using the built-in provider (`memory.provider: holographic`)?** You don't need to do anything. When Hermes
>   removes the bundled copy, `hermes update` (or the next agent start) installs the catalog plugin for you. Your
>   facts (`$HERMES_HOME/memory_store.db`) and settings (`plugins.hermes-memory-store`) carry over unchanged.
> - **Installed this repo by URL?** Reinstall once from the catalog so you get updates:
>   `hermes plugins remove holographic && hermes plugins install holographic`. Your data stays where it is.
>
> Nous Research doesn't maintain memory providers; report issues and send PRs to the new repo.

# Holographic Memory Provider

Local SQLite fact store with FTS5 search, trust scoring, entity resolution, and HRR-based compositional retrieval.

## Requirements

None — uses SQLite (always available). NumPy optional for HRR algebra.

## Setup

```bash
hermes memory setup    # select "holographic"
```

Or manually:
```bash
hermes config set memory.provider holographic
```

## Config

Config in `config.yaml` under `plugins.hermes-memory-store`:

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `$HERMES_HOME/memory_store.db` | SQLite database path |
| `auto_extract` | `false` | Auto-extract facts at session end |
| `default_trust` | `0.5` | Default trust score for new facts |
| `hrr_dim` | `1024` | HRR vector dimensions |

## Tools

| Tool | Description |
|------|-------------|
| `fact_store` | 9 actions: add, search, probe, related, reason, contradict, update, remove, list |
| `fact_feedback` | Rate facts as helpful/unhelpful (trains trust scores) |
