"""Store / provider fixes from the review of 0.6.2–0.9.0: a failed COMMIT no longer wedges the connection, a forget
does not wait on a foreign reader, credentials are never stored, short CJK sentences are extracted, a missing space
after a full stop still splits sentences, user_id_alt names the user store, revert explains itself after a revert, and
a file written by a newer release is not opened for writing."""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

pytest.importorskip("numpy")

from hermes_plugin_holographic import HolographicMemoryProvider, _RE_SENTENCE
from hermes_plugin_holographic.history import HistoryTooNew
from hermes_plugin_holographic.store import MemoryStore

DIM = 64


def _provider(tmp_path, config=None, **init):
    cfg = {"db_path": str(tmp_path / "memory_store.db"), "hrr_dim": DIM, **(config or {})}
    p = HolographicMemoryProvider(config=cfg)
    p.initialize(session_id=init.pop("session_id", "s1"), **init)
    return p


def _call(provider, **args):
    return json.loads(provider.handle_tool_call("fact_store", args))


def _contents(provider):
    return [f["content"] for f in provider._store.list_facts(limit=100)]


def _journal(store, mode: str) -> None:
    """Pin the journal mode for the test: the core picks WAL or DELETE by the SQLite it runs on."""
    assert store._conn.execute(f"PRAGMA journal_mode={mode}").fetchone()[0].lower() == mode.lower()


def _reader(db) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    """Another process in the middle of a long query: a statement left open holds its read lock."""
    other = sqlite3.connect(str(db), isolation_level=None)
    cur = other.execute("SELECT fact_id, content FROM facts")
    cur.fetchone()
    return other, cur


# --- a failed COMMIT ------------------------------------------------------------------------------------------------


def test_a_failed_commit_is_rolled_back_and_the_next_write_works(tmp_path):
    """Rollback-journal mode: a foreign SHARED lock makes COMMIT fail with "database is locked". The transaction
    must not stay open, or every later write would join it and nothing would ever be committed again."""
    db = tmp_path / "memory_store.db"
    store = MemoryStore(str(db), hrr_dim=DIM)
    _journal(store, "DELETE")
    for i in range(5):  # several rows: Python's cursor steps one row ahead, and a finished statement holds no lock
        store.add_fact(f"Alice Smith lives in Paris, flat {i}")
    other, cur = _reader(db)
    store._conn.execute("PRAGMA busy_timeout = 200")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.add_fact("Bob Brown lives in Rome")
        assert not store._conn.in_transaction  # rolled back, not dangling
    finally:
        cur.close()
        other.close()
    fid = store.add_fact("Bob Brown lives in Rome")
    assert fid > 0 and store.count_facts() == 6
    assert sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 6  # committed for real
    store.close()


# --- forget and a foreign reader ------------------------------------------------------------------------------------


def test_forget_does_not_wait_for_a_foreign_reader(tmp_path):
    """WAL: the reader blocks only the checkpoint. The forget must report `pending` at once (every provider
    operation queues behind it), not after the connection's 10 s busy timeout, and leave the timeout as it was."""
    db = tmp_path / "memory_store.db"
    store = MemoryStore(str(db), hrr_dim=DIM)
    _journal(store, "WAL")
    for i in range(50):
        store.add_fact(f"Person{i} Surname{i} lives in city number {i}")
    timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    other, cur = _reader(db)
    try:
        started = time.monotonic()
        result = store.forget_fact(50)
        elapsed = time.monotonic() - started
    finally:
        cur.close()
        other.close()
    assert result["forgotten"] is True and result["scrub"] == "pending" and elapsed < 3.0
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == timeout
    assert store.read("SELECT COUNT(*) FROM facts_fts WHERE facts_fts MATCH 'Surname49'")[0][0] == 0
    again = store.forget_fact(50)
    assert again["already"] is True and again["scrub"] == "done"
    assert store.read("SELECT COUNT(*) FROM meta WHERE key = 'scrub_pending'")[0][0] == 0
    store.close()


