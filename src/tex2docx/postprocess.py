"""docx post-processing with lxml: RTL/bidi, languages, script fonts, styles, highlights, NFC."""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .diagnostics import Diagnostics
from .fonts import FontPlan

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"w": W_NS}


def q(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


RPR_ORDER = ["rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
             "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish",
             "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight",
             "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang",
             "eastAsianLayout", "specVanish", "oMath"]
PPR_ORDER = ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl",
             "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku",
             "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi",
             "adjustRightInd", "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents",
             "suppressOverlap", "jc", "textDirection", "textAlignment", "textboxTightWrap",
             "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr", "pPrChange"]


def set_child(parent: etree._Element, tag: str, order: list[str], attrs: dict | None = None) -> etree._Element:
    """Get or create ``w:tag`` under ``parent`` at its schema position; set attributes."""
    el = parent.find(q(tag))
    if el is None:
        el = etree.Element(q(tag))
        idx = order.index(tag)
        inserted = False
        for i, child in enumerate(parent):
            local = etree.QName(child).localname
            if local in order and order.index(local) > idx:
                parent.insert(i, el)
                inserted = True
                break
        if not inserted:
            parent.append(el)
    for k, v in (attrs or {}).items():
        el.set(q(k), v)
    return el


def get_rpr(run: etree._Element) -> etree._Element:
    rpr = run.find(q("rPr"))
    if rpr is None:
        rpr = etree.Element(q("rPr"))
        run.insert(0, rpr)
    return rpr


def get_ppr(p: etree._Element) -> etree._Element:
    ppr = p.find(q("pPr"))
    if ppr is None:
        ppr = etree.Element(q("pPr"))
        p.insert(0, ppr)
    return ppr


# ------------------------------------------------------------------ scripts
def char_class(ch: str) -> str:
    """'R-arabic' / 'R-hebrew' / 'R-syriac' / 'L-greek' / 'L' / 'N' (neutral) / 'M' (combining)."""
    o = ord(ch)
    bidi = unicodedata.bidirectional(ch)
    if bidi == "NSM":
        return "M"
    if 0x0590 <= o <= 0x05FF or 0xFB1D <= o <= 0xFB4F:
        return "R-hebrew" if bidi in ("R", "AL") or o >= 0x05D0 else "M"
    if 0x0700 <= o <= 0x074F:
        return "R-syriac"
    if bidi in ("R", "AL") or (bidi == "AN" and (0x0600 <= o <= 0x06FF or 0x0660 <= o <= 0x0669)):
        return "R-arabic"
    if bidi == "L":
        if 0x0370 <= o <= 0x03FF or 0x1F00 <= o <= 0x1FFF:
            return "L-greek"
        return "L"
    return "N"


def is_rtl_class(c: str) -> bool:
    return c.startswith("R")


def classify_chars(text: str, para_rtl: bool, para_script: str = "R-arabic") -> list[str]:
    """Class per character. Neutrals between two runs of the same direction take that
    direction (UAX #9 N1); otherwise they take the paragraph direction (N2)."""
    classes = []
    prev = None
    for ch in text:
        c = char_class(ch)
        if c == "M":
            c = prev if prev not in (None, "N") else "N"
        classes.append(c)
        if c != "N":
            prev = c
    n = len(classes)
    i = 0
    while i < n:
        if classes[i] != "N":
            i += 1
            continue
        j = i
        while j < n and classes[j] == "N":
            j += 1
        left = classes[i - 1] if i > 0 else None
        right = classes[j] if j < n else None
        if left and right and left == right:
            fill = left
        elif left and right and is_rtl_class(left) and is_rtl_class(right):
            fill = left
        elif left and right and not is_rtl_class(left) and not is_rtl_class(right):
            fill = "L"
        elif para_rtl:
            fill = next((c for c in (left, right) if c and is_rtl_class(c)), para_script)
        else:
            fill = "L"
        for k in range(i, j):
            classes[k] = fill
        i = j
    return classes


def segments(text: str, classes: list[str]) -> list[tuple[str, str]]:
    segs: list[tuple[str, str]] = []
    for ch, c in zip(text, classes):
        if segs and segs[-1][0] == c:
            segs[-1] = (c, segs[-1][1] + ch)
        else:
            segs.append((c, ch))
    return segs


def classify_text(text: str, para_rtl: bool, para_script: str = "R-arabic") -> list[tuple[str, str]]:
    return segments(text, classify_chars(text, para_rtl, para_script))


