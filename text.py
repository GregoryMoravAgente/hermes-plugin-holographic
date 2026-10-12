"""Text normalisation shared by search, the prefetch gate, entity matching and duplicate checks.

Every word gets comparison keys: its folded form plus its stem in the language of the text, and two words match when
their keys meet, so "Маши" finds "Маша", "kucingnya" finds "kucing" and "لإيفا" finds "إيفا". The language comes from
the script (Cyrillic: Russian; Arabic script: Arabic; Devanagari: Hindi; Bengali script: Bengali) and, for Latin
script, from the function words the text uses ("the", "el", "le", "o", "yang": English, Spanish, French, Portuguese,
Indonesian); Latin text with none of them gets the stems of all five. Stems come from Snowball (English, Russian,
Spanish, French, Portuguese, Indonesian, Hindi), Larkey's light10 for Arabic (as in Lucene) next to Snowball's, and a
light suffix stripper for Bengali. Folding drops Latin accents, Arabic vowel marks and letter variants and the Indic
nukta, and turns native digits into ASCII. A stem is a search key, not a dictionary lemma. Russian and English
words also get root keys (word_roots: "ночные" / "ночь", "teacher" / "teaches"), matched at _DERIV_WEIGHT in search and
recall, never in duplicate checks. Identifiers (anything with
a digit, or a dotted / pathed token) are never stemmed and never stopwords. Chinese, Japanese, Korean and the scripts
of Southeast Asia are compared by character n-grams instead (see UNSEGMENTED)."""

from __future__ import annotations

import gzip
import re
import sys
import threading
import unicodedata
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path

from .cjk_fold import SIMPLIFIED, TRADITIONAL

try:  # pure Python, BSD; optional: without it words compare exactly (Arabic and Bengali keep their light stemmers)
    import snowballstemmer
    _STEMMERS = {code: snowballstemmer.stemmer(name) for code, name in (
        ("en", "english"), ("ru", "russian"), ("es", "spanish"), ("fr", "french"), ("pt", "portuguese"),
        ("id", "indonesian"), ("de", "german"), ("hi", "hindi"), ("ar", "arabic"))}
except ImportError:  # pragma: no cover - exercised only without the dependency
    _STEMMERS = {}

PUNCT = ".,;:!?\"'()[]{}#@<>«»“”„…"
_MIN_STEM = 3  # shorter stems match too much as prefixes ("ма*")
_LOOSE_FROM, _LOOSE_MIN = 7, 5  # stems this long also match on their first len-2 (at least 5) letters
_SHORT_LOOSE = True  # Russian stems of _SHORT_FROM-6 letters also match on their first len-1 letters ("покупа" / "покупк")
_SHORT_FROM = 6  # not five: "больш(ой)" would leave "боль", which begins "больница"
_ROOTS = True  # same-root keys: "ночь" / "ночной", "город" / "городской", "teach" / "teacher"
_DERIV_WEIGHT = 0.6  # a same-root word or a shorter shared beginning counts this much of an exact match

# -- scripts and folding -----------------------------------------------------------------------------------------

def _script(ch: str) -> str:
    o = ord(ch)
    if o < 0x250 or 0x1E00 <= o <= 0x1EFF:
        return "latin"
    if 0x400 <= o <= 0x52F:
        return "cyrillic"
    if 0x600 <= o <= 0x6FF or 0x750 <= o <= 0x77F or 0x8A0 <= o <= 0x8FF or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF:
        return "arabic"
    if 0x900 <= o <= 0x97F:
        return "devanagari"
    if 0x980 <= o <= 0x9FF:
        return "bengali"
    if 0xA00 <= o <= 0xDFF:
        return "indic"
    return "other"


def word_script(word: str) -> str:
    """Script of the first letter; "common" for a token without letters (numbers, symbols)."""
    return next((_script(c) for c in word if c.isalpha()), "common")


# Scripts the FTS5 unicode61 index cannot serve: it splits Indic words at their vowel signs (marks are not token
# characters), and Arabic attaches conjunctions, prepositions and the article to the front of a word, where a prefix
# query cannot see past them. Words in these scripts are matched against the text of every fact instead.
_SCAN_SCRIPTS = frozenset({"arabic", "devanagari", "bengali", "indic"})
_SCRIPT_LANGS = {"cyrillic": ("ru",), "arabic": ("ar",), "devanagari": ("hi",), "bengali": ("bn",)}
LATIN = ("en", "es", "fr", "pt", "id", "de")

_FOLD = str.maketrans({
    "œ": "oe", "æ": "ae",  # Latin ligatures ("cœur" = "coeur")
    **{c: "ا" for c in "أإآٱ"}, "ى": "ي", "ة": "ه", "ـ": None,  # Arabic letter variants, tatweel
    **{chr(c): None for c in range(0x64B, 0x660)}, "ٰ": None,  # Arabic vowel marks
    "़": None, "ँ": "ं",  # Devanagari nukta; candrabindu written as anusvara
    "়": None, "ঁ": None,  # Bengali nukta and candrabindu
    **{chr(base + d): str(d) for base in (0x660, 0x6F0, 0x966, 0x9E6) for d in range(10)},  # native digits
})
# Traditional Chinese characters compare as Simplified ones (學校 = 学校, 醫院 = 医院): a Taiwan user's fact and a mainland
# user's question, or the other way round, share their characters. OpenCC's table (cjk_fold.py); stored text unchanged.
_HAN_FOLD = True
_FOLD_HAN = {**_FOLD, **str.maketrans(TRADITIONAL, SIMPLIFIED)}


def _fold_text(text: str) -> str:
    if text.isascii():
        return text
    out, latin = [], False
    for c in unicodedata.normalize("NFD", text):
        if "̀" <= c <= "ͯ":
            if latin:
                continue  # an accent on a Latin letter; the breve of Cyrillic й stays
        else:
            latin = c.isascii() or _script(c) == "latin"
        out.append(c)
    return unicodedata.normalize("NFC", "".join(out)).translate(_FOLD_HAN if _HAN_FOLD else _FOLD)


@lru_cache(maxsize=65536)
def fold(word: str) -> str:
    """Comparison form of a lower-cased word: Latin accents dropped ("está" = "esta"), Arabic vowel marks, tatweel and
    letter variants normalised (أ إ آ -> ا, ى -> ي, ة -> ه), the Indic nukta dropped, native digits as ASCII, Traditional
    Chinese characters as Simplified."""
    return _fold_text(word)


def _folded(words: str) -> frozenset:
    return frozenset(fold(w) for w in words.split())


# -- function words ------------------------------------------------------------------------------------------------

