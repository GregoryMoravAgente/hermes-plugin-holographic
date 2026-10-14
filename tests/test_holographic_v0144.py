"""0.14.4: fact_store search shows a fact closer in meaning than anything its words found (issue #3: the words filled
the limit while the fact asked for was the store's closest in meaning, and search by meaning was never asked)."""
from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")

from hermes_plugin_holographic import HolographicMemoryProvider, embeddings  # noqa: E402

WORDS = ["The garden backup report is mailed on Mondays",      # share "garden" / "backup" with the query
         "The garden hose is kept behind the shed",
         "The garden gate code changes every backup cycle"]
TARGET = "Lena's birthday is on June 3"
OTHERS = ["The kitchen tap drips at night", "Our router sits in the hallway", "Mark drives a blue Skoda",
          "The cat eats twice a day", "Rent is paid on the first of the month"]
QUERY = "garden backup plans"
ON = {"embeddings": "on", "embedding_url": "https://embeddings.example.net/v1", "embedding_model": "m",
      "embedding_key_env": "TEST_EMBEDDINGS_KEY"}


class _NoNetwork:
    def open(self, *a, **k):
        raise AssertionError("no request in these tests: meaning is given")


def _provider(tmp_path, monkeypatch, **config):
    monkeypatch.setattr(embeddings, "_OPENER", _NoNetwork())
    monkeypatch.setenv("TEST_EMBEDDINGS_KEY", "sk-test-not-a-real-key")
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "memory_store.db"), "hrr_dim": 64, **ON, **config})
    p.initialize(session_id="s1")
    return p


@pytest.fixture
def provider(tmp_path, monkeypatch):
    p = _provider(tmp_path, monkeypatch)
    yield p
    p.shutdown()


def _store(p, target_category="general"):
    ids = {text: p._store.add_fact(text) for text in WORDS + OTHERS}
    ids[TARGET] = p._store.add_fact(TARGET, category=target_category)
    return ids


def _meaning(ids, sims: dict, texts: dict | None = None, only=None, weight=0.3):
    """*sims* by text (0.1 for the rest); *texts* — what the vector was made from, when not the fact's own text;
    *only* — the facts that have a vector at all."""
    order = [t for t in ids if only is None or t in only]
    return embeddings.Meaning([ids[t] for t in order], np.array([sims.get(t, 0.1) for t in order]), weight,
                              [embeddings.text_hash((texts or {}).get(t, t)) for t in order])


def _search(p, query, meaning, monkeypatch, **args):
    monkeypatch.setattr(p, "_meaning_of", lambda q, before_turn: meaning)
    raw = p.handle_tool_call("fact_store", {"action": "search", "query": query, "limit": 3, **args})
    found = json.loads(raw)
    assert "results" in found, raw
    return found["results"]


def _split(got):
    return ([f["fact_id"] for f in got if f.get("match") != "meaning"],
            [f["fact_id"] for f in got if f.get("match") == "meaning"])


def test_a_fact_closer_than_the_words_is_shown_after_them(provider, monkeypatch):
    ids = _store(provider)
    got = _search(provider, QUERY, _meaning(ids, {**{t: 0.40 for t in WORDS}, TARGET: 0.80}), monkeypatch)
    words, extra = _split(got)
    assert sorted(words) == sorted(ids[t] for t in WORDS)  # the words filled the limit: 0.14.3 never asked meaning
    assert extra == [ids[TARGET]] and got[-1]["similarity"] == 0.8
    assert provider._pending[provider._scope_key][ids[TARGET]] == 1  # counted as recall, like the rest


def test_not_shown_when_no_closer_than_the_words(provider, monkeypatch):
    ids = _store(provider)
    meaning = _meaning(ids, {WORDS[0]: 0.78, WORDS[1]: 0.40, WORDS[2]: 0.40, TARGET: 0.80})  # beats by 0.02 < 0.06
    words, extra = _split(_search(provider, QUERY, meaning, monkeypatch))
    assert sorted(words) == sorted(ids[t] for t in WORDS) and extra == []


def test_not_shown_when_it_does_not_stand_out(provider, monkeypatch):
    ids = _store(provider)
    flat = {t: 0.70 for t in OTHERS}  # everything about as close: z of the target below 1
    meaning = _meaning(ids, {**flat, **{t: 0.40 for t in WORDS}, TARGET: 0.72})
    assert meaning.z(ids[TARGET]) < embeddings.SEARCH_Z_FLOOR
    words, extra = _split(_search(provider, QUERY, meaning, monkeypatch))
    assert sorted(words) == sorted(ids[t] for t in WORDS) and extra == []


