"""0.17.0: an alias "X aka Y" in a fact's text gave is a hint for text, never the target of an action someone takes with
a name (HANDOFF Open work, review 0.16.2). Before: after facts about Alice Smith, "Alice Smith aka Bob Brown" made Bob
Brown her alias, and entities=["Bob Brown"] then filed the second person's facts under her; rename and delete_entity by
"Bob Brown" renamed and deleted her. Names here are made up."""
from __future__ import annotations

import json

import pytest

from hermes_plugin_holographic import _WRITE_ACTIONS, HolographicMemoryProvider
from hermes_plugin_holographic.store import MemoryStore

DIM = 64


def _provider(tmp_path, **kwargs):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "m.db"), "hrr_dim": DIM})
    p.initialize(session_id="s", **kwargs)
    return p


@pytest.fixture
def p(tmp_path):
    provider = _provider(tmp_path)
    yield provider
    provider.shutdown()


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "facts.db"), hrr_dim=DIM)
    yield s
    s.close()


def _call(provider, **args):
    return json.loads(provider.handle_tool_call("fact_store", args))


def _add(provider, content, **args):
    out = _call(provider, action="add", content=content, **args)
    assert "error" not in out, out
    return out


def _names(store, fact_id):
    return sorted(r[0] for r in store.read("SELECT e.name FROM fact_entities fe JOIN entities e ON e.entity_id = fe.entity_id "
                                           "WHERE fe.fact_id = ?", (fact_id,)))


def _entity(store, name):
    rows = [r for r in store.list_entities(limit=1000) if r["name"] == name]
    return rows[0] if rows else None


def _aliases(store):
    return sorted((r[0], r[1]) for r in store.read(
        "SELECT ea.alias, e.name FROM entity_aliases ea JOIN entities e ON e.entity_id = ea.entity_id"))


def _assert_consistent(store):
    """Links and aliases point at what exists, no two entities share a key, and both alias representations (the
    entity_aliases rows and the comma-separated column older versions read) say the same."""
    assert not store.read("SELECT 1 FROM fact_entities WHERE entity_id NOT IN (SELECT entity_id FROM entities)")
    assert not store.read("SELECT 1 FROM fact_entities WHERE fact_id NOT IN (SELECT fact_id FROM facts)")
    assert not store.read("SELECT 1 FROM entity_aliases WHERE entity_id NOT IN (SELECT entity_id FROM entities)")
    assert not store.read("SELECT name_key FROM entities WHERE name_key IS NOT NULL GROUP BY name_key HAVING COUNT(*) > 1")
    from hermes_plugin_holographic.store import entity_key
    for r in store.read("SELECT entity_id, name, aliases FROM entities"):
        rows = {entity_key(a[0]) for a in store.read("SELECT alias FROM entity_aliases WHERE entity_id = ?", (r[0],))}
        column = {entity_key(a) for a in (r[2] or "").split(",") if a.strip()} - {entity_key(r[1])}
        assert rows == column, (r[1], rows, column)


def _probe(provider, name):
    return sorted(f["fact_id"] for f in _call(provider, action="probe", entity=name)["results"])


AKA = [("en", "Alice Smith", "Bob Brown", "{} aka {}", "lives in Paris", "plays chess", "He works at the bank"),
       ("en", "Alice Smith", "Bob Brown", "{} a.k.a. {}", "lives in Paris", "plays chess", "He works at the bank"),
       ("en", "Alice Smith", "Bob Brown", "{} also known as {}", "lives in Paris", "plays chess", "He works at the bank"),
       ("ru", "Мария Иванова", "Пётр Сидоров", "{} aka {}", "живёт в Казани", "играет в шахматы", "Он работает в банке")]


def _story(provider, x, y, aka, one, two):
    a = _add(provider, f"{x} {one}")["fact_id"]
    b = _add(provider, f"{x} {two}")["fact_id"]
    c = _add(provider, aka.format(x, y))["fact_id"]
    return a, b, c


# -- the report -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang,x,y,aka,one,two,other", AKA, ids=["aka", "a.k.a.", "also known as", "ru"])
def test_a_declared_name_splits_from_an_alias_a_text_gave(p, lang, x, y, aka, one, two, other):
    a, b, c = _story(p, x, y, aka, one, two)
    assert _aliases(p._store) == [(y, x)]
    out = _add(p, other, entities=[y])
    assert f"alias {{entity: '{x}', content: '{y}'}}" in out["split"][y]  # 0.16.3: no answer, filed under X
    d = out["fact_id"]
    s = p._store
    assert _names(s, d) == [y] and _names(s, a) == [x] and _names(s, b) == [x]
    assert _names(s, c) == sorted([x, y])
    assert _aliases(s) == []
    assert _entity(s, x)["declared"] is False and _entity(s, y)["declared"] is True
    assert _probe(p, y) == sorted([c, d])
    _assert_consistent(s)


