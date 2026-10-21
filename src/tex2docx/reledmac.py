"""reledmac / reledpar critical apparatus -> ordinary footnotes placed after the lemma.

``\\edtext{lemma}{\\Afootnote{v. P}\\Bfootnote{src}}`` becomes
``lemma\\footnote{[A] lemma] v. P}\\footnote{[B] lemma] src}``. Nested ``\\edtext``
notes are attached to the inner lemma; ``\\lemma{...}`` overrides the lemma shown
in the note. Line numbers are not reproduced.
"""

from __future__ import annotations

import re
from collections import Counter

from .diagnostics import Diagnostics, context_of
from .markers import Markers
from .texscan import (ScanError, find_environments, iter_control_sequences, read_arg, read_group,
                      read_optional, replace_spans, skip_spaces)

NOTE_RE = re.compile(r"^([A-Z])(footnote|endnote)$")
SIDENOTES = {"ledsidenote", "ledleftnote", "ledrightnote", "ledinnernote", "ledouternote"}
FAMILIAR = re.compile(r"^footnote([A-Z])$")
STRUCTURAL_DROP = {"beginnumbering", "endnumbering", "pausenumbering", "resumenumbering", "autopar",
                   "startsub", "endsub", "numberpstarttrue", "numberpstartfalse", "Columns", "Pages",
                   "AtEveryPstart", "ledinnote", "skipnumbering"}
STRUCTURAL_ONE_ARG = {"firstlinenum", "linenumincrement", "setline", "advanceline", "setlinenum",
                      "firstsublinenum", "sublinenumincrement", "linenummargin", "Xnumberonlyfirstinline"}
HEADING_MAP = {"eledchapter": "chapter", "eledsection": "section", "eledsubsection": "subsection",
               "eledsubsubsection": "subsubsection", "eledpart": "part"}


def _first_strong_is_rtl(tex: str) -> bool:
    from .postprocess import char_class
    plain = re.sub(r"\\[A-Za-z@]+\*?|\{|\}|\[[^\]]*\]", " ", tex)
    for ch in plain:
        c = char_class(ch)
        if c.startswith("R"):
            return True
        if c.startswith("L"):
            return False
    return False