def test_not_shown_when_it_is_about_someone_else(provider, monkeypatch):
    ids = _store(provider)
    meaning = _meaning(ids, {**{t: 0.40 for t in WORDS}, TARGET: 0.80})
    query = "When is the garden backup for Peter?"
    target = provider._retriever._rows([ids[TARGET]], None, 0.0)[0]
    assert not provider._retriever.names_fit(query, target)  # "Peter" is named, the fact is about Lena
    assert provider._retriever.names_fit(QUERY, target)
    words, extra = _split(_search(provider, query, meaning, monkeypatch))
    assert words and extra == []


def test_only_the_closest_three_are_looked_at(provider, monkeypatch):
    ids = _store(provider)
    closer = {OTHERS[0]: 0.90, OTHERS[1]: 0.89, OTHERS[2]: 0.88}  # three facts ahead of the target
    meaning = _meaning(ids, {**{t: 0.40 for t in WORDS}, **closer, TARGET: 0.85})
    _, extra = _split(_search(provider, QUERY, meaning, monkeypatch))
    assert extra == [ids[OTHERS[0]], ids[OTHERS[1]], ids[OTHERS[2]]]  # closest first; the 4th is not reached


def test_filters_apply(tmp_path, monkeypatch):
    p = _provider(tmp_path, monkeypatch, embedding_min_score=0.85)
    try:
        ids = _store(p, target_category="project")
        meaning = _meaning(ids, {**{t: 0.40 for t in WORDS}, TARGET: 0.80})
        assert _split(_search(p, QUERY, meaning, monkeypatch))[1] == []  # below embedding_min_score
    finally:
        p.shutdown()
    p = _provider(tmp_path / "b", monkeypatch)
    try:
        ids = _store(p, target_category="project")
        meaning = _meaning(ids, {**{t: 0.40 for t in WORDS}, TARGET: 0.80})
        assert _split(_search(p, QUERY, meaning, monkeypatch, category="general"))[1] == []  # another category
        p._store.update_fact(ids[TARGET], trust_delta=-0.4)  # 0.5 -> 0.1, under the default floor of 0.3
        assert _split(_search(p, QUERY, meaning, monkeypatch))[1] == []  # below the trust floor
    finally:
        p.shutdown()


def test_not_shown_when_its_text_changed_since_the_vector(provider, monkeypatch):
    ids = _store(provider)
    meaning = _meaning(ids, {**{t: 0.40 for t in WORDS}, TARGET: 0.80}, texts={TARGET: "Lena's birthday is in May"})
    assert _split(_search(provider, QUERY, meaning, monkeypatch))[1] == []


def test_words_without_a_vector_count_as_neutral(provider, monkeypatch):
    ids = _store(provider)
    meaning = _meaning(ids, {TARGET: 0.80}, only=OTHERS + [TARGET])  # the word matches have no vector yet
    assert meaning.neutral == pytest.approx(0.1)
    words, extra = _split(_search(provider, QUERY, meaning, monkeypatch))
    assert len(words) == 3 and extra == [ids[TARGET]]


def test_shown_once_when_the_fill_already_took_it(provider, monkeypatch):
    ids = _store(provider)
    meaning = _meaning(ids, {**{t: 0.40 for t in WORDS}, TARGET: 0.95})
    got = _search(provider, QUERY, meaning, monkeypatch, limit=10)  # room left: by_meaning fills first
    assert [f["fact_id"] for f in got].count(ids[TARGET]) == 1


def test_not_on_a_search_as_of_a_day(provider, monkeypatch):
    ids = _store(provider)
    meaning = _meaning(ids, {**{t: 0.40 for t in WORDS}, TARGET: 0.80})
    monkeypatch.setattr(provider, "_meaning_of", lambda q, before_turn: meaning)
    raw = provider.handle_tool_call("fact_store", {"action": "search", "query": QUERY, "as_of": "2026-01-01"})
    assert '"meaning"' not in raw


def test_prefetch_does_not_use_it(provider, monkeypatch):
    """Before a turn a fact arrives unasked: only by_meaning's bar admits one, never beyond_words."""
    ids = _store(provider)
    meaning = _meaning(ids, {**{t: 0.40 for t in WORDS}, TARGET: 0.80})
    monkeypatch.setattr(provider, "_meaning_of", lambda q, before_turn: meaning)

    def _called(*a, **k):
        raise AssertionError("beyond_words before a turn")
    monkeypatch.setattr(provider._retriever, "beyond_words", _called)
    block = provider.prefetch(QUERY, session_id="q")
    assert any(f"#{ids[t]} " in block for t in WORDS)  # recall ran through (an exception would leave it empty)


def test_no_words_no_beyond(provider):
    ids = _store(provider)
    meaning = _meaning(ids, {TARGET: 0.80})
    assert provider._retriever.beyond_words(meaning, "anything", [], k=3, beat=0.06, z_min=1.0) == []