STOP = {
"en": _folded("""
    a about above after again all am an and any are as at be because been before being between both but by can could
    did do does doing don down during each few for from further had has have having he her here hers herself him himself
    his how i if in into is it its itself just me more most my myself no nor not now of off on once only or other our
    ours ourselves out over own same she should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where which while who whom why will with
    would you your yours yourself yourselves ok okay yes yeah thanks thank please hi hello hey"""),
"ru": _folded("""
    а без более бы был была были было быть в вам вас весь во вот все всего всех вы где да даже для до его ее её если
    есть еще ещё же за здесь и из или им их к как ко когда кто ли либо мне может мы на над надо наш не него нее неё нет
    ни них но ну о об однако он она они оно от очень по под при про с со так также такой там те тебе тем то того тоже
    той только том ты у уже хотя чего чей чем что чтобы чье чья эта эти это этот я ага ок окей спасибо привет пожалуйста
    да нет ладно хорошо понял поняла давай конечно просто вообще кстати понятно ясно угу кого кому чему чем ведь вон
    чём всё моё твоё своё нём"""),
"es": _folded("""
    a al algo alguna algunas alguno algunos algún ante antes como cómo con contra cual cuál cuales cuando cuándo cuanto
    cuánto cuanta cuánta cuantos cuántos cuantas cuántas de del desde donde dónde adónde durante e el él ella ellas ellos
    en entre era eran eres es esa esas ese eso esos esta está estaba estaban estamos están estar estas estás este esto
    estos estoy fue fueron fui ha había han has hasta hay he hemos la las le les lo los más me mi mí mis mucho muchos
    muy nada ni no nos nosotros nuestra nuestro o os otra otras otro otros para pero poco por porque que qué quien
    quién quienes quiénes se sea ser si sí sin sobre sois somos son soy su sus también tanto te tenemos tener tengo
    tiene tienen ti tu tú tus un una unas uno unos usted ustedes vosotros y ya yo cada hacer hace hacen hago puedo
    puede pueden puedes podemos debo debe hola gracias vale ok bueno claro dale vamos"""),
"fr": _folded("""
    à a ai as au aux avec avez avoir avons c ça ce ceci cela celle celles celui ces cet cette chez comme comment d dans
    de des donc dont du elle elles en encore est et étaient était été être eu eux fait faire fais font il ils j je l la
    le les leur leurs lui m ma mais me même mes moi mon n ne ni nos notre nous on ont ou où par pas peu peut peux
    pouvez pour pourquoi qu quand que quel quelle quelles quels qui quoi s sa sans se ses si son sont sous suis sur t
    ta te tes toi ton tous tout toute toutes très tu un une vers vos votre vous y combien quelque lequel laquelle oui
    non merci salut bonjour accord ok va vais vas allons allez vont aller faut"""),
"pt": _folded("""
    a à ao aos as às o os um uma uns umas de do da dos das em no na nos nas num numa por pelo pela pelos pelas para pra
    com sem e ou mas que se não mais como quando onde quem qual quais quanto quanta quantos quantas porque porquê eu tu
    você vocês ele ela eles elas nós me te lhe lhes meu minha meus minhas teu tua seu sua seus suas nosso nossa este
    esta estes estas esse essa esses essas isto isso aquilo aquele aquela já também só muito é são era eram foi ser sou
    somos está estão estou estava estar tem têm tenho tinha ter vai vou vamos há pode posso podem poder devo deve devem
    algum alguma alguns algumas nenhum nenhuma cada outro outra ok obrigado obrigada oi olá sim valeu"""),
"id": _folded("""
    yang dan di ke dari ini itu dengan untuk tidak tak ada akan juga atau pada adalah ialah saya aku kamu engkau dia ia
    mereka kita kami anda sudah telah belum bisa dapat harus sedang masih lebih sangat apa siapa kapan mana bagaimana
    mengapa kenapa berapa apakah yaitu yakni oleh karena jika kalau agar supaya tetapi tapi namun hanya saja pun lagi
    para sebuah seorang setiap tiap semua banyak sedikit nya ya oke ok makasih halo hai sip seperti
    sebaiknya punya mau ingin boleh perlu nih dong kok sih deh"""),
"de": _folded("""
    der die das den dem des ein eine einer eines einem einen und oder aber nicht kein keine ist sind war waren bin
    bist sein hat haben habe hast hatte wird werden wurde ich du er sie es wir ihr mich mir dich dir ihn ihm uns euch
    sich mein meine dein deine seine ihre unser zu im in am an auf aus bei mit nach von vor für über unter um durch
    gegen ohne bis als wie wo was wer wen wem wann warum welche welcher welches viel viele noch schon nur auch so ja
    nein doch mal da dort hier dann denn dass weil wenn ob kann können soll sollen muss müssen will wollen darf gibt
    etwas ok okay danke bitte hallo"""),
"ar": _folded("""
    في من إلى الى على عن عمن مع هذا هذه ذلك تلك هؤلاء هو هي هم هن هما أنا نحن أنت أنتم كان كانت يكون تكون كانوا ما
    ماذا متى أين كيف لماذا لم لن لا ليس ليست قد كل بعض غير بين عند حتى إذا إن أن أو ثم بل لكن التي الذي الذين يتم تم
    لدى لديه لديها لدي لديك له لها لهم لك لي به بها فيه فيها منه منها عليه عليها و ف ب ل ك يا أيضا فقط جدا شكرا
    مرحبا حسنا تمام نعم أهلا هل أي كم يمكن يجب يفعل تفعل يقوم تقوم"""),
"hi": _folded("""
    का की के को में से पर है हैं था थी थे हो होता होती होते होगा होगी होंगे और या भी तो ही न नहीं यह वह ये वे वो इस उस
    इन उन इसे उसे मैं हम तुम आप मेरा मेरी मेरे हमारा हमारी हमारे तुम्हारा तेरा आपका आपकी आपके उसका उसकी उसके उनका
    उनकी उनके इसका इसकी इसके कौन क्या कब कहाँ कहां कैसे क्यों कितना कितनी कितने किस किसे किसी कोई कुछ एक लिए साथ बाद
    पहले जो जब तक अब यहाँ वहाँ सकता सकती सकते रहा रही रहे गया गई गए कर करता करती करते करना करें किया हाँ ठीक
    धन्यवाद नमस्ते शुक्रिया अच्छा ओके तरह वाला वाली वाले बहुत"""),
"bn": _folded("""
    এবং ও আর বা কিন্তু যে যা যিনি যারা এই ওই সেই এটা ওটা সেটা এটি ওটি সেটি সে তারা আমি আমরা তুমি তোমরা আপনি আপনারা
    তিনি তাঁরা আমার আমাদের তোমার তোমাদের আপনার আপনাদের তার তাদের তাঁর তাঁদের কে কী কি কেন কোন কোথায় কখন কবে কীভাবে
    কিভাবে কেমন কত কতটা কাকে কার একটি একটা এক জন্য সাথে সঙ্গে থেকে দিয়ে নিয়ে হয় হয়ে হলে হবে হচ্ছে ছিল ছিলেন আছে
    আছেন নেই না নয় হ্যাঁ ঠিক আচ্ছা ধন্যবাদ হ্যালো নমস্কার প্রতি করে করেন করা করছে যায় খুব এর এ তে র"""),
}
STOPWORDS = frozenset().union(*STOP.values())

# Question words that say nothing about the subject of a QUESTION ("какая сегодня погода": "какая" met "как" in every
# fact that says "считать как …"), but are part of a statement ("Иван знает, почему" is not "Иван знает, куда"): left
# out of a query's terms only, never out of a fact's words, duplicate or conflict checks.
QUERY_ONLY_STOP = _folded("""какой какая какое какие какого какому каким каком какую каких какими каков который которая
    которое которые которого которой которому которым котором которую которых сколько куда откуда почему зачем""")

NEGATORS = _folded("""nicht kein keine keinen keinem keiner nie niemals nichts
    not no never nobody nothing cannot don't doesn't didn't isn't aren't wasn't weren't won't can't
    couldn't shouldn't haven't hasn't dont doesnt didnt isnt cant wont не нет никогда ни nunca jamás tampoco nadie
    ningún ninguna ninguno ne pas jamais aucun aucune non não nem nenhum nenhuma tidak tak bukan belum jangan لا لم لن
    ليس ليست नहीं न मत না নয় নেই নি""")


# Function words a claim still needs ("with sugar" / "without sugar"), and conjunctions after which a negation belongs
# to another clause ("likes cats but not dogs").
CLAIM_WORDS = _folded("with without avec sans con sin com sem dengan tanpa mit ohne مع بدون साथ बिना সাথে ছাড়া с без")
CONTRASTIVE = _folded("but however pero sino mais mas porém tapi tetapi namun aber sondern jedoch لكن बल्कि लेकिन किंतु "
                      "কিন্তু но")


def _greeting_key(text: str) -> str:
    folded = _fold_text(unicodedata.normalize("NFC", text or "").lower())
    return "".join(c for c in folded if c.isalnum() or unicodedata.category(c)[0] == "M")


