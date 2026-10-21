from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from tex2docx.config import load_config
from tex2docx.pipeline import convert_document
from tex2docx.tools import ToolError, find_tools

GOLDEN = Path(__file__).parent / "golden"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _tools(required: set[str]):
    cfg = load_config()
    try:
        return cfg, find_tools(cfg, required, {"pdftotext", "biber", "make4ht", "ghostscript"})
    except ToolError as exc:
        pytest.skip(str(exc).splitlines()[0])


@pytest.fixture
def convert(tmp_path):
    """Convert a golden .tex (by name) and return (result, DocxXml)."""

    def _convert(name: str, compile: bool = False, cfg_update: dict | None = None):
        required = {"pandoc"} | ({"xelatex"} if compile else set())
        cfg, tools = _tools(required)
        cfg["compile"]["enabled"] = compile
        cfg["verify"]["pdf_compare"] = compile
        for section, values in (cfg_update or {}).items():
            cfg[section].update(values)
        src = GOLDEN / name
        work = tmp_path / "src"
        work.mkdir(exist_ok=True)
        for f in GOLDEN.iterdir():
            if f.is_file():
                (work / f.name).write_bytes(f.read_bytes())
        res = convert_document(work / name, work, tmp_path / "out", cfg, tools)
        assert res.error is None, res.error
        return res, DocxXml(res.output)

    return _convert


class DocxXml:
    def __init__(self, path: Path):
        with zipfile.ZipFile(path) as z:
            self.document = etree.fromstring(z.read("word/document.xml"))
            self.footnotes = etree.fromstring(z.read("word/footnotes.xml")) if \
                "word/footnotes.xml" in z.namelist() else None
            self.styles = etree.fromstring(z.read("word/styles.xml"))

    def parts(self):
        return [p for p in (self.document, self.footnotes) if p is not None]

    def runs_containing(self, text: str):
        out = []
        for part in self.parts():
            for r in part.iter(W + "r"):
                t = "".join(x.text or "" for x in r.findall(W + "t"))
                if text in t:
                    out.append(r)
        return out

    def text(self) -> str:
        return "\n".join("".join(t.text or "" for t in p.iter(W + "t"))
                         for part in self.parts() for p in part.iter(W + "p"))

    def footnote_texts(self) -> list[str]:
        if self.footnotes is None:
            return []
        out = []
        for fn in self.footnotes.findall(W + "footnote"):
            if fn.get(W + "type") in (None, "normal"):
                out.append("".join(t.text or "" for t in fn.iter(W + "t")).strip().lstrip("‎‏"))
        return out

    def paragraph_of(self, run):
        p = run.getparent()
        while p is not None and p.tag != W + "p":
            p = p.getparent()
        return p

    def para_style(self, p) -> str | None:
        ps = p.find(f"{W}pPr/{W}pStyle")
        return ps.get(W + "val") if ps is not None else None


def rpr_val(run, tag: str, attr: str = "val"):
    el = run.find(f"{W}rPr/{W}{tag}")
    if el is None:
        return None
    return el.get(W + attr, "")
