"""0.5.0: Spanish, Hindi, Arabic, French, Bengali, Portuguese and Indonesian — per-language stems and function words,
folding, elision and hyphens, Arabic clitics, Indic scripts the FTS5 index splits, Indonesian prefixes, greetings,
negation, and a deterministic order for an entity's facts."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("snowballstemmer")

from hermes_plugin_holographic import HolographicMemoryProvider
from hermes_plugin_holographic.retrieval import FactRetriever
from hermes_plugin_holographic.store import MemoryStore
from hermes_plugin_holographic.text import analyze, fold, is_greeting, is_identifier, query_terms, words

DIM = 512


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "facts.db"), hrr_dim=DIM)
    yield s
    s.close()


@pytest.fixture
def provider(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "memory_store.db"), "hrr_dim": DIM})
    p.initialize(session_id="s")
    yield p
    p.shutdown()


def _r(store):
    return FactRetriever(store, hrr_dim=DIM)


def _call(provider, **args):
    return json.loads(provider.handle_tool_call("fact_store", args))


def _found(store, query, **kw):
    return [f["content"] for f in _r(store).search(query, min_trust=0, **kw)]


# -- text ----------------------------------------------------------------------------------------------------------

def test_latin_language_follows_the_function_words():
    assert analyze("¿Dónde está la gata de Ana?").langs == ("es",)
    assert analyze("Où est la chatte d'Ana ?").langs == ("fr",)
    assert analyze("Onde está a gata da Ana?").langs == ("pt",)
    assert analyze("Di mana kucing yang hilang itu?").langs == ("id",)
    assert analyze("Where is the cat?").langs == ("en",)
    assert len(analyze("Proxmox cluster upgrade").langs) == 5  # no function word: every Latin stemmer


def test_a_function_word_of_another_language_stays_informative():
    # "son" is French for "his"; in English it is a word to search for
    assert [t.word for t in query_terms("where does my son study")] == ["son", "study"]


def test_folding():
    assert fold("está") == "esta" and fold("ação") == "acao"
    assert fold("й") == "й"  # Cyrillic keeps its breve
    assert fold("إيفا") == fold("ايفا") and fold("قطة") == "قطه" and fold("مرحبًا") == "مرحبا"
    assert fold("कॉफ़ी") == fold("कॉफी")  # nukta
    assert fold("২০২৭") == "2027" and fold("٢٠٢٧") == "2027"


def test_elision_inversion_and_hyphens():
    assert words("le rendez-vous d'Eva") == ["le", "rendez", "eva"]  # the same on both sides of a comparison
    assert {"rendez-vous", "vous"} <= analyze("le rendez-vous d'Eva").bag
    assert words("Comment Anton prend-il son café ?") == ["comment", "anton", "prend", "son", "cafe"]
    assert words("Quand Eva a-t-elle natation") == ["quand", "eva", "a", "natation"]
    assert words("anak-anak bermain") == ["anak", "bermain"]
    assert words("hr-ovh-এর ব্যাকআপ") == ["hr-ovh", "এর", "ব্যাকআপ"]
    assert words("IT-тусовка в пятницу") == ["it", "тусовка", "в", "пятницу"]
    assert "sextas" in analyze("nas sextas-feiras").bag
    assert not is_identifier("prend-il") and is_identifier("pve-01") and is_identifier("v0.21.6")


def test_unicode_punctuation_is_stripped():
    assert words("¿Qué hace HR24؟") == ["que", "hace", "hr24"]
    assert words("यह क्या है।") == ["यह", "क्या", "है"]


@pytest.mark.parametrize("text", ["¡Buenos días!", "Bonjour !", "Bom dia!", "Selamat pagi", "صباح الخير",
                                  "सुप्रभात!", "শুভ সকাল", "muchas gracias", "شكرًا", "ধন্যবাদ"])
def test_greetings_in_every_language(text):
    assert is_greeting(text)


# -- search --------------------------------------------------------------------------------------------------------

def test_spanish_word_forms_and_accents(store):
    store.add_fact("La gata de la familia se llama Mia")
    store.add_fact("El vuelo sale mañana")
    assert _found(store, "¿Cómo se llama el gato?")[0] == "La gata de la familia se llama Mia"
    assert _found(store, "manana vuelo")[0] == "El vuelo sale mañana"  # typed without the tilde


def test_french_inversion_is_not_a_required_identifier(provider):
    _call(provider, action="add", content="Anton boit son café noir, sans sucre", entities=["Anton"])
    _call(provider, action="add", content="Anton roule en scooter à Pattaya", entities=["Anton"])
    assert "café noir" in provider.prefetch("Comment Anton prend-il son café ?")


def test_arabic_clitics(store):
    store.add_fact("قطة العائلة اسمها ميا")
    store.add_fact("تدرس إيفا في مدرسة دولية")
    assert _found(store, "ما اسم القطة؟")[0] == "قطة العائلة اسمها ميا"  # article on one side only
    assert _found(store, "مدرسة لإيفا")[0] == "تدرس إيفا في مدرسة دولية"  # preposition glued to the name


def test_arabic_entity_mentioned_with_a_preposition(provider):
    _call(provider, action="add", content="إيفا لديها حساسية من الفول السوداني", entities=["إيفا"])
    assert "حساسية" in provider.prefetch("متى دروس السباحة لإيفا؟")


def test_arabic_and_indic_queries_never_reach_fts():
    assert FactRetriever._sanitize_fts_query("ما اسم القطة") == ""
    assert FactRetriever._sanitize_fts_query("बिल्ली किस नस्ल की है") == ""
    assert FactRetriever._sanitize_fts_query("HR24 क्या करती है") == '"hr24"'  # the identifier still does


def test_hindi_in_a_store_where_letters_are_everywhere(store):
    # unicode61 indexes Devanagari letter by letter; with many facts sharing letters, FTS ranking was noise
    for i in range(60):
        store.add_fact(f"पटाया में शुक्रवार की पार्टी नंबर {i} रात को होती है")
    target = store.add_fact("परिवार की बिल्ली ब्रिटिश शॉर्टहेयर नस्ल की है")
    assert _r(store).search("बिल्ली किस नस्ल की है?", min_trust=0, limit=3)[0]["fact_id"] == target
    coffee = store.add_fact("एंटोन ब्लैक कॉफ़ी पीते हैं")
    assert _r(store).search("एंटोन कॉफी", min_trust=0, limit=1)[0]["fact_id"] == coffee  # written without the nukta


def test_bengali_case_endings_and_digits(store):
    store.add_fact("পরিবার ২০২৭ সালের বসন্তে চিয়াং মাইতে চলে যাবে")
    store.add_fact("প্রতি শনিবার সকালে ইভার সাঁতারের ক্লাস হয়")
    assert _found(store, "চিয়াং মাই কবে")[0].startswith("পরিবার")
    assert _found(store, "2027")[0].startswith("পরিবার")
    assert _found(store, "ইভা সাঁতার")[0].startswith("প্রতি")


def test_indonesian_prefixes(store):
    store.add_fact("Skrip yang tidak terkait Hermes dijalankan dengan Python 3.12")
    store.add_fact("Kucing keluarga bernama Mia")
    assert _found(store, "bisa jalankan skrip apa?")[0].startswith("Skrip")
    assert _found(store, "kucingnya siapa namanya?")[0].startswith("Kucing")


def test_reason_with_an_unknown_arabic_word(store):
    store.add_fact_status("إيفا لديها حساسية من الفول السوداني", entities=["إيفا"])[0]
    store.add_fact_status("تدرس إيفا في مدرسة دولية", entities=["إيفا"])[0]
    assert [f["content"] for f in _r(store).reason(["إيفا", "الحساسية"])] == ["إيفا لديها حساسية من الفول السوداني"]


# -- gate, conflicts, entity facts ---------------------------------------------------------------------------------

def test_function_words_do_not_make_a_message_relevant(provider):
    _call(provider, action="add", content="La familia planea mudarse a Chiang Mai en la primavera de 2027")
    _call(provider, action="add", content="ईवा को मूंगफली से एलर्जी है")
    assert provider.prefetch("¿Cuál es la capital de Australia?") == ""
    assert provider.prefetch("ठीक है, धन्यवाद") == ""


@pytest.mark.parametrize("old,new", [
    ("Ana come carne todos los días", "Ana no come carne"),
    ("أنطون يشرب القهوة", "أنطون لا يشرب القهوة"),
    ("Ana makan daging", "Ana tidak makan daging"),
])
def test_negation_is_a_possible_conflict(provider, old, new):
    who = new.split()[0]
    first = _call(provider, action="add", content=old, entities=[who])["fact_id"]
    result = _call(provider, action="add", content=new, entities=[who])
    assert [c["fact_id"] for c in result.get("possible_conflicts", [])] == [first]


def test_entity_facts_come_newest_first_when_timestamps_tie(store):
    ids = [store.add_fact_status(f"Eva fact number {i}", entities=["Eva"])[0] for i in range(4)]
    store._conn.execute("UPDATE facts SET updated_at = '2026-01-01 00:00:00'")
    group = store.entity_ids("Eva")
    assert [f["fact_id"] for f in _r(store)._linked_facts([group], None, 0.0, 2)] == [ids[3], ids[2]]
