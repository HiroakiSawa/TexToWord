"""Normalise polyglossia / bidi language switches into forms pandoc keeps as lang attributes.

Inline switches become ``\\foreignlanguage{lang}{...}`` (pandoc: Span with lang),
block switches become ``\\begin{otherlanguage}{lang}`` (pandoc: Div with lang).
"""

from __future__ import annotations

import re
from collections import Counter

from .diagnostics import Diagnostics, context_of
from .texscan import (ScanError, iter_control_sequences, read_arg, read_group, read_optional,
                      replace_spans, skip_spaces)

POLYGLOSSIA_LANGS = {
    "albanian", "amharic", "arabic", "armenian", "asturian", "bahasai", "bahasam", "basque",
    "belarusian", "bengali", "bosnian", "breton", "british", "bulgarian", "catalan", "coptic",
    "croatian", "czech", "danish", "divehi", "dutch", "english", "esperanto", "estonian",
    "farsi", "finnish", "french", "friulian", "gaelic", "galician", "georgian", "german",
    "greek", "hebrew", "hindi", "hungarian", "icelandic", "interlingua", "italian", "japanese",
    "kannada", "khmer", "korean", "kurdish", "lao", "latin", "latvian", "lithuanian", "macedonian",
    "malay", "malayalam", "marathi", "mongolian", "nko", "norwegian", "occitan", "persian",
    "piedmontese", "polish", "portuguese", "romanian", "romansh", "russian", "sami", "sanskrit",
    "serbian", "slovak", "slovenian", "sorbian", "spanish", "swedish", "syriac", "tamil",
    "telugu", "thai", "tibetan", "turkish", "turkmen", "ukrainian", "urdu", "vietnamese", "welsh",
    "american", "usenglish", "ngerman", "austrian", "naustrian",
}
LANG_DECL = re.compile(r"\\set(?:main|default|other)languages?\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def declared_languages(preamble: str) -> tuple[str | None, set[str]]:
    main = None
    langs: set[str] = set()
    for m in LANG_DECL.finditer(preamble):
        names = [x.strip() for x in m.group(1).split(",") if x.strip()]
        if m.group(0).startswith(("\\setmainlanguage", "\\setdefaultlanguage")) and names:
            main = names[0]
        langs.update(names)
    return main, langs


def normalize_languages(text: str, known: set[str], diag: Diagnostics) -> tuple[str, Counter]:
    langs = {l.lower() for l in known} | POLYGLOSSIA_LANGS
    stats: Counter = Counter()
    repl: list[tuple[int, int, str]] = []
    last = -1
    for name, start, end in iter_control_sequences(text):
        if start < last:
            continue
        try:
            if name.startswith("text") and name[4:].lower() in langs and name[4:] == name[4:].lower():
                lang = name[4:]
                _, i = read_optional(text, end)
                j = skip_spaces(text, i)
                if j < len(text) and text[j] == "{":
                    arg, stop = read_group(text, j)
                else:
                    continue
                repl.append((start, stop, f"\\foreignlanguage{{{lang}}}{{{arg}}}"))
                stats[lang] += 1
                last = stop
            elif name in ("foreignlanguage", "textlang"):
                _, i = read_optional(text, end)
                lang, i = read_arg(text, i)
                arg, stop = read_arg(text, i)
                lang = lang.strip().lower()
                repl.append((start, stop, f"\\foreignlanguage{{{lang}}}{{{arg}}}"))
                stats[lang] += 1
                last = stop
            elif name in ("RL", "LR", "RLE", "LRE"):
                arg, stop = read_arg(text, end)
                repl.append((start, stop, "{" + arg + "}"))
                stats["bidi:" + name] += 1
                last = stop
            elif name == "begin":
                env, i = read_group(text, skip_spaces(text, end))
                env_l = env.lower().rstrip("*")
                if env_l in ("otherlanguage", "lang"):
                    _, i = read_optional(text, i)
                    lang, stop = read_arg(text, i)
                    repl.append((start, stop, f"\\begin{{otherlanguage}}{{{lang.strip().lower()}}}"))
                    stats[lang.strip().lower()] += 1
                    last = stop
                elif env_l in langs:
                    _, stop = read_optional(text, i)
                    repl.append((start, stop, f"\\begin{{otherlanguage}}{{{env_l}}}"))
                    stats[env_l] += 1
                    last = stop
            elif name == "end":
                env, stop = read_group(text, skip_spaces(text, end))
                env_l = env.lower().rstrip("*")
                if env_l in ("otherlanguage", "lang") or env_l in langs:
                    repl.append((start, stop, "\\end{otherlanguage}"))
                    last = stop
        except ScanError:
            diag.warning("language", f"言語切替 \\{name} を解析できませんでした", context_of(text, start))
    return replace_spans(text, repl), stats
