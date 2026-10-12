"""Build data/ru_derivation.tsv.gz from UDer 1.1 DerivBase.Ru: direct parent-child links, as Snowball stem pairs,
kept only when their shape is a derivation (suffix extension, or a regular consonant alternation)."""
import gzip, sys, re, snowballstemmer
st = snowballstemmer.stemmer("russian")
src, dst = sys.argv[1], sys.argv[2]
rows = [l.split("\t") for l in gzip.open(src, "rt", encoding="utf-8").read().splitlines() if l.strip()]
by_id = {r[0]: r for r in rows}
CYR = re.compile(r"^[а-яё]+$")
SHORT_EXT = ("н", "ов", "ев", "ск", "ин")           # what a three-letter stem may grow by: ноч → ночн, лет → летн
ALT = {"г": "ж", "к": "ч", "х": "ш", "д": "ж", "т": "ч", "з": "ж", "с": "ш", "ц": "ч"}  # друг → друж, собак → собач
def stem(w):
    return st.stemWord(w.replace("ё", "е"))
def shape_ok(a, b):
    s, l = sorted((a, b), key=len)
    if len(s) < 3 or s == l:
        return False
    if l.startswith(s):
        extra = l[len(s):]
        return extra in SHORT_EXT if len(s) == 3 else len(extra) <= 5
    if len(s) >= 4 and l[:len(s) - 1] == s[:-1] and ALT.get(s[-1]) == l[len(s) - 1] and len(l) - len(s) <= 4:
        return True
    if len(s) >= 5 and s.endswith(("ст", "ск")) and l[:len(s) - 2] == s[:-2] and l[len(s) - 2:len(s) - 1] == "щ":
        return True
    return False
pairs = set()
for r in rows:
    if not r[6] or r[6] not in by_id:
        continue
    child, parent = r[2].lower(), by_id[r[6]][2].lower()
    if CYR.match(child) and CYR.match(parent):
        a, b = stem(child), stem(parent)
        if shape_ok(a, b):
            pairs.add(tuple(sorted((a, b))))
header = ("# Russian derivation pairs (Snowball stems), from DerivBase.Ru in UDer 1.1 (ru-DerivBaseRU), Apache-2.0,\n"
          "# https://github.com/s231644/DerivBaseRu. Changes: direct parent-child links only, both words as Snowball\n"
          "# stems, kept when their shape is a derivation (suffix extension; a three-letter stem only by н/ов/ев/ск/ин;\n"
          "# a regular consonant alternation). See LICENSES/DerivBase.Ru-Apache-2.0.txt.\n")
out = (header + "\n".join(f"{a}\t{b}" for a, b in sorted(pairs)) + "\n").encode("utf-8")
open(dst, "wb").write(gzip.compress(out, 9, mtime=0))
print(len(pairs), "pairs;", len(out), "bytes raw;", len(gzip.compress(out, 9, mtime=0)), "gz")