def test_a_text_fact_linked_through_the_alias_moves_with_the_split(p):
    a, b, c = _story(p, "Alice Smith", "Bob Brown", "{} aka {}", "lives in Paris", "plays chess")
    berlin = _add(p, "Bob Brown lives in Berlin")["fact_id"]
    assert _names(p._store, berlin) == ["Alice Smith"]  # what a text gives a text: unchanged
    out = _add(p, "He works at the bank", entities=["Bob Brown"])
    assert out["split"]
    assert _names(p._store, berlin) == ["Bob Brown"] and _names(p._store, a) == ["Alice Smith"]
    _assert_consistent(p._store)


def test_declaring_y_with_the_fact_that_says_x_aka_y_makes_no_alias(p):
    _add(p, "Alice Smith lives in Paris")
    out = _add(p, "Alice Smith aka Bob Brown", entities=["Bob Brown"])
    assert "split" not in out and _aliases(p._store) == []
    assert _names(p._store, out["fact_id"]) == ["Alice Smith", "Bob Brown"]


def test_two_declared_names_report_both(p):
    _add(p, "Alice Smith aka Bob Brown")
    _add(p, "Carol King aka Dan Dale")
    out = _add(p, "They met at the bank", entities=["Bob Brown", "Dan Dale"])
    assert set(out["split"]) == {"Bob Brown", "Dan Dale"}
    assert _names(p._store, out["fact_id"]) == ["Bob Brown", "Dan Dale"]


# -- a declared owner, a chain, forget, rebuild -------------------------------------------------------------------------

def test_an_owner_someone_declared_keeps_what_the_text_cannot_tell(p):
    """Review 0.17.0 (Codex P1.1): the file does not record why a declared link exists. A fact whose text names Y
    goes to Y even from a declared owner; one whose text names neither stays and is listed to check."""
    _add(p, "Alice Smith lives in Paris", entities=["Alice Smith"])
    _add(p, "Alice Smith aka Bob Brown")
    berlin = _add(p, "Bob Brown lives in Berlin")["fact_id"]
    bank = _add(p, "Works at the bank", entities=["Alice Smith"])["fact_id"]
    out = _add(p, "He plays the violin", entities=["Bob Brown"])
    assert _names(p._store, berlin) == ["Bob Brown"]
    assert _names(p._store, bank) == ["Alice Smith"]
    assert f"Facts [{bank}]" in out["split"]["Bob Brown"] and "check" in out["split"]["Bob Brown"]
    assert _entity(p._store, "Alice Smith")["declared"] is True
    _assert_consistent(p._store)


def test_splitting_the_middle_of_a_chain_takes_what_hangs_on_it(p):
    """Review 0.17.0 (Codex P1.2): "Alice aka Bob", "Bob aka Carl": split Bob, and Carl is Bob's, not Alice's."""
    _add(p, "Alice Smith aka Bob Brown")
    _add(p, "Bob Brown aka Carl Cole")
    tea = _add(p, "Carl Cole likes green tea")["fact_id"]
    assert _names(p._store, tea) == ["Alice Smith"]
    out = _call(p, action="split", entity="Bob Brown")
    assert out["from"] == "Alice Smith" and out["was"] == "aka"
    assert _aliases(p._store) == [("Carl Cole", "Bob Brown")]
    assert _names(p._store, tea) == ["Bob Brown"]
    again = _call(p, action="split", entity="Bob Brown")  # an entity's own name now: refused, nothing changes
    assert "not an alias" in again["error"] and _aliases(p._store) == [("Carl Cole", "Bob Brown")]
    _assert_consistent(p._store)


def test_declaring_the_end_of_a_chain_leaves_the_rest(p):
    _add(p, "Alice Smith aka Bob Brown")
    _add(p, "Bob Brown aka Carl Cole")
    out = _add(p, "He plays the violin", entities=["Carl Cole"])
    assert out["split"]["Carl Cole"]
    assert _aliases(p._store) == [("Bob Brown", "Alice Smith")]
    assert _names(p._store, out["fact_id"]) == ["Carl Cole"]
    assert _entity(p._store, "Alice Smith")["declared"] is False