class Apparatus:
    def __init__(self, cfg: dict, markers: Markers, diag: Diagnostics):
        self.cfg = cfg["apparatus"]
        self.styles = cfg["styles"]
        self.layout = cfg["parallel"].get("layout", "interleave")
        self.markers = markers
        self.diag = diag
        self.stats: Counter = Counter()

    # -------------------------------------------------------------- notes
    def _note(self, layer: str, lemma: str | None, content: str, kind: str) -> str:
        prefix = self.cfg["prefixes"].get(layer, f"[{layer}]")
        if kind == "endnote":
            self.stats["endnote→footnote"] += 1
        if lemma is None or not lemma.strip():
            text = self.cfg["format_no_lemma"].format(prefix=prefix, note=content, lemma="")
        else:
            text = self.cfg["format"].format(prefix=prefix, lemma=lemma.strip(), note=content.strip())
        direction = self.cfg.get("direction", "auto")
        if direction == "auto":
            # the note reads in the direction of the text it annotates
            direction = "rtl" if _first_strong_is_rtl(lemma if lemma and lemma.strip() else content) else "auto"
        # pandoc forces "Footnote Text" on note paragraphs, so the direction travels as a
        # leading RLM/LRM mark, which the post-processor (and Word) treat as explicit
        lead = {"rtl": "‏", "ltr": "‎"}.get(direction, "")
        self.stats[f"[{layer}]"] += 1
        return "\\footnote{" + lead + text + "}"

    def _parse_notes(self, notes: str, ctx: str) -> tuple[str | None, list[tuple[str, str, str]]]:
        lemma_override = None
        found: list[tuple[str, str, str]] = []
        leftovers = []
        pos = 0
        for name, start, end in iter_control_sequences(notes):
            if start < pos:
                continue
            try:
                if name == "lemma":
                    lemma_override, stop = read_arg(notes, end)
                elif NOTE_RE.match(name):
                    m = NOTE_RE.match(name)
                    _, i = read_optional(notes, end)
                    content, stop = read_arg(notes, i)
                    found.append((m.group(1), self.transform(content), m.group(2)))
                else:
                    continue
            except ScanError:
                self.diag.warning("apparatus", f"\\{name} を解析できませんでした", ctx)
                continue
            leftovers.append(notes[pos:start])
            pos = stop
        leftovers.append(notes[pos:])
        rest = "".join(leftovers).strip()
        if rest:
            self.diag.warning("apparatus", "\\edtext の第 2 引数に注以外の内容があります（そのまま lemma の後に残します）", rest[:120])
            found.append(("", rest, "raw"))
        return lemma_override, found

    def _plain_lemma(self, lemma: str) -> str:
        """Lemma text as shown in notes: inner \\edtext reduced to their lemmas."""
        repl = []
        last = -1
        for name, start, end in iter_control_sequences(lemma):
            if start < last or name != "edtext":
                continue
            try:
                inner, i = read_arg(lemma, end)
                _, stop = read_arg(lemma, i)
            except ScanError:
                continue
            repl.append((start, stop, self._plain_lemma(inner)))
            last = stop
        return replace_spans(lemma, repl)

    # -------------------------------------------------------------- main
    def transform(self, text: str) -> str:
        repl = []
        last = -1
        for name, start, end in iter_control_sequences(text):
            if start < last:
                continue
            ctx = context_of(text, start)
            try:
                if name == "edtext":
                    lemma, i = read_arg(text, end)
                    notes, stop = read_arg(text, i)
                    override, found = self._parse_notes(notes, ctx)
                    shown = override if override is not None else self._plain_lemma(lemma)
                    out = self.transform(lemma)
                    for layer, content, kind in found:
                        out += content if kind == "raw" else self._note(layer, shown, content, kind)
                    self.stats["edtext"] += 1
                    repl.append((start, stop, out))
                    last = stop
                elif name in SIDENOTES:
                    content, stop = read_arg(text, end)
                    repl.append((start, stop, "\\footnote{" + self.cfg["sidenote_prefix"] + " "
                                 + self.transform(content) + "}"))
                    self.stats["sidenote"] += 1
                    last = stop
                elif FAMILIAR.match(name):
                    _, i = read_optional(text, end)
                    content, stop = read_arg(text, i)
                    repl.append((start, stop, "\\footnote{" + self.transform(content) + "}"))
                    self.stats["familiar footnote"] += 1
                    last = stop
                elif name in ("pstart", "pend"):
                    stop = end
                    if text.startswith("*", stop):
                        stop += 1
                    _, stop = read_optional(text, stop)
                    repl.append((start, stop, "\n\n"))
                    last = stop
                elif name in STRUCTURAL_DROP:
                    repl.append((start, end, ""))
                    last = end
                elif name in STRUCTURAL_ONE_ARG:
                    _, stop = read_arg(text, end)
                    repl.append((start, stop, ""))
                    last = stop
                elif name in HEADING_MAP:
                    repl.append((start, end, "\\" + HEADING_MAP[name]))
                    last = end
                elif name in ("edlabel", "edindex"):
                    _, stop = read_arg(text, end)
                    repl.append((start, stop, ""))
                    last = stop
            except ScanError:
                self.diag.warning("apparatus", f"\\{name} を解析できませんでした（未変換として残します）", ctx)
        return replace_spans(text, repl)

    # -------------------------------------------------------------- reledpar
    @staticmethod
    def _paragraphs(side: str) -> list[str]:
        parts = re.split(r"\\pstart\b\*?(?:\[[^\]]*\])?|\\pend\b\*?(?:\[[^\]]*\])?", side)
        pieces = []
        for chunk in re.split(r"\n\s*\n", "\n\n".join(parts)):
            chunk = re.sub(r"\\(?:beginnumbering|endnumbering|autopar)\b", "", chunk).strip()
            if chunk:
                pieces.append(chunk)
        return pieces

    def parallel(self, text: str) -> str:
        repl = []
        for env in ("pairs", "pages"):
            try:
                spans = list(find_environments(text, env))
            except ScanError:
                continue
            for b, bs, be, e in spans:
                body = text[bs:be]
                left = right = ""
                try:
                    for lb, lbs, lbe, _ in find_environments(body, "Leftside"):
                        left += body[lbs:lbe]
                    for rb, rbs, rbe, _ in find_environments(body, "Rightside"):
                        right += body[rbs:rbe]
                except ScanError:
                    self.diag.warning("parallel", f"{env} 環境の Leftside/Rightside を解析できませんでした")
                    continue
                L = [self.transform(p) for p in self._paragraphs(left)]
                R = [self.transform(p) for p in self._paragraphs(right)]
                if len(L) != len(R):
                    self.diag.warning("parallel", f"対訳の段落数が左右で異なります（左 {len(L)} / 右 {len(R)}）")
                repl.append((b, e, self._layout(L, R)))
                self.stats["parallel blocks"] += 1
        return replace_spans(text, repl)

    def _layout(self, L: list[str], R: list[str]) -> str:
        n = max(len(L), len(R))
        L = L + [""] * (n - len(L))
        R = R + [""] * (n - len(R))
        if self.layout == "table":
            rows = " \\\\\n".join(f"{l} & {r}" for l, r in zip(L, R))
            return ("\n\n\\begin{tabular}{p{0.47\\linewidth}p{0.47\\linewidth}}\n" + rows +
                    " \\\\\n\\end{tabular}\n\n")
        if self.layout == "sequential":
            return "\n\n" + "\n\n".join(p for p in L + R if p) + "\n\n"
        out = []
        for l, r in zip(L, R):
            out += [p for p in (l, r) if p]
        return "\n\n" + "\n\n".join(out) + "\n\n"


def transform_apparatus(text: str, cfg: dict, markers: Markers, diag: Diagnostics) -> tuple[str, Counter]:
    app = Apparatus(cfg, markers, diag)
    text = app.parallel(text)
    text = app.transform(text)
    return text, app.stats
