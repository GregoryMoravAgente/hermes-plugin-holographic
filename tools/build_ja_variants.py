"""Build ja_variants.tsv.gz from SudachiDict's synonym source (src/main/text/synonyms.txt): Japanese spelling variants
(表記揺れ) of one word, each mapped to its representative spelling. Only spellings of the same word are taken, never
synonyms (other words of the group), translations, aliases or abbreviations of another form.

    py -3.12 -I tools/build_ja_variants.py <synonyms.txt> <ja_variants.tsv.gz> <SudachiDict tag> <commit>

Columns of synonyms.txt (SudachiDict docs/synonyms.md): 0 group, 1 noun/predicate, 2 expansion control (0 always,
1 not a trigger itself, 2 never: kept as deletion history), 3 lexeme number in the group, 4 form type within the lexeme
(0 representative word, 1 translation, 2 alias, 3 old name, 4 misuse), 5 abbreviation within that form (0
representative, 1 alphabet abbreviation, 2 other abbreviation), 6 spelling within that abbreviation form (0
representative spelling, 1 alphabet / romanisation, 2 variant spelling, 3 misspelling), 7 domain, 8 headword."""
import collections
import csv
import gzip
import re
import sys
import unicodedata

src, dst, tag, commit = sys.argv[1:5]
JAPANESE = re.compile(r"^[ぁ-ゟァ-ヿ㐀-䶿一-鿿豈-﫿々〆〇]+$")
HIRAGANA = re.compile(r"^[ぁ-ゟ]+$")
# Hiragana spellings whose representative is also another common word: 何時も / 何時頃 hold 何時 ("what time"), 大分 is
# also Ōita. Folded, "いつも" would meet every "何時に".
EXCLUDE = {"いつも", "いつごろ", "だいぶ", "だいぶん"}

rows = [r for r in csv.reader(open(src, encoding="utf-8", newline="")) if r and any(x.strip() for x in r)]
clusters = collections.defaultdict(list)  # one spelling cluster: same group, lexeme, form type and abbreviation form
for r in rows:
    clusters[(r[0], r[3], r[4], r[5])].append(r)

stats = collections.Counter()
edges = {}  # variant -> {targets}
order = {}  # form -> first position in the file, for a stable choice
for key, members in clusters.items():
    reps = [m for m in members if m[6] == "0"]
    if not any(m[6] in ("2", "3") for m in members):
        continue  # no variant spelling
    if len(reps) != 1:
        stats["clusters skipped: not exactly one representative spelling"] += 1
        continue
    rep = reps[0]
    target = unicodedata.normalize("NFC", rep[8]).lower()
    if rep[2] == "2" or not JAPANESE.match(target):
        stats["clusters skipped: representative never expanded, or not in Japanese script"] += 1
        continue
    order.setdefault(target, len(order))
    for m in members:
        if m[6] not in ("2", "3"):
            continue  # the representative itself, or an alphabet / romanised spelling
        variant = unicodedata.normalize("NFC", m[8]).lower()
        if m[2] != rep[2]:
            stats["variants dropped: expansion flag differs from the representative's (an ambiguous spelling)"] += 1
        elif not JAPANESE.match(variant) or variant.startswith("・") or variant.endswith("・"):
            stats["variants dropped: not Japanese script only"] += 1
        elif len(variant) < 2 or (HIRAGANA.match(variant) and len(variant) < 3):
            stats["variants dropped: one character, or hiragana of two"] += 1
        elif HIRAGANA.match(variant) and m[1] == "2" and m[2] != "0":
            # a verb or adjective in hiragana that Sudachi marks as no trigger: a homophone (はなす is 話す, 離す, 放す)
            stats["variants dropped: hiragana predicate marked ambiguous (homophones)"] += 1
        elif variant in EXCLUDE:
            stats["variants dropped: listed (representative is another common word)"] += 1
        elif variant != target:
            edges.setdefault(variant, set()).add(target)
            order.setdefault(variant, len(order))

ambiguous = {v for v, ts in edges.items() if len(ts) > 1}  # one spelling of two different words: left alone
stats["variants dropped: a spelling of two different words"] = len(ambiguous)
parent = {}


def find(x):
    while parent.get(x, x) != x:
        parent[x] = parent.get(parent[x], parent[x])
        x = parent[x]
    return x


for v, ts in edges.items():  # a representative that is another cluster's variant: one word, one spelling
    if v not in ambiguous:
        a, b = find(v), find(next(iter(ts)))
        if a != b:
            parent[max(a, b, key=order.get)] = min(a, b, key=order.get)
indegree = collections.Counter(next(iter(ts)) for v, ts in edges.items() if v not in ambiguous)
components = collections.defaultdict(set)
for v, ts in edges.items():
    if v not in ambiguous:
        components[find(v)] |= {v, next(iter(ts))}
pairs = {}
for members in components.values():
    targets = [t for t in members if indegree[t]]
    canonical = min(targets, key=lambda t: (-indegree[t], order[t]))  # the most-used representative, then the first
    stats["words with more than one representative (joined)"] += len(targets) > 1
    if len(canonical) == 1:
        # たまご -> 卵: a lone kanji has no bigram of its own, so two texts that both wrote たまご would stop meeting
        stats["variants dropped: the representative is one character"] += len(members) - 1
        continue
    for form in members - {canonical}:
        if form not in ambiguous:
            pairs[form] = canonical
stats["variant spellings kept"] = len(pairs)
stats["words (representative spellings)"] = len(set(pairs.values()))

header = (f"# Japanese spelling variants (表記揺れ) -> the representative spelling of the word, from SudachiDict {tag}\n"
          f"# (commit {commit}), src/main/text/synonyms.txt, https://github.com/WorksApplications/SudachiDict,\n"
          "# Copyright (c) 2017-2023 Works Applications Co., Ltd., Apache License 2.0 (LICENSES/SudachiDict-Apache-2.0.txt).\n"
          "# Changes: only spellings of one word (same group, lexeme, form type and abbreviation form; variant and\n"
          "# misspelling rows) with the expansion flag of their representative; no synonyms, translations, aliases,\n"
          "# abbreviations or alphabet spellings; Japanese script only; no single characters or two-letter hiragana;\n"
          "# no hiragana verb or adjective that SudachiDict marks as no expansion trigger (homophones); not いつも,\n"
          "# いつごろ, だいぶ, だいぶん (何時, 大分 are other words); no word whose representative is one character; a\n"
          "# spelling of two words dropped; representatives linked through a variant joined under one spelling.\n"
          f"# {len(pairs)} variants of {len(set(pairs.values()))} words. Built by tools/build_ja_variants.py.\n")
body = "".join(f"{v}\t{t}\n" for v, t in sorted(pairs.items()))
data = (header + body).encode("utf-8")
open(dst, "wb").write(gzip.compress(data, 9, mtime=0))
for k, n in sorted(stats.items()):
    print(f"{n:6d}  {k}")
print(len(pairs), "pairs;", len(data), "bytes raw;", len(gzip.compress(data, 9, mtime=0)), "gz")