# Whole-message greetings and courtesies: they name a time of day ("good morning") but ask nothing, so they must not
# recall facts that mention mornings. Compared without case, accents, punctuation and spaces.
GREETINGS = frozenset(_greeting_key(g) for g in """
    goodmorning|goodafternoon|goodevening|goodnight|morning|gm|gn|hello|hi|hey|hiall|hellothere|hithere|howareyou|
    thankyou|thanksalot|thankssomuch|
    доброеутро|добрыйдень|добрыйвечер|спокойнойночи|добройночи|утро|привет|приветик|всемпривет|здравствуй|здравствуйте|
    какдела|спасибо|спасибобольшое|благодарю|
    早上好|早安|上午好|中午好|下午好|晚上好|晚安|你好|您好|大家好|嗨|谢谢|多谢|非常感谢|
    hola|buenos días|buen día|buenas|buenas tardes|buenas noches|hola a todos|qué tal|gracias|muchas gracias|mil gracias|
    bonjour|bonsoir|salut|coucou|bonne nuit|bonne journée|bonne soirée|bonjour à tous|salut à tous|ça va|merci|
    merci beaucoup|
    olá|oi|bom dia|boa tarde|boa noite|olá a todos|oi pessoal|tudo bem|obrigado|obrigada|muito obrigado|muito obrigada|
    halo|hai|selamat pagi|selamat siang|selamat sore|selamat malam|halo semua|apa kabar|terima kasih|makasih|
    terima kasih banyak|
    مرحبا|أهلا|أهلا وسهلا|السلام عليكم|صباح الخير|مساء الخير|تصبح على خير|كيف حالك|شكرا|شكرا جزيلا|
    नमस्ते|नमस्कार|हैलो|हेलो|सुप्रभात|शुभ प्रभात|शुभ रात्रि|आप कैसे हैं|धन्यवाद|बहुत धन्यवाद|शुक्रिया|
    নমস্কার|হ্যালো|শুভ সকাল|সুপ্রভাত|শুভ রাত্রি|আসসালামু আলাইকুম|কেমন আছেন|কেমন আছো|ধন্যবাদ|অনেক ধন্যবাদ|
    guten morgen|guten tag|guten abend|gute nacht|hallo zusammen|servus|moin|danke schön|dankeschön|vielen dank|
    danke sehr|wie geht's|
    おはよう|おはようございます|こんにちは|こんばんは|おやすみ|おやすみなさい|ありがとう|ありがとうございます|
    どうもありがとう|どうも|よろしく|よろしくお願いします|はじめまして|
    안녕|안녕하세요|안녕하십니까|좋은 아침|좋은 아침이에요|고마워|고마워요|고맙습니다|감사합니다|감사해요|잘 자|잘 자요|
    สวัสดี|สวัสดีครับ|สวัสดีค่ะ|อรุณสวัสดิ์|อรุณสวัสดิ์ครับ|อรุณสวัสดิ์ค่ะ|ขอบคุณ|ขอบคุณครับ|ขอบคุณค่ะ|ขอบคุณมาก|
    ขอบคุณมากครับ|ขอบคุณมากค่ะ|ราตรีสวัสดิ์""".split("|"))


# Pieces a courtesy message is made of in English and Russian, beyond the whole greetings above: thanks, sign-offs,
# reactions, "how are you"-questions and the words that pad them ("today", "there", "so much"). A message whose every
# clause is a run of these pieces ("Hi, good morning!", "Thanks a lot, that's all for now", "Good morning! How are you
# today?", "спасибо, на этом всё") asks nothing. A clause with any other word ("how is Tim doing at school") is a
# question. Compared like GREETINGS (no case, accents, spaces or punctuation inside a clause).
COURTESY = frozenset(_greeting_key(g) for g in """
    good|morning|afternoon|evening|night|day|hi|hello|hey|heya|hiya|yo|sup|whatsup|howdy|there|all|everyone|guys|
    team|folks|mate|buddy|dear|again|today|tonight|so|much|a lot|very|too|really|
    thanks|thank you|thx|ty|cheers|appreciate it|much appreciated|you're welcome|no problem|np|no worries|
    that's all|that's it|for now|all good|all set|ok|okay|k|kk|great|cool|nice|awesome|perfect|excellent|super|
    sounds good|got it|understood|noted|sure|yes|yeah|yep|no|nope|lol|haha|hahaha|hehe|wow|oh|ah|hm|hmm|
    see you|see ya|see you later|see you tomorrow|bye|goodbye|bye bye|take care|talk later|later|
    have a nice day|have a good day|have a great day|have a good one|have a good evening|have a good night|
    have a good weekend|have a nice weekend|happy friday|happy monday|happy weekend|happy holidays|
    how are you|how are you doing|how are things|how's it going|how is it going|how have you been|what's new|
    how's your day|how's your day going|how is your day going|how was your day|how's your week|how's your week going|
    how was your weekend|how's everything|you good|well done|good job|nice one|
    sleep well|sweet dreams|fine|i'm fine|i'm good|and you|you too|same to you|have a great weekend|have a great week|
    have a nice week|have a good week|happy weekend|happy birthday|congrats|congratulations|good luck|
    добрый|доброе|доброго|утро|утра|день|дня|вечер|вечера|ночи|привет|приветик|приветствую|здравствуй|здравствуйте|
    хай|всем|всех|ребята|друзья|еще раз|ещё раз|снова|сегодня|
    спасибо|спс|благодарю|большое|огромное|пожалуйста|не за что|отлично|супер|класс|круто|здорово|прекрасно|понятно|
    ясно|хорошо|ок|окей|ладно|договорились|принято|понял|поняла|ага|угу|да|нет|ха|хаха|ахах|ахаха|лол|ого|ух ты|
    на этом всё|на этом все|это всё|это все|пока всё|пока все|всё|все|пока|до встречи|до завтра|увидимся|до связи|
    хорошего дня|хорошего вечера|хороших выходных|спокойной ночи|удачи|всего доброго|
    как дела|как ты|как вы|как у тебя дела|как у вас дела|как поживаешь|как поживаете|как прошёл день|как прошел день|
    как твой день|как настроение|что нового|как сам|как сама|как твоя неделя|как прошла неделя|как выходные|
    как прошли выходные|с пятницей|с праздником|с выходными|с днём рождения|с днем рождения|поздравляю|
    сладких снов|спи спокойно|и тебе|и вам|взаимно|у меня всё хорошо|у меня все хорошо|нормально|""".split("|"))
_CLAUSE = re.compile(r"[.,;:!?…\n\r]+")
# Pieces that only pad a courtesy ("good", "day", "today", "fine", "сегодня", "всё"): a clause of these alone is a
# question or an answer ("fine?" about a fine, "сегодня?" about a lesson), not small talk.
_COURTESY_PADDING = frozenset(_greeting_key(g) for g in """
    good|afternoon|evening|night|day|there|all|everyone|guys|team|folks|mate|buddy|dear|again|today|tonight|so|much|
    a lot|very|too|really|fine|later|
    добрый|доброе|доброго|утра|день|дня|вечер|вечера|ночи|всем|всех|ребята|друзья|еще раз|ещё раз|снова|сегодня|
    большое|огромное|всё|все|пока|нормально""".split("|"))


def _clause_cuts(clause: str) -> tuple[str, frozenset]:
    """The clause folded as _greeting_key folds it, and the positions a piece may start or end at: word boundaries,
    and any position next to a character of a script written without spaces (Chinese, Japanese, Thai)."""
    folded = _fold_text(unicodedata.normalize("NFC", clause).lower())
    key, cuts, gap = [], {0}, True
    for c in folded:
        if c.isalnum() or unicodedata.category(c)[0] == "M":
            if key and (gap or UNSEGMENTED.match(c) or UNSEGMENTED.match(key[-1])):
                cuts.add(len(key))
            key.append(c)
            gap = False
        else:
            gap = True
    cuts.add(len(key))
    return "".join(key), frozenset(cuts)


