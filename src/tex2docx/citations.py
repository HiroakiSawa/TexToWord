"""biblatex citations and bibliographies rendered by biblatex itself ("citation oracle").

All citation commands are copied, in document order and with their footnote
context, into a small document that uses the same biblatex setup. make4ht/tex4ht
renders it to HTML; text between ASCII sentinels is mapped back to LaTeX and
substituted for the original commands. The result is the formatting biblatex
produces in the PDF (ibid./op. cit. tracking, manuscripts style, ...), including
the period and capitalisation of \\footcite notes.
"""

from __future__ import annotations

import html
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from .diagnostics import Diagnostics, context_of
from .markers import Markers
from .texscan import (ScanError, escape_latex, find_group_end, iter_control_sequences,
                      read_control_sequence, read_group, read_optional, replace_spans, skip_spaces)
from .tools import Tool, run
from . import translit

SINGLE = {
    "cite", "Cite", "parencite", "Parencite", "footcite", "footcitetext", "textcite", "Textcite",
    "smartcite", "Smartcite", "autocite", "Autocite", "supercite", "citeauthor", "Citeauthor",
    "citetitle", "Citetitle", "citeyear", "citedate", "citeurl", "fullcite", "footfullcite",
    "citet", "citep", "citealt", "citealp", "Citet", "Citep", "nocite", "citeurldate",
    "citeyearpar", "citeauthorpar",
}
MULTI = {"cites", "Cites", "parencites", "Parencites", "footcites", "footcitetexts", "textcites",
         "Textcites", "smartcites", "Smartcites", "autocites", "Autocites", "supercites"}
VOLCITE = {"volcite", "Volcite", "pvolcite", "Pvolcite", "fvolcite", "ftvolcite", "svolcite",
           "Svolcite", "tvolcite", "Tvolcite", "avolcite", "Avolcite"}
BIBLIO = {"printbibliography", "bibbysection", "bibbysegment", "bibbycategory", "printshorthands",
          "printbiblist"}
SECTIONING = {"newrefsection", "newrefsegment", "endrefsection", "endrefsegment"}
FOOTNOTE_CMDS = {"footnote", "footnotetext", "mkbibfootnote"}

# preamble material that the oracle document needs
KEEP_PACKAGES = {"fontspec", "polyglossia", "babel", "csquotes", "biblatex", "xspace", "semtrans",
                 "color", "xcolor", "etoolbox", "xpatch", "xstring", "url", "xurl", "ifthen"}
BIB_COMMAND = re.compile(
    r"^(?:Declare\w+|Execute\w+|DefineBibliography\w+|UndefineBibliography\w+|renewbibmacro|"
    r"newbibmacro|providebibmacro|AtEvery\w+|AtBeginBibliography|AtBeginShorthands|AtNextCite\w*|"
    r"defbib\w+|addbibresource|addglobalbib|addsectionbib|bibliography|setotherlanguages?|"
    r"setmainlanguage|setdefaultlanguage|setmainfont|setsansfont|setmonofont|newfontfamily|"
    r"setfontfamily|newfontface|defaultfontfeatures|newcommand|providecommand|"
    r"DeclareRobustCommand|NewDocumentCommand|DeclareDocumentCommand|crefname|Crefname|"
    r"SetCiteCommand|DeclareCiteCommand|DeclareNameAlias|DeclareFieldFormat|ExecuteBibliographyOptions|"
    r"DeclareSourcemap|DeclareStyleSourcemap|DeclareDatamodelFields|bibhang|setlength)$")
RENEW_TARGET = re.compile(r"^\\(?:mkbib\w*|bib\w*|newunitpunct|finentrypunct|nameyeardelim|"
                          r"multinamedelim|finalnamedelim|postnotedelim|prenotedelim|multicitedelim|"
                          r"subtitlepunct|intitlepunct|labelnamepunct|revsdnamepunct|namelabeldelim|"
                          r"compcitedelim|textcitedelim|nametitledelim|bibnamedash|cite\w*)$")

SENTINELS = {"TDXIO": "\\emph{", "TDXIC": "}", "TDXBO": "\\textbf{", "TDXBC": "}",
             "TDXSO": "\\textsc{", "TDXSC": "}", "TDXUO": "\\textsuperscript{", "TDXUC": "}",
             "TDXFNOPEN": "\\footnote{", "TDXFNCLOSE": "}"}
