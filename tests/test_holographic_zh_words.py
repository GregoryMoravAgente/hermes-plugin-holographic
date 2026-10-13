"""Chinese words from jieba's dictionary: text whose Han is Chinese is split into words, a pair of characters across a
seam between two words is not that pair (和服 in 我和服务员, 研究生 in 研究生命), one-character words are searched alone
(猫, 药, 税); Traditional text is split folded; Japanese text, names the dictionary lacks and a copy without the data
file are compared as before."""

from __future__ import annotations

import gzip

import pytest

pytest.importorskip("numpy")
pytest.importorskip("snowballstemmer")

from hermes_plugin_holographic import text
from hermes_plugin_holographic.retrieval import FactRetriever
from hermes_plugin_holographic.store import MemoryStore

DIM = 512


def _clear():
    for cached in (text.fold, text._fold_ja, text.analyze, text._courtesy_clause, text.zh_words):
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


def _alone(query):
    """The one-character words a query searches on their own."""
    return {g for g, _ in text.query_grams(query) if len(g) == 1}


@pytest.mark.parametrize("run, words", [
    ("南京市长江大桥", ("南京市", "长江大桥")),
    ("我和服务员", ("我", "和", "服务员")),
    ("研究生命", ("研究", "生命")),
    ("研究生命科学", ("研究", "生命科学")),
    ("表哥在上海读研究生", ("表哥", "在", "上海", "读", "研究生")),
])
def test_classic_splits(run, words):
    assert text.zh_words(run) == words


def test_seams_are_marked_in_the_text_and_in_the_query():
    seam = text.SEAM
    assert text.analyze("我和服务员确认过").seamed == f"我和{seam}服务员{seam}确认过"
    assert text.analyze("研究生命科学").seamed == f"研究{seam}生命科学"
    grams = dict(text.query_grams("我和服务员说了"))
    assert f"和{seam}服" in grams and "和服" not in grams and "服务" in grams
    # next to a content character standing alone no seam: 看 | 牙医 still holds 看牙
    assert text.SEAM not in text.analyze("下午去看牙医").seamed


def test_traditional_text_is_split_folded():
    assert text.analyze("學校長期招生").seamed == text.analyze("学校长期招生").seamed == f"学校{text.SEAM}长期{text.SEAM}招生"


@pytest.mark.parametrize("fact, query", [
    ("上次聚餐我和服务员确认过，包厢订在周六晚上。", "和服放在哪？"),  # 和服 only across 和 | 服务员
    ("舅舅在大学研究生命科学，周末很少休息。", "研究生毕业要几年？"),  # 研究生 only across 研究 | 生命
    ("爷爷的老朋友住在南京市长江路附近。", "新市长是谁？"),  # 市长 only across 南京市 | 长江路
    ("學校長期招收籃球特長生，每年九月考試。", "校长姓什么？"),  # 校长 only across 學校 | 長期, Traditional
])
def test_a_pair_across_a_seam_is_no_hit(store, fact, query, monkeypatch):
    store.add_fact(fact)
    assert _recalled(store, query) == []
    monkeypatch.setattr(text, "_ZH_WORDS", False)  # pairs alone: the false hit this replaces
    _clear()
    assert _recalled(store, query) != []


@pytest.mark.parametrize("target, trap, query", [
    ("晓燕有一件和服，放在衣柜最上层。", "上次聚餐我和服务员确认过，包厢订在周六晚上。", "和服放在哪？"),
    ("表哥在上海读研究生，明年六月毕业。", "舅舅在大学研究生命科学，周末很少休息。", "研究生什么时候毕业？"),
    ("思琪學校的校長姓陳，以前是數學老師。", "学校长期招收篮球特长生，每年九月考试。", "校长姓什么？"),
])
def test_the_word_wins_over_the_pair_across_a_seam(store, target, trap, query):
    target_id = store.add_fact(target)
    store.add_fact(trap)
    assert _ids(store, query)[0] == target_id
    assert _recalled(store, query) == [target_id]


def test_a_query_pair_across_a_seam_does_not_find_the_word(store):
    store.add_fact("晓燕有一件和服，是去京都旅游时买的。")
    assert _recalled(store, "我和服务员说了不要放香菜。") == []