@lru_cache(maxsize=4096)
def _courtesy_clause(clause: str) -> int:
    """0: the clause is not made of greeting and courtesy pieces alone (pieces start and end at word boundaries, so
    "history" is not "hi" + "story"); 1: it is, of padding only; 2: it is, with a greeting, thanks, sign-off, reaction
    or "how are you"."""
    key, cuts = _clause_cuts(clause)
    pieces = COURTESY | GREETINGS
    longest = max(len(p) for p in pieces)
    best = [-1] * (len(key) + 1)  # best[i]: -1 unreachable, 1 padding only, 2 with a real courtesy piece
    best[0] = 1
    for end in sorted(cuts):
        for start in sorted(c for c in cuts if end - longest <= c < end):
            piece = key[start:end]
            if best[start] > 0 and piece in pieces:
                best[end] = max(best[end], best[start], 1 if piece in _COURTESY_PADDING else 2)
    return max(best[len(key)], 0) if key else 0


def is_greeting(text: str) -> bool:
    """A whole-message greeting, or a message every clause of which is made only of greeting and courtesy pieces, with
    at least one that is more than padding ("Hi, good morning!", "Thanks, that's all for now", "спасибо, на этом всё";
    not "fine?" or "сегодня?")."""
    if _greeting_key(text) in GREETINGS:
        return True
    clauses = [c for c in _CLAUSE.split(text or "") if _greeting_key(c)]
    kinds = [_courtesy_clause(c) for c in clauses]
    return bool(kinds) and all(kinds) and max(kinds) == 2


def is_identifier(token: str) -> bool:
    """'pve-01', 'v0.21.6', 'gpt-4o', 'host:22', '/etc/hosts', 'hr-ovh': kept exact, never stemmed or dropped, and a
    message naming one is about that one. A hyphenated word with a letter outside ASCII is a word ("кто-то",
    "peut-être"), and so is any letters-only hyphenated word in Spanish, French, Portuguese or Indonesian text (see
    analyze); French inversion, Portuguese enclisis and reduplication are taken apart before this is asked."""
    return any(c.isdigit() for c in token) or (len(token) > 2 and (
        any(c in token for c in "._:/") or ("-" in token and token.isascii())))


# -- stemming ------------------------------------------------------------------------------------------------------

# Arabic light10 (Larkey, Ballesteros and Connell; the stemmer Lucene ships): one prefix, then each suffix in turn,
# leaving at least two letters. Input is folded, so ة is already ه.
_AR_PREFIXES = ("ال", "وال", "بال", "كال", "فال", "لل", "و")
_AR_SUFFIXES = ("ها", "ان", "ات", "ون", "ين", "يه", "ه", "ي")


def _arabic_light(word: str) -> str:
    for prefix in _AR_PREFIXES:
        if word.startswith(prefix) and len(word) >= (4 if prefix == "و" else len(prefix) + 2):
            word = word[len(prefix):]
            break
    for suffix in _AR_SUFFIXES:
        if word.endswith(suffix) and len(word) >= len(suffix) + 2:
            word = word[:-len(suffix)]
    return word


# Bengali: Snowball has no stemmer; one inflectional suffix (case, plural, classifier, common verb endings) is
# stripped, longest first, leaving at least three characters (so "মাটি", soil, does not become "মা", mother). A bare
# word that loses a letter it does not inflect with ("শুক্রবার" -> "শুক্রবা") still meets its inflected forms through
# its unstripped form.
_BN_SUFFIXES = sorted({fold(unicodedata.normalize("NFC", s)) for s in """
    গুলোকে গুলোতে গুলোর গুলো গুলিকে গুলিতে গুলির গুলি দেরকে দের েরা রা টাকে টিকে টাতে টিতে টার টির টা টি খানা খানি
    য়েরা য়ের য়ে য় ের কে তে েতে র ে এর এ চ্ছিলাম চ্ছিলেন চ্ছিল চ্ছেন চ্ছিস চ্ছি চ্ছে ছিলাম ছিলেন ছিল ছেন ছিস ছি ছে
    েছিলাম েছিলেন েছেন েছি েছে লাম লেন বেন বে বো তাম তেন ওয়ার ওয়া ানো""".split()}, key=len, reverse=True)


def _bengali_stem(word: str) -> str:
    for suffix in _BN_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


_DEFAULT_LANG = {"latin": "en", "cyrillic": "ru", "arabic": "ar", "devanagari": "hi", "bengali": "bn"}


@lru_cache(maxsize=65536)
def stem(word: str, lang: str | None = None) -> str:
    """Stem of a folded, lower-cased word in *lang* (by its script when None); the word itself for identifiers, scripts
    without a stemmer, or without snowballstemmer."""
    if is_identifier(word):
        return word
    lang = lang or _DEFAULT_LANG.get(word_script(word))
    if lang == "ar":
        return _arabic_light(word)
    if lang == "bn":
        return _bengali_stem(word)
    stemmer = _STEMMERS.get(lang)
    return fold(stemmer.stemWord(word)) if stemmer else word


# A conjunction (و ف) and/or a preposition (ب ك ل) glued to a word that starts with alef, as names and the article
# do: "لإيفا" is "for Eva", "ولإيفا" "and for Eva". A lexical first letter ("كتاب") is left alone.
_AR_CLITICS = re.compile(r"^[وف]?[بكل]?(?=ا)")


@lru_cache(maxsize=65536)
def _keys(word: str, langs: tuple[str, ...]) -> frozenset:
    keys = {word, *(stem(word, lang) for lang in langs)}
    if "ar" in langs:
        if "ar" in _STEMMERS:
            keys.add(fold(_STEMMERS["ar"].stemWord(word)))
        match = _AR_CLITICS.match(word)
        cut = match.end() if match else 0
        if cut and len(word) - cut >= 3:
            keys |= {word[cut:], _arabic_light(word[cut:])}
    if "pt" in langs and word.endswith("oes") and len(word) > 4:  # ação / ações: Snowball keeps -ão and -ões apart
        keys.add(stem(word[:-3] + "ao", "pt"))
    if "es" in langs and word.endswith("ces") and len(word) > 4:  # lápiz / lápices, vez / veces
        keys.add(stem(word[:-3] + "z", "es"))
    return frozenset(keys)


# -- same-root words ---------------------------------------------------------------------------------------------
# Snowball removes inflection and some derivation, not all: "ночь" is "ноч" but "ночные" is "ночн"; "teacher",
# "swimmer" and "dentist" stay apart from "teach", "swim" and "dental". A root key strips one or two derivational
# suffixes off the stem, so the noun, the adjective, the verb and the agent noun of one root meet. A root is a real
# beginning of the word (the FTS prefix query finds the other forms), and a match through it counts _DERIV_WEIGHT of an
# exact one. Alternating consonants ("друг" / "дружба", "собака" / "собачий") are not joined.
_NOT_RUSSIAN = re.compile(r"[іїєґў]")  # Ukrainian and Belarusian letters
_RU_ADJ = re.compile(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ым|им|ых|их|ую|юю|ыми|ими)$")
_RU_VERB = re.compile(r"(?:ть|ться|тся|ет|ют|ут|ит|ат|ят|ем|ешь|ишь|ете|ите|ется|ится|ются|утся|ятся|ла|ло|ли|лся|"
                      r"лась|лись)$")
_RU_VERBAL_NOUN = re.compile(r"ни(?:е|я|ю|ем|и|й|ям|ями|ях)$")
_RU_ADJ_SUFFIXES = ("ическ", "еск", "ск", "енн", "онн", "ичн", "ов", "ев", "ив", "н")
_EN_SUFFIXES = ("ist", "ian", "er", "or", "al", "ic")
# -er words that are not "one who …": "mother" is not one who moths, "summer" not one who sums
_EN_NOT_AGENT = frozenset("""mother father brother sister daughter water number summer winter corner paper letter order
    member other under after never over ever together weather power matter answer finger dinner butter tower flower
    river silver center centre monster master chapter cancer danger hunger anger manner banner border shoulder
    ladder filter shower sweater copper hammer wonder rubber pepper oyster lobster liver offer cover enter remember
    consider deliver discover gather former latter outer inner upper lower proper super tiger spider timber lumber
    minister register semester character temper litter corridor mirror horror error color colour honor honour
    favor favour labor labour humor humour mayor motor tractor doctor actor sector factor vendor""".split())


