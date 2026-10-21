"""Cross-references, heading numbers and caption numbers taken from the LaTeX .aux files.

Numbers come from the real XeLaTeX run, so they match the PDF exactly. Page
numbers do not exist in Word, so ``\\pageref`` is rendered as the number of the
section that contains the label, and reledmac line references are kept as the
PDF line number with a review highlight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import Diagnostics, context_of
from .markers import Markers
from .texscan import (ScanError, find_environments, iter_control_sequences, read_arg,
                      read_group, read_optional, replace_spans, skip_spaces)

SECTION_LEVELS = {"part": -1, "chapter": 0, "section": 1, "subsection": 2,
                  "subsubsection": 3, "paragraph": 4, "subparagraph": 5}
FLOAT_ENVS = {"figure": "figure", "figure*": "figure", "sidewaysfigure": "figure",
              "wrapfigure": "figure", "table": "table", "table*": "table",
              "sidewaystable": "table", "longtable": "table", "wraptable": "table"}

CREF_DEFAULTS = {
    "part": ("part", "parts"), "chapter": ("chapter", "chapters"),
    "section": ("section", "sections"), "subsection": ("section", "sections"),
    "subsubsection": ("section", "sections"), "paragraph": ("paragraph", "paragraphs"),
    "appendix": ("appendix", "appendices"), "figure": ("fig.", "figs."),
    "table": ("table", "tables"), "equation": ("eq.", "eqs."), "footnote": ("footnote", "footnotes"),
    "enumi": ("item", "items"), "enumii": ("item", "items"), "enumiii": ("item", "items"),
    "page": ("page", "pages"), "line": ("line", "lines"), "theorem": ("theorem", "theorems"),
    "lemma": ("lemma", "lemmas"), "definition": ("definition", "definitions"),
}
CREF_DEFAULTS_CAP = {
    "figure": ("Figure", "Figures"), "equation": ("Equation", "Equations"),
}
AUTOREF_NAMES = {"part": "Part", "chapter": "Chapter", "section": "section",
                 "subsection": "subsection", "subsubsection": "subsubsection",
                 "figure": "Figure", "table": "Table", "equation": "Equation",
                 "footnote": "footnote", "page": "page", "enumi": "item"}
PAGE_PREFIX = re.compile(r"\b(?:pp?\.|pages?|S\.|Seite)(?:~|\\,|\\ |\s)*$")


@dataclass
class TocEntry:
    kind: str
    number: str | None
    title: str
    page: str


@dataclass
class AuxData:
    labels: dict[str, tuple[str, str]] = field(default_factory=dict)
    label_titles: dict[str, str] = field(default_factory=dict)
    cref_types: dict[str, str] = field(default_factory=dict)
    toc: list[TocEntry] = field(default_factory=list)
    lof: list[str] = field(default_factory=list)
    lot: list[str] = field(default_factory=list)
    edlabels: dict[str, tuple[str, str]] = field(default_factory=dict)
    found: bool = False


def _clean_number(s: str) -> str:
    s = re.sub(r"\\(?:relax|protect|ignorespaces|nobreakspace)\b\s*", "", s)
    s = re.sub(r"\\[A-Za-z@]+\s*", "", s)
    return s.replace("{", "").replace("}", "").replace("~", " ").strip()


def _groups(s: str, i: int, count: int) -> tuple[list[str], int]:
    out = []
    for _ in range(count):
        j = skip_spaces(s, i)
        if j >= len(s) or s[j] != "{":
            break
        g, i = read_group(s, j)
        out.append(g)
    return out, i


def parse_aux(path: Path, data: AuxData | None = None, seen: set | None = None) -> AuxData:
    data = data or AuxData()
    seen = seen or set()
    if path in seen or not path.is_file():
        return data
    seen.add(path)
    data.found = True
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    for m in re.finditer(r"\\@input\{([^}]*)\}", text):
        parse_aux(path.parent / m.group(1), data, seen)
    for name, start, end in iter_control_sequences(text, allow_at=True):
        try:
            if name == "newlabel":
                key, i = read_group(text, skip_spaces(text, end))
                outer, _ = read_group(text, skip_spaces(text, i))
                parts, _ = _groups(outer, 0, 3)
                if key.endswith("@cref"):
                    if parts:
                        m = re.match(r"\[([^\]]*)\]", parts[0])
                        if m:
                            data.cref_types[key[:-5]] = m.group(1)
                    continue
                num = _clean_number(parts[0]) if parts else ""
                page = _clean_number(parts[1]) if len(parts) > 1 else ""
                data.labels[key] = (num, page)
                if len(parts) > 2 and parts[2].strip():
                    data.label_titles[key] = parts[2].strip()
            elif name == "@writefile":
                kind, i = read_group(text, skip_spaces(text, end))
                content, _ = read_group(text, skip_spaces(text, i))
                m = re.search(r"\\contentsline\s*", content)
                if not m:
                    continue
                parts, _ = _groups(content, m.end(), 3)
                if len(parts) < 2:
                    continue
                level, body = parts[0].strip(), parts[1]
                nm = re.search(r"\\numberline\s*\{", body)
                number = None
                title = body
                if nm:
                    num, after = read_group(body, nm.end() - 1)
                    number = _clean_number(num)
                    title = body[after:]
                elif level == "part":
                    pm = re.match(r"\s*([IVXLCDM]+|\d+)\s*\\hspace\s*\{[^}]*\}", body)
                    if pm:
                        number = pm.group(1)
                        title = body[pm.end():]
                title = _clean_number(title)
                page = _clean_number(parts[2]) if len(parts) > 2 else ""
                if kind == "toc":
                    data.toc.append(TocEntry(level, number, title, page))
                elif kind == "lof" and number:
                    data.lof.append(number)
                elif kind == "lot" and number:
                    data.lot.append(number)
        except ScanError:
            continue
    for m in re.finditer(r"\\l@dmake@labelsR?\s+([^|\n]*)\|([^|\n]*)\|[^\n]*?\{([^{}\n]*)\}\s*$",
                         text, re.M):
        data.edlabels[m.group(3)] = (m.group(1).strip(), m.group(2).strip())
    return data


def _num_key(n: str) -> tuple:
    parts = re.split(r"[.\-]", n)
    key = []
    for p in parts:
        key.append((0, int(p), "") if p.isdigit() else (1, 0, p))
    return tuple(key)


@dataclass
class Heading:
    start: int
    arg_start: int
    arg_end: int
    kind: str
    starred: bool
    number: str | None = None


class Structure:
    """Assign PDF numbers to headings/captions and resolve references in the body text."""

    def __init__(self, body: str, aux: AuxData, cfg: dict, preamble: str, markers: Markers,
                 diag: Diagnostics, has_chapters: bool):
        self.body = body
        self.aux = aux
        self.cfg = cfg
        self.markers = markers
        self.diag = diag
        self.has_chapters = has_chapters
        self.cref_names = dict(CREF_DEFAULTS)
        self.cref_names_cap: dict[str, tuple[str, str]] = {}
        self._read_cref_names(preamble)
        self.label_section: dict[str, str | None] = {}
        self.heading_titles: dict[str, str] = {}

    def _read_cref_names(self, preamble: str) -> None:
        for name, start, end in iter_control_sequences(preamble):
            if name not in ("crefname", "Crefname"):
                continue
            try:
                t, i = read_arg(preamble, end)
                s, i = read_arg(preamble, i)
                p, _ = read_arg(preamble, i)
            except ScanError:
                continue
            target = self.cref_names if name == "crefname" else self.cref_names_cap
            target[t] = (s.replace("~", " "), p.replace("~", " "))
        if re.search(r"\\usepackage\[[^\]]*noabbrev", preamble):
            self.cref_names["figure"] = ("figure", "figures")
            self.cref_names["equation"] = ("equation", "equations")
        self.capitalise = bool(re.search(r"\\usepackage\[[^\]]*capitali[sz]e", preamble))

    # ------------------------------------------------------------ headings
    def _headings(self) -> list[Heading]:
        out = []
        text = self.body
        for name, start, end in iter_control_sequences(text):
            if name not in SECTION_LEVELS:
                continue
            i = end
            starred = i < len(text) and text[i] == "*"
            if starred:
                i += 1
            try:
                _, i = read_optional(text, i)
                j = skip_spaces(text, i)
                if j >= len(text) or text[j] != "{":
                    continue
                _, stop = read_group(text, j)
            except ScanError:
                continue
            out.append(Heading(start, j, stop, name, starred))
        return out

    def number_headings(self) -> list[Heading]:
        headings = self._headings()
        queues: dict[str, list[TocEntry]] = {}
        for e in self.aux.toc:
            if e.number:
                queues.setdefault(e.kind, []).append(e)
        matter = "main"
        matter_cmds = [(s, n) for n, s, _ in iter_control_sequences(self.body)
                       if n in ("frontmatter", "mainmatter", "backmatter", "appendix")]
        for h in headings:
            for pos, cmd in matter_cmds:
                if pos < h.start:
                    matter = {"frontmatter": "front", "mainmatter": "main",
                              "backmatter": "back", "appendix": "main"}[cmd]
            if h.starred or (h.kind == "chapter" and matter != "main"):
                continue
            q = queues.get(h.kind)
            if q:
                h.number = q.pop(0).number
        if self.aux.found:
            leftovers = sum(len(q) for q in queues.values())
            if leftovers:
                self.diag.warning("structure", f"見出し番号の対応付けで .aux 側の項目が {leftovers} 件余りました")
        return headings

    # ------------------------------------------------------------ main pass
    def apply(self) -> str:
        text = self.body
        repl: list[tuple[int, int, str]] = []
        headings = self.number_headings() if self.aux.found else self._headings()
        hfmt = self.cfg["headings"]["formats"]
        numbering = self.cfg["headings"].get("numbering", "static")
        sec_style = self.cfg["styles"]["section_number"]

        # current-section tracking for labels
        events = sorted([(h.start, "h", h) for h in headings] +
                        [(s, "l", (n, e)) for n, s, e in iter_control_sequences(text)
                         if n in ("label", "edlabel")])
        current: dict[int, str | None] = {}
        for pos, kind, obj in events:
            if kind == "h":
                lvl = SECTION_LEVELS[obj.kind]
                for k in list(current):
                    if k >= lvl:
                        del current[k]
                current[lvl] = obj.number
            else:
                name, end = obj
                try:
                    key, stop = read_group(text, skip_spaces(text, end))
                except ScanError:
                    continue
                numbered = [current[k] for k in sorted(current) if current[k]]
                self.label_section[key] = numbered[-1] if numbered else None
                repl.append((pos, stop, ""))
                if name == "label":
                    for h in headings:
                        if h.arg_start <= pos < h.arg_end or 0 <= pos - h.arg_end < 3:
                            self.heading_titles[key] = text[h.arg_start + 1:h.arg_end - 1]

        if numbering == "static":
            for h in headings:
                if not h.number:
                    continue
                fmt = hfmt.get(h.kind, hfmt.get("default", "{n} "))
                label = fmt.format(n=h.number)
                core, tail = label.rstrip(), label[len(label.rstrip()):]
                repl.append((h.arg_start + 1, h.arg_start + 1,
                             self.markers.char(core, sec_style, kind="heading-number") + tail))

        repl += self._captions(text)
        repl += self._references(text)
        return replace_spans(text, self._drop_overlaps(repl))

    @staticmethod
    def _drop_overlaps(repl):
        repl = sorted(repl, key=lambda r: (r[0], r[1]))
        out = []
        last = -1
        for r in repl:
            if r[0] < last:
                continue
            out.append(r)
            last = max(last, r[1]) if r[1] > r[0] else last
        return out

    # ------------------------------------------------------------ captions
    def _captions(self, text: str) -> list[tuple[int, int, str]]:
        repl = []
        cap = self.cfg["captions"]
        names = {"figure": cap["figure_name"], "table": cap["table_name"]}
        counters = {"figure": sorted(self.aux.lof, key=_num_key),
                    "table": sorted(self.aux.lot, key=_num_key)}
        occurrences = []
        for env, kind in FLOAT_ENVS.items():
            try:
                for b, bs, be, e in find_environments(text, env):
                    for n, s, en in iter_control_sequences(text[bs:be]):
                        if n == "caption":
                            occurrences.append((bs + s, bs + en, kind))
            except ScanError:
                continue
        for n, s, en in iter_control_sequences(text):
            if n == "captionof":
                try:
                    k, after = read_arg(text, en)
                except ScanError:
                    continue
                kind = "figure" if k.strip().startswith("fig") else "table"
                occurrences.append((s, after, kind))
        for start, name_end, kind in sorted(set(occurrences)):
            i = name_end
            if i < len(text) and text[i] == "*":
                continue
            try:
                _, i = read_optional(text, i)
                j = skip_spaces(text, i)
                if text[j] != "{":
                    continue
            except (ScanError, IndexError):
                continue
            if not self.aux.found or not counters[kind]:
                continue
            number = counters[kind].pop(0)
            prefix = cap["format"].format(name=names[kind], n=number)
            repl.append((start, j + 1, "\\caption{" + prefix))
        return repl

    # ------------------------------------------------------------ references
    def _ref_number(self, key: str) -> str | None:
        if key in self.aux.labels:
            return self.aux.labels[key][0]
        return None

    def _type_of(self, key: str) -> str:
        if key in self.aux.cref_types:
            return self.aux.cref_types[key]
        prefix = key.split(":", 1)[0] if ":" in key else ""
        return {"sec": "section", "ch": "chapter", "chap": "chapter", "fig": "figure",
                "tab": "table", "eq": "equation", "fn": "footnote"}.get(prefix, "section")

    def _unresolved(self, key: str, cmd: str, ctx: str) -> str:
        self.diag.warning("reference", f"\\{cmd}{{{key}}} を解決できません", ctx)
        fmt = self.cfg["references"]["unresolved_format"]
        return self.markers.char(fmt.format(key=key).replace("_", "\\_"),
                                 self.cfg["styles"]["needs_review"], highlight=True,
                                 kind="unresolved-ref")

    def _section_of(self, key: str) -> str | None:
        return self.label_section.get(key)

    def _cref_text(self, keys: list[str], capital: bool) -> str | None:
        nums = []
        types = []
        for k in keys:
            n = self._ref_number(k)
            if n is None:
                return None
            nums.append(n)
            types.append(self._type_of(k))
        groups: list[tuple[str, list[str]]] = []
        for t, n in zip(types, nums):
            if groups and groups[-1][0] == t:
                groups[-1][1].append(n)
            else:
                groups.append((t, [n]))
        out = []
        for t, ns in groups:
            names = self.cref_names_cap.get(t) if capital else None
            if names is None:
                sing, plur = self.cref_names.get(t, (t, t + "s"))
                if capital or self.capitalise:
                    sing, plur = CREF_DEFAULTS_CAP.get(t, (sing[:1].upper() + sing[1:],
                                                          plur[:1].upper() + plur[1:]))
                names = (sing, plur)
            fmt = (lambda x: f"({x})") if t == "equation" else (lambda x: x)
            ns = [fmt(x) for x in ns]
            if len(ns) == 1:
                out.append(f"{names[0]} {ns[0]}")
            else:
                out.append(f"{names[1]} " + ", ".join(ns[:-1]) + " and " + ns[-1])
            capital = False
        return out[0] if len(out) == 1 else ", ".join(out[:-1]) + " and " + out[-1]

    def _references(self, text: str) -> list[tuple[int, int, str]]:
        repl = []
        refcfg = self.cfg["references"]
        review = self.cfg["styles"]["needs_review"]
        handled = {"ref", "eqref", "autoref", "Autoref", "cref", "Cref", "labelcref", "pageref",
                   "cpageref", "Cpageref", "vref", "Vref", "nameref", "edlineref", "edpageref",
                   "sublineref", "lineref", "crefrange", "Crefrange"}
        for name, start, end in iter_control_sequences(text):
            if name not in handled:
                continue
            i = end
            if i < len(text) and text[i] == "*":
                i += 1
            try:
                key, stop = read_arg(text, i)
                key2 = None
                if name in ("crefrange", "Crefrange"):
                    key2, stop = read_arg(text, stop)
            except ScanError:
                continue
            key = key.strip()
            ctx = context_of(text, start, stop)
            out = None
            rstart = start
            if name in ("ref", "vref", "Vref"):
                n = self._ref_number(key)
                out = n if n is not None else None
            elif name == "eqref":
                n = self._ref_number(key)
                out = f"({n})" if n is not None else None
            elif name in ("autoref", "Autoref"):
                n = self._ref_number(key)
                if n is not None:
                    t = self._type_of(key)
                    nm = AUTOREF_NAMES.get(t, t)
                    out = f"{nm[:1].upper() + nm[1:] if name == 'Autoref' else nm}~{n}"
            elif name in ("cref", "Cref", "labelcref"):
                keys = [k.strip() for k in key.split(",") if k.strip()]
                if name == "labelcref":
                    nums = [self._ref_number(k) for k in keys]
                    out = None if None in nums else ", ".join(nums)
                else:
                    out = self._cref_text(keys, capital=name == "Cref")
            elif name in ("crefrange", "Crefrange"):
                a, b = self._ref_number(key), self._ref_number(key2.strip())
                if a and b:
                    t = self._type_of(key)
                    names = self.cref_names.get(t, (t, t + "s"))
                    plur = names[1][:1].upper() + names[1][1:] if name == "Crefrange" else names[1]
                    out = f"{plur} {a} to {b}"
            elif name == "nameref":
                out = self.aux.label_titles.get(key) or self.heading_titles.get(key)
            elif name in ("pageref", "cpageref", "Cpageref", "edpageref"):
                keys = [k.strip() for k in key.split(",")]
                secs = [self._section_of(k) for k in keys]
                if all(secs):
                    out = ", ".join(refcfg["section_sign"] + s for s in secs)
                    if refcfg.get("pageref_absorb_prefix", True):
                        m = PAGE_PREFIX.search(text[max(0, start - 12):start])
                        if m:
                            rstart = start - (len(text[max(0, start - 12):start]) - m.start())
                    self.diag.info("pageref", f"\\{name}{{{key}}} を節番号 {out} に置き換えました", ctx)
            elif name in ("edlineref", "lineref", "sublineref"):
                lab = self.aux.edlabels.get(key)
                if lab and lab[1].isdigit() and int(lab[1]) > 0:
                    out = self.markers.char(refcfg["edlineref_format"].format(n=lab[1]), review,
                                            highlight=True, kind="edlineref")
                    self.diag.warning("edlineref",
                                      f"\\{name}{{{key}}} は PDF の行番号 {lab[1]} として残しました（要確認）", ctx)
            if out is None:
                out = self._unresolved(key, name, ctx)
            repl.append((rstart, stop, out))
        return repl