# --- credentials ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("sentence", [
    "I use password hunter2 for the home wifi.",
    "The wifi password is Qwerty!23 at home.",
    "My passphrase for the vault is correct-horse-battery",
    "Пароль от wifi: Qwerty123, не забудь.",
    "My API key is sk-live-9f8e7d6c5b4a for prod.",
    "The door pin code is 4821 for guests.",
])
def test_a_credential_with_its_value_is_never_stored(tmp_path, sentence):
    provider = _provider(tmp_path, config={"auto_extract": "true"})
    provider.on_session_end([{"role": "user", "content": sentence}])
    assert _contents(provider) == []
    result = _call(provider, action="add", content=sentence)
    assert "credential" in result["error"]
    provider.shutdown()


@pytest.mark.parametrize("sentence", [
    "I prefer tea.",
    "I use a password manager for everything.",
    "I always forget my password for the bank.",
    "We decided the token expires after thirty days.",
    "I prefer a PIN over a fingerprint.",
])
def test_the_word_alone_is_not_a_credential(tmp_path, sentence):
    provider = _provider(tmp_path, config={"auto_extract": "true"})
    provider.on_session_end([{"role": "user", "content": sentence}])
    assert _contents(provider) == [sentence]
    provider.shutdown()


# --- sentences ------------------------------------------------------------------------------------------------------


def test_short_cjk_sentences_are_extracted(tmp_path):
    provider = _provider(tmp_path, config={"auto_extract": "true"})
    provider.on_session_end([{"role": "user", "content": "我们决定用Go。"}, {"role": "user", "content": "我偏好簡潔的解法。"},
                             {"role": "user", "content": "I use Go."}])  # nine Latin characters: too short still
    assert _contents(provider) == ["我们决定用Go。", "我偏好簡潔的解法。"]
    provider.shutdown()


def test_a_missing_space_after_a_full_stop_still_splits(tmp_path):
    assert [m.group(0) for m in _RE_SENTENCE.finditer("I prefer tea.I use vim daily.")] == ["I prefer tea.", "I use vim daily."]
    assert [m.group(0) for m in _RE_SENTENCE.finditer("We pin Python 3.12, keep notes in topic.md and run on hr-ovh.local.")] \
        == ["We pin Python 3.12, keep notes in topic.md and run on hr-ovh.local."]
    provider = _provider(tmp_path, config={"auto_extract": "true"})
    provider.on_session_end([{"role": "user", "content": "I prefer tea.I use vim daily."}])
    assert sorted(_contents(provider)) == ["I prefer tea.", "I use vim daily."]
    provider.shutdown()


# --- user_id_alt, revert, a newer schema ----------------------------------------------------------------------------


def test_user_id_alt_names_the_user_store(tmp_path):
    provider = _provider(tmp_path, config={"scope": "user"}, platform="telegram", user_id_alt="999")
    assert _call(provider, action="add", content="Alice Smith lives in Paris")["status"] == "added"
    provider.shutdown()
    assert (tmp_path / "holographic-users" / "telegram-999.db").exists()


def test_revert_after_a_revert_explains_itself(tmp_path):
    provider = _provider(tmp_path)
    fid = _call(provider, action="add", content="Frank Hill prefers green tea")["fact_id"]
    _call(provider, action="change", fact_id=fid, content="Frank Hill prefers black coffee")
    assert _call(provider, action="revert", fact_id=fid)["status"] == "reverted"
    error = _call(provider, action="revert", fact_id=fid)["error"]
    assert "not undone by another revert" in error and "single version" not in error
    lone = _call(provider, action="add", content="Gina Ray lives in Oslo")["fact_id"]
    assert "single version" in _call(provider, action="revert", fact_id=lone)["error"]
    provider.shutdown()


def test_a_file_from_a_newer_release_is_not_opened_for_writing(tmp_path):
    db = tmp_path / "memory_store.db"
    store = MemoryStore(str(db), hrr_dim=DIM)
    store.add_fact("Alice Smith lives in Paris")
    store._conn.execute("UPDATE meta SET value = '99' WHERE key = 'history_schema'")
    store.close()
    with pytest.raises(HistoryTooNew, match="update the plugin"):
        MemoryStore(str(db), hrr_dim=DIM)
    assert sqlite3.connect(str(db)).execute("SELECT value FROM meta WHERE key = 'history_schema'").fetchone()[0] == "99"
    reader = MemoryStore(str(db), hrr_dim=DIM, migrate=False)
    assert reader.count_facts() == 1
    reader.close()
    provider = _provider(tmp_path)
    assert "update the plugin" in provider.system_prompt_block() and ".recover" not in provider.system_prompt_block()
    provider.shutdown()