def word_roots(word: str, stem_: str, lang: str) -> frozenset:
    """Root keys of a folded word whose stem in *lang* is *stem_* (Russian and English; empty for other languages)."""
    if not _ROOTS or not stem_ or is_identifier(word):
        return frozenset()
    if lang == "ru":
        return _ru_roots(word, stem_)
    if lang == "en":
        return _en_roots(word, stem_)
    return frozenset()


# Russian words of one root come from a dictionary, not from suffix rules: DerivBase.Ru (UDer 1.1, Apache-2.0), its
# direct parent-child links as pairs of Snowball stems, kept when their shape is a derivation (tools/
# build_ru_derivation.py). Suffix rules cannot tell "ночные" / "ночь" from "полный" / "пол"; the dictionary can, and it
# also has what rules cannot reach ("друг" / "дружеский"). "rules" keeps 0.9.0's first rules, "both" joins the two.
_RU_DERIVATION = "dict"
_RU_PAIRS: dict | None = None
_RU_PAIRS_LOCK = threading.Lock()


def _ru_related(stem_: str) -> frozenset:
    """Stems linked to *stem_* by a derivation in the dictionary (loaded on first use, about 47,000 pairs)."""
    global _RU_PAIRS
    if _RU_PAIRS is None:
        with _RU_PAIRS_LOCK:
            if _RU_PAIRS is None:
                pairs: dict[str, tuple] = {}
                try:
                    with gzip.open(Path(__file__).with_name("ru_derivation.tsv.gz"), "rt", encoding="utf-8") as fh:
                        for line in fh:
                            a, tab, b = line.rstrip("\n").partition("\t")
                            if tab and not line.startswith("#"):
                                a, b = sys.intern(a), sys.intern(b)  # each stem kept once
                                pairs[a] = pairs.get(a, ()) + (b,)
                                pairs[b] = pairs.get(b, ()) + (a,)
                except OSError:  # a copy without the data file: no dictionary roots
                    pairs = {}
                _RU_PAIRS = pairs
    return frozenset(_RU_PAIRS.get(stem_, ()))


@lru_cache(maxsize=65536)
def _ru_roots(word: str, stem_: str) -> frozenset:
    if _RU_DERIVATION == "dict":
        return _ru_related(stem_)
    rules = _ru_rule_roots(word, stem_)
    return rules | _ru_related(stem_) if _RU_DERIVATION == "both" else rules


def _ru_rule_roots(word: str, stem_: str) -> frozenset:
    out = set()

    def keep(s: str) -> str:
        s = s[:-1] if s.endswith("ь") else s
        if len(s) >= _MIN_STEM and s != stem_:
            out.add(s)
        return s

    if _RU_VERBAL_NOUN.search(word) and stem_[-2:] in ("ен", "ан") and len(stem_) >= 5:  # плаван(ие) → плав
        keep(stem_[:-2])
    elif _RU_ADJ.search(word):  # ночн(ые) → ноч, городск(ой) → город, банковск(ий) → банков → банк, школьн(ый) → школ
        s = stem_
        for _ in range(2):
            cut = next((x for x in _RU_ADJ_SUFFIXES if s.endswith(x) and len(s) - len(x) >= _MIN_STEM), "")
            if not cut:
                break
            s = keep(s[:-len(cut)])
    elif _RU_VERB.search(word) and stem_[-1:] in ("а", "я", "и", "е") and len(stem_) >= 4:  # плава(ет) → плав
        keep(stem_[:-3] if stem_[-3:] in ("ова", "ева", "ыва", "ива") and len(stem_) >= 6 else stem_[:-1])
    elif stem_.endswith("ост") and len(stem_) >= 6:  # сладост(и) → слад
        keep(stem_[:-3])
    elif stem_.endswith("ниц"):  # учительниц(а) → учитель
        keep(stem_[:-3])
    elif stem_.endswith(("ник", "чик")) and len(stem_) >= 6:  # праздник → праздн, как праздничн(ый)
        keep(stem_[:-2])
    elif stem_.endswith("к") and (len(stem_) >= 5 or stem_.endswith("чк")):  # соседк(а) → сосед, дочк(а) → доч; банк — нет
        keep(stem_[:-1])
    return frozenset(out)


@lru_cache(maxsize=65536)
def _en_roots(word: str, stem_: str) -> frozenset:
    """One step off the Porter2 stem, never two: teacher → teach, swimmer → swim, driver → drive, dentist / dental →
    dent, electrician → electric, investor → invest, photographi → photograph (battery stops at batter, not bat;
    general at gener, not gene). Words and stems in _EN_NOT_AGENT get none ("mothers" is not "moths")."""
    if word in _EN_NOT_AGENT or stem_ in _EN_NOT_AGENT:
        return frozenset()
    out = set()
    if stem_.endswith("i") and len(stem_) >= 7:  # Porter2 writes a final -y as -i: photographi, allergi, veterinari;
        out.add(stem_[:-1])                      # not every → ever, party → part, Sophie → soph
    else:
        cut = next((x for x in _EN_SUFFIXES if stem_.endswith(x) and len(stem_) - len(x) >= 3), "")
        agent = cut in ("er", "or")
        if cut:
            s = stem_[:-len(cut)]
            doubled = agent and len(s) >= 4 and s[-1] == s[-2] and s[-1] not in "aeiouls"
            s = s[:-1] if doubled else s  # swimm → swim, runn → run
            if len(s) >= 4 or (agent and len(s) >= 3):
                out.add(s)
                if agent and not doubled and s[-1] not in "aeiouy" and s[-2:-1] in tuple("aeiou"):
                    out.add(s + "e")  # driv → drive, bak → bake, writ → write (a doubled letter says no -e: swimmer)
    return frozenset(r for r in out if len(r) >= 3 and r != stem_)


# -- tokenisation --------------------------------------------------------------------------------------------------

# French/Italian elision: "d'Eva", "l'offre", "qu'on" are Eva, offre, on.
_ELISION = re.compile(r"^(?:l|d|j|m|n|s|t|c|qu|jusqu|lorsqu|puisqu|quoiqu|dell|all|nell|sull|dall|un)'(?=[^\W\d_])")
# Pronouns after the verb, taken off only in text of that language: French inversion ("prend-il", "a-t-elle",
# "est-ce", "vas-y"; not the noun "rendez-vous") and Portuguese enclisis ("chama-se", "lembra-me").
_FR_CLITICS = frozenset("je tu il elle on nous vous ils elles ce moi toi lui leur le la les y en".split())
_FR_COMPOUNDS = frozenset({"rendez-vous"})
_PT_CLITICS = frozenset("se me te lhe lhes nos vos o a os as lo la los las no na nas".split())
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _edge(c: str) -> bool:
    return c in PUNCT or (not c.isascii() and unicodedata.category(c)[0] == "P")


def _strip_edges(token: str) -> str:
    i, j = 0, len(token)
    while i < j and _edge(token[i]):
        i += 1
    while j > i and _edge(token[j - 1]):
        j -= 1
    return token[i:j]


def _script_split(token: str) -> list[str]:
    """A hyphenated token split where the script changes: "hr-ovh-এর" is hr-ovh + এর, "IT-тусовка" is it +
    тусовка, "03:00-এ" is 03:00 + এ (digits and symbols go with Latin)."""
    if "-" not in token or UNSEGMENTED.search(token):
        return [token]
    groups: list[list] = []
    for part in token.split("-"):
        script = word_script(part)
        script = "latin" if script == "common" else script
        if groups and groups[-1][0] == script:
            groups[-1][1].append(part)
        else:
            groups.append([script, [part]])
    return ["-".join(parts) for _, parts in groups]


