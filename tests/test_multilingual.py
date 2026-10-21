from conftest import W, rpr_val


def test_transliteration_and_ain_hamza(convert):
    _, docx = convert("multilingual.tex")
    text = docx.text()
    assert "Ḥunayn’s school" in text
    assert "ʿAlī wrote" in text
    assert "Taʾrīḫ" in text
    assert "Kitāb al-Firaq" in text
    assert "comment" not in text


def test_arabic_runs_are_rtl_with_bidi_language(convert):
    _, docx = convert("multilingual.tex")
    runs = docx.runs_containing("كتاب الفرق") or docx.runs_containing("كتاب")
    assert runs
    for r in runs:
        assert r.find(f"{W}rPr/{W}rtl") is not None
        assert rpr_val(r, "lang", "bidi") == "ar-SA"


def test_rtl_block_paragraph_uses_rtl_style(convert):
    _, docx = convert("multilingual.tex")
    run = docx.runs_containing("حنين")[0]
    p = docx.paragraph_of(run)
    assert docx.para_style(p) == "BodyTextRTL" or p.find(f"{W}pPr/{W}bidi") is not None


def test_hebrew_and_greek_languages(convert):
    _, docx = convert("multilingual.tex")
    he = docx.runs_containing("ספר")[0]
    assert rpr_val(he, "lang", "bidi") == "he-IL"
    assert he.find(f"{W}rPr/{W}rtl") is not None
    gr = docx.runs_containing("Περὶ")[0]
    assert rpr_val(gr, "lang") == "el-GR"


def test_real_footnote_with_arabic(convert):
    _, docx = convert("multilingual.tex")
    notes = docx.footnote_texts()
    assert len(notes) == 1
    assert "قال جالينوس" in notes[0]
    refs = list(docx.document.iter(W + "footnoteReference"))
    assert len(refs) == 1


def test_unknown_macro_is_kept_and_highlighted(convert):
    res, docx = convert("multilingual.tex")
    runs = [r for r in docx.runs_containing("unknownmacro")]
    assert runs
    assert any(rpr_val(r, "highlight") == "yellow" for r in runs)
    assert "[[未変換: \\unknownmacro{kept text}]]" in docx.text()
    assert res.diag.count(category="unconverted") >= 1


def test_verification_checks_pass_for_rtl(convert):
    res, _ = convert("multilingual.tex")
    checks = {c.name: c for c in res.checks}
    assert checks["RTL 属性"].status == "ok"
    assert checks["アラビア語・ヘブライ語の網羅性"].status == "ok"
    assert checks["脚注数"].status == "ok"
    assert checks["不正文字"].status == "ok"
