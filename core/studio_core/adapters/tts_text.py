"""Text side of the CPU TTS: Vietnamese → espeak-style IPA for the Piper voice (sherpa-onnx 2.x ships no espeak-ng,
which is GPL), number words, English respelling of words the Kokoro lexicon lacks, and subtitle cue timing."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable

PUNCT = ".,!?;:…"
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?|[.,!?;:…]")

# espeak-ng vi tone digits (ngang carries none).
TONES = {"̀": "2", "̃": "3", "̉": "4", "́": "5", "̣": "6"}
ONSETS = [("ngh", "ŋ"), ("ng", "ŋ"), ("nh", "ɲ"), ("ch", "tʃ"), ("tr", "ʈ"), ("th", "tʰ"), ("ph", "f"), ("kh", "x"),
          ("gh", "ɣ"), ("gi", "z"), ("qu", "kw"), ("đ", "ɗ"), ("d", "z"), ("g", "ɣ"), ("c", "k"), ("k", "k"),
          ("q", "k"), ("x", "s"), ("s", "s"), ("r", "z"), ("b", "ɓ"), ("v", "v"), ("l", "l"), ("m", "m"),
          ("n", "n"), ("h", "h"), ("t", "t"), ("p", "p")]
# Rhymes longest first: (spelling, nucleus, coda).
RHYMES = [("uyên", "wiə", "n"), ("uyêt", "wiə", "t"), ("uya", "wiə", ""), ("oai", "waː", "j"), ("oay", "wa", "j"),
          ("uôi", "uə", "j"), ("ươi", "ɯə", "j"), ("ươu", "ɯə", "w"), ("iêu", "iə", "w"), ("yêu", "iə", "w"),
          ("uây", "wɤ", "j"), ("ay", "a", "j"), ("ây", "ɤ", "j"), ("au", "a", "w"), ("âu", "ɤ", "w"),
          ("anh", "a", "jŋ"), ("ach", "a", "jk"), ("ênh", "e", "jŋ"), ("êch", "e", "jk"),
          ("inh", "i", "ŋ"), ("ich", "i", "k"), ("ong", "ɔ", "wŋ"), ("oc", "ɔ", "wk"), ("ông", "o", "wŋ"),
          ("ôc", "o", "wk")]
NUCLEI = [("iê", "iə"), ("yê", "iə"), ("ia", "iə"), ("ya", "iə"), ("uô", "uə"), ("ua", "uə"), ("ươ", "ɯə"),
          ("ưa", "ɯə"), ("oa", "waː"), ("oă", "wa"), ("oe", "wɛ"), ("uê", "we"), ("uy", "wi"), ("uơ", "wɤː"),
          ("a", "aː"), ("ă", "a"), ("â", "ɤ"), ("e", "ɛ"), ("ê", "e"), ("i", "i"), ("y", "i"), ("o", "ɔ"), ("ô", "o"),
          ("ơ", "ɤː"), ("u", "u"), ("ư", "ɯ")]
CODAS = [("ng", "ŋ"), ("nh", "ŋ"), ("ch", "k"), ("c", "k"), ("t", "t"), ("p", "p"), ("m", "m"), ("n", "n"),
         ("i", "j"), ("y", "j"), ("o", "w"), ("u", "w")]
VI_VOWELS = set("aăâeêioôơuưy")
VI_LETTERS = {"a": "a", "b": "bê", "c": "xê", "d": "dê", "e": "e", "f": "ép", "g": "giê", "h": "hát", "i": "i",
              "j": "di", "k": "ca", "l": "lờ", "m": "mờ", "n": "nờ", "o": "o", "p": "pê", "q": "quy", "r": "rờ",
              "s": "ét", "t": "tê", "u": "u", "v": "vê", "w": "đắp liu", "x": "ích", "y": "y", "z": "dét"}

VI_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
EN_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
           "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def split_tone(syl: str) -> tuple[str, str]:
    d = unicodedata.normalize("NFD", syl.lower())
    tone = next((TONES[c] for c in d if c in TONES), "")
    return unicodedata.normalize("NFC", "".join(c for c in d if c not in TONES)), tone


def vi_syllable(word: str) -> str | None:
    """One Vietnamese syllable → onset + ˈ + nucleus + tone digit + coda, the layout espeak-ng's vi voice prints."""
    base, tone = split_tone(word)
    if not base or not any(c in VI_VOWELS for c in base):
        return None
    onset, rest = "", base
    if base.startswith("gi") and len(base) > 2 and base[2] in VI_VOWELS:
        onset, rest = "z", base[2:]
    elif base.startswith("gi"):
        onset, rest = "z", base[1:]
    else:
        for sp, ph in ONSETS:
            if base.startswith(sp) and len(base) > len(sp):
                onset, rest = ph, base[len(sp):]
                break
    if onset == "kw" and rest.startswith("u"):
        rest = rest[1:]
    for sp, nuc, coda in RHYMES:
        if rest == sp:
            return f"{onset}ˈ{nuc}{tone}{coda}"
    for sp, nuc in NUCLEI:
        if rest.startswith(sp):
            tail = rest[len(sp):]
            coda = next((ph for csp, ph in CODAS if tail == csp), None)
            if coda is None and tail:
                continue
            if onset == "kw" and nuc.startswith("w"):
                nuc = nuc[1:]
            return f"{onset}ˈ{nuc}{tone}{coda or ''}"
    return None