def dominant_rtl_script(text: str) -> str:
    counts = Counter(c for c in map(char_class, text) if c.startswith("R"))
    return counts.most_common(1)[0][0] if counts else "R-arabic"


def strong_counts(text: str) -> tuple[int, int]:
    rtl = ltr = 0
    for ch in text:
        c = char_class(ch)
        if c.startswith("R"):
            rtl += 1
        elif c.startswith("L"):
            ltr += 1
    return rtl, ltr


# ------------------------------------------------------------------ styles
@dataclass
class StyleSpec:
    style_id: str
    name: str
    kind: str  # paragraph | character
    based_on: str | None = None
    ppr: dict = field(default_factory=dict)
    rpr: dict = field(default_factory=dict)


def ensure_style(styles_root: etree._Element, spec: StyleSpec) -> etree._Element:
    for st in styles_root.findall(q("style")):
        if st.get(q("styleId")) == spec.style_id:
            return st
    st = etree.SubElement(styles_root, q("style"))
    st.set(q("type"), spec.kind)
    st.set(q("customStyle"), "1")
    st.set(q("styleId"), spec.style_id)
    etree.SubElement(st, q("name")).set(q("val"), spec.name)
    if spec.based_on:
        etree.SubElement(st, q("basedOn")).set(q("val"), spec.based_on)
    etree.SubElement(st, q("qFormat"))
    if spec.ppr:
        ppr = etree.SubElement(st, q("pPr"))
        for tag, attrs in spec.ppr.items():
            set_child(ppr, tag, PPR_ORDER, attrs)
    if spec.rpr:
        rpr = etree.SubElement(st, q("rPr"))
        for tag, attrs in spec.rpr.items():
            set_child(rpr, tag, RPR_ORDER, attrs)
    return st


def style_ids(styles_root) -> dict[str, str]:
    """Style display name -> styleId."""
    out = {}
    for st in styles_root.findall(q("style")):
        name = st.find(q("name"))
        if name is not None:
            out[name.get(q("val"))] = st.get(q("styleId"))
    return out


def set_style_fonts(styles_root, style_id: str, fonts: dict[str, str], lang: dict | None = None) -> None:
    for st in styles_root.findall(q("style")):
        if st.get(q("styleId")) != style_id:
            continue
        rpr = st.find(q("rPr"))
        if rpr is None:
            rpr = etree.SubElement(st, q("rPr"))
        set_child(rpr, "rFonts", RPR_ORDER, fonts)
        if lang:
            set_child(rpr, "lang", RPR_ORDER, lang)


def base_style_specs(cfg: dict) -> list[StyleSpec]:
    s = cfg["styles"]
    hl = {}
    specs = [
        StyleSpec("Unconverted", s["unconverted"], "character", "DefaultParagraphFont",
                  rpr={"color": {"val": "9C0006"}, **hl}),
        StyleSpec("NeedsReview", s["needs_review"], "character", "DefaultParagraphFont",
                  rpr={"color": {"val": "7F6000"}}),
        StyleSpec("SectionNumber", s["section_number"], "character", "DefaultParagraphFont"),
        StyleSpec("ArabicText", s["script_char_styles"]["arabic"], "character", "DefaultParagraphFont",
                  rpr={"rtl": {}}),
        StyleSpec("HebrewText", s["script_char_styles"]["hebrew"], "character", "DefaultParagraphFont",
                  rpr={"rtl": {}}),
        StyleSpec("GreekText", s["script_char_styles"]["greek"], "character", "DefaultParagraphFont"),
        StyleSpec("Caption", "Caption", "paragraph", "Normal",
                  ppr={"spacing": {"before": "120", "after": "120"}}, rpr={"i": {}}),
        StyleSpec("Bibliography", "Bibliography", "paragraph", "Normal",
                  ppr={"spacing": {"after": "120"}, "ind": {"left": "720", "hanging": "720"}}),
        StyleSpec("BodyTextRTL", "Body Text RTL", "paragraph", "BodyText", ppr={"bidi": {}}),
        StyleSpec("BlockTextRTL", "Block Text RTL", "paragraph", "BlockText", ppr={"bidi": {}}),
        StyleSpec("FootnoteTextRTL", "Footnote Text RTL", "paragraph", "FootnoteText", ppr={"bidi": {}}),
        StyleSpec("BibliographyRTL", "Bibliography RTL", "paragraph", "Bibliography", ppr={"bidi": {}}),
    ]
    return specs


