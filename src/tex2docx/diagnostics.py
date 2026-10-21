"""Per-document diagnostics collected by every pipeline stage."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

LEVELS = ("info", "warning", "error")


@dataclass
class Entry:
    level: str
    category: str
    message: str
    context: str = ""


@dataclass
class Diagnostics:
    entries: list[Entry] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, level: str, category: str, message: str, context: str = "") -> None:
        if level not in LEVELS:
            raise ValueError(level)
        self.entries.append(Entry(level, category, message, context[:300]))

    def info(self, category: str, message: str, context: str = "") -> None:
        self.add("info", category, message, context)

    def warning(self, category: str, message: str, context: str = "") -> None:
        self.add("warning", category, message, context)

    def error(self, category: str, message: str, context: str = "") -> None:
        self.add("error", category, message, context)

    def count(self, level: str | None = None, category: str | None = None) -> int:
        return sum(
            1 for e in self.entries
            if (level is None or e.level == level) and (category is None or e.category == category)
        )

    def by_category(self, level: str | None = None) -> Counter:
        return Counter(e.category for e in self.entries if level is None or e.level == level)

    def to_dict(self) -> dict:
        return {"entries": [asdict(e) for e in self.entries], "stats": self.stats}

    def write_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def context_of(text: str, start: int, end: int | None = None, width: int = 40) -> str:
    end = start if end is None else end
    a = max(0, start - width)
    b = min(len(text), end + width)
    return text[a:b].replace("\n", " ")
