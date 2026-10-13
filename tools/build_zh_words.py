"""Build zh_words.txt.gz from jieba's dictionary (jieba/dict.txt: word, frequency, part of speech): the Chinese words
the segmenter in text.py splits text into, with their frequencies, folded as text is (Traditional -> Simplified).

    py -3.12 -I tools/build_zh_words.py <dict.txt> <zh_words.txt.gz> <jieba commit>

Kept: every entry written in Han characters only (single characters included: the segmenter needs their frequencies
too), NFC-normalised and folded with the plugin's own Traditional -> Simplified table (cjk_fold.py); entries that fold
onto one word add up. Dropped: the part of speech, except that single characters jieba tags as a person name (nr, nrt,
nrfg: 王, 李, 陈, most surnames) are listed, and entries with Latin letters or digits ("B超", "T恤": text.py compares
those by n-grams). A frequency is kept as one character, round(4 * ln f) written in 0-9A-Za-z (a step of a quarter of a
natural-log unit, about 28 %).

Format (UTF-8, gzip): lines starting with "#" are comments; the line "=<number>" is the natural log of the total of
the quantised frequencies (what the segmenter divides by); the line "!<characters>" lists the single characters tagged
as a person name; then one line per first character: the character, and for each word that begins with it a space,
the rest of the word (nothing for the character itself) and its frequency character. "研 Q 究g 究生U" is 研 (round(4 ln
f) = Q, 26), 研究 (g, 42) and 研究生 (U, 30). One line per character keeps the file small and its loading fast."""
import gzip
import math
import re
import runpy
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

src, dst, commit = sys.argv[1:4]
cjk = runpy.run_path(str(Path(__file__).resolve().parent.parent / "cjk_fold.py"))
FOLD = str.maketrans(cjk["TRADITIONAL"], cjk["SIMPLIFIED"])
HAN = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+$")
DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
SCALE = 4
NAME_TAGS = {"nr", "nrt", "nrfg"}

stats = Counter()
freq: dict[str, int] = {}
names: set[str] = set()
for line in open(src, encoding="utf-8"):
    parts = line.split()
    if len(parts) < 2:
        stats["lines skipped: no frequency"] += 1
        continue
    stats["entries read"] += 1
    word = unicodedata.normalize("NFC", parts[0]).lower()
    if not HAN.match(word):
        stats["entries dropped: not Han characters only"] += 1
        continue
    folded = word.translate(FOLD)
    stats["entries folded (Traditional -> Simplified)"] += folded != word
    stats["entries merged into another (same folded word)"] += folded in freq
    freq[folded] = freq.get(folded, 0) + int(parts[1])
    if len(folded) == 1 and len(parts) > 2 and parts[2] in NAME_TAGS:
        names.add(folded)

weights = {w: round(SCALE * math.log(f)) for w, f in freq.items()}
assert min(weights.values()) >= 0 and max(weights.values()) < len(DIGITS), "a frequency does not fit one character"
by_first = defaultdict(list)
for word in sorted(weights):
    by_first[word[0]].append(f"{word[1:]}{DIGITS[weights[word]]}")
lines = [first + "".join(" " + rest for rest in rests) for first, rests in sorted(by_first.items())]
by_len = Counter(len(w) for w in weights)
log_total = math.log(sum(math.exp(q / SCALE) for q in weights.values()))
stats["words kept"] = len(weights)
stats["words kept: one character"] = by_len[1]
stats["words kept: two or more characters"] = len(weights) - by_len[1]
stats["single characters tagged as a person name"] = len(names)

header = (f"# Chinese words and their frequencies, from jieba's dictionary jieba/dict.txt (commit {commit}),\n"
          "# https://github.com/fxsjy/jieba, Copyright (c) 2013 Sun Junyi, MIT License (LICENSES/jieba-MIT.txt).\n"
          "# Changes: entries in Han characters only (no entries with Latin letters or digits), folded Traditional ->\n"
          "# Simplified with cjk_fold.py (entries that fold onto one word add up), frequency quantised to\n"
          f"# round({SCALE} * ln f), the part of speech dropped except for the single characters tagged as a\n"
          f"# person name (nr, nrt, nrfg), listed on the line '!'. {len(weights)} words, {by_len[1]} of them one\n"
          "# character.\n"
          "# '=' is ln of the total of the quantised frequencies. Then one line per first character: the character,\n"
          "# then for each word beginning with it a space, the rest of the word and round(4 * ln f) as one character\n"
          f"# of {DIGITS}. Built by tools/build_zh_words.py.\n")
data = (header + f"={log_total:.6f}\n!{''.join(sorted(names))}\n" + "\n".join(lines) + "\n").encode("utf-8")
packed = gzip.compress(data, 9, mtime=0)
open(dst, "wb").write(packed)
for k, n in sorted(stats.items()):
    print(f"{n:7d}  {k}")
print(len(weights), "words;", len(data), "bytes raw;", len(packed), "gz")
