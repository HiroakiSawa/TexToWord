"""Collection and textual expansion of user-defined macros and environments."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .diagnostics import Diagnostics, context_of
from .texscan import (ScanError, find_environments, iter_control_sequences, read_arg,
                      read_control_sequence, read_group, read_optional, replace_spans,
                      skip_spaces)

DEFINERS_NEW = {"newcommand", "providecommand", "DeclareRobustCommand", "newrobustcmd",
                "providerobustcmd"}
DEFINERS_RENEW = {"renewcommand", "renewrobustcmd"}
DEFINERS_XPARSE = {"NewDocumentCommand", "DeclareDocumentCommand", "ProvideDocumentCommand",
                   "RenewDocumentCommand", "NewExpandableDocumentCommand",
                   "DeclareExpandableDocumentCommand"}
DEFINERS_DEF = {"def", "gdef", "edef", "xdef"}
ENV_NEW = {"newenvironment", "NewDocumentEnvironment", "DeclareDocumentEnvironment"}
ENV_RENEW = {"renewenvironment", "RenewDocumentEnvironment"}

XSPACE_EXCEPTIONS = set(",.'/?;:!~-)}{") | {"\n"}
MAX_PASSES = 60
PARAM = re.compile(r"#(#|[1-9])")
TRAILING_CS = re.compile(r"\\[A-Za-z@]+$")


@dataclass
class MacroDef:
    name: str
    body: str
    nargs: int = 0
    default: str | None = None
    argspec: list[str] | None = None
    kind: str = "newcommand"


@dataclass
class EnvDef:
    name: str
    begin: str
    end: str
    nargs: int = 0
    default: str | None = None
    argspec: list[str] | None = None


@dataclass
class MacroTable:
    macros: dict[str, MacroDef] = field(default_factory=dict)
    envs: dict[str, EnvDef] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def _read_name(s: str, i: int) -> tuple[str, int]:
    j = skip_spaces(s, i)
    if s[j] == "{":
        inner, end = read_group(s, j)
        inner = inner.strip()
        if not inner.startswith("\\"):
            raise ScanError("macro name must start with a backslash")
        name, _ = read_control_sequence(inner, 0, allow_at=True)
        return name, end
    name, end = read_control_sequence(s, j, allow_at=True)
    return name, end


def _parse_argspec(spec: str) -> list[str] | None:
    out = []
    i = 0
    spec = spec.replace(" ", "").replace("\n", "")
    while i < len(spec):
        c = spec[i]
        if c in "mos":
            out.append(c)
            i += 1
        elif c == "O" and i + 1 < len(spec) and spec[i + 1] == "{":
            inner, end = read_group(spec, i + 1)
            out.append("O" + inner)
            i = end
        elif c == "+":
            i += 1
        else:
            return None
    return out


def collect_definitions(text: str, table: MacroTable, diag: Diagnostics,
                        expand_renewed: set[str]) -> str:
    """Record definitions found in ``text`` and return ``text`` with them removed."""
    removals = []
    for name, start, end in list(iter_control_sequences(text)):
        if removals and start < removals[-1][1]:
            continue
        try:
            if name in DEFINERS_NEW or name in DEFINERS_RENEW:
                i = end
                if i < len(text) and text[i] == "*":
                    i += 1
                mname, i = _read_name(text, i)
                nargs_s, i = read_optional(text, i)
                default, i = read_optional(text, i)
                body, i = read_arg(text, i)
                nargs = int(nargs_s) if nargs_s else 0
                renew = name in DEFINERS_RENEW
                if "@" in mname:
                    pass
                elif renew and mname not in table.macros and mname not in expand_renewed:
                    table.skipped.append(mname)
                elif name == "providecommand" and mname in table.macros:
                    pass
                else:
                    table.macros[mname] = MacroDef(mname, body, nargs, default)
                removals.append((start, i, ""))
            elif name in DEFINERS_XPARSE:
                mname, i = _read_name(text, end)
                spec, i = read_arg(text, i)
                body, i = read_arg(text, i)
                argspec = _parse_argspec(spec)
                if argspec is None:
                    diag.warning("macro", f"\\{mname} の引数仕様 {{{spec}}} には未対応のため展開しません")
                    table.skipped.append(mname)
                elif name.startswith("Renew") and mname not in table.macros and mname not in expand_renewed:
                    table.skipped.append(mname)
                else:
                    table.macros[mname] = MacroDef(mname, body, len(argspec), argspec=argspec,
                                                   kind="xparse")
                removals.append((start, i, ""))
            elif name in DEFINERS_DEF:
                j = skip_spaces(text, end)
                mname, i = read_control_sequence(text, j, allow_at=True)
                k = text.find("{", i)
                if k < 0:
                    continue
                params = text[i:k]
                body, i = read_group(text, k)
                if "@" in mname:
                    continue
                if re.fullmatch(r"(#[1-9])*", params.strip()) and \
                        params.replace(" ", "") == "".join(f"#{n}" for n in range(1, params.count("#") + 1)):
                    table.macros[mname] = MacroDef(mname, body, params.count("#"), kind="def")
                else:
                    diag.warning("macro", f"区切り付き引数の \\def\\{mname} は展開しません", params)
                    table.skipped.append(mname)
                    continue
                removals.append((start, i, ""))
            elif name in ENV_NEW or name in ENV_RENEW:
                ename, i = read_arg(text, end)
                if name in ("NewDocumentEnvironment", "DeclareDocumentEnvironment", "RenewDocumentEnvironment"):
                    spec, i = read_arg(text, i)
                    argspec = _parse_argspec(spec)
                    b, i = read_arg(text, i)
                    e, i = read_arg(text, i)
                    if argspec is None:
                        diag.warning("macro", f"環境 {ename} の引数仕様 {{{spec}}} には未対応です")
                        table.skipped.append(ename)
                    else:
                        table.envs[ename] = EnvDef(ename, b, e, len(argspec), argspec=argspec)
                else:
                    nargs_s, i = read_optional(text, i)
                    default, i = read_optional(text, i)
                    b, i = read_arg(text, i)
                    e, i = read_arg(text, i)
                    if name in ENV_RENEW and ename not in table.envs:
                        table.skipped.append(ename)
                    else:
                        table.envs[ename] = EnvDef(ename, b, e, int(nargs_s) if nargs_s else 0, default)
                removals.append((start, i, ""))
            elif name == "let":
                j = skip_spaces(text, end)
                a, i = read_control_sequence(text, j, allow_at=True)
                i = skip_spaces(text, i)
                if i < len(text) and text[i] == "=":
                    i = skip_spaces(text, i + 1)
                if i >= len(text) or text[i] != "\\":
                    continue
                b, i = read_control_sequence(text, i, allow_at=True)
                if b in table.macros and "@" not in a:
                    src = table.macros[b]
                    table.macros[a] = MacroDef(a, src.body, src.nargs, src.default, src.argspec, src.kind)
                    removals.append((start, i, ""))
        except (ScanError, IndexError, ValueError):
            diag.warning("macro", f"定義 \\{name} を解析できませんでした", context_of(text, start))
    return replace_spans(text, removals)


def _substitute(body: str, args: list[str]) -> str:
    def repl(m: re.Match) -> str:
        if m.group(1) == "#":
            return "#"
        idx = int(m.group(1)) - 1
        return args[idx] if idx < len(args) else ""

    out = []
    pos = 0
    for m in PARAM.finditer(body):
        out.append(body[pos:m.start()])
        value = repl(m)
        before = "".join(out)
        if TRAILING_CS.search(before) and value[:1].isalpha():
            value = " " + value
        out.append(value)
        pos = m.end()
    out.append(body[pos:])
    return "".join(out)


def _read_args(text: str, i: int, d: MacroDef | EnvDef) -> tuple[list[str], int]:
    args: list[str] = []
    if d.argspec is not None:
        for spec in d.argspec:
            if spec == "m":
                a, i = read_arg(text, i)
                args.append(a)
            elif spec == "s":
                if i < len(text) and text[i] == "*":
                    args.append("\\BooleanTrue")
                    i += 1
                else:
                    args.append("\\BooleanFalse")
            else:
                val, i2 = read_optional(text, i)
                if val is None:
                    args.append(spec[1:] if spec.startswith("O") else "-NoValue-")
                else:
                    args.append(val)
                    i = i2
        return args, i
    n = d.nargs
    if d.default is not None and n >= 1:
        val, i2 = read_optional(text, i)
        if val is None:
            args.append(d.default)
        else:
            args.append(val)
            i = i2
        n -= 1
    for _ in range(n):
        a, i = read_arg(text, i)
        args.append(a)
    return args, i


def _swallow_after_control_word(text: str, i: int) -> int:
    """TeX ignores blanks (and one line end, but never a blank line) after a control word."""
    return skip_spaces(text, i, newlines=True)


def _xspace_needed(text: str, i: int) -> bool:
    if i >= len(text):
        return False
    c = text[i]
    if c in XSPACE_EXCEPTIONS:
        return False
    if c == "\\":
        nxt = text[i + 1:i + 2]
        if nxt in (" ", "/", "\\") or text.startswith(("\\footnote", "\\footnotemark", "\\xspace",
                                                       "\\edtext", "\\par"), i):
            return False
    return True


def _protected(text: str) -> list[tuple[int, int]]:
    from .texscan import VERBATIM_ENVS
    spans = []
    for env in VERBATIM_ENVS:
        try:
            spans += [(b, e) for b, _, _, e in find_environments(text, env)]
        except ScanError:
            pass
    for name, start, end in iter_control_sequences(text):
        if name in ("url", "verb"):
            if name == "verb" and end < len(text):
                k = text.find(text[end], end + 1)
                if k > 0:
                    spans.append((start, k + 1))
            else:
                j = skip_spaces(text, end)
                if j < len(text) and text[j] == "{":
                    try:
                        _, e = read_group(text, j)
                        spans.append((start, e))
                    except ScanError:
                        pass
    return spans


def _in(spans, pos) -> bool:
    return any(a <= pos < b for a, b in spans)


def expand(text: str, table: MacroTable, diag: Diagnostics) -> tuple[str, int]:
    """Expand user macros and environments until a fixed point. Returns (text, count)."""
    total = 0
    for _ in range(MAX_PASSES):
        spans = _protected(text)
        replacements = []
        last_end = -1
        for name, start, end in iter_control_sequences(text):
            if start < last_end or _in(spans, start):
                continue
            if name == "begin" and table.envs:
                try:
                    ename, after = read_group(text, skip_spaces(text, end))
                except ScanError:
                    continue
                env = table.envs.get(ename)
                if env is None:
                    continue
                try:
                    for b, bstart, bend, e in find_environments(text[start:], ename):
                        body_start, body_end, env_end = start + bstart, start + bend, start + e
                        break
                    else:
                        continue
                    args, i = _read_args(text, after, env)
                    content = text[i:body_end]
                except ScanError:
                    diag.warning("macro", f"環境 {ename} の展開に失敗しました", context_of(text, start))
                    continue
                replacements.append((start, env_end, _substitute(env.begin, args) + content + env.end))
                last_end = env_end
                continue
            d = table.macros.get(name)
            if d is None:
                continue
            try:
                args, i = _read_args(text, end, d)
            except ScanError:
                diag.warning("macro", f"\\{name} の引数を読めませんでした", context_of(text, start))
                continue
            body = _substitute(d.body, args)
            xspace = False
            stripped = body.rstrip()
            if stripped.endswith("\\xspace"):
                body = stripped[: -len("\\xspace")]
                xspace = True
            if (d.nargs == 0 and not d.argspec) and name[:1].isalpha():
                i = _swallow_after_control_word(text, i)
            if xspace and _xspace_needed(text, i):
                body += " "
            elif TRAILING_CS.search(body) and text[i:i + 1].isalpha():
                body += "{}"
            replacements.append((start, i, body))
            last_end = i
        if not replacements:
            return text, total
        total += len(replacements)
        text = replace_spans(text, replacements)
    diag.error("macro", "マクロ展開が収束しません（再帰的な定義の可能性）。展開を打ち切りました")
    return text, total


def add_replacements(table: MacroTable, replace: dict[str, str]) -> None:
    for name, value in (replace or {}).items():
        table.macros.setdefault(name, MacroDef(name, value, kind="config"))