# tex4ht font classes look like "rm-lmri-12"; match whole font-name tokens only
ITALIC_TOKEN = re.compile(r"^(?:lmri|cmti|ecti|lmbxi|lmssi|lmtti|cmbxti|ecbi|italic|it|oblique|\w*-?italic)\d*$", re.I)
BOLD_TOKEN = re.compile(r"^(?:lmbx|cmbx|ecbx|lmssbx|bold)\w*$", re.I)
SC_TOKEN = re.compile(r"^(?:lmcsc|cmcsc|eccc|smallcaps)\d*$", re.I)


def _class_has(cls: str, pattern: re.Pattern) -> bool:
    return any(pattern.match(tok) for tok in re.split(r"[\s\-]+", cls) if tok)


@dataclass
class CiteOccurrence:
    start: int
    end: int
    text: str
    in_footnote: bool
    kind: str  # cite | biblio | section


CS_FIRST = {"newfontfamily", "newfontface", "setfontfamily", "renewfontfamily", "newcommand",
            "renewcommand", "providecommand", "DeclareRobustCommand", "NewDocumentCommand",
            "DeclareDocumentCommand", "renewrobustcmd", "newrobustcmd"}


def _consume_args(s: str, i: int, max_groups: int = 12, cs_first: bool = False) -> int:
    """Greedily consume ``*``, ``(..)``, ``[..]`` and ``{..}`` groups following a command."""
    if i < len(s) and s[i] == "*":
        i += 1
    if cs_first:
        j = skip_spaces(s, i)
        if j < len(s) and s[j] == "\\":
            _, i = read_control_sequence(s, j, allow_at=True)
    for _ in range(max_groups):
        j = skip_spaces(s, i)
        if j >= len(s):
            return i
        c = s[j]
        if c == "{":
            i = find_group_end(s, j)
        elif c == "[":
            _, i = read_optional(s, j)
        elif c == "(":
            k = s.find(")", j)
            if k < 0:
                return i
            i = k + 1
        else:
            return i
    return i


def _cite_end(s: str, name: str, end: int) -> int:
    i = end
    if i < len(s) and s[i] == "*":
        i += 1
    if name in MULTI:
        while True:
            j = skip_spaces(s, i)
            if j < len(s) and s[j] == "(":
                k = s.find(")", j)
                i = k + 1
            else:
                break
        while True:
            j = i
            for _ in range(2):
                _, j2 = read_optional(s, j)
                if j2 == j:
                    break
                j = j2
            k = skip_spaces(s, j)
            if k < len(s) and s[k] == "{":
                i = find_group_end(s, k)
            else:
                return i
    if name in VOLCITE:
        _, i = read_optional(s, i)
        i = find_group_end(s, skip_spaces(s, i))
        _, i = read_optional(s, i)
        return find_group_end(s, skip_spaces(s, i))
    if name in BIBLIO:
        _, i = read_optional(s, i)
        return i
    for _ in range(2):
        _, j = read_optional(s, i)
        if j == i:
            break
        i = j
    k = skip_spaces(s, i)
    if k >= len(s) or s[k] != "{":
        raise ScanError(f"\\{name} without key")
    return find_group_end(s, k)


def _footnote_spans(s: str) -> list[tuple[int, int]]:
    spans = []
    for name, start, end in iter_control_sequences(s):
        if name in FOOTNOTE_CMDS:
            try:
                _, i = read_optional(s, end)
                j = skip_spaces(s, i)
                if j < len(s) and s[j] == "{":
                    spans.append((j, find_group_end(s, j)))
            except ScanError:
                continue
    return spans


