"""Automatic checks on the produced docx (see README: 検証)."""

from __future__ import annotations

import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .fonts import FontPlan, is_east_asian, missing_glyphs
from .postprocess import char_class, q
from .texscan import iter_control_sequences
from .tools import Tool, run

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
M_T = "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"


@dataclass
class Check:
    name: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass
class DocxRun:
    text: str
    style: str | None
    rtl: bool
    bidi_lang: str | None
    part: str
    para: int = 0


@dataclass
class DocxContent:
    runs: list[DocxRun]
    paragraphs: list[str]
    footnote_count: int
    style_names: dict[str, str]

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs)


def read_docx(path: Path) -> DocxContent:
    with zipfile.ZipFile(path) as z:
        parts = {n: etree.fromstring(z.read(n)) for n in
                 ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml") if n in z.namelist()}
        styles = etree.fromstring(z.read("word/styles.xml"))
    names = {}
    for st in styles.findall(q("style")):
        nm = st.find(q("name"))
        if nm is not None:
            names[st.get(q("styleId"))] = nm.get(q("val"))
    runs: list[DocxRun] = []
    paragraphs: list[str] = []
    footnotes = 0
    for pname, root in parts.items():
        if pname == "word/footnotes.xml":
            footnotes = sum(1 for fn in root.findall(q("footnote"))
                            if fn.get(q("type")) in (None, "normal"))
        for p in root.iter(q("p")):
            for r in p.iter(q("r")):
                text = "".join(t.text or "" for t in r.findall(q("t")))
                if not text:
                    continue
                rpr = r.find(q("rPr"))
                style = rtl = lang = None
                if rpr is not None:
                    rs = rpr.find(q("rStyle"))
                    style = names.get(rs.get(q("val"))) if rs is not None else None
                    rtl = rpr.find(q("rtl")) is not None
                    le = rpr.find(q("lang"))
                    lang = le.get(q("bidi")) if le is not None else None
                runs.append(DocxRun(text, style, bool(rtl), lang, pname, len(paragraphs)))
            paragraphs.append("".join(t.text or "" for t in p.iter(q("t"), M_T)))
    return DocxContent(runs, paragraphs, footnotes, names)


# ------------------------------------------------------------------ helpers
FOOTNOTE_COMMANDS = {"footnote", "footnotetext", "footcite", "footcites", "footcitetext",
                     "footcitetexts", "smartcite", "Smartcite"}
APPARATUS_NOTE = re.compile(r"^(?:[A-Z]footnote|[A-Z]endnote|ledsidenote|ledleftnote|ledrightnote|"
                            r"ledinnernote|ledouternote)$")


def expected_footnotes(body: str, autocite_footnote: bool = False) -> Counter:
    c: Counter = Counter()
    for name, _, _ in iter_control_sequences(body):
        if name in FOOTNOTE_COMMANDS:
            c[name] += 1
        elif autocite_footnote and name in ("autocite", "Autocite", "autocites", "Autocites"):
            c[name] += 1
        elif APPARATUS_NOTE.match(name):
            c["apparatus:" + name] += 1
    return c


def _strip_marks(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("M"):
            continue
        out.append(" " if cat == "Lm" else ch)
    return "".join(out)


WORD = re.compile(r"[^\W\d_]+")


def _latin_greek(word: str) -> bool:
    c = char_class(word[0])
    return c in ("L", "L-greek")


def lg_tokens(text: str) -> list[str]:
    return [w.casefold() for w in WORD.findall(_strip_marks(text)) if len(w) >= 2 and _latin_greek(w)]


def rtl_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text)
    out = []
    cur = []
    for ch in text:
        c = char_class(ch)
        if c.startswith("R") or (c == "M" and cur):
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return [w for w in out if any(char_class(ch).startswith("R") for ch in w)]


# pdftotext (without -layout) may glue an all-caps running head onto the first body line
RUNNING_HEAD_PREFIX = re.compile(r"^\s*(?:(?:[\dIVXL]+\.)*[\dIVXL]*\.?\s+)?"
                                 r"(?:(?:[\d.]+\s+)?[A-Z][A-Z\d'’.,:;()\-]+\s+){2,}"
                                 r"(?=[A-Za-z])")


def _mostly_upper(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) >= 0.7 * len(letters)


def _remove_running_heads(pdf_text: str) -> str:
    """Drop page-edge lines that are running heads/feet (repeated, all caps, or page numbers)."""
    pdf_text = pdf_text.replace("\r\n", "\n").replace("\r", "\n")
    pages = pdf_text.split("\f")
    norm = lambda s: re.sub(r"\s+", " ", re.sub(r"\b[ivxlcdmIVXLCDM]+\b|\d+", "", s)).strip().casefold()
    cand_count: Counter = Counter()
    page_lines = []
    for page in pages:
        lines = page.split("\n")
        nonempty = [i for i, l in enumerate(lines) if l.strip()]
        edges = set(nonempty[:3] + nonempty[-3:])
        page_lines.append((lines, edges))
        for n in {norm(lines[i]) for i in edges}:
            if n:
                cand_count[n] += 1
    repeated = {n for n, k in cand_count.items() if k >= 2}
    out_pages = []
    for lines, edges in page_lines:
        kept = []
        for i, l in enumerate(lines):
            s = l.strip()
            if i in edges and (norm(l) in repeated or _mostly_upper(l)
                               or re.fullmatch(r"[\divxlcdmIVXLCDM]+", s or "x")):
                continue
            if i in edges:
                l = RUNNING_HEAD_PREFIX.sub("", l)
            kept.append(l)
        out_pages.append("\n".join(kept))
    return "\n".join(out_pages)


def _join_hyphenation(pdf_text: str, vocab: set[str]) -> str:
    def repl(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        joined = _strip_marks(a + b).casefold()
        return a + b if joined in vocab else a + "-" + b
    return re.sub(r"([^\W\d_]+)-[ \t]*\n\s*([^\W\d_]+)", repl, pdf_text)


def _context(text: str, word: str, width: int = 40) -> str:
    plain = _strip_marks(text).casefold()
    m = re.search(r"(?<![^\W\d_])" + re.escape(word) + r"(?![^\W\d_])", plain)
    if not m:
        return ""
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return text[a:b].replace("\n", " ")


# ------------------------------------------------------------------ checks
def check_footnotes(content: DocxContent, expected: Counter) -> Check:
    exp = sum(expected.values())
    got = content.footnote_count
    detail = [f"{k}: {v}" for k, v in sorted(expected.items())]
    if exp == got:
        return Check("脚注数", OK, f"ソース {exp} / docx {got}", detail)
    return Check("脚注数", FAIL, f"不一致: ソース {exp} / docx {got}", detail)


def check_rtl(content: DocxContent, limit: int) -> Check:
    bad = []
    total = 0
    for r in content.runs:
        if any(char_class(ch).startswith("R") for ch in r.text):
            total += 1
            if not r.rtl or not r.bidi_lang:
                bad.append(f"[{r.part}] {r.text[:60]} (rtl={r.rtl}, bidi={r.bidi_lang})")
    if not total:
        return Check("RTL 属性", SKIP, "アラビア文字・ヘブライ文字なし")
    if bad:
        return Check("RTL 属性", FAIL, f"{len(bad)}/{total} 個の run に w:rtl か bidi 言語がありません", bad[:limit])
    return Check("RTL 属性", OK, f"RTL 文字を含む {total} 個の run すべてに w:rtl と bidi 言語あり")


def check_bad_chars(content: DocxContent, limit: int) -> Check:
    bad = []
    for i, p in enumerate(content.paragraphs):
        for ch in p:
            o = ord(ch)
            if ch == "�" or 0xE000 <= o <= 0xF8FF or o >= 0xF0000:
                bad.append(f"U+{o:04X} 段落 {i + 1}: {p[:60]}")
    if bad:
        return Check("不正文字", FAIL, f"U+FFFD・私用領域の文字が {len(bad)} 個", bad[:limit])
    return Check("不正文字", OK, "U+FFFD・私用領域の文字なし")


def check_glyphs(content: DocxContent, plan: FontPlan, limit: int) -> Check:
    by_font: dict[str, set[str]] = {}
    for r in content.runs:
        for ch in r.text:
            if ch.isspace() or ord(ch) < 0x80:
                continue
            c = char_class(ch)
            script = {"R-arabic": "arabic", "R-hebrew": "hebrew", "R-syriac": "syriac",
                      "L-greek": "greek"}.get(c)
            if script is None:
                o = ord(ch)
                if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF:
                    script = "arabic"  # Arabic punctuation and harakat are shown in the Arabic font
                elif 0x0590 <= o <= 0x05FF or 0xFB1D <= o <= 0xFB4F:
                    script = "hebrew"
                elif is_east_asian(ch):
                    script = "east_asian"
            font = plan.for_script(script) if script else plan.main
            by_font.setdefault(font, set()).add(ch)
    details = []
    unknown = []
    for font, chars in sorted(by_font.items()):
        miss = missing_glyphs(font, chars, plan)
        if miss is None:
            unknown.append(font)
            continue
        for ch in sorted(miss):
            details.append(f"{font}: '{ch}' U+{ord(ch):04X} {unicodedata.name(ch, '?')}")
    note = f"（フォントファイル未検出のため未確認: {', '.join(unknown)}）" if unknown else ""
    if details:
        return Check("グリフ", WARN, f"指定フォントに無い文字 {len(details)} 種{note}", details[:limit])
    return Check("グリフ", OK if not unknown else WARN, f"指定フォントで表示できない文字なし{note}")


def styled_items(content: DocxContent, style: str) -> list[str]:
    """Texts of consecutive runs (within one paragraph) carrying ``style``."""
    items: list[str] = []
    prev = None
    for r in content.runs:
        if r.style == style:
            if prev is not None and prev.style == style and prev.para == r.para and prev.part == r.part:
                items[-1] += r.text
            else:
                items.append(r.text)
        prev = r
    return items


def check_unconverted(content: DocxContent, cfg: dict, limit: int) -> Check:
    unconv_style = cfg["styles"]["unconverted"]
    items = styled_items(content, unconv_style)
    residual = []
    for r in content.runs:
        if r.style == unconv_style:
            continue
        for m in re.finditer(r"\\[A-Za-z@]+", r.text):
            residual.append(f"{m.group(0)}: …{r.text[max(0, m.start() - 20):m.end() + 20]}…")
    details = [f"未変換: {t[:120]}" for t in items[:limit]] + [f"残存: {t}" for t in residual[:limit]]
    if items or residual:
        return Check("未変換マクロ", WARN, f"未変換（ハイライト）{len(items)} 件 / ハイライト外の \\ 文字列 {len(residual)} 件", details)
    return Check("未変換マクロ", OK, "未変換のマクロなし")


def check_citations(content: DocxContent, bib_keys: set[str], limit: int) -> Check:
    text = content.text
    issues = []
    for m in re.finditer(r"\?\?", text):
        issues.append(f"'??': …{text[max(0, m.start() - 30):m.end() + 30]}…")
    for key in sorted(bib_keys):
        if len(key) < 4:
            continue
        for m in re.finditer(r"(?<![\w-])" + re.escape(key) + r"(?![\w-])", text):
            issues.append(f"キー '{key}': …{text[max(0, m.start() - 30):m.end() + 30]}…")
    if issues:
        return Check("引用", FAIL, f"未解決の引用の疑い {len(issues)} 件", issues[:limit])
    return Check("引用", OK, "?? や素の引用キーなし")


def pdf_text(pdf: Path, pdftotext: Tool) -> str | None:
    proc = run(pdftotext.cmd("-enc", "UTF-8", str(pdf), "-"), timeout=600)
    if proc.returncode != 0:
        return None
    return proc.stdout


def check_pdf_coverage(content: DocxContent, ptext: str | None, limit: int) -> Check:
    if ptext is None:
        return Check("PDF とのテキスト網羅性", SKIP, "PDF テキストを取得できませんでした")
    docx_tokens = lg_tokens(content.text)
    vocab = set(docx_tokens)
    cleaned = _join_hyphenation(_remove_running_heads(ptext), vocab)
    pdf_counts = Counter(lg_tokens(cleaned))
    docx_counts = Counter(docx_tokens)
    missing = pdf_counts - docx_counts
    dup = Counter({w: docx_counts[w] - pdf_counts[w] for w in docx_counts
                   if pdf_counts[w] and docx_counts[w] > pdf_counts[w] * 2 and docx_counts[w] - pdf_counts[w] >= 3})
    total = sum(pdf_counts.values())
    miss_n = sum(missing.values())
    details = [f"欠落? '{w}' ×{n}: …{_context(cleaned, w)}…" for w, n in missing.most_common(limit)]
    details += [f"重複? '{w}' docx {docx_counts[w]} / PDF {pdf_counts[w]}" for w, _ in dup.most_common(limit)]
    rate = 100.0 * (total - miss_n) / total if total else 100.0
    summary = f"ラテン・ギリシア文字の語 {total} 語中 {total - miss_n} 語が docx に存在（{rate:.1f}%）。欠落の疑い {miss_n} 語、重複の疑い {len(dup)} 種"
    status = OK if miss_n == 0 and not dup else WARN
    if miss_n >= max(10, 0.05 * total):
        status = FAIL
    return Check("PDF とのテキスト網羅性", status, summary, details)


def check_rtl_coverage(content: DocxContent, source_text: str, limit: int) -> Check:
    src = Counter(rtl_tokens(source_text))
    if not src:
        return Check("アラビア語・ヘブライ語の網羅性", SKIP, "ソースに RTL 文字なし")
    got = Counter(rtl_tokens(content.text))
    missing = src - got
    n = sum(missing.values())
    total = sum(src.values())
    details = [f"'{w}' ×{k}" for w, k in missing.most_common(limit)]
    if n:
        return Check("アラビア語・ヘブライ語の網羅性", FAIL,
                     f"ソースの RTL 語 {total} 語のうち {n} 語が docx に見つかりません", details)
    return Check("アラビア語・ヘブライ語の網羅性", OK, f"ソースの RTL 語 {total} 語すべてが docx に存在")


def check_codepoints(content: DocxContent, source_text: str, limit: int) -> Check:
    def counts(s: str) -> Counter:
        s = unicodedata.normalize("NFC", s)
        return Counter(ch for ch in s if ord(ch) > 0x7F and not ch.isspace()
                       and ch not in "“”‘’–—…")
    src = counts(source_text)
    got = counts(content.text)
    short = [(ch, n, got[ch]) for ch, n in src.items() if got[ch] < n]
    details = [f"'{ch}' U+{ord(ch):04X} {unicodedata.name(ch, '?')}: ソース {n} / docx {g}"
               for ch, n, g in sorted(short, key=lambda x: x[1] - x[2], reverse=True)[:limit]]
    if short:
        return Check("特殊文字の個数", WARN, f"ソースより少ない文字 {len(short)} 種", details)
    return Check("特殊文字の個数", OK, f"非 ASCII 文字 {len(src)} 種すべてがソース以上の個数で存在")


def expected_cell_spans(source: str) -> tuple[int, int]:
    cols = sum(1 for m in re.finditer(r"\\multicolumn\s*\{\s*(\d+)\s*\}", source) if int(m.group(1)) > 1)
    rows = sum(1 for m in re.finditer(r"\\multirow\s*(?:\[[^\]]*\])?\s*\{\s*(-?\d+)\s*\}", source)
               if abs(int(m.group(1))) > 1)
    return cols, rows


def check_cell_spans(docx_path: Path, source: str) -> Check:
    exp_cols, exp_rows = expected_cell_spans(source)
    if not exp_cols and not exp_rows:
        return Check("表のセル結合", SKIP, "\\multicolumn / \\multirow なし")
    with zipfile.ZipFile(docx_path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    got_cols = sum(1 for g in root.iter(q("gridSpan")) if int(g.get(q("val"), "1")) > 1)
    got_rows = sum(1 for v in root.iter(q("vMerge")) if v.get(q("val")) == "restart")
    summary = f"横結合 ソース {exp_cols} / docx {got_cols}、縦結合 ソース {exp_rows} / docx {got_rows}"
    if got_cols < exp_cols or got_rows < exp_rows:
        return Check("表のセル結合", WARN, "再現できなかったセル結合があります: " + summary)
    return Check("表のセル結合", OK, summary)


def bib_keys(bib_files: list[Path]) -> set[str]:
    keys = set()
    for p in bib_files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        keys.update(m.group(1).strip() for m in re.finditer(r"@\w+\s*\{\s*([^,\s]+)\s*,", text)
                    if not m.group(0).lower().startswith(("@string", "@preamble", "@comment")))
    return keys
