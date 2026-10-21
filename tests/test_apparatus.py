from conftest import W

from tex2docx.diagnostics import Diagnostics
from tex2docx.markers import Markers
from tex2docx.config import load_config
from tex2docx.reledmac import transform_apparatus


def _transform(tex, **parallel):
    cfg = load_config()
    cfg["parallel"].update(parallel)
    out, stats = transform_apparatus(tex, cfg, Markers(), Diagnostics())
    return out, stats


def test_edtext_becomes_footnotes_after_lemma():
    out, stats = _transform(r"the \edtext{three}{\Afootnote{two P}\Bfootnote{src}} sects")
    assert out == r"the three\footnote{[A] three] two P}\footnote{[B] three] src} sects"
    assert stats["[A]"] == 1 and stats["[B]"] == 1


def test_nested_edtext_and_lemma_override():
    out, _ = _transform(r"\edtext{a \edtext{b}{\Afootnote{v1}} c}{\lemma{a \ldots{} c}\Afootnote{om.}}")
    assert out == r"a b\footnote{[A] b] v1} c\footnote{[A] a \ldots{} c] om.}"


def test_nested_lemma_without_override_is_plain():
    out, _ = _transform(r"\edtext{a \edtext{b}{\Afootnote{v1}} c}{\Afootnote{om.}}")
    assert out.endswith(r"\footnote{[A] a b c] om.}")


def test_structure_commands_removed():
    out, _ = _transform("\\beginnumbering\n\\pstart X\\pend\n\\pstart Y\\pend\n\\endnumbering")
    assert [p.strip() for p in out.split("\n\n") if p.strip()] == ["X", "Y"]


def test_parallel_interleave_and_table():
    tex = (r"\begin{pairs}\begin{Leftside}\pstart L1\pend\pstart L2\pend\end{Leftside}"
           r"\begin{Rightside}\pstart R1\pend\pstart R2\pend\end{Rightside}\end{pairs}")
    out, _ = _transform(tex)
    assert [p.strip() for p in out.split("\n\n") if p.strip()] == ["L1", "R1", "L2", "R2"]
    out, _ = _transform(tex, layout="table")
    assert "L1 & R1" in out and "L2 & R2" in out and "\\begin{tabular}" in out


def test_apparatus_docx(convert):
    res, docx = convert("apparatus.tex")
    notes = docx.footnote_texts()
    assert "[A] sects] schools P" in notes
    assert "[A] three] two I" in notes
    assert "[B] three] Source note" in notes
    assert "[A] التجربة] التجارب I" in notes
    assert "[A] وأما … يقولون] om. P" in notes
    assert len(notes) == 5
    checks = {c.name: c for c in res.checks}
    assert checks["脚注数"].status == "ok"
    # order of paragraphs in the parallel text
    text = docx.text()
    assert text.index("Left one") < text.index("Right one") < text.index("Left two") < text.index("Right two")
    # adjacent A/B note references are separated
    body = docx.document
    refs = [r for r in body.iter(W + "r") if r.find(W + "footnoteReference") is not None]
    sep = [r for r in body.iter(W + "r") if "".join(t.text or "" for t in r.findall(W + "t")) == ","
           and r.getprevious() is not None and r.getprevious().find(W + "footnoteReference") is not None]
    assert len(refs) == 5 and len(sep) == 1


def test_arabic_apparatus_note_is_rtl(convert):
    _, docx = convert("apparatus.tex")
    for fn in docx.footnotes.findall(W + "footnote"):
        text = "".join(t.text or "" for t in fn.iter(W + "t"))
        if "التجارب" in text:
            p = fn.find(W + "p")
            assert docx.para_style(p) == "FootnoteTextRTL"
            break
    else:
        raise AssertionError("Arabic apparatus note not found")
