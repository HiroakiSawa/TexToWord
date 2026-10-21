"""Build the bundled reference.docx (Word style template) from pandoc's default."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from .postprocess import PPR_ORDER, RPR_ORDER, base_style_specs, ensure_style, q, set_child
from .tools import Tool, run


def build_reference_docx(pandoc: Tool, out: Path, cfg: dict, main_font: str = "Times New Roman") -> Path:
    with tempfile.TemporaryDirectory() as td:
        default = Path(td) / "default.docx"
        proc = run(pandoc.cmd("-o", str(default), "--print-default-data-file", "reference.docx"))
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        with zipfile.ZipFile(default) as z:
            names = z.namelist()
            data = {n: z.read(n) for n in names}
    styles = etree.fromstring(data["word/styles.xml"])
    for spec in base_style_specs(cfg):
        ensure_style(styles, spec)
    defaults = styles.find(q("docDefaults"))
    rpr = defaults.find(q("rPrDefault")).find(q("rPr"))
    rf = set_child(rpr, "rFonts", RPR_ORDER)
    for a in list(rf.attrib):
        del rf.attrib[a]
    for k in ("ascii", "hAnsi", "cs"):
        rf.set(q(k), main_font)
    set_child(rpr, "sz", RPR_ORDER, {"val": "24"})
    set_child(rpr, "szCs", RPR_ORDER, {"val": "24"})
    set_child(rpr, "lang", RPR_ORDER, {"val": "en-US", "bidi": "ar-SA"})
    for st in styles.findall(q("style")):
        if st.get(q("styleId")) == "FootnoteText":
            srpr = st.find(q("rPr"))
            if srpr is None:
                srpr = etree.SubElement(st, q("rPr"))
            set_child(srpr, "sz", RPR_ORDER, {"val": "20"})
            set_child(srpr, "szCs", RPR_ORDER, {"val": "20"})
        if st.get(q("styleId")) in ("BodyText", "FirstParagraph"):
            ppr = st.find(q("pPr"))
            if ppr is None:
                ppr = etree.SubElement(st, q("pPr"))
            set_child(ppr, "spacing", PPR_ORDER, {"before": "0", "after": "120", "line": "360",
                                                  "lineRule": "auto"})
    for rfonts in styles.iter(q("rFonts")):
        for a in ("asciiTheme", "hAnsiTheme", "cstheme", "eastAsiaTheme"):
            rfonts.attrib.pop(q(a), None)
    data["word/styles.xml"] = etree.tostring(styles, xml_declaration=True, encoding="UTF-8", standalone=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, data[n])
    return out