# ------------------------------------------------------------------ main
@dataclass
class PostStats:
    rtl_runs: int = 0
    rtl_paragraphs: int = 0
    greek_runs: int = 0
    split_runs: int = 0
    highlighted: int = 0
    unconverted: int = 0
    needs_review: int = 0
    separators: int = 0


class PostProcessor:
    def __init__(self, cfg: dict, plan: FontPlan, main_lang: str, diag: Diagnostics):
        self.cfg = cfg
        self.plan = plan
        self.diag = diag
        self.stats = PostStats()
        self.lang_map = self._lang_map(cfg)
        self.main_lang = self._word_lang(main_lang) or "en-US"
        self.style_ids: dict[str, str] = {}

    @staticmethod
    def _lang_map(cfg: dict) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, code in cfg["languages"].items():
            out.setdefault(name.lower(), code)
            out.setdefault(code.lower(), code)
            out.setdefault(code.split("-")[0].lower(), code)
        out.setdefault("grc", out.get("el", "el-GR"))
        return out

    def _word_lang(self, code: str | None) -> str | None:
        if not code:
            return None
        c = code.lower()
        return self.lang_map.get(c) or self.lang_map.get(c.split("-")[0]) or code

    SCRIPT_LANG = {"R-arabic": "ar-SA", "R-hebrew": "he-IL", "R-syriac": "syr-SY", "L-greek": "el-GR"}
    SCRIPT_NAME = {"R-arabic": "arabic", "R-hebrew": "hebrew", "R-syriac": "syriac", "L-greek": "greek"}

    # ---------------------------------------------------------- styles.xml
    def process_styles(self, root: etree._Element, scripts_used: set[str]) -> None:
        for spec in base_style_specs(self.cfg):
            ensure_style(root, spec)
        self.style_ids = style_ids(root)
        defaults = root.find(q("docDefaults"))
        if defaults is None:
            defaults = etree.Element(q("docDefaults"))
            root.insert(0, defaults)
        rpr_default = defaults.find(q("rPrDefault"))
        if rpr_default is None:
            rpr_default = etree.SubElement(defaults, q("rPrDefault"))
        rpr = rpr_default.find(q("rPr"))
        if rpr is None:
            rpr = etree.SubElement(rpr_default, q("rPr"))
        cs_font = self.plan.for_script("arabic") if "R-arabic" in scripts_used else \
            self.plan.for_script("hebrew") if "R-hebrew" in scripts_used else self.plan.main
        rfonts = set_child(rpr, "rFonts", RPR_ORDER)
        for a in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            rfonts.attrib.pop(q(a), None)
        for k, v in {"ascii": self.plan.main, "hAnsi": self.plan.main, "cs": cs_font,
                     "eastAsia": self.plan.for_script("east_asian")}.items():
            rfonts.set(q(k), v)
        bidi_lang = "ar-SA" if "R-arabic" in scripts_used else "he-IL" if "R-hebrew" in scripts_used else "ar-SA"
        set_child(rpr, "lang", RPR_ORDER, {"val": self.main_lang, "bidi": bidi_lang})
        # theme fonts in Normal etc. would override docDefaults: strip theme attributes everywhere
        for rf in root.iter(q("rFonts")):
            for a in ("asciiTheme", "hAnsiTheme", "cstheme"):
                if rf.get(q(a)) is not None:
                    rf.attrib.pop(q(a))
        names = self.cfg["styles"]["script_char_styles"]
        for script_cls, style_name in (("R-arabic", names["arabic"]), ("R-hebrew", names["hebrew"]),
                                       ("L-greek", names["greek"])):
            sid = self.style_ids.get(style_name)
            if not sid:
                continue
            font = self.plan.for_script(self.SCRIPT_NAME[script_cls])
            lang = {"bidi": self.SCRIPT_LANG[script_cls]} if script_cls.startswith("R") else \
                {"val": self.SCRIPT_LANG[script_cls]}
            set_style_fonts(root, sid, {"ascii": font, "hAnsi": font, "cs": font}, lang)
            pct = (self.cfg["fonts"].get("size_percent") or {}).get(self.SCRIPT_NAME[script_cls])
            if pct and script_cls.startswith("R"):
                sz = rpr.find(q("sz"))
                base = int(sz.get(q("val"))) if sz is not None else 24
                size = str(round(base * float(pct) / 100))
                for st in root.findall(q("style")):
                    if st.get(q("styleId")) == sid:
                        srpr = st.find(q("rPr"))
                        set_child(srpr, "sz", RPR_ORDER, {"val": size})
                        set_child(srpr, "szCs", RPR_ORDER, {"val": size})

    # ---------------------------------------------------------- paragraphs
    def _run_text(self, run) -> str:
        return "".join(t.text or "" for t in run.findall(q("t")))

    def _para_text(self, p) -> str:
        return "".join(t.text or "" for t in p.iter(q("t")))

    def process_part(self, root: etree._Element) -> None:
        for t in root.iter(q("t")):
            if t.text:
                t.text = unicodedata.normalize("NFC", t.text)
        ratio = float(self.cfg["styles"].get("rtl_paragraph_ratio", 0.5))
        rtl_variants = self.cfg["styles"]["rtl_variants"]
        for p in root.iter(q("p")):
            rtl, ltr = strong_counts(self._para_text(p))
            para_rtl = rtl > 0 and rtl > ratio * (rtl + ltr)
            if para_rtl:
                self.stats.rtl_paragraphs += 1
                ppr = get_ppr(p)
                pstyle = ppr.find(q("pStyle"))
                current = pstyle.get(q("val")) if pstyle is not None else "Normal"
                variant = rtl_variants.get(current)
                if variant and variant in self.style_ids.values():
                    set_child(ppr, "pStyle", PPR_ORDER, {"val": variant})
                else:
                    set_child(ppr, "bidi", PPR_ORDER)
            self._process_paragraph(p, para_rtl)
        self._captions_and_highlights(root)

    def _process_paragraph(self, p, para_rtl: bool) -> None:
        runs = [r for r in p.iter(q("r")) if self._run_text(r)]
        if not runs:
            return
        texts = [self._run_text(r) for r in runs]
        full = "".join(texts)
        classes = classify_chars(full, para_rtl, dominant_rtl_script(full))
        pos = 0
        for run, text in zip(runs, texts):
            self._process_run(run, text, classes[pos:pos + len(text)])
            pos += len(text)

    def _process_run(self, run, text: str, classes: list[str]) -> None:
        children = [c for c in run if c.tag != q("rPr")]
        splittable = len(children) == 1 and children[0].tag == q("t")
        segs = segments(text, classes)
        if not splittable or len(segs) == 1:
            if len(segs) == 1:
                cls = segs[0][0]
            else:
                counts = Counter()
                for c, chunk in segs:
                    counts[c] += len(chunk)
                cls = counts.most_common(1)[0][0]
            self._apply_class(run, cls)
            return
        parent = run.getparent()
        idx = parent.index(run)
        rpr = run.find(q("rPr"))
        parent.remove(run)
        self.stats.split_runs += 1
        for offset, (cls, chunk) in enumerate(segs):
            new = etree.Element(q("r"))
            if rpr is not None:
                new.append(copy.deepcopy(rpr))
            t = etree.SubElement(new, q("t"))
            t.text = chunk
            t.set(f"{{{XML_NS}}}space", "preserve")
            parent.insert(idx + offset, new)
            self._apply_class(new, cls)

    def _apply_class(self, run, cls: str) -> None:
        rpr = get_rpr(run)
        lang_el = rpr.find(q("lang"))
        pandoc_lang = lang_el.get(q("val")) if lang_el is not None else None
        rstyle = rpr.find(q("rStyle"))
        names = self.cfg["styles"]["script_char_styles"]
        if cls.startswith("R"):
            self.stats.rtl_runs += 1
            script = self.SCRIPT_NAME[cls]
            word_lang = self._word_lang(pandoc_lang)
            if not word_lang or not self._is_rtl_lang(word_lang):
                word_lang = self.SCRIPT_LANG[cls]
            style_name = names.get(script)
            sid = self.style_ids.get(style_name) if style_name else None
            if rstyle is None and sid:
                set_child(rpr, "rStyle", RPR_ORDER, {"val": sid})
            else:
                font = self.plan.for_script(script)
                set_child(rpr, "rFonts", RPR_ORDER, {"ascii": font, "hAnsi": font, "cs": font, "hint": "cs"})
            if rpr.find(q("b")) is not None:
                set_child(rpr, "bCs", RPR_ORDER)
            if rpr.find(q("i")) is not None:
                set_child(rpr, "iCs", RPR_ORDER)
            set_child(rpr, "rtl", RPR_ORDER)
            set_child(rpr, "lang", RPR_ORDER, {"val": self.main_lang, "bidi": word_lang})
        elif cls == "L-greek":
            self.stats.greek_runs += 1
            sid = self.style_ids.get(names.get("greek"))
            if rstyle is None and sid:
                set_child(rpr, "rStyle", RPR_ORDER, {"val": sid})
            elif self.plan.for_script("greek") != self.plan.main:
                font = self.plan.for_script("greek")
                set_child(rpr, "rFonts", RPR_ORDER, {"ascii": font, "hAnsi": font})
            set_child(rpr, "lang", RPR_ORDER, {"val": "el-GR"})
        else:
            if lang_el is not None:
                code = self._word_lang(pandoc_lang)
                if not code or self._is_rtl_lang(code) or code.lower().startswith("el"):
                    code = self.main_lang
                lang_el.set(q("val"), code)
            rtl_el = rpr.find(q("rtl"))
            if rtl_el is not None:
                rpr.remove(rtl_el)
        if len(rpr) == 0:
            run.remove(rpr)

    @staticmethod
    def _is_rtl_lang(code: str) -> bool:
        return code.split("-")[0].lower() in {"ar", "he", "fa", "ur", "syr", "yi", "ps", "dv", "ug", "ckb"}

    def separate_adjacent_refs(self, root) -> None:
        sep = (self.cfg.get("footnotes") or {}).get("adjacent_separator", ",")
        if not sep:
            return
        is_ref = lambda el: el is not None and el.tag == q("r") and el.find(q("footnoteReference")) is not None
        for run in list(root.iter(q("r"))):
            nxt = run.getnext()
            if is_ref(run) and is_ref(nxt):
                new = etree.Element(q("r"))
                rpr = run.find(q("rPr"))
                if rpr is not None:
                    new.append(copy.deepcopy(rpr))
                else:
                    set_child(get_rpr(new), "vertAlign", RPR_ORDER, {"val": "superscript"})
                t = etree.SubElement(new, q("t"))
                t.text = sep
                if self._paragraph_is_rtl(run):
                    set_child(get_rpr(new), "rtl", RPR_ORDER)
                run.addnext(new)
                self.stats.separators += 1

    def _paragraph_is_rtl(self, el) -> bool:
        p = el.getparent()
        while p is not None and p.tag != q("p"):
            p = p.getparent()
        if p is None:
            return False
        ppr = p.find(q("pPr"))
        if ppr is None:
            return False
        if ppr.find(q("bidi")) is not None:
            return True
        ps = ppr.find(q("pStyle"))
        return ps is not None and ps.get(q("val")) in set(self.cfg["styles"]["rtl_variants"].values())

    def _captions_and_highlights(self, root) -> None:
        cap = self.style_ids.get(self.cfg["styles"]["caption"], "Caption")
        for ps in root.iter(q("pStyle")):
            if ps.get(q("val")) in ("ImageCaption", "TableCaption"):
                ps.set(q("val"), cap)
        hl_ids = {self.style_ids.get(self.cfg["styles"]["unconverted"]),
                  self.style_ids.get(self.cfg["styles"]["needs_review"])}
        unconv = self.style_ids.get(self.cfg["styles"]["unconverted"])
        for rs in root.iter(q("rStyle")):
            val = rs.get(q("val"))
            if val in hl_ids:
                rpr = rs.getparent()
                set_child(rpr, "highlight", RPR_ORDER, {"val": "yellow"})
                self.stats.highlighted += 1
                if val == unconv:
                    self.stats.unconverted += 1
                else:
                    self.stats.needs_review += 1


def scripts_in(xml_roots) -> set[str]:
    used = set()
    for root in xml_roots:
        for t in root.iter(q("t")):
            for ch in t.text or "":
                c = char_class(ch)
                if c not in ("N", "M", "L"):
                    used.add(c)
    return used


PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")


def postprocess_docx(src: Path, dst: Path, cfg: dict, plan: FontPlan, main_lang: str,
                     diag: Diagnostics) -> PostStats:
    pp = PostProcessor(cfg, plan, main_lang, diag)
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        data = {n: zin.read(n) for n in names}
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    roots = {n: etree.fromstring(data[n], parser) for n in PARTS if n in data}
    styles = etree.fromstring(data["word/styles.xml"], parser)
    used = scripts_in(roots.values())
    pp.process_styles(styles, used)
    for n, root in roots.items():
        pp.process_part(root)
        pp.separate_adjacent_refs(root)
        data[n] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    data["word/styles.xml"] = etree.tostring(styles, xml_declaration=True, encoding="UTF-8", standalone=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".docx", dir=dst.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in names:
                zout.writestr(n, data[n])
        shutil.move(str(tmp), str(dst))
    finally:
        if tmp.exists():
            tmp.unlink()
    return pp.stats