def test_a_pair_across_a_seam_meets_the_same_seam():
    grams = text.query_grams("猫几岁了？")
    assert (f"岁{text.SEAM}了", 0.5) in grams
    assert text.gram_coverage(grams, text.analyze("家里的橘猫今年三岁了。"), True) > \
        text.gram_coverage(grams, text.analyze("表姐开了一家猫粮网店。"), True)


@pytest.mark.parametrize("fact, distractor, query", [
    ("奶奶每天早饭后吃一片降压药。", "公司下周组织体检，要空腹抽血。", "药一天吃几次？"),
    ("公司的税每个季度报一次，会计姓韩。", "银行的理财经理每个月打电话推荐基金。", "税是谁在报？"),
    ("家里的橘猫叫咪咪，今年三岁了。", "表姐开了一家猫粮网店，生意不错。", "猫几岁了？"),
    ("家里的橘貓叫咪咪，今年三歲了。", "表姐開了一家貓糧網店，生意不錯。", "貓幾歲了？"),
])
def test_one_character_words_are_searched_alone(store, fact, distractor, query):
    target = store.add_fact(fact)
    store.add_fact(distractor)
    assert _ids(store, query)[0] == target
    assert target in _recalled(store, query)


def test_which_one_character_words_are_searched_alone():
    assert _alone("药一天吃几次？") == {"药", "吃"}
    assert _alone("建国喝什么茶？") == {"喝", "茶"}
    assert not _alone("我们去看三次")  # function characters, the commonest words (去, 看), numerals
    assert "王" not in _alone("王浩然的牙医在哪？")  # a surname
    # two side by side are more likely a name the dictionary lacks: their pair stands for them
    assert not _alone("怡君的会议在哪？") and ("怡君", 1.0) in text.query_grams("怡君的会议在哪？")
    assert "桂" not in _alone("桂芳吃什么药？") and "药" in _alone("桂芳吃什么药？")


def test_names_the_dictionary_lacks_are_still_found(store):
    target = store.add_fact("思琪的好朋友叫林可馨，住在同一栋楼。")
    store.add_fact("思琪的同学下周过生日。")
    assert _ids(store, "林可馨住哪？")[0] == target
    assert target in _recalled(store, "可馨是谁的朋友？")


def test_japanese_text_is_compared_as_before():
    for phrase in ("子どもの学校は8時に始まる", "申込は明日", "学校は何時から"):
        assert not text.is_chinese(phrase)
        assert text.analyze(phrase).seamed == ""
        lowered = text._fold_text(phrase.lower())
        plain = [g for run in text.UNSEGMENTED.findall(lowered) for g in text.run_grams(run) if g[1]]
        assert text.query_grams(phrase) == list(dict(plain).items())
    # a Japanese query is matched as a substring, in a Chinese fact too
    grams = text.query_grams("研究生は何人？")
    assert text.gram_coverage(grams, text.analyze("舅舅在大学研究生命科学。")) > 0


def test_korean_text_is_not_split():
    assert not text.is_chinese("학교 学校") and text.analyze("학교 研究生命").seamed == ""


def test_the_dictionary():
    table = text._zh_table()
    assert len(table.words) > 300_000 and table.words["研究生"] < 0 and table.longest["南"] >= 4
    assert "王" in table.names and "猫" not in table.names
    assert "學" not in table.words and "学" in table.words  # folded to Simplified
    with gzip.open(text._ZH_FILE, "rt", encoding="utf-8") as fh:
        head = fh.read(400)
    assert "jieba" in head and "MIT" in head


def test_switch_off(monkeypatch):
    monkeypatch.setattr(text, "_ZH_WORDS", False)
    assert not text.is_chinese("研究生命") and text.analyze("研究生命科学").seamed == ""
    assert text.query_grams("药一天吃几次？") == [("药一", 0.5), ("一天", 0.5), ("天吃", 1.0), ("吃几", 0.5), ("几次", 0.5)]


def test_a_copy_without_the_data_file_still_works(monkeypatch, tmp_path, store):
    monkeypatch.setattr(text, "_ZH_FILE", tmp_path / "missing.txt.gz")
    monkeypatch.setattr(text, "_ZH_TABLE", None)
    assert text.zh_words("研究生命") == ("研", "究", "生", "命")
    assert text.SEAM not in text.analyze("我和服务员确认过").seamed
    assert not _alone("药一天吃几次？")
    target = store.add_fact("表哥在上海读研究生，明年六月毕业。")
    assert target in _ids(store, "表哥在上海读什么？") and target in _recalled(store, "表哥在上海读什么？")