def test_forgetting_the_aka_fact_after_a_split_leaves_two_people(p):
    a, b, c = _story(p, "Alice Smith", "Bob Brown", "{} aka {}", "lives in Paris", "plays chess")
    d = _add(p, "He works at the bank", entities=["Bob Brown"])["fact_id"]
    _call(p, action="forget", fact_id=c)
    assert _names(p._store, d) == ["Bob Brown"] and _names(p._store, a) == ["Alice Smith"]
    assert _entity(p._store, "Alice Smith")["declared"] is False  # 0.16.3: Alice had Bob's declaration
    _assert_consistent(p._store)


def test_a_rebuild_keeps_a_split(p):
    _story(p, "Alice Smith", "Bob Brown", "{} aka {}", "lives in Paris", "plays chess")
    _add(p, "He works at the bank", entities=["Bob Brown"])
    before = {f: _names(p._store, f) for f, in p._store.read("SELECT fact_id FROM facts")}
    for _ in range(2):
        out = _call(p, action="rebuild_entities")
        assert out["links_removed"] == out["links_added"] == 0
    assert {f: _names(p._store, f) for f, in p._store.read("SELECT fact_id FROM facts")} == before
    assert _aliases(p._store) == []


# -- actions by such a name -------------------------------------------------------------------------------------------

def test_alias_rename_and_delete_by_a_name_a_text_gave_are_refused(p):
    _story(p, "Alice Smith", "Bob Brown", "{} aka {}", "lives in Paris", "plays chess")
    state = (p._store.list_entities(limit=100), _aliases(p._store))
    for args in ({"action": "alias", "entity": "Bob Brown", "content": "Bobby B"},
                 {"action": "rename", "entity": "Bob Brown", "content": "Robert Brown"},
                 {"action": "delete_entity", "entity": "Bob Brown"},
                 {"action": "delete_entity", "entities": ["Zed Quux", "Bob Brown"]},
                 {"action": "alias", "entity": "Carol King", "content": "Bob Brown"}):
        error = _call(p, **args)["error"]
        assert "Alice Smith" in error and "split" in error and "Nothing was changed" in error, (args, error)
    assert (p._store.list_entities(limit=100), _aliases(p._store)) == state
    assert not p._store.read("SELECT 1 FROM entity_tombstones")  # 0.16.3: Alice Smith was deleted


def test_alias_confirms_a_name_a_text_gave(p):
    _story(p, "Alice Smith", "Bob Brown", "{} aka {}", "lives in Paris", "plays chess")
    out = _call(p, action="alias", entity="Alice Smith", content="Bob Brown")
    assert out["confirmed"] is True and _entity(p._store, "Alice Smith")["text_aliases"] == []
    added = _add(p, "He works at the bank", entities=["Bob Brown"])
    assert "split" not in added and _names(p._store, added["fact_id"]) == ["Alice Smith"]
    renamed = _call(p, action="rename", entity="Bob Brown", content="Robert Brown")
    assert renamed["name"] == "Robert Brown"


@pytest.mark.parametrize("source", ["aka", "user", "legacy"])
def test_split_detaches_any_alias(p, source):
    if source == "aka":
        _add(p, "Alice Smith aka Bob Brown")
    else:
        _add(p, "Alice Smith lives in Paris")
        _call(p, action="alias", entity="Alice Smith", content="Bob Brown")
        if source == "legacy":
            p._store._conn.execute("UPDATE entity_aliases SET source = NULL")
            p._store._conn.commit()
    berlin = _add(p, "Bob Brown lives in Berlin")["fact_id"]
    out = _call(p, action="split", entity="Bob Brown")
    assert out["from"] == "Alice Smith" and out["was"] == source and out["entity"] == "Bob Brown"
    assert berlin in out["facts_moved"] and _names(p._store, berlin) == ["Bob Brown"]
    assert _entity(p._store, "Bob Brown")["declared"] is True and _aliases(p._store) == []
    _assert_consistent(p._store)


def test_split_refuses_an_unknown_name_an_entity_s_own_name_and_a_reader(tmp_path, p):
    _add(p, "Alice Smith aka Bob Brown")
    assert "entities lists the names" in _call(p, action="split", entity="Zed Quux")["error"]
    assert "not an alias" in _call(p, action="split", entity="Alice Smith")["error"]
    assert "split" in _WRITE_ACTIONS
    reader = _provider(tmp_path, agent_context="subagent")
    assert "read-only" in _call(reader, action="split", entity="Bob Brown")["error"]
    reader.shutdown()
    assert _aliases(p._store) == [("Bob Brown", "Alice Smith")]


