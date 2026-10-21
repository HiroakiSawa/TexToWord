"""Resolve \\input/\\include/\\subfile/\\import into one document and strip comments."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import Diagnostics
from .texscan import (ScanError, find_environments, iter_control_sequences, read_arg,
                      read_group, read_optional, skip_spaces)

VERBATIM_BEGIN = re.compile(r"\\begin\{(verbatim\*?|Verbatim|lstlisting|minted)\}")
INCLUDE_COMMANDS = {"input", "include", "subfile", "subfileinclude", "import", "subimport",
                    "inputfrom", "subinputfrom", "includefrom", "subincludefrom"}
MAX_DEPTH = 30
PRIMITIVE_CONDITIONALS = {
    "if", "ifx", "ifnum", "ifdim", "ifodd", "ifvmode", "ifhmode", "ifmmode", "ifinner",
    "ifcase", "ifcat", "ifvoid", "ifhbox", "ifvbox", "ifeof", "iftrue", "iffalse",
    "ifdefined", "ifcsname", "iffontchar",
}


def read_text(path: Path) -> str:
    """Read UTF-8 TeX source, normalising CRLF/CR line endings and dropping a BOM."""
    data = path.read_bytes()
    text = data.decode("utf-8-sig", errors="strict")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _unescaped_percent(line: str) -> int:
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            return i
        i += 1
    return -1


def strip_comments(text: str) -> str:
    """Remove ``%`` comments with TeX semantics (the comment also eats the line end)."""
    out: list[str] = []
    joining = False
    verbatim_end: str | None = None
    for line in text.split("\n"):
        if verbatim_end is not None:
            out.append(line)
            if verbatim_end in line:
                verbatim_end = None
            joining = False
            continue
        m = VERBATIM_BEGIN.search(line)
        if m:
            verbatim_end = "\\end{" + m.group(1) + "}"
            pos = _unescaped_percent(line[: m.start()])
            if pos < 0:
                if verbatim_end in line[m.end():]:
                    verbatim_end = None
                if joining:
                    out[-1] += line.lstrip(" \t")
                else:
                    out.append(line)
                joining = False
                continue
            verbatim_end = None
        pos = _unescaped_percent(line)
        content = line if pos < 0 else line[:pos]
        if joining:
            stripped = content.lstrip(" \t")
            if stripped == "" and pos < 0:
                out.append("")
            else:
                out[-1] += stripped
        else:
            out.append(content)
        joining = pos >= 0
    return "\n".join(out)


def remove_iffalse(text: str) -> str:
    """Drop ``\\iffalse ... \\fi`` blocks (respecting nested conditionals)."""
    result = []
    pos = 0
    tokens = list(iter_control_sequences(text))
    idx = 0
    while idx < len(tokens):
        name, start, end = tokens[idx]
        if name != "iffalse":
            idx += 1
            continue
        depth = 1
        j = idx + 1
        while j < len(tokens) and depth:
            n2 = tokens[j][0]
            if n2 in PRIMITIVE_CONDITIONALS:
                depth += 1
            elif n2 == "fi":
                depth -= 1
            j += 1
        if depth:
            break
        stop = tokens[j - 1][2]
        result.append(text[pos:start])
        pos = stop
        idx = j
    result.append(text[pos:])
    return "".join(result)


def remove_comment_envs(text: str) -> str:
    try:
        spans = [(b, e) for b, _, _, e in find_environments(text, "comment")]
    except ScanError:
        return text
    for b, e in reversed(spans):
        text = text[:b] + text[e:]
    return text


def clean_source(text: str) -> str:
    text = strip_comments(text)
    text = remove_comment_envs(text)
    text = remove_iffalse(text)
    m = re.search(r"\\endinput(?![A-Za-z@])", text)
    if m:
        text = text[: m.start()]
    return text


@dataclass
class FlatDocument:
    main: Path
    root: Path
    documentclass: str
    class_options: str
    preamble: str
    body: str
    tex_files: list[Path] = field(default_factory=list)
    bib_files: list[Path] = field(default_factory=list)

    @property
    def has_chapters(self) -> bool:
        return self.documentclass in {"book", "report", "memoir", "scrbook", "scrreprt"} or \
            "\\chapter" in self.body


class FlattenError(RuntimeError):
    pass


def _resolve(name: str, bases: list[Path]) -> Path | None:
    name = name.strip()
    candidates = [name] if name.endswith(".tex") else [name + ".tex", name]
    for base in bases:
        for cand in candidates:
            p = (base / cand)
            if p.is_file():
                return p.resolve()
    return None


def _document_body(text: str) -> str:
    b = text.find("\\begin{document}")
    if b < 0:
        return text
    e = text.find("\\end{document}", b)
    return text[b + len("\\begin{document}") : e if e >= 0 else len(text)]


class Flattener:
    def __init__(self, root: Path, diag: Diagnostics):
        self.root = root
        self.diag = diag
        self.files: list[Path] = []

    def load(self, path: Path) -> str:
        if path not in self.files:
            self.files.append(path)
        return clean_source(read_text(path))

    def expand(self, text: str, current_dir: Path, depth: int = 0, stack: tuple = ()) -> str:
        if depth > MAX_DEPTH:
            raise FlattenError("\\input の入れ子が深すぎます（循環参照の可能性）")
        pieces = []
        pos = 0
        for name, start, end in list(iter_control_sequences(text)):
            if start < pos or name not in INCLUDE_COMMANDS:
                continue
            try:
                replacement, stop = self._include(text, name, start, end, current_dir, depth, stack)
            except ScanError:
                continue
            if replacement is None:
                continue
            pieces.append(text[pos:start])
            pieces.append(replacement)
            pos = stop
        pieces.append(text[pos:])
        return "".join(pieces)

    def _include(self, text, name, start, end, current_dir, depth, stack):
        if name in {"import", "subimport", "inputfrom", "subinputfrom", "includefrom", "subincludefrom"}:
            d, i = read_arg(text, end)
            f, stop = read_arg(text, i)
            base = (current_dir if name.startswith("sub") else self.root) / d
            bases = [base]
            next_dir = base
        else:
            j = skip_spaces(text, end)
            if name == "input" and j < len(text) and text[j] != "{":
                m = re.match(r"[^\s{}\\]+", text[j:])
                if not m:
                    return None, end
                f, stop = m.group(0), j + m.end()
            else:
                f, stop = read_group(text, j) if j < len(text) and text[j] == "{" else read_arg(text, end)
            bases = [self.root, current_dir]
            next_dir = None
        target = _resolve(f, bases)
        if target is None:
            self.diag.error("include", f"\\{name}{{{f}}} のファイルが見つかりません", text[start:stop])
            return f"\\tdxmissinginclude{{{f}}}", stop
        if target in stack:
            self.diag.error("include", f"循環した \\{name}: {target.name}")
            return "", stop
        content = self.load(target)
        if name in {"subfile", "subfileinclude"} or "\\documentclass" in content:
            content = _document_body(content)
        if name in {"include", "subfileinclude", "includefrom", "subincludefrom"}:
            content = "\n\n" + content + "\n\n"
        sub_dir = next_dir or target.parent
        return self.expand(content, sub_dir, depth + 1, stack + (target,)), stop


DOCCLASS = re.compile(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")


def flatten(main: Path, diag: Diagnostics) -> FlatDocument:
    main = main.resolve()
    root = main.parent
    fl = Flattener(root, diag)
    text = fl.load(main)
    m = DOCCLASS.search(text)
    if not m:
        raise FlattenError(f"{main.name} に \\documentclass がありません")
    b = text.find("\\begin{document}")
    e = text.rfind("\\end{document}")
    if b < 0 or e < 0:
        raise FlattenError(f"{main.name} に \\begin{{document}} / \\end{{document}} がありません")
    preamble = fl.expand(text[m.end():b], root)
    body = fl.expand(text[b + len("\\begin{document}"):e], root)
    doc = FlatDocument(main=main, root=root, documentclass=m.group(2).strip(),
                       class_options=(m.group(1) or "").strip(), preamble=preamble, body=body,
                       tex_files=list(fl.files))
    doc.bib_files = find_bib_files(preamble + body, root, diag)
    return doc


def find_bib_files(text: str, root: Path, diag: Diagnostics) -> list[Path]:
    out: list[Path] = []
    for name, start, end in iter_control_sequences(text):
        if name not in ("addbibresource", "bibliography", "addglobalbib", "addsectionbib"):
            continue
        try:
            _, i = read_optional(text, end)
            arg, _ = read_arg(text, i)
        except ScanError:
            continue
        for item in arg.split(",") if name == "bibliography" else [arg]:
            item = item.strip()
            if not item:
                continue
            if name == "bibliography" and not item.endswith(".bib"):
                item += ".bib"
            p = (root / item)
            if p.is_file():
                if p.resolve() not in out:
                    out.append(p.resolve())
            else:
                diag.warning("bibliography", f"文献ファイルが見つかりません: {item}")
    return out