def find_citations(body: str, diag: Diagnostics) -> list[CiteOccurrence]:
    spans = _footnote_spans(body)
    out = []
    last = -1
    for name, start, end in iter_control_sequences(body):
        if start < last:
            continue
        if name in SINGLE or name in MULTI or name in VOLCITE or name in BIBLIO:
            try:
                stop = _cite_end(body, name, end)
            except ScanError:
                diag.warning("citation", f"\\{name} の引数を解析できませんでした", context_of(body, start))
                continue
            kind = "biblio" if name in BIBLIO else "cite"
            infn = any(a <= start < b for a, b in spans)
            out.append(CiteOccurrence(start, stop, body[start:stop], infn, kind))
            last = stop
        elif name in SECTIONING:
            stop = _consume_args(body, end, 1)
            out.append(CiteOccurrence(start, stop, body[start:stop], False, "section"))
        elif name == "begin" or name == "end":
            try:
                env, stop = read_group(body, skip_spaces(body, end))
            except ScanError:
                continue
            if env in ("refsection", "refsegment"):
                if name == "begin":
                    stop = _consume_args(body, stop, 1)
                out.append(CiteOccurrence(start, stop, body[start:stop], False, "section"))
    return out


# ------------------------------------------------------------------ oracle document
def oracle_preamble(preamble: str, root: Path, bib_map: dict[str, str]) -> str:
    keep: list[str] = []
    last = -1
    for name, start, end in iter_control_sequences(preamble):
        if start < last:
            continue
        try:
            if name == "usepackage":
                stop = _consume_args(preamble, end, 3)
                m = re.search(r"\{([^}]*)\}\s*$", preamble[start:stop].strip())
                pkgs = {p.strip() for p in m.group(1).split(",")} if m else set()
                if pkgs & KEEP_PACKAGES:
                    kept = ",".join(sorted(pkgs & KEEP_PACKAGES))
                    opts = re.match(r"\\usepackage\s*(\[[^\]]*\])?", preamble[start:stop])
                    keep.append(f"\\usepackage{(opts.group(1) or '') if len(pkgs) == 1 else ''}{{{kept}}}")
                last = stop
            elif BIB_COMMAND.match(name):
                stop = _consume_args(preamble, end, cs_first=name in CS_FIRST)
                keep.append(preamble[start:stop])
                last = stop
            elif name in ("renewcommand", "renewrobustcmd"):
                stop = _consume_args(preamble, end, cs_first=True)
                frag = preamble[start:stop]
                target = re.match(r"\\\w+\*?\s*\{?(\\[A-Za-z@]+)", frag)
                if target and RENEW_TARGET.match(target.group(1)):
                    keep.append(frag)
                last = stop
        except ScanError:
            continue
    text = "\n".join(keep)
    for orig, new in bib_map.items():
        text = text.replace("{" + orig + "}", "{" + new + "}")
    font_root = root.as_posix().rstrip("/") + "/"
    text = re.sub(r"Path\s*=\s*(?!/|[A-Za-z]:)([^,\]]+)", lambda m: "Path=" + font_root + m.group(1).strip(), text)
    return text


ORACLE_SETUP = r"""
\makeatletter
\newcommand{\tdxinfoot}[1]{\begingroup\toggletrue{blx@footnote}#1\endgroup}
\renewrobustcmd{\blx@mkbibfootnote}[2]{TDXFNOPEN\blxmkbibnote{foot}{#2}TDXFNCLOSE}
\makeatother
"""
ORACLE_BODY_SETUP = r"""
\DeclareRobustCommand{\emph}[1]{TDXIO#1TDXIC}
\DeclareRobustCommand{\textit}[1]{TDXIO#1TDXIC}
\DeclareRobustCommand{\textsl}[1]{TDXIO#1TDXIC}
\DeclareRobustCommand{\textbf}[1]{TDXBO#1TDXBC}
\DeclareRobustCommand{\textsc}[1]{TDXSO#1TDXSC}
\DeclareRobustCommand{\textsuperscript}[1]{TDXUO#1TDXUC}
\renewrobustcmd{\mkbibemph}[1]{TDXIO#1TDXIC}
\renewrobustcmd{\mkbibitalic}[1]{TDXIO#1TDXIC}
\renewrobustcmd{\mkbibbold}[1]{TDXBO#1TDXBC}
\renewrobustcmd{\mkbibsuperscript}[1]{TDXUO#1TDXUC}
"""


