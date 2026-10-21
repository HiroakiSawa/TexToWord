"""Unit tests for the TeX preprocessing stages (no external tools needed)."""

from pathlib import Path

from tex2docx import langs, macros, translit
from tex2docx.diagnostics import Diagnostics
from tex2docx.flatten import flatten, strip_comments
from tex2docx.postprocess import classify_text
from tex2docx.refs import parse_aux


def test_strip_comments_keeps_paragraph_breaks_and_escaped_percent():
    src = "a 50\\% b % comment\nc\n\n% only comment\nd%\n   e"
    assert strip_comments(src) == "a 50\\% b c\n\nde"


def test_accents_and_semtrans():
    d = Diagnostics()
    out, _ = translit.convert_accents(r"\d{h} \={\i} \=\i \U{h} \b{T} \v{s} \.{g} \'{e} \`{ι} \begin{x}", d)
    assert out == "ḥ ī ī ḫ Ṯ š ġ é ὶ \\begin{x}"


def test_accents_untouched_in_math_and_url():
    d = Diagnostics()
    out, _ = translit.convert_accents(r"$\bar{x} \d$ \url{http://a/\d}", d)
    assert out == r"$\bar{x} \d$ \url{http://a/\d}"


def test_ain_hamza_rules():
    d = Diagnostics()
    out, stats = translit.convert_ain_hamza("`Al\u012b and al-Ma'm\u016bn, Ta'r\u012bkh; Is\u1e25\u0101q's book; ``quoted'' text", d)
    assert out == "ʿAlī and al-Maʾmūn, Taʾrīkh; Isḥāq's book; ``quoted'' text"
    assert stats["ain"] == 1 and stats["hamza"] == 2 and stats["apostrophe"] == 1


def test_english_single_quote_pair_is_kept():
    d = Diagnostics()
    out, _ = translit.convert_ain_hamza("he called it `De sectis' and", d)
    assert out == "he called it `De sectis' and"


def test_macro_expansion_with_xspace_and_args():
    d = Diagnostics()
    t = macros.MacroTable()
    macros.collect_definitions(
        r"\newcommand{\hun}{\d{H}unayn\xspace}\newcommand\w[2][x]{#1-#2}"
        r"\NewDocumentCommand{\pair}{m o}{(#1)}\def\two#1#2{#2#1}", t, d, set())
    out, n = macros.expand(r"\hun ibn and \hun's, \w{a} \w[b]{c} \pair{z} \two ab", t, d)
    assert out == r"\d{H}unayn ibn and \d{H}unayn's, x-a b-c (z) ba"


def test_macro_without_xspace_swallows_space():
    d = Diagnostics()
    t = macros.MacroTable()
    macros.collect_definitions(r"\newcommand{\foo}{FOO}", t, d, set())
    assert macros.expand(r"\foo bar \foo{} baz", t, d)[0] == "FOObar FOO{} baz"


def test_renewcommand_of_latex_internal_is_not_expanded():
    d = Diagnostics()
    t = macros.MacroTable()
    macros.collect_definitions(r"\renewcommand{\figurename}{Fig.}", t, d, set())
    assert "figurename" not in t.macros


def test_language_normalisation():
    d = Diagnostics()
    out, stats = langs.normalize_languages(
        r"\textarabic{ك} \foreignlanguage[variant=x]{greek}{α} \begin{Arabic}ق\end{Arabic}", {"arabic"}, d)
    assert out == (r"\foreignlanguage{arabic}{ك} \foreignlanguage{greek}{α} "
                   r"\begin{otherlanguage}{arabic}ق\end{otherlanguage}")


def test_bidi_neutrals_follow_surrounding_direction():
    segs = classify_text("see كتاب الفرق (x)", para_rtl=False)
    assert segs == [("L", "see "), ("R-arabic", "كتاب الفرق"), ("L", " (x)")]
    segs = classify_text("[A] جالينوس] جالينس I", para_rtl=True)
    assert segs == [("R-arabic", "["), ("L", "A"), ("R-arabic", "] جالينوس] جالينس "), ("L", "I")]


def test_flatten_subfiles(tmp_path):
    (tmp_path / "chap").mkdir()
    (tmp_path / "main.tex").write_text(
        "\\documentclass{book}\n\\input{pre}\n\\begin{document}\n\\subfile{chap/c1}\n\\end{document}\n",
        encoding="utf-8")
    (tmp_path / "pre.tex").write_text("\\newcommand{\\x}{X}\n", encoding="utf-8")
    (tmp_path / "chap" / "c1.tex").write_text(
        "\\documentclass[../main.tex]{subfiles}\r\n\\begin{document}\r\nBody % c\r\n\\end{document}\r\n",
        encoding="utf-8")
    doc = flatten(tmp_path / "main.tex", Diagnostics())
    assert "\\newcommand{\\x}{X}" in doc.preamble
    assert doc.body.strip() == "Body"
    assert len(doc.tex_files) == 3


def test_parse_aux(tmp_path):
    aux = tmp_path / "m.aux"
    aux.write_text(
        "\\newlabel{sec:a}{{1.2}{5}}\n"
        "\\newlabel{sec:a@cref}{{[section][2][1]1.2}{[1][5][]5}}\n"
        "\\@writefile{toc}{\\contentsline {part}{I\\hspace {1em}Studies}{3}{}\\protected@file@percent }\n"
        "\\@writefile{toc}{\\contentsline {section}{\\numberline {1.2}Title}{5}{}}\n"
        "\\@writefile{lot}{\\contentsline {table}{\\numberline {1.1}{\\ignorespaces T}}{6}{}}\n"
        "\\l@dmake@labels 11|4|0|1||1|{ed:x}\n", encoding="utf-8")
    data = parse_aux(aux)
    assert data.labels["sec:a"] == ("1.2", "5")
    assert data.cref_types["sec:a"] == "section"
    assert [(e.kind, e.number) for e in data.toc] == [("part", "I"), ("section", "1.2")]
    assert data.lot == ["1.1"]
    assert data.edlabels["ed:x"] == ("11", "4")