def _letters_only(group: str) -> bool:
    return not any(c.isdigit() or c in "._:/" for c in group)


def _resolve_hyphen(group: str, langs: tuple, determined: bool) -> tuple[str, list[str]]:
    """(the word, extra bag entries) of a hyphenated word: reduplication is the word ("anak-anak"); a French or
    Portuguese verb loses its pronoun in text of that language; the whole and its parts stay available to
    containment checks ("sextas-feiras" contains "sextas")."""
    parts = group.split("-")
    if len(parts) < 2 or not all(parts) or not _letters_only(group):
        return group, []
    kept = parts
    if len(set(parts)) == 1:
        kept = parts[:1]
    elif determined and "fr" in langs and group not in _FR_COMPOUNDS:
        while len(kept) > 1 and kept[-1] in _FR_CLITICS:
            kept = kept[:-1]
        if len(kept) > 1 and kept[-1] == "t":
            kept = kept[:-1]
    elif determined and "pt" in langs:
        while len(kept) > 1 and kept[-1] in _PT_CLITICS:
            kept = kept[:-1]
    return "-".join(kept), [group] * (len(kept) != len(parts)) + parts


@dataclass(frozen=True)
class Word:
    text: str  # folded, lower-cased
    stem: str  # primary stem (the first language's)
    keys: frozenset  # the word and its stems
    stop: bool
    identifier: bool
    scan: bool  # matched against the facts' text: the FTS5 index cannot find its other forms
    fts: bool = True  # the FTS5 index can find the word itself (not Arabic or Indic script)
    roots: frozenset = frozenset()  # same-root keys (word_roots), compared at _DERIV_WEIGHT
    root_lang: str = ""  # "en" / "ru" when the word's language is determined: roots are built and compared only then


@dataclass(frozen=True)
class Analysis:
    words: tuple  # Word, in order
    bag: frozenset  # folded words, plus the parts of hyphenated ones
    keys: frozenset  # every key of every word in the bag
    text: str  # folded, lower-cased text, for boundary matches
    langs: tuple  # languages of the Latin words (all of LATIN when undetermined)
    german: bool = False  # determined German: its compounds end in the word they name ("Familienkatze" is a Katze)
    roots: frozenset = frozenset()  # (language, root key) of every word (search and recall only, not duplicate checks)
    lang_keys: frozenset = frozenset()  # (language, key) of the words whose language is determined


def _stop(word: str, script: str, votes: dict, sentence: bool) -> bool:
    """A function word of the text's language. For Latin script that language is told by the other words: "son" in
    "where does my son study" is a word, in "il boit son café" French "his". A word that is the only evidence decides
    only in a sentence ("Ana come carne todos los días"), not in a short message ("son", "bisa"): there, with no other
    evidence, the English list applies, as it did to all Latin text before 0.5.0."""
    if script != "latin":
        return any(word in STOP[lang] for lang in _SCRIPT_LANGS.get(script, ()))
    others = {lang: votes[lang] - (word in STOP[lang]) for lang in LATIN}
    best = max(others.values())
    if best:
        return any(word in STOP[lang] for lang in LATIN if others[lang] == best)
    if sentence:
        return any(word in STOP[lang] for lang in LATIN if votes[lang])
    return word in STOP["en"]


@lru_cache(maxsize=1024)
def analyze(text: str) -> Analysis:
    lowered = _CONTROL.sub(" ", unicodedata.normalize("NFC", text or "")).lower().replace("’", "'")
    groups = []
    for token in lowered.split():
        token = _ELISION.sub("", _strip_edges(token))
        groups += [g for g in (_strip_edges(g) for g in _script_split(token)) if g] if token else []
    # Latin language by function words (ties keep every tied language; none found: all five). A hyphenated word votes
    # with its first part only: "est-ce" is French by "est", "carry-on" is not French by "on".
    latin = [fold(g.split("-")[0]) for g in groups if word_script(g) == "latin" and _letters_only(g) and g.split("-")[0]]
    votes = {lang: sum(w in STOP[lang] for w in latin) for lang in LATIN}
    best = max(votes.values())
    langs, determined = (tuple(lang for lang in LATIN if votes[lang] == best), True) if best else (LATIN, False)
    # Spanish, French, Portuguese and Indonesian write compound words with a hyphen; English text and text of no
    # determined language keep letters-only hyphenated tokens as identifiers ("hr-ovh")
    compounds = determined and "en" not in langs
    english = determined and "en" in langs  # a tie ("on" is English and French) still includes English
    # Cyrillic is Russian for roots unless the text has letters Russian does not (Ukrainian, Belarusian): the Russian
    # suffix rules would join "головний" (main) with "гол" (goal)
    russian = _NOT_RUSSIAN.search(lowered) is None
    lang_keys = set()
    words, bag, keys, roots = [], set(), set(), set()
    for group in groups:
        raw, extra = _resolve_hyphen(group, langs, determined) if "-" in group else (group, [])
        word, script, ident = fold(raw), word_script(raw), is_identifier(raw)
        if ident and compounds and "-" in raw and _letters_only(raw):
            ident = False  # "week-end", "sexta-feira", "rendez-vous": a compound word, not a host name
        unsegmented = UNSEGMENTED.search(raw) is not None
        word_langs = () if ident or unsegmented else langs if script == "latin" else _SCRIPT_LANGS.get(script, ())
        is_stop = not ident and not unsegmented and _stop(word, script, votes, len(latin) >= 4)
        word_keys = _keys(word, word_langs) if word_langs else frozenset({word})
        bag.add(word)
        keys |= word_keys
        for piece in (fold(e) for e in extra):  # the whole and the parts of a hyphenated word, for containment
            bag.add(piece)
            keys |= _keys(piece, word_langs) if word_langs and _letters_only(piece) else {piece}
        root_lang = "" if is_stop or ident or unsegmented or "-" in word else \
            "en" if script == "latin" and english else "ru" if script == "cyrillic" and russian else ""
        word_roots_ = word_roots(word, stem(word, root_lang), root_lang) - word_keys if root_lang else frozenset()
        if root_lang:
            roots |= {(root_lang, r) for r in word_roots_}
            lang_keys |= {(root_lang, k) for k in word_keys}
        words.append(Word(
            text=word, stem=stem(word, word_langs[0]) if word_langs else word, keys=word_keys,
            stop=is_stop, identifier=ident,
            # Indonesian prefixes (me-, di-, ber-) hide a word from a prefix query; text of undetermined language
            # might be Indonesian
            scan=script in _SCAN_SCRIPTS or (script == "latin" and ("id" in word_langs or "de" in word_langs)),
            fts=script not in _SCAN_SCRIPTS, roots=word_roots_, root_lang=root_lang))
    return Analysis(tuple(words), frozenset(bag), frozenset(keys), _fold_text(lowered), langs,
                    german=determined and "de" in langs and "en" not in langs, roots=frozenset(roots),
                    lang_keys=frozenset(lang_keys))


def name_keys(name: str) -> tuple[tuple[frozenset, bool], ...]:
    """(forms, exact?) of each word of an entity name. Latin-script names are not inflected, so they match only
    themselves (and the English possessive): "Jean" is not mentioned by "jeans", "Rose" not by "roses". Names in
    other scripts match by their keys, which carry their inflections ("Маши" mentions "Маша", "لإيفا" "إيفا")."""
    out = []
    for word in analyze(name).words:
        script = word_script(word.text)
        if script in _SCRIPT_LANGS and not word.identifier and not UNSEGMENTED.search(word.text):
            out.append((_keys(word.text, _SCRIPT_LANGS[script]), False))
        else:
            out.append((frozenset({word.text, word.text + "'s"}), True))
    return tuple(out)


def words(text: str) -> list[str]:
    """Folded, lower-cased words with surrounding punctuation stripped."""
    return [w.text for w in analyze(text).words]


def stems(text: str) -> set[str]:
    """Every comparison key of the text's words."""
    return set(analyze(text).keys)


