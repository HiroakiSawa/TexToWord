"""Low-level LaTeX scanning helpers.

All functions operate on plain ``str`` positions. They understand escaped
characters (``\\{``, ``\\%``), control words vs. control symbols and balanced
brace groups, which is all the structure the preprocessing stages need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LETTERS = re.compile(r"[A-Za-z@]")


class ScanError(ValueError):
    pass


def read_control_sequence(s: str, i: int, allow_at: bool = False) -> tuple[str, int]:
    """``s[i]`` is a backslash. Return (name, end) where name excludes the backslash."""
    if s[i] != "\\":
        raise ScanError(f"expected backslash at {i}")
    j = i + 1
    if j >= len(s):
        return "", j
    if s[j].isalpha() or (allow_at and s[j] == "@"):
        k = j
        while k < len(s) and (s[k].isalpha() or (allow_at and s[k] == "@")):
            k += 1
        return s[j:k], k
    return s[j], j + 1


def skip_spaces(s: str, i: int, newlines: bool = True) -> int:
    """Skip blanks; with ``newlines`` a single line break is skipped but never a blank line."""
    n = len(s)
    while i < n:
        c = s[i]
        if c in " \t":
            i += 1
        elif c == "\n" and newlines:
            k = i + 1
            while k < n and s[k] in " \t":
                k += 1
            if k < n and s[k] == "\n":
                return i
            i = k
        else:
            break
    return i


def find_group_end(s: str, i: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """``s[i] == open_ch``; return the index just after the matching close."""
    depth = 0
    n = len(s)
    j = i
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ScanError(f"unbalanced {open_ch} starting at {i}")


def read_group(s: str, i: int) -> tuple[str, int]:
    end = find_group_end(s, i)
    return s[i + 1 : end - 1], end


def read_optional(s: str, i: int) -> tuple[str | None, int]:
    """Read ``[...]`` at ``i`` (after optional spaces). Braces inside protect ``]``."""
    j = skip_spaces(s, i)
    if j >= len(s) or s[j] != "[":
        return None, i
    depth = 0
    k = j + 1
    n = len(s)
    while k < n:
        c = s[k]
        if c == "\\":
            k += 2
            continue
        if c == "{":
            k = find_group_end(s, k)
            continue
        if c == "]" and depth == 0:
            return s[j + 1 : k], k + 1
        k += 1
    raise ScanError(f"unterminated optional argument at {j}")


def read_arg(s: str, i: int) -> tuple[str, int]:
    """Read one mandatory TeX argument: a brace group, a control sequence or one char."""
    j = skip_spaces(s, i)
    if j >= len(s):
        raise ScanError("missing argument at end of input")
    c = s[j]
    if c == "{":
        return read_group(s, j)
    if c == "\\":
        _, end = read_control_sequence(s, j)
        return s[j:end], end
    if c == "}":
        raise ScanError(f"missing argument before '}}' at {j}")
    return c, j + 1


def read_star(s: str, i: int) -> tuple[bool, int]:
    if i < len(s) and s[i] == "*":
        return True, i + 1
    return False, i


@dataclass
class Command:
    name: str
    start: int
    end: int
    star: bool = False
    optionals: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)


def iter_control_sequences(s: str, start: int = 0, allow_at: bool = False):
    """Yield (name, start, end) for every control sequence, skipping escaped pairs."""
    i = start
    n = len(s)
    while i < n:
        j = s.find("\\", i)
        if j < 0:
            return
        name, end = read_control_sequence(s, j, allow_at=allow_at)
        yield name, j, end
        i = end


def parse_command(s: str, start: int, name_end: int, name: str, n_args: int,
                  max_optionals: int = 2, star: bool = True,
                  optional_positions: str = "leading") -> Command:
    """Parse a command whose control sequence ends at ``name_end``.

    ``optional_positions='leading'`` reads up to ``max_optionals`` ``[...]`` before
    the mandatory arguments (``\\cite[pre][post]{key}``).
    """
    i = name_end
    has_star = False
    if star:
        has_star, i = read_star(s, i)
    opts: list[str] = []
    if optional_positions == "leading":
        for _ in range(max_optionals):
            val, j = read_optional(s, i)
            if val is None:
                break
            opts.append(val)
            i = j
    args = []
    for _ in range(n_args):
        a, i = read_arg(s, i)
        args.append(a)
    return Command(name, start, i, has_star, opts, args)


def find_commands(s: str, names: set[str] | dict, n_args: int | dict = 1,
                  max_optionals: int | dict = 2):
    """Find top-level occurrences of the given commands (not nested inside each other's args)."""
    out = []
    for name, start, end in iter_control_sequences(s):
        if name not in names:
            continue
        na = n_args[name] if isinstance(n_args, dict) else n_args
        mo = max_optionals[name] if isinstance(max_optionals, dict) else max_optionals
        try:
            cmd = parse_command(s, start, end, name, na, mo)
        except ScanError:
            continue
        out.append(cmd)
    return out


def replace_spans(s: str, replacements: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping (start, end, text) replacements."""
    if not replacements:
        return s
    replacements = sorted(replacements, key=lambda r: r[0])
    parts = []
    pos = 0
    for start, end, text in replacements:
        if start < pos:
            continue
        parts.append(s[pos:start])
        parts.append(text)
        pos = end
    parts.append(s[pos:])
    return "".join(parts)


def find_environments(s: str, name: str):
    """Yield (start, body_start, body_end, end) for ``\\begin{name}...\\end{name}``, outermost only."""
    begin = "\\begin{" + name + "}"
    end_tag = "\\end{" + name + "}"
    i = 0
    while True:
        b = s.find(begin, i)
        if b < 0:
            return
        depth = 1
        k = b + len(begin)
        while depth:
            nb = s.find(begin, k)
            ne = s.find(end_tag, k)
            if ne < 0:
                raise ScanError(f"unterminated environment {name}")
            if 0 <= nb < ne:
                depth += 1
                k = nb + len(begin)
            else:
                depth -= 1
                k = ne + len(end_tag)
        yield b, b + len(begin), k - len(end_tag), k
        i = k


MATH_ENVS = (
    "equation", "equation*", "align", "align*", "gather", "gather*", "multline",
    "multline*", "flalign", "flalign*", "alignat", "alignat*", "math", "displaymath",
    "eqnarray", "eqnarray*",
)
VERBATIM_ENVS = ("verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "comment")
VERBATIM_COMMANDS = ("url", "path", "href", "nolinkurl", "texttt", "verb")


def protected_spans(s: str, commands=VERBATIM_COMMANDS) -> list[tuple[int, int]]:
    """Regions where text transformations must not apply: math, verbatim, URLs."""
    spans: list[tuple[int, int]] = []
    for env in MATH_ENVS + VERBATIM_ENVS:
        try:
            for b, _, _, e in find_environments(s, env):
                spans.append((b, e))
        except ScanError:
            pass
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            name, end = read_control_sequence(s, i)
            if name in ("(", "["):
                close = "\\)" if name == "(" else "\\]"
                k = s.find(close, end)
                if k >= 0:
                    spans.append((i, k + 2))
                    i = k + 2
                    continue
            elif name == "verb" and end < n:
                delim = s[end]
                k = s.find(delim, end + 1)
                if k >= 0:
                    spans.append((i, k + 1))
                    i = k + 1
                    continue
            elif name in commands:
                j = skip_spaces(s, end)
                if j < n and s[j] == "{":
                    try:
                        g = find_group_end(s, j)
                        spans.append((i, g))
                        i = g
                        continue
                    except ScanError:
                        pass
            i = end
            continue
        if c == "$":
            double = s.startswith("$$", i)
            delim = "$$" if double else "$"
            k = i + len(delim)
            while True:
                k = s.find(delim, k)
                if k < 0 or s[k - 1] != "\\":
                    break
                k += 1
            if k >= 0:
                spans.append((i, k + len(delim)))
                i = k + len(delim)
                continue
        i += 1
    return merge_spans(spans)


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans = sorted(spans)
    merged: list[tuple[int, int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def apply_outside(s: str, spans: list[tuple[int, int]], func) -> str:
    """Apply ``func`` to every stretch of ``s`` that is outside the protected spans."""
    out = []
    pos = 0
    for a, b in spans:
        out.append(func(s[pos:a]))
        out.append(s[a:b])
        pos = b
    out.append(func(s[pos:]))
    return "".join(out)


def escape_latex(text: str) -> str:
    """Escape plain text so that it survives as literal text in LaTeX."""
    repl = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "#": r"\#",
        "%": r"\%",
        "_": r"\_",
        "^": r"\^{}",
        "~": r"\~{}",
    }
    return "".join(repl.get(c, c) for c in text)