# -- what the agent sees ----------------------------------------------------------------------------------------------

def test_entities_review_and_stats_show_names_a_text_gave(p):
    _story(p, "Alice Smith", "Bob Brown", "{} aka {}", "lives in Paris", "plays chess")
    listed = _call(p, action="entities")
    alice = [e for e in listed["entities"] if e["name"] == "Alice Smith"][0]
    assert alice["aliases"] == alice["text_aliases"] == ["Bob Brown"] and listed["text_aliases"] == 1
    review = _call(p, action="review")
    assert [(c["entity"], c["alias"], len(c["facts"])) for c in review["alias_candidates"]] == \
           [("Alice Smith", "Bob Brown", 1)]
    assert "split {entity: <alias>}" in review["hint"]
    assert _call(p, action="stats")["text_aliases"] == 1
    _call(p, action="alias", entity="Alice Smith", content="Bob Brown")
    assert _call(p, action="review")["alias_candidates"] == [] and _call(p, action="stats")["text_aliases"] == 0


# -- declarations that were dropped before 0.17.0 -------------------------------------------------------------------

def test_a_declaration_with_a_text_already_stored_still_counts(p):
    """Review 0.17.0 (Codex P1.3): an add of a stored text, a correct that changes no text (or only the date), a
    change into a text another fact holds — each dropped its entities until 0.16.3."""
    _add(p, "Alice Smith aka Bob Brown")
    tea = _add(p, "He likes green tea")["fact_id"]
    again = _add(p, "He likes green tea", entities=["Bob Brown"])
    assert again["status"] == "exists" and again["split"] and _names(p._store, tea) == ["Bob Brown"]
    chess = _add(p, "She plays chess")["fact_id"]
    out = _call(p, action="correct", fact_id=chess, content="She plays chess", entities=["Carol King"])
    assert out["status"] == "unchanged" and _names(p._store, chess) == ["Carol King"]
    out = _call(p, action="correct", fact_id=chess, valid_from="2026-01-02", entities=["Dan Dale"])
    assert out["status"] == "corrected" and _names(p._store, chess) == ["Carol King", "Dan Dale"]
    walk = _add(p, "He walks the dog")["fact_id"]
    out = _call(p, action="change", fact_id=walk, content="He likes green tea", entities=["Eve Ames"])
    assert out["status"] == "exists" and "Eve Ames" in _names(p._store, tea)


def test_auto_extracted_text_gives_a_hint_a_declared_name_still_splits(store):
    store.add_fact_status("we decided that Alice Smith aka Bob Brown will handle it")
    assert _aliases(store) == [("Bob Brown", "Alice Smith")]
    with store.collect_splits() as splits:
        fact_id, _ = store.add_fact_status("He works at the bank", entities=["Bob Brown"])
    assert _names(store, fact_id) == ["Bob Brown"] and splits["Bob Brown"]["from"] == "Alice Smith"


def test_a_split_and_an_alias_back_in_russian(p):
    """An honest second name: split, then the agent says it is one person; probe finds both again."""
    a = _add(p, "Маша любит плавать", entities=["Маша"])["fact_id"]
    _add(p, "Маша aka Мария Петровна")
    b = _add(p, "Работает в школе", entities=["Мария Петровна"])["fact_id"]
    assert _names(p._store, b) == ["Мария Петровна"] and _names(p._store, a) == ["Маша"]
    _call(p, action="alias", entity="Маша", content="Мария Петровна")
    assert a in _probe(p, "Мария Петровна") and b in _probe(p, "Маша")
    _assert_consistent(p._store)


def test_a_split_reencodes_and_leaves_no_tombstone(p):
    _story(p, "Alice Smith", "Bob Brown", "{} aka {}", "lives in Paris", "plays chess")
    p._store._conn.execute("INSERT OR REPLACE INTO memory_banks (bank_name, vector, dim, fact_count) "
                           "VALUES ('cat:general', x'00', 1, 1)")
    p._store._conn.commit()
    _call(p, action="split", entity="Bob Brown")
    assert not p._store.read("SELECT 1 FROM memory_banks") and not p._store.read("SELECT 1 FROM entity_tombstones")


def test_a_failed_call_reports_no_split(p):
    _add(p, "Alice Smith aka Bob Brown")
    out = _call(p, action="correct", fact_id=999, content="x y z", entities=["Bob Brown"])
    assert "error" in out and "split" not in out
    assert _aliases(p._store) == [("Bob Brown", "Alice Smith")]
