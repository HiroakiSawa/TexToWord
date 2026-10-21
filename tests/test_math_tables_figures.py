from conftest import W

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"

from tex2docx.diagnostics import Diagnostics
from tex2docx.figures import unwrap_boxes


def _math_text(docx) -> list[str]:
    out = []
    for om in docx.document.iter(M + "oMath"):
        out.append("".join(t.text or "" for t in om.iter(M + "t")))
    return out


def test_equation_numbers_and_tags(convert):
    res, docx = convert("math_tables.tex")
    maths = " | ".join(_math_text(docx))
    assert "(1)" in maths
    assert "(2)" in maths
    assert "(A)" in maths
    assert "(3)" not in maths  # \nonumber row is not numbered
    assert "See (1)." in docx.text()


def test_table_spans_and_wrappers(convert):
    res, docx = convert("math_tables.tex")
    spans = [g for g in docx.document.iter(W + "gridSpan") if g.get(W + "val") == "2"]
    merges = [v for v in docx.document.iter(W + "vMerge") if v.get(W + "val") == "restart"]
    assert spans and merges
    text = docx.text()
    assert "Paris" in text and "Istanbul" in text
    assert "未変換" not in text
    checks = {c.name: c for c in res.checks}
    assert checks["表のセル結合"].status == "ok"


def test_unwrap_boxes():
    out = unwrap_boxes(r"\resizebox{\textwidth}{!}{\scalebox{0.5}{X}} \rotatebox[origin=c]{90}{Y}", Diagnostics())
    assert out == "X Y"


def test_tikz_is_rendered_as_image(convert):
    res, docx = convert("tikz.tex", compile=True)
    drawings = list(docx.document.iter(W + "drawing"))
    assert len(drawings) == 1
    assert "未変換" not in docx.text()
    assert "Figure 1: Transmission" in docx.text()