def _vi_under_100(n: int) -> list[str]:
    if n < 10:
        return [VI_DIGITS[n]]
    tens, ones = divmod(n, 10)
    out = ["mười"] if tens == 1 else [VI_DIGITS[tens], "mươi"]
    if ones == 1 and tens > 1:
        out.append("mốt")
    elif ones == 5:
        out.append("lăm")
    elif ones:
        out.append(VI_DIGITS[ones])
    return out


def vi_number(n: int) -> str:
    if n >= 10000:
        return " ".join(VI_DIGITS[int(c)] for c in str(n))
    words: list[str] = []
    th, rest = divmod(n, 1000)
    if th:
        words += [VI_DIGITS[th], "nghìn"]
    h, r = divmod(rest, 100)
    if h or (th and r):
        words += [VI_DIGITS[h], "trăm"]
        if 0 < r < 10:
            words.append("lẻ")
    if r or not words:
        words += _vi_under_100(r)
    return " ".join(words)


def en_number(n: int) -> str:
    if n >= 10000:
        return " ".join(EN_ONES[int(c)] for c in str(n))
    words: list[str] = []
    th, rest = divmod(n, 1000)
    if th:
        words += [EN_ONES[th], "thousand"]
    h, r = divmod(rest, 100)
    if h:
        words += [EN_ONES[h], "hundred"]
    if r or not words:
        words.append(EN_ONES[r] if r < 20 else EN_TENS[r // 10] + (f"-{EN_ONES[r % 10]}" if r % 10 else ""))
    return " ".join(words)


def spell_numbers(text: str, lang: str) -> str:
    fn = vi_number if lang == "vi" else en_number
    return re.sub(r"\d+", lambda m: f" {fn(int(m.group()))} ", text)


def vi_phonemes(text: str) -> list[str]:
    """Sentences of IPA; punctuation stays attached like Piper's own phonemizer output."""
    sentences: list[str] = []
    cur: list[str] = []
    for tok in _WORD.findall(spell_numbers(unicodedata.normalize("NFC", text), "vi")):
        if tok in PUNCT:
            if cur:
                cur[-1] += "," if tok in ",;:" else tok if tok in ".!?" else "."
                if tok in ".!?…":
                    sentences.append(" ".join(cur))
                    cur = []
            continue
        ipa = vi_syllable(tok)
        if ipa is None:
            # Loanwords and acronyms: read letter by letter with Vietnamese letter names.
            ipa = " ".join(p for ch in tok.lower() for p in [vi_syllable(w) for w in VI_LETTERS.get(ch, "").split()]
                           if p)
        if ipa:
            cur.append(ipa)
    if cur:
        sentences.append(" ".join(cur))
    return sentences


def en_respell(text: str, known: Callable[[str], bool]) -> str:
    """Kokoro's lexicon front end drops unknown words, so invented names would go silent: split them greedily into
    known pieces of ≥ 2 letters (Oakvale → oak vale)."""
    out: list[str] = []
    for tok in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|[^A-Za-z]+", spell_numbers(text, "en")):
        if not tok[0].isalpha() or known(tok.lower()):
            out.append(tok)
            continue
        word, pieces, i = tok.lower(), [], 0
        while i < len(word):
            j = next((j for j in range(len(word), i + 1, -1) if known(word[i:j])), None)
            if j is None:
                i += 1
                continue
            pieces.append(word[i:j])
            i = j
        out.append(" ".join(pieces) if pieces else tok)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def cue_chunks(text: str, max_chars: int = 42) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p.strip()]
    out: list[str] = []
    for p in parts:
        while len(p) > max_chars and ("," in p[:max_chars] or " " in p[:max_chars]):
            cut = p.rfind(",", 0, max_chars)
            cut = cut + 1 if cut > 0 else p.rfind(" ", 0, max_chars)
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def subtitle_cues(text: str, seconds: float) -> list[dict[str, object]]:
    """Cues split at sentence ends (then commas past `max_chars`), timed by character share of the clip."""
    chunks = cue_chunks(text)
    if not chunks or seconds <= 0:
        return []
    total = sum(len(c) for c in chunks)
    cues, t = [], 0.0
    for c in chunks:
        end = t + seconds * len(c) / total
        cues.append({"start": round(t, 3), "end": round(end, 3), "text": c})
        t = end
    cues[-1]["end"] = round(seconds, 3)
    return cues


def lexicon_words(lines: Iterable[str]) -> set[str]:
    return {ln.split(maxsplit=1)[0].lower() for ln in lines if ln.strip()}
