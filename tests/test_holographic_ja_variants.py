"""Japanese spelling variants (表記揺れ) from SudachiDict's synonym source: in text with kana a variant compares as the
word's representative spelling (子ども / 子供, 引越し / 引っ越し, コンピュータ / コンピューター); Chinese text, words inside
other words, fillers and greetings are left alone; stored text keeps its spelling; a copy without the data works."""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("snowballstemmer")

from hermes_plugin_holographic import text
from hermes_plugin_holographic.retrieval import FactRetriever
from hermes_plugin_holographic.store import MemoryStore

DIM = 512


def _clear():
    for cached in (text.fold, text._fold_ja, text.analyze, text._courtesy_clause):
        cached.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_caches():
    _clear()
    yield
    _clear()


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "facts.db"), hrr_dim=DIM)
    yield s
    s.close()


def _ids(store, query, limit=5):
    return [f["fact_id"] for f in FactRetriever(store, hrr_dim=DIM).search(query, min_trust=0, limit=limit)]


def _recalled(store, query):
    r = FactRetriever(store, hrr_dim=DIM)
    return [f["fact_id"] for f in r.relevant(query, r.search(query, min_trust=0, limit=10), 0.3)]


def _same(a, b):  # the folded text (a fact the variants changed also keeps its spelling, on a line of its own)
    return text.analyze(a).text.split("\n")[0] == text.analyze(b).text.split("\n")[0]


@pytest.mark.parametrize("a, b", [
    ("子どもの学校", "子供の学校"),
    ("引越しは3月", "引っ越しは3月"), ("引越は3月", "引っ越しは3月"),
    ("申込書を出す", "申し込み書を出す"),
    ("コンピュータが壊れた", "コンピューターが壊れた"),
    ("ウィルスに感染した", "ウイルスに感染した"),
    ("打合せは火曜日", "打ち合わせは火曜日"),
    ("いっしょに行く", "一緒に行く"),
    ("子どもたちが来た", "子供達が来た"),  # 子ども -> 子供, then 子供たち -> 子供達
])
def test_spelling_variants_compare_as_one(a, b):
    assert _same(a, b)


@pytest.mark.parametrize("phrase", [
    "べんきょうする", "きょうだいがいる",  # きょう (今日) inside other words
    "アイスを食べた", "カスタマイズする",  # イス, カスタマ inside longer katakana words
    "白い草が生えた", "話し終える",  # い草, し終える after a kanji: okurigana
    "日本語をはなす", "いつも元気",  # a hiragana homophone, and いつも (何時も holds 何時, "what time")
])
def test_kana_inside_other_words_is_left_alone(phrase):
    folded = text.analyze(phrase).text
    assert folded == text._fold_text(phrase, False)


def test_words_at_their_edges_are_folded():
    assert _same("きょうは雨", "今日は雨") and _same("イスに座る", "椅子に座る") and _same("い草のマット", "藺草のマット")


def test_representative_and_folded_text_stay_as_they_are():
    for phrase in ("コンピューターウイルス", "支払いました", "セキュリティーポリシー", "引っ越しの予定"):
        assert text.analyze(phrase).text == text._fold_text(phrase, False)
    for phrase in ("子どもたちが来た", "コンピュータウイルス", "申込みは明日", "セキュリティポリシーを読む"):
        once = text._fold_text(phrase)
        assert text._fold_text(once) == once


def test_chinese_text_is_never_touched():
    # no kana: a kanji-only variant (申込, 支払) and Traditional characters keep to the Han fold alone
    for phrase in ("申込表格在桌上", "支払方法", "學校的申込"):
        assert text.analyze(phrase).text == text._fold_text(phrase, False)
    assert text.analyze("申込は明日").text != text._fold_text("申込は明日", False)


def test_a_word_without_kana_folds_with_its_text():
    """Spaces between Japanese words: 申込 has no kana itself, the text has, and the word folds as the text does."""
    words = text.words("申込 締切 いつ？")
    assert words[:2] == [text._fold_text("申し込み", False), text._fold_text("締め切り", False)]


def test_fillers_and_greetings_compare_as_written():
    for phrase in [*text.FILLER_RUNS, *text.GREETINGS]:
        if text._KANA.search(phrase):
            assert text._fold_text(phrase) == phrase
    assert text.is_greeting("よろしくお願いします") and text.is_greeting("ありがとうございます")


@pytest.mark.parametrize("fact, distractor, query", [
    ("子供たちは毎朝7時半に家を出る。", "毎朝7時に犬の散歩で家を出る。", "子どもは何時に家を出る？"),
    ("クライアントとの打ち合わせは毎週火曜日の午後2時。", "クライアントへの請求書は毎月末に送る。", "クライアントとの打合せは何曜日？"),
    ("事務所のコンピューターがウィルスに感染した。", "事務所のプリンターが故障した。", "事務所のコンピュータのウイルスは？"),
])
def test_other_spelling_is_found_and_recalled(store, fact, distractor, query):
    target = store.add_fact(fact)
    store.add_fact(distractor)
    assert _ids(store, query)[0] == target
    assert target in _recalled(store, query)


def test_katakana_word_is_not_found_inside_a_longer_one(store):
    store.add_fact("リビングのイスを先週新しくした。")
    assert _recalled(store, "アイスクリームはどこ？") == []


def test_stored_text_keeps_its_spelling(store):
    fact_id = store.add_fact("子どもの引越しは来月。")
    assert store.read("SELECT content FROM facts WHERE fact_id = ?", (fact_id,))[0][0] == "子どもの引越しは来月。"


def test_switch_off(monkeypatch):
    monkeypatch.setattr(text, "_JA_VARIANTS", False)
    assert not _same("子どもの学校", "子供の学校")


def test_a_copy_without_the_data_file_still_works(monkeypatch, tmp_path, store):
    monkeypatch.setattr(text, "_JA_FILE", tmp_path / "missing.tsv.gz")
    monkeypatch.setattr(text, "_JA_TABLE", None)
    assert text.analyze("子どもの学校").text == "子どもの学校"
    target = store.add_fact("子どもの学校は8時に始まる。")
    assert target in _ids(store, "子どもの学校は何時？")