# --- provider retirement -------------------------------------------------------------------------------------------


def test_retirement_receipt_is_strict_and_never_reports_absent(tmp_path):
    uninitialized = HolographicMemoryProvider(config={"db_path": str(tmp_path / "unused.db"), "hrr_dim": DIM})
    result = uninitialized.retire()
    assert result["status"] == "unknown"
    assert result["reason"] == "not_initialized"
    assert result["provider"] == result["home_id"] == result["incarnation"] == ""

    provider = _provider(tmp_path)
    receipt = provider.retirement_receipt()
    assert set(receipt) == {"provider", "home_id", "incarnation"}
    assert all(isinstance(value, str) and value for value in receipt.values())

    foreign = dict(receipt, home_id="foreign-home")
    refused = provider.retire(foreign)
    assert refused["status"] == "unknown" and refused["reason"] == "foreign_receipt"
    assert refused["active_store_count"] == 1
    stale = provider.retire(dict(receipt, incarnation="stale-incarnation"))
    assert stale["status"] == "unknown" and stale["reason"] == "stale_receipt"
    malformed = [
        dict(receipt, extra="field"),
        {"provider": receipt["provider"], "home_id": receipt["home_id"]},
        dict(receipt, incarnation=7),
    ]
    for candidate in malformed:
        refused = provider.retire(candidate)
        assert refused["status"] == "unknown" and refused["reason"] == "foreign_receipt"
        assert refused["active_store_count"] == 1
    assert stale["active_store_count"] == 1

    released = provider.retire(receipt)
    assert set(released) == {
        "status", "provider", "home_id", "incarnation", "active_store_count", "pending_write_count",
        "pending_flush_count", "close_failure_count", "reason",
    }
    assert released["status"] == "released"
    assert released["reason"] == "released"
    assert released["active_store_count"] == released["pending_write_count"] == released["pending_flush_count"] == 0
    assert provider.retire(receipt) == released
    provider.shutdown()  # hard-retirement shutdown is deliberately idempotent


def test_retirement_failed_open_and_no_identity_stay_unknown(tmp_path):
    db = tmp_path / "memory_store.db"
    store = MemoryStore(str(db), hrr_dim=DIM)
    store._conn.execute("UPDATE meta SET value = '99' WHERE key = 'history_schema'")
    store.close()

    failed_open = _provider(tmp_path)
    failed = failed_open.retire(failed_open.retirement_receipt())
    assert failed["status"] == "unknown" and failed["reason"] == "store_open_failed"
    assert failed["active_store_count"] == 0

    no_identity = _provider(tmp_path / "user", config={"scope": "user"}, platform="telegram")
    refused = no_identity.retire(no_identity.retirement_receipt())
    assert refused["status"] == "unknown" and refused["reason"] == "no_identity"
    assert refused["active_store_count"] == 0


