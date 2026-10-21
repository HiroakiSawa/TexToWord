import pytest
from conftest import W, rpr_val

from tex2docx.citations import _finish, find_citations, html_to_marked_latex
from tex2docx.diagnostics import Diagnostics


def test_find_citations_with_footnote_context():
    body = r"A \cite[p][q]{k1} B\footnote{see \textcite{k2}} \cites(g)[a]{x}[b]{y} \printbibliography[title=Refs]"
    occ = find_citations(body, Diagnostics())
    assert [o.text for o in occ] == [r"\cite[p][q]{k1}", r"\textcite{k2}", r"\cites(g)[a]{x}[b]{y}",
                                     r"\printbibliography[title=Refs]"]
    assert [o.in_footnote for o in occ] == [False, True, False, False]


def test_html_markup_to_latex():
    html = ("<html><body><p>TDXCB00000X Ullmann <span class='rm-lmri-12'>Die Medizin</span> "
            "TDXIOImTDXIC &amp; 50% TDXCE00000X</p></body></html>")
    marked = html_to_marked_latex(html)
    inner = marked.split("TDXCB00000X")[1].split("TDXCE00000X")[0]
    assert _finish(inner) == r"Ullmann \emph{Die Medizin} \emph{Im} \& 50\%"


def _needs_oracle(convert):
    from tex2docx.config import load_config
    from tex2docx.tools import find_tools
    tools = find_tools(load_config(), set(), {"make4ht", "biber"})
    if not {"make4ht", "biber"} <= set(tools):
        pytest.skip("make4ht / biber not installed")


def test_citations_rendered_by_biblatex(convert):
    _needs_oracle(convert)
    res, docx = convert("citations.tex")
    text = docx.text()
    errors = [f"{e.message} :: {e.context}" for e in res.diag.entries if e.level == "error"]
    assert not errors, errors
    assert "As Ullmann (1970) showed." in text
    assert "(e.g. Koetschet 2019, p. 12)" in text
    notes = docx.footnote_texts()
    assert notes == ["Ullmann 1970, p. 59.", "Koetschet 2019, p. 12."]
    checks = {c.name: c for c in res.checks}
    assert checks["引用"].status == "ok"
    assert checks["脚注数"].status == "ok"


def test_bibliography_paragraph_style_and_italics(convert):
    _needs_oracle(convert)
    _, docx = convert("citations.tex")
    paras = [p for p in docx.document.iter(W + "p")
             if "Ullmann, Manfred" in "".join(t.text or "" for t in p.iter(W + "t"))]
    assert paras and docx.para_style(paras[0]) == "Bibliography"
    italic = [r for r in paras[0].iter(W + "r") if r.find(f"{W}rPr/{W}i") is not None]
    assert "".join(t.text for r in italic for t in r.findall(W + "t")).strip() == "Die Medizin im Islam"
    assert "Abū Bakr al-Rāzī" in docx.text()