def build_oracle(documentclass: str, preamble: str, occurrences: list[CiteOccurrence]) -> str:
    body = []
    for i, occ in enumerate(occurrences):
        if occ.kind == "section":
            body.append(occ.text)
            continue
        tag = "B" if occ.kind == "biblio" else "C"
        cmd = f"\\tdxinfoot{{{occ.text}}}" if occ.in_footnote and occ.kind == "cite" else occ.text
        body.append(f"\\par TDX{tag}B{i:05d}X {cmd} TDX{tag}E{i:05d}X\\par")
    return (f"\\documentclass{{{documentclass}}}\n{preamble}\n{ORACLE_SETUP}\n\\begin{{document}}\n"
            f"{ORACLE_BODY_SETUP}\n" + "\n".join(body) + "\n\\end{document}\n")


# ------------------------------------------------------------------ HTML -> LaTeX
class _ToLatex(HTMLParser):
    BLOCK = {"p", "div", "dd", "dt", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "table", "dl"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
            return
        close = ""
        cls = a.get("class", "") or ""
        if tag in ("em", "i") or _class_has(cls, ITALIC_TOKEN):
            self.out.append("TDXIO")
            close = "TDXIC"
        elif tag in ("b", "strong") or _class_has(cls, BOLD_TOKEN):
            self.out.append("TDXBO")
            close = "TDXBC"
        elif _class_has(cls, SC_TOKEN):
            self.out.append("TDXSO")
            close = "TDXSC"
        elif tag == "sup":
            self.out.append("TDXUO")
            close = "TDXUC"
        if tag in self.BLOCK:
            self.out.append("\n\n" if tag not in ("br",) else " ")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append(f"TDXH:{tag}:{cls}:")
        if tag not in ("br", "img", "meta", "link", "hr"):
            self.stack.append((tag, close))

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in ("br", "img", "meta", "link", "hr"):
            return
        if not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            t, close = self.stack.pop()
            self.out.append(close)
            if t == tag:
                break
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append(":TDXH")
        if tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self.skip:
            return
        self.out.append(escape_latex(data))


def html_to_marked_latex(html_text: str) -> str:
    m = re.search(r"<body[^>]*>(.*)</body>", html_text, re.S | re.I)
    p = _ToLatex()
    p.feed(m.group(1) if m else html_text)
    return "".join(p.out)


def _finish(fragment: str) -> str:
    text = re.sub(r"[ \t\r\n]+", " ", fragment).strip()
    for k in sorted(SENTINELS, key=len, reverse=True):
        text = text.replace(k, SENTINELS[k])
    text = re.sub(r"\\(emph|textbf|textsc|textsuperscript)\{\s*\}", "", text)
    text = re.sub(r"\\emph\{([^{}]*)\}\\emph\{", r"\\emph{\1", text)
    return text.strip()


HEAD_LEVEL = {"likepartHead": "part", "likechapterHead": "chapter", "likesectionHead": "section",
              "likesubsectionHead": "subsection", "likesubsubsectionHead": "subsubsection",
              "partHead": "part", "chapterHead": "chapter", "sectionHead": "section",
              "subsectionHead": "subsection"}


def _bibliography(fragment: str, markers: Markers, cfg: dict) -> str:
    out = []
    style = cfg["styles"]["bibliography"]
    for block in re.split(r"\n\s*\n", fragment):
        block = block.strip()
        if not block:
            continue
        h = re.match(r"TDXH:(h\d):([^:]*):(.*?):TDXH", block, re.S)
        if h:
            level = HEAD_LEVEL.get(h.group(2).split()[0] if h.group(2) else "", None) or \
                {"h1": "part", "h2": "chapter", "h3": "section", "h4": "subsection"}.get(h.group(1), "section")
            out.append(f"\\{level}*{{{_finish(h.group(3))}}}")
            continue
        entry = _finish(block)
        if entry:
            out.append(markers.para(style, kind="bibliography") + entry)
    return "\n\n" + "\n\n".join(out) + "\n\n"


@dataclass
class OracleResult:
    replacements: dict[int, str]
    missing: list[int]


def run_oracle(occurrences: list[CiteOccurrence], documentclass: str, preamble: str, root: Path,
               bib_files: list[Path], build: Path, tools: dict[str, Tool], cfg: dict, markers: Markers,
               diag: Diagnostics) -> OracleResult:
    work = build / "citations"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    bib_map = {}
    for i, bib in enumerate(bib_files):
        text = bib.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        if cfg["citations"].get("convert_bib_ain_hamza", True) and \
                cfg["transliteration"].get("ain_hamza") == "convert":
            text, _ = translit.convert_accents(text, Diagnostics())
            text, _ = translit.convert_ain_hamza(text, Diagnostics(), cfg["transliteration"]["ain"],
                                                 cfg["transliteration"]["hamza"])
        target = work / f"tdxbib{i}.bib"
        target.write_text(translit.nfc(text), encoding="utf-8", newline="\n")
        rel = bib.relative_to(root).as_posix() if bib.is_relative_to(root) else bib.as_posix()
        bib_map[rel] = target.name
        bib_map[rel[:-4] if rel.endswith(".bib") else rel] = target.stem
    pre = oracle_preamble(preamble, root, bib_map)
    (work / "oracle.tex").write_text(build_oracle(documentclass, pre, occurrences), encoding="utf-8",
                                     newline="\n")
    m4h = tools["make4ht"]
    timeout = int(cfg["compile"].get("timeout", 1200))

    def make4ht() -> None:
        run(m4h.cmd("-x", "-u", "-d", "out", "oracle.tex"), cwd=work, timeout=timeout)

    make4ht()
    if (work / "oracle.bcf").exists():
        if "biber" not in tools:
            diag.error("citation", "biber が無いため引用を整形できません")
        else:
            proc = run(tools["biber"].cmd("oracle"), cwd=work, timeout=timeout)
            if proc.returncode != 0:
                diag.error("citation", "引用整形用文書の biber がエラーで終了しました",
                           (proc.stdout + proc.stderr)[-400:])
        make4ht()
    html_path = work / "out" / "oracle.html"
    if not html_path.exists():
        html_path = work / "oracle.html"
    if not html_path.exists():
        diag.error("citation", "make4ht が HTML を出力しませんでした。引用は未変換のまま残ります")
        return OracleResult({}, list(range(len(occurrences))))
    marked = html_to_marked_latex(html_path.read_text(encoding="utf-8", errors="replace"))
    repl: dict[int, str] = {}
    missing = []
    for i, occ in enumerate(occurrences):
        if occ.kind == "section":
            repl[i] = ""
            continue
        tag = "B" if occ.kind == "biblio" else "C"
        m = re.search(rf"TDX{tag}B{i:05d}X(.*?)TDX{tag}E{i:05d}X", marked, re.S)
        if not m:
            missing.append(i)
            continue
        if occ.kind == "biblio":
            repl[i] = _bibliography(m.group(1), markers, cfg)
        else:
            repl[i] = _finish(m.group(1))
    return OracleResult(repl, missing)


def apply_citations(body: str, documentclass: str, preamble: str, root: Path, bib_files: list[Path],
                    build: Path, tools: dict[str, Tool], cfg: dict, markers: Markers,
                    diag: Diagnostics) -> tuple[str, Counter]:
    stats: Counter = Counter()
    occ = find_citations(body, diag)
    cites = [o for o in occ if o.kind != "section"]
    if not cites:
        return body, stats
    if cfg["citations"].get("method", "oracle") != "oracle":
        diag.warning("citation", "引用の整形が無効化されています（citations.method）。引用は未変換として残ります")
        return body, stats
    if "make4ht" not in tools:
        diag.error("citation", "make4ht が見つからないため引用を整形できません。引用は未変換として残ります")
        return body, stats
    result = run_oracle(occ, documentclass, preamble, root, bib_files, build, tools, cfg, markers, diag)
    replacements = []
    for i, o in enumerate(occ):
        if i in result.replacements:
            text = result.replacements[i]
            replacements.append((o.start, o.end, text))
            if o.kind == "cite":
                stats["引用"] += 1
                if not text and not o.text.startswith("\\nocite"):
                    diag.warning("citation", "整形結果が空でした", o.text)
                if re.search(r"\\textbf\{[^{}]*\}", text) and "textbf" not in o.text:
                    diag.warning("citation", "biblatex が未定義キーとして太字で出力した可能性があります", text)
            else:
                stats["文献目録"] += 1
        elif o.kind != "section":
            diag.error("citation", "引用の整形結果を取得できませんでした（未変換として残します）", o.text)
    return replace_spans(body, replacements), stats