def test_retirement_flush_failure_keeps_counts_for_same_receipt_retry(tmp_path):
    provider = _provider(tmp_path)
    store = provider._store
    assert store is not None
    # Keep unread rows behind the foreign cursor.  A one-row SELECT is stepped
    # to exhaustion by sqlite3 and therefore does not retain the DELETE-mode
    # shared read lock needed to exercise the real busy/flush boundary.
    fact_ids = [store.add_fact("Alice Smith prefers green tea with mint")]
    fact_ids += [store.add_fact(f"Person {i} lives in city {i}") for i in range(4)]
    fact_id = fact_ids[0]
    assert provider.prefetch("Alice Smith prefers green tea with mint", session_id="flush-one")
    assert provider.prefetch("Alice Smith prefers green tea with mint", session_id="flush-two")
    assert provider._pending[provider._scope_key][fact_id] == 2
    _journal(store, "DELETE")
    other, cur = _reader(store.db_path)
    receipt = provider.retirement_receipt()
    try:
        assert cur.fetchone() is not None  # the reader still has rows, so its shared lock is live
        refused = provider.retire(receipt)
    finally:
        cur.close()
        other.close()
    assert refused["status"] == "unknown" and refused["reason"] == "flush_failed"
    assert refused["pending_write_count"] == 2 and refused["pending_flush_count"] == 1
    assert provider._pending[provider._scope_key][fact_id] == 2

    released = provider.retire(receipt)
    assert released["status"] == "released" and released["reason"] == "released"
    persisted = sqlite3.connect(str(store.db_path))
    try:
        assert persisted.execute(
            "SELECT retrieval_count FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0] == 2
    finally:
        persisted.close()


def test_retirement_close_failure_keeps_exact_store_entry_for_retry(tmp_path):
    provider = _provider(tmp_path)
    store = provider._store
    assert store is not None
    store.add_fact("Alice Smith prefers green tea with mint")
    entry = store._entry
    key = store._key

    class CloseOnce:
        def __init__(self, connection):
            self.connection = connection
            self.fail = True

        def close(self):
            if self.fail:
                self.fail = False
                raise sqlite3.OperationalError("busy")
            return self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    proxy = CloseOnce(entry["conn"])
    entry["conn"] = proxy
    receipt = provider.retirement_receipt()
    first = provider.retire(receipt)
    assert first["status"] == "unknown" and first["reason"] == "close_failed"
    assert first["close_failure_count"] == 1
    assert provider._stores[provider._scope_key][0] is store
    assert MemoryStore._shared[key] is entry and entry["refs"] == 1
    assert proxy.connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1

    second = provider.retire(receipt)
    assert second["status"] == "released" and second["close_failure_count"] == 1
    assert key not in MemoryStore._shared
    verify = sqlite3.connect(str(store.db_path))
    try:
        assert verify.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    finally:
        verify.close()


def test_retirement_stale_store_does_not_close_successor_or_sibling(tmp_path):
    provider = _provider(tmp_path)
    store = provider._store
    assert store is not None
    store.add_fact("Alice Smith prefers green tea with mint")
    receipt = provider.retirement_receipt()
    sibling_root = tmp_path.parent / f"{tmp_path.name}-sibling"
    sibling_root.mkdir()
    successor = sibling = None
    try:
        # This is the existing profile-removal path: it force-closes the old shared entry and
        # removes only that registry entry, leaving the provider's holder stale.
        assert MemoryStore.release_all_under(tmp_path) == 1
        successor = MemoryStore(store.db_path, hrr_dim=DIM)
        successor.add_fact("Bob Brown prefers black coffee")
        sibling = MemoryStore(sibling_root / "memory_store.db", hrr_dim=DIM)
        sibling.add_fact("Carol Jones prefers mint tea")

        refused = provider.retire(receipt)
        assert refused["status"] == "unknown" and refused["reason"] == "stale_store"
        assert refused["close_failure_count"] == 0
        assert successor.count_facts() == 2  # the old row plus the successor's new row
        assert sibling.count_facts() == 1
        assert MemoryStore._shared[successor._key] is successor._entry
    finally:
        if successor is not None:
            successor.close()
        if sibling is not None:
            sibling.close()
        MemoryStore.release_all_under(tmp_path)
        MemoryStore.release_all_under(sibling_root)


def test_retirement_waits_for_real_sync_turn_catch_up_boundary(tmp_path):
    provider = _provider(tmp_path, config={"embeddings": "on", "embedding_url": "http://127.0.0.1:9/v1",
                                          "embedding_model": "local", "embedding_key_env": ""})
    store = provider._store
    assert store is not None
    store.add_fact("Alice Smith prefers green tea with mint")
    receipt = provider.retirement_receipt()
    entered = threading.Event()
    release = threading.Event()

    class FakeEmbedder:
        def usable(self):
            return True

        def fact_batch(self, sizes, budget, probe=True):
            return len(sizes)

        def embed_facts(self, texts, budget, decisive=True):
            entered.set()
            assert release.wait(2)
            return None

    provider._embedder = FakeEmbedder()
    worker = threading.Thread(target=provider.sync_turn, args=("", ""), daemon=True)
    worker.start()
    assert entered.wait(2)
    try:
        with pytest.raises(RuntimeError, match="work is unsettled"):
            provider.initialize(session_id="replacement", hermes_home=str(tmp_path), platform="telegram", user_id="one")
        refused = provider.retire(receipt)
        assert refused["status"] == "unknown" and refused["reason"] == "pending_catch_up"
        assert refused["active_store_count"] == 1
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    failed = provider.retire(receipt)
    assert failed["status"] == "unknown" and failed["reason"] == "pending_catch_up"
    released = provider.retire(receipt)
    assert released["status"] == "released"
    verify = sqlite3.connect(str(store.db_path))
    try:
        assert verify.execute("SELECT COUNT(*) FROM fact_embeddings").fetchone()[0] == 0
    finally:
        verify.close()


def test_retirement_keeps_a_failed_catch_up_after_a_later_success(tmp_path):
    provider = _provider(tmp_path, config={"embeddings": "on", "embedding_url": "http://127.0.0.1:9/v1",
                                          "embedding_model": "local", "embedding_key_env": ""})
    store = provider._store
    assert store is not None
    store.add_fact("Alice Smith prefers green tea with mint")

    class Embedder:
        def __init__(self):
            self.calls = 0

        def usable(self):
            return True

        def fact_batch(self, sizes, budget, probe=True):
            return len(sizes)

        def embed_facts(self, texts, budget, decisive=True):
            self.calls += 1
            return None if self.calls == 1 else [[0.0] * DIM for _ in texts]

    provider._embedder = Embedder()
    provider._catch_up_vectors()
    provider._catch_up_vectors()
    receipt = provider.retirement_receipt()
    refused = provider.retire(receipt)
    assert refused["status"] == "unknown" and refused["reason"] == "pending_catch_up"
    released = provider.retire(receipt)
    assert released["status"] == "released"
    verify = sqlite3.connect(str(store.db_path))
    try:
        assert verify.execute("SELECT COUNT(*) FROM fact_embeddings").fetchone()[0] == 1
    finally:
        verify.close()


def test_retirement_keeps_failed_scope_while_sibling_flushes(tmp_path):
    provider = _provider(tmp_path, config={"scope": "user"}, platform="telegram", user_id="one")
    first_key, first_store = provider._scope_key, provider._store
    assert first_store is not None
    first_ids = [first_store.add_fact("Alice Smith prefers green tea with mint")]
    first_ids += [first_store.add_fact(f"Person {i} lives in city {i}") for i in range(4)]
    first_id = first_ids[0]
    assert provider.prefetch("Alice Smith prefers green tea with mint", session_id="first")
    provider.on_turn_start(1, "", author_id="two")
    second_key, second_store = provider._scope_key, provider._store
    assert second_store is not None and second_key != first_key
    second_id = second_store.add_fact("Bob Brown prefers black coffee")
    assert provider.prefetch("Bob Brown prefers black coffee", session_id="second")
    assert provider._pending[first_key][first_id] == 1
    assert provider._pending[second_key][second_id] == 1
    _journal(first_store, "DELETE")
    other, cur = _reader(first_store.db_path)
    receipt = provider.retirement_receipt()
    try:
        assert cur.fetchone() is not None
        refused = provider.retire(receipt)
    finally:
        cur.close()
        other.close()
    assert refused["status"] == "unknown" and refused["reason"] == "flush_failed"
    assert first_key in provider._stores and second_key in provider._stores
    assert first_key in provider._pending and second_key not in provider._pending
    released = provider.retire(receipt)
    assert released["status"] == "released" and released["active_store_count"] == 0


def test_retirement_releases_only_this_provider_ref(tmp_path):
    db = tmp_path / "memory_store.db"
    provider = _provider(tmp_path)
    sibling = MemoryStore(db, hrr_dim=DIM)
    receipt = provider.retirement_receipt()
    released = provider.retire(receipt)
    assert released["status"] == "released"
    sibling._conn.execute("SELECT 1").fetchone()
    sibling.close()


def test_retirement_releases_all_provider_scope_stores(tmp_path):
    provider = _provider(tmp_path, config={"scope": "user"}, platform="telegram", user_id="one")
    provider.on_turn_start(1, "", author_id="two")
    assert len(provider._stores) == 2
    released = provider.retire(provider.retirement_receipt())
    assert released["status"] == "released" and released["active_store_count"] == 0