def content_keys(text: str) -> list[frozenset]:
    """Key sets of the informative words (stopwords dropped), distinct; unsegmented runs give their content n-grams.
    Compared with soft_overlap, so two texts whose language was told apart differently (one has a function word, the
    other none) still meet on a shared stem."""
    out = []
    for w in analyze(text).words:
        if UNSEGMENTED.search(w.text):
            out += [frozenset({g}) for g, weight in run_grams(w.text) if weight]
        elif w.identifier or (not w.stop and len(w.text) > 1):
            out.append(w.keys)
    return list(dict.fromkeys(out))


def soft_overlap(a: list[frozenset], b: list[frozenset]) -> tuple[int, int]:
    """(words of a found in b, words of b found in a); two words match when their keys meet."""
    return (sum(1 for x in a if any(not x.isdisjoint(y) for y in b)),
            sum(1 for y in b if any(not y.isdisjoint(x) for x in a)))


def soft_jaccard(a: list[frozenset], b: list[frozenset]) -> float:
    if not a or not b:
        return 0.0
    shared = min(soft_overlap(a, b))
    return shared / (len(a) + len(b) - shared)


def content_stems(text: str) -> set[str]:
    """Stems of the informative words only (stopwords dropped); runs of unsegmented scripts contribute their
    content n-grams, so Chinese or Thai text compares by fragments rather than as one huge "word"."""
    out = set()
    for w in analyze(text).words:
        if UNSEGMENTED.search(w.text):
            out |= {g for g, weight in run_grams(w.text) if weight}
        elif w.identifier or (not w.stop and len(w.text) > 1):
            out.add(w.stem)
    return out


# Scripts written without spaces between words (Thai, Lao, Khmer, Myanmar, Chinese, Japanese) and Korean, whose
# particles attach to the word: the FTS5 unicode61 tokenizer indexes a whole run as one token, so these are matched
# by character n-grams against the text (bigrams for CJK and Hangul, trigrams for Southeast Asian scripts).
UNSEGMENTED = re.compile(r"[฀-໿က-႟ក-៿]+|[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")
_SEA = re.compile(r"[฀-໿က-႟ក-៿]")
# Chinese function characters (particles, pronouns, question words, auxiliaries). A gram made only of them (可以, 什么,
# 怎么, 我们) carries no topic; a gram with one of them carries half; others carry full weight.
CJK_FUNCTION = frozenset(_fold_text("的是了吗呢吧啊呀哦嗯么怎什哪谁几多少在有和与及或就都也还要会能可以把被给让对为从到这那你我他她它们个一不没很太该应些吗嘛"
                         "はがをにのでともへやかねよなだ"))  # + Japanese particles and copula
# Thai function words: a trigram inside them carries no topic, one touching them half (Thai letters, unlike Chinese
# characters, mean nothing alone, so Thai is weighted by words found in the run, longest first).
_TH_FUNCTION = sorted(set("""ของ ที่ และ ใน กับ ไม่ ได้ จะ แล้ว ไหม อะไร ยังไง อย่างไร คือ เป็น มี ให้ ครับ ค่ะ คะ นะ เลย ด้วย แบบ
    ไหน เท่าไหร่ กี่ ว่า ก็ หรือ แต่ จาก ถึง เรา ฉัน ผม คุณ เขา นี้ นั้น หน่อย ควร""".split()), key=len, reverse=True)
# Whole runs that are chat fillers, as "ok", "yes", "thanks" are in the English list: they recall nothing.
FILLER_RUNS = frozenset(_fold_text(w) for w in """はい うん ええ いいえ 了解 了解です りょうかい オーケー ありがとう ありがとうございます お願い お願いします
    よろしく そう そうです わかった わかりました 네 예 응 아니 아니요 좋아 좋아요 그래 그래요 알겠어 알겠어요 알겠습니다 오케이
    고마워 고마워요 감사 감사합니다 부탁해 부탁해요 ครับ ค่ะ คะ นะ จ้ะ จ้า โอเค ได้ ได้เลย ใช่ ขอบคุณ ขอบคุณครับ ขอบคุณค่ะ
    ขอบคุณมาก""".split())
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
CJK_NEGATORS = frozenset(_fold_text("不没无别非未"))
_MAX_GRAMS = 32
# Bengali, Arabic-Indic, Persian and Devanagari digits: the FTS5 index keeps them as written, so a number is also
# searched in each of them ("2027" finds "২০২৭"); folding makes the comparison itself digit-agnostic.
_NATIVE_DIGITS = tuple(str.maketrans("0123456789", d) for d in ("০১২৩৪৫৬৭৮৯", "٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹", "०१२३४५६७८९"))
_LATIN_IN_CJK = re.compile(r"[A-Za-z][A-Za-z0-9._:/-]*[A-Za-z0-9]|[A-Za-z0-9]*\d[A-Za-z0-9._:/-]*")


def _thai_mask(run: str) -> list[bool]:
    """Which characters of a Thai run belong to a function word (greedy, longest word first)."""
    mask, i = [False] * len(run), 0
    while i < len(run):
        word = next((w for w in _TH_FUNCTION if run.startswith(w, i)), "")
        for k in range(i, i + len(word)):
            mask[k] = True
        i += len(word) or 1
    return mask


def is_sea(run: str) -> bool:
    """Thai, Lao, Khmer or Myanmar: scripts whose characters are letters, compared by trigrams."""
    return _SEA.search(run) is not None


def run_grams(run: str) -> list[tuple[str, float]]:
    """(n-gram, weight) of one unsegmented run; a run no longer than n is one gram; a filler run has none."""
    if run in FILLER_RUNS:
        return []
    sea = is_sea(run)
    n = 3 if sea else 2
    function = _thai_mask(run) if sea else [c in CJK_FUNCTION for c in run]
    spans = [(0, len(run))] if len(run) <= n else [(i, i + n) for i in range(len(run) - n + 1)]
    out = []
    for i, j in spans:
        hits = sum(function[i:j])
        out.append((run[i:j], 0.0 if hits == j - i else 0.5 if hits else 1.0))
    return out


def query_grams(text: str) -> list[tuple[str, float]]:
    """Weighted substrings to look for when *text* contains unsegmented script: its content n-grams, plus the Latin
    words and identifiers written into it ("HR24", "hr-ovh", "Python"), which the tokenizer glues to the
    neighbouring characters. Lower-cased; weight-0 grams dropped; [] for text without unsegmented script."""
    lowered = _fold_text((text or "").lower())
    if not UNSEGMENTED.search(lowered):
        return []
    grams: dict[str, float] = {}
    for run in UNSEGMENTED.findall(lowered):
        for g, weight in run_grams(run):
            if weight:
                grams[g] = max(grams.get(g, 0.0), weight)
    for m in _LATIN_IN_CJK.finditer(UNSEGMENTED.sub(" ", lowered)):
        token = m.group(0).strip("._:/-")
        if len(token) >= 3 and token not in STOPWORDS:
            grams[token] = 1.0
    return list(grams.items())[:_MAX_GRAMS]


def content_chars(text: str) -> list[str]:
    """Distinct Chinese characters (also in Japanese text) that carry meaning: no function characters or negators.
    Kana, Hangul and Thai are left out: their characters are sounds, not words."""
    chars = []
    for run in UNSEGMENTED.findall(_fold_text((text or "").lower())):
        if run not in FILLER_RUNS:
            chars += [c for c in run if _HAN.match(c) and c not in CJK_FUNCTION and c not in CJK_NEGATORS]
    return list(dict.fromkeys(chars))


def gram_coverage(grams: list[tuple[str, float]], text: str) -> float:
    """Weighted share of the grams present in *text* (case-insensitive, folded as the grams are)."""
    total = sum(w for _, w in grams)
    if not total:
        return 0.0
    lowered = _fold_text((text or "").lower())
    return sum(w for g, w in grams if g in lowered) / total


