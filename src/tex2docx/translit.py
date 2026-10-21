"""TeX accent / transliteration macros to NFC Unicode, and raw ain/hamza handling."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .diagnostics import Diagnostics, context_of
from .texscan import (ScanError, apply_outside, protected_spans, read_control_sequence,
                      read_group, skip_spaces)

# accent macro -> combining character
COMBINING = {
    "d": "̣",   # dot below        ḥ ṣ ḍ ṭ ẓ
    "b": "̱",   # macron below     ṯ ḏ
    "v": "̌",   # caron            š ǧ
    ".": "̇",   # dot above        ġ ż
    "=": "̄",   # macron           ā ī ū
    "u": "̆",   # breve            ğ
    "H": "̋",   # double acute
    "c": "̧",   # cedilla
    "t": "͡",   # tie (spans two letters)
    "r": "̊",   # ring above
    "k": "̨",   # ogonek
    "'": "́",   # acute
    "`": "̀",   # grave
    "^": "̂",   # circumflex
    '"': "̈",   # diaeresis
    "~": "̃",   # tilde
    # semtrans
    "U": "̮",   # breve below      ḫ
    "D": "̤",   # diaeresis below
}
BELOW_ACCENTS = {"d", "b", "c", "k", "U", "D"}
SPACING_ACCENTS = {"^": "^", "~": "~", "'": "´", "`": "`", '"': "¨", "=": "¯", ".": "˙"}
SPECIAL_LETTERS = {
    "i": "ı", "j": "ȷ", "ss": "ß", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ",
    "o": "ø", "O": "Ø", "aa": "å", "AA": "Å", "l": "ł", "L": "Ł", "dh": "ð", "DH": "Ð",
    "th": "þ", "TH": "Þ",
}
ACCENT_BASE = {"i": "i", "j": "j"}
STANDALONE = {"Alif": "ʾ", "Ayn": "ʿ", "textrhooksh": "ʾ", "textlhooksh": "ʿ",
              "textquoteleft": "‘", "textquoteright": "’", "textquotedblleft": "“",
              "textquotedblright": "”", "textendash": "–", "textemdash": "—",
              "textsection": "§", "textdagger": "†", "textdaggerdbl": "‡"}


def _arg_text(s: str, i: int) -> tuple[str | None, int]:
    """Read an accent argument and return its plain-text base (or None if not plain)."""
    j = skip_spaces(s, i, newlines=False)
    if j >= len(s):
        return None, i
    c = s[j]
    if c == "{":
        try:
            inner, end = read_group(s, j)
        except ScanError:
            return None, i
        if inner.strip() in ("\\i", "\\j"):
            return inner.strip()[1], end
        inner_conv = convert_accents_simple(inner)
        if "\\" in inner_conv or "{" in inner_conv:
            return None, i
        return inner_conv, end
    if c == "\\":
        name, end = read_control_sequence(s, j)
        if name in ACCENT_BASE:
            return ACCENT_BASE[name], end
        return None, i
    if c in "}$%&#":
        return None, i
    return c, j + 1


def convert_accents_simple(s: str) -> str:
    out, _, _ = _convert(s, {})
    return out


def _convert(s: str, extra: dict[str, str]) -> tuple[str, Counter, list[tuple[str, int]]]:
    table = dict(COMBINING)
    for k, v in (extra or {}).items():
        table[k.lstrip("\\")] = v
    counts: Counter = Counter()
    failures: list[tuple[str, int]] = []
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        name, end = read_control_sequence(s, i)
        if name == "\\":
            out.append(s[i:end])
            i = end
            continue
        if name in table:
            arg, after = _arg_text(s, end)
            if arg == "":
                out.append(SPACING_ACCENTS.get(name, ""))
                i = after
                continue
            if arg is not None:
                comb = table[name]
                if name in BELOW_ACCENTS and arg in ("i", "j") and "\\" in s[end:after]:
                    arg = {"i": "ı", "j": "ȷ"}[arg]  # TeX puts below-accents under the dotless \i
                if name == "~" and _is_greek(arg[0]):
                    comb = "͂"  # Greek perispomeni, so that NFC gives ῶ, ᾶ, ...
                seq = arg[0] + comb + arg[1:]
                res = unicodedata.normalize("NFC", seq)
                counts[(f"\\{name}{{{arg}}}", res)] += 1
                out.append(res)
                i = after
                continue
            failures.append((name, i))
            out.append(s[i:end])
            i = end
            continue
        if name in STANDALONE:
            counts[(f"\\{name}", STANDALONE[name])] += 1
            out.append(STANDALONE[name])
            i = end
            if s[i:i + 2] == "{}":
                i += 2
            continue
        if name in SPECIAL_LETTERS and (end >= n or not s[end].isalpha()):
            out.append(SPECIAL_LETTERS[name])
            j = end
            if s[j:j + 2] == "{}":
                j += 2
            else:
                j = skip_spaces(s, j)
            i = j
            continue
        out.append(s[i:end])
        i = end
    return "".join(out), counts, failures


def convert_accents(s: str, diag: Diagnostics, extra: dict | None = None) -> tuple[str, Counter]:
    spans = protected_spans(s, commands=("url", "path", "href", "nolinkurl", "verb"))
    total: Counter = Counter()

    def work(chunk: str) -> str:
        res, counts, failures = _convert(chunk, extra or {})
        total.update(counts)
        for name, pos in failures:
            diag.warning("accent", f"アクセントマクロ \\{name} の引数を文字に変換できませんでした",
                         context_of(chunk, pos))
        return res

    return apply_outside(s, spans, work), total


# ---------------------------------------------------------------- ain / hamza
_CONTRACTIONS = re.compile(r"'(?=(?:s|t|re|ve|ll|d|m)(?![^\W\d_]))")
_LETTER = re.compile(r"[^\W\d_]")


def _is_letter(ch: str) -> bool:
    return bool(ch) and bool(_LETTER.match(ch))


def _is_greek(ch: str) -> bool:
    return bool(ch) and (0x0370 <= ord(ch) <= 0x03FF or 0x1F00 <= ord(ch) <= 0x1FFF)


def convert_ain_hamza(s: str, diag: Diagnostics, ain: str = "ʿ", hamza: str = "ʾ") -> tuple[str, Counter]:
    """Convert raw ` (ain) and ' (hamza) outside math, verbatim, URLs and \\texttt.

    Kept as typographic quotes: ``...'' pairs, English contractions/possessives (``'s``),
    and `...' single-quote pairs that enclose a space (an English quotation).
    """
    spans = protected_spans(s)
    stats: Counter = Counter()

    def work(chunk: str) -> str:
        chars = list(chunk)
        n = len(chars)
        result = chars[:]
        i = 0
        while i < n:
            c = chars[i]
            prev = chars[i - 1] if i else ""
            nxt = chars[i + 1] if i + 1 < n else ""
            if c == "\\":
                i += 2
                continue
            if c == "`":
                if nxt == "`":
                    i += 2
                    continue
                if not _is_letter(prev) and prev != "-":
                    close = _find_quote_close(chunk, i)
                    if close is not None:
                        stats["single_quote_pair"] += 1
                        diag.info("ain_hamza", "`…' を英語の引用符として残しました",
                                  context_of(chunk, i, close + 1))
                        i = close + 1
                        continue
                result[i] = ain
                stats["ain"] += 1
            elif c == "'":
                if nxt == "'" or prev == "'":
                    i += 1 if prev == "'" else 2
                    continue
                if _is_greek(prev) or _is_greek(nxt):
                    stats["greek_apostrophe"] += 1
                    diag.info("ain_hamza", "ギリシア文字に接する ' はハムザに変換しませんでした", context_of(chunk, i))
                    i += 1
                    continue
                if _is_letter(prev) and _CONTRACTIONS.match(chunk, i):
                    stats["apostrophe"] += 1
                    i += 1
                    continue
                if not _is_letter(nxt) and not _is_letter(prev):
                    stats["quote"] += 1
                    i += 1
                    continue
                if _is_letter(prev) and not _is_letter(nxt) and nxt not in ("-",):
                    if prev in "sS" and i >= 2 and _is_letter(chars[i - 2]):
                        stats["apostrophe"] += 1
                        diag.info("ain_hamza", "語末の s' は英語の所有格とみなしアポストロフィのままにしました",
                                  context_of(chunk, i))
                        i += 1
                        continue
                result[i] = hamza
                stats["hamza"] += 1
            i += 1
        return "".join(result)

    return apply_outside(s, spans, work), stats


TRANSLIT_CHARS = re.compile("[āīūĀĪŪḥḤṣṢḍḌṭṬẓẒḫḪṯṮḏḎšŠǧǦġĠʿʾ]")


def _find_quote_close(s: str, i: int) -> int | None:
    """For a ` at ``i``, find a closing ' of an English single-quoted phrase on the same paragraph."""
    limit = s.find("\n\n", i)
    limit = len(s) if limit < 0 else limit
    first_word = re.match(r"[^\W\d_]+", s[i + 1:])
    if first_word and TRANSLIT_CHARS.search(first_word.group(0)):
        return None  # `Alī, `ālat: a transliterated word, so the ` is an ain
    j = i + 1
    saw_space = False
    while j < limit:
        ch = s[j]
        if ch == "`" or ch == "&" or s.startswith("\\\\", j):
            return None  # quotations do not span table cells or lines
        if ch in " \n":
            saw_space = True
        if ch == "'" and saw_space:
            prev = s[j - 1]
            nxt = s[j + 1] if j + 1 < len(s) else ""
            if prev not in " \n" and not _is_letter(nxt) and nxt != "'":
                if _is_letter(prev) and _CONTRACTIONS.match(s, j):
                    j += 1
                    continue
                return j
        j += 1
    return None


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)
