"""Marker protocol between the Python preprocessor and the Lua filter.

Pandoc turns ``\\hypertarget{id}{content}`` into ``Span(id)[content]`` while still
parsing ``content`` as LaTeX. Each marker id indexes a JSON table that tells the
Lua filter which Word style to apply (character or paragraph) and whether the
text must be highlighted for review.
"""

from __future__ import annotations

import json
from pathlib import Path

PREFIX = "tdx-m-"


class Markers:
    def __init__(self) -> None:
        self.table: dict[str, dict] = {}
        self._n = 0

    def _new(self, info: dict) -> str:
        self._n += 1
        mid = f"{PREFIX}{self._n}"
        self.table[mid] = info
        return mid

    def char(self, latex: str, style: str, highlight: bool = False, kind: str = "") -> str:
        mid = self._new({"type": "char", "style": style, "highlight": highlight, "kind": kind})
        return f"\\hypertarget{{{mid}}}{{{latex}}}"

    def para(self, style: str, kind: str = "") -> str:
        """Place at the start of a paragraph to give it a paragraph style."""
        mid = self._new({"type": "para", "style": style, "kind": kind})
        return f"\\hypertarget{{{mid}}}{{}}"

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.table, ensure_ascii=False, indent=1), encoding="utf-8")