# FTS5 syntax characters (quotes, grouping, prefix, column filter, NOT/required). Never interpolated into a MATCH
# expression: inside a token they split it into a phrase of the tokens unicode61 indexes ("pve-01" -> "pve 01").
FTS_OPERATORS = str.maketrans({c: " " for c in '"()*^:-+'})


@dataclass(frozen=True)
class Term:
    """One informative query term: the folded word, its primary stem, whether it is an identifier, the FTS-safe
    tokens it is searched as (operator characters removed), its comparison keys, and whether it is matched by
    scanning the facts' text instead of through the FTS5 index."""
    word: str
    stem: str
    identifier: bool
    tokens: tuple[str, ...] = ()
    keys: frozenset = frozenset()
    scan: bool = False
    fts_ok: bool = True
    roots: frozenset = frozenset()  # same-root keys, matched at _DERIV_WEIGHT
    root_lang: str = ""  # the language its roots (and the fact's) are compared in; "": no root matching

    @property
    def loose(self) -> str:
        """A shorter shared beginning for long Russian words, so derivations Snowball keeps apart still meet
        ("переезжаем" / "переезд" share "переез"); "" for other words (in English "contra" would join "contract" and
        "contrast"), short words and identifiers."""
        if self.identifier or len(self.tokens) != 1 or len(self.stem) < _LOOSE_FROM or word_script(self.word) != "cyrillic":
            return ""
        return self.stem[:max(_LOOSE_MIN, len(self.stem) - 2)]

    @property
    def short_loose(self) -> str:
        """The same for Russian stems of six letters, one letter shorter ("покупа" / "покупк" share "покуп",
        "работа" / "рабоч" share "рабо"); matched at _DERIV_WEIGHT, since a four-letter beginning says less."""
        if not _SHORT_LOOSE or self.identifier or len(self.tokens) != 1 or not _SHORT_FROM <= len(self.stem) < _LOOSE_FROM \
                or self.root_lang != "ru":
            return ""
        return self.stem[:-1]

    def fts(self, operators: dict | None = None) -> str:
        """FTS5 expression built only from operator-free tokens: the exact word (a phrase when it splits into
        several tokens), OR each of its keys as a prefix when the word is one plain token."""
        tokens = self.tokens or tuple(self.word.translate(FTS_OPERATORS).split())
        exact = '"' + " ".join(tokens) + '"'
        if self.identifier or len(tokens) != 1 or tokens[0] != self.word:
            if not any("0" <= c <= "9" for t in tokens for c in t):
                return exact
            native = ['"' + " ".join(t.translate(d) for t in tokens) + '"' for d in _NATIVE_DIGITS]
            return "(" + " OR ".join([exact, *native]) + ")"
        prefixes = sorted(k for k in (self.keys or {self.stem}) | ({self.loose} if self.loose else set())
                          if len(k) >= _MIN_STEM and k.translate(FTS_OPERATORS) == k and k.split() == [k])
        parts = [f'"{p}"*' for p in prefixes] + ([] if self.word in prefixes else [exact])
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    def fts_extra(self) -> str:
        """FTS5 expression of the root keys and the short beginning only, "" when there are none. A separate query:
        OR-ed into fts() it would score a fact that has the word itself twice (the root prefix matches it too) and
        push it above facts that answer better; here it only brings in facts the word's own forms miss."""
        if self.identifier or len(self.tokens) != 1 or self.tokens[0] != self.word:
            return ""
        prefixes = sorted(k for k in self.roots | ({self.short_loose} if self.short_loose else set())
                          if len(k) >= _MIN_STEM and k.translate(FTS_OPERATORS) == k and k.split() == [k])
        parts = [f'"{p}"*' for p in prefixes]
        return "" if not parts else parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    @cached_property
    def _bounded(self) -> re.Pattern:
        # An ASCII identifier is bounded by ASCII word characters only: in Chinese text "hr-ovh" is glued to the
        # characters around it ("服务器hr-ovh运行").
        edge = r"A-Za-z0-9_\-" if self.word.isascii() else r"\w\-"
        return re.compile(rf"(?<![{edge}])" + re.escape(self.word) + rf"(?![{edge}])")

    @cached_property
    def _prefix_keys(self) -> tuple:
        """Latin: keys of six letters or more that a word of the fact may extend ("allergi" reaches "allergic", as
        the FTS prefix query does); a whole stem, so "contract" does not reach "contrast"."""
        if self.identifier or len(self.tokens) != 1 or word_script(self.word) != "latin":
            return ()
        return tuple(k for k in self.keys if len(k) >= 6)

    @cached_property
    def _head_keys(self) -> tuple:
        """Latin: keys of four letters or more that may be the last part of a German compound ("katze" in
        "familienkatze", "laptop" in "arbeitslaptop"). Checked only against facts written in German."""
        if self.identifier or len(self.tokens) != 1 or word_script(self.word) != "latin":
            return ()
        return tuple(k for k in self.keys if len(k) >= 4)

    def found_in(self, fact: Analysis) -> bool:
        """Does a fact contain this term, exactly or through a word of the same root (see match)."""
        return self.match(fact) > 0.0

    def match(self, fact: Analysis) -> float:
        """How a fact contains this term: 1.0 for the word itself, a shared key, the loose beginning or the same
        identifier; _DERIV_WEIGHT for a word of the same root ("ночные" / "ночь", "teacher" / "teaches") or the
        short beginning of a middle-length Russian stem; 0.0 for none."""
        if self._exact(fact):
            return 1.0
        if self.identifier or len(self.tokens) != 1:
            return 0.0
        lang = self.root_lang
        if lang and fact.roots | fact.lang_keys:  # only roots and keys of the same, determined language meet
            fact_roots = {r for lng, r in fact.roots if lng == lang}
            if (self.roots and not self.roots.isdisjoint(k for lng, k in fact.lang_keys if lng == lang)) \
                    or (fact_roots and not (self.keys | self.roots).isdisjoint(fact_roots)):
                return _DERIV_WEIGHT
        short = self.short_loose  # Russian only, so against the fact's Russian words only (not "покупки" in Ukrainian)
        if short and short in fact.text and any(lng == "ru" and k.startswith(short) for lng, k in fact.lang_keys):
            return _DERIV_WEIGHT
        return 0.0

    def _exact(self, fact: Analysis) -> bool:
        """The same word, a shared key (word or stem), the loose beginning, or (for identifiers and multi-token
        words) the same token run with word boundaries, so "pve-01" does not match inside "pve-010"."""
        if self.word in fact.bag:
            return True
        if not self.identifier and len(self.tokens) <= 1 and not self.keys.isdisjoint(fact.keys):
            return True
        if self.loose and self.loose in fact.text and any(w.startswith(self.loose) for w in fact.bag):
            return True
        if self._prefix_keys and any(k in fact.text for k in self._prefix_keys) and \
                any(f.startswith(k) for k in self._prefix_keys for f in fact.keys):
            return True
        if fact.german and self._head_keys and any(k in fact.text for k in self._head_keys) and \
                any(len(f) >= len(k) + 3 and f.endswith(k) for k in self._head_keys for f in fact.bag):
            return True
        return self.word in fact.text and self._bounded.search(fact.text) is not None


def query_terms(text: str) -> list[Term]:
    """Distinct informative terms of a natural-language query, in order of appearance."""
    seen, terms = set(), []
    for w in analyze(text).words:
        if w.stop or w.text in QUERY_ONLY_STOP or (not w.identifier and len(w.text) < 2):
            continue
        tokens = tuple(w.text.translate(FTS_OPERATORS).split())
        if not tokens:
            continue
        single = len(tokens) == 1
        key = w.stem if single else w.text
        if key not in seen:
            seen.add(key)
            terms.append(Term(w.text, key, w.identifier, tokens, w.keys if single else frozenset({w.text}), w.scan, w.fts,
                              w.roots if single else frozenset(), w.root_lang if single else ""))
    return terms
