"""Discovery and invocation of external tools (pandoc, xelatex, biber, ...)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import current_os

MIN_PANDOC = (3, 1)

CANDIDATES = {
    "pandoc": ["pandoc"],
    "xelatex": ["xelatex"],
    "biber": ["biber"],
    "make4ht": ["make4ht"],
    "pdftotext": ["pdftotext"],
    "ghostscript": ["gswin64c", "gswin32c", "gs", "rungs"],
    "inkscape": ["inkscape"],
}

VERSION_ARGS = {
    "pandoc": ["--version"],
    "xelatex": ["--version"],
    "biber": ["--version"],
    "make4ht": ["--version"],
    "pdftotext": ["-v"],
    "ghostscript": ["--version"],
    "inkscape": ["--version"],
}

INSTALL_HINTS = {
    "pandoc": {
        "macos": "brew install pandoc",
        "windows": "winget install --id JohnMacFarlane.Pandoc -e   （または scoop install pandoc）",
    },
    "xelatex": {
        "macos": "MacTeX を導入: brew install --cask mactex",
        "windows": "TeX Live を導入: https://tug.org/texlive/windows.html の install-tl-windows.exe",
    },
    "biber": {
        "macos": "MacTeX に同梱。無ければ: sudo tlmgr install biber",
        "windows": "TeX Live に同梱。無ければ: tlmgr install biber",
    },
    "make4ht": {
        "macos": "MacTeX に同梱。無ければ: sudo tlmgr install make4ht tex4ht",
        "windows": "TeX Live に同梱。無ければ: tlmgr install make4ht tex4ht",
    },
    "pdftotext": {
        "macos": "brew install poppler",
        "windows": "scoop install poppler （Git for Windows にも同梱。winget search poppler でも可）",
    },
    "ghostscript": {
        "macos": "brew install ghostscript",
        "windows": "winget install --id ArtifexSoftware.GhostScript -e （TeX Live の rungs でも代用可）",
    },
    "inkscape": {
        "macos": "brew install --cask inkscape",
        "windows": "winget install --id Inkscape.Inkscape -e",
    },
}

PURPOSE = {
    "pandoc": "LaTeX → docx 変換本体",
    "xelatex": "相互参照番号・比較用 PDF の取得、tikz 図の画像化",
    "biber": "biblatex の文献処理",
    "make4ht": "biblatex による引用・文献目録の整形結果の取得",
    "pdftotext": "PDF とのテキスト網羅性の比較",
    "ghostscript": "EPS 図の変換（任意）",
    "inkscape": "SVG 図の変換（任意）",
}


class ToolError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    path: Path
    version: str

    def cmd(self, *args: str | os.PathLike) -> list[str]:
        return [str(self.path), *[str(a) for a in args]]


def _extra_locations(name: str) -> list[Path]:
    if current_os() != "windows":
        return [Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name,
                Path("/Library/TeX/texbin") / name]
    local = os.environ.get("LOCALAPPDATA", "")
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    out = []
    if name == "pandoc":
        out += [Path(local) / "Pandoc" / "pandoc.exe", Path(prog) / "Pandoc" / "pandoc.exe"]
    if name == "inkscape":
        out += [Path(prog) / "Inkscape" / "bin" / "inkscape.exe"]
    return out


def locate(name: str, configured: str | None) -> Path | None:
    if configured:
        p = Path(configured).expanduser()
        if p.is_file():
            return p
        found = shutil.which(configured)
        return Path(found) if found else None
    for cand in CANDIDATES[name]:
        found = shutil.which(cand)
        if found:
            return Path(found)
    for base in CANDIDATES[name]:
        for p in _extra_locations(base):
            if p.is_file():
                return p
    return None


def run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Run without a shell; decode output as UTF-8 regardless of the console code page."""
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, timeout=timeout,
        env=env, stdin=subprocess.DEVNULL,
    )
    proc.stdout = proc.stdout.decode("utf-8", errors="replace")
    proc.stderr = proc.stderr.decode("utf-8", errors="replace")
    return proc


def probe_version(name: str, path: Path) -> str:
    try:
        proc = run([str(path), *VERSION_ARGS[name]], timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(バージョン取得失敗: {exc})"
    text = (proc.stdout or "") + (proc.stderr or "")
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return "(不明)"


def pandoc_version_tuple(version: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.groups() if x is not None)


def hint(name: str) -> str:
    os_name = current_os()
    hints = INSTALL_HINTS[name]
    if os_name in hints:
        return hints[os_name]
    return " / ".join(f"{k}: {v}" for k, v in hints.items())


def find_tools(cfg: dict, required: set[str], optional: set[str] = frozenset()) -> dict[str, Tool]:
    found: dict[str, Tool] = {}
    missing = []
    for name in sorted(required | set(optional)):
        path = locate(name, cfg["tools"].get(name))
        if path is None:
            if name in required:
                missing.append(name)
            continue
        found[name] = Tool(name, path, probe_version(name, path))
    if "pandoc" in found:
        ver = pandoc_version_tuple(found["pandoc"].version)
        if ver < MIN_PANDOC:
            raise ToolError(
                f"pandoc {'.'.join(map(str, MIN_PANDOC))} 以上が必要です（検出: {found['pandoc'].version}）。\n"
                f"  導入: {hint('pandoc')}"
            )
    if missing:
        lines = ["必要な外部ツールが見つかりません:"]
        for name in missing:
            lines.append(f"  - {name}（{PURPOSE[name]}）\n      導入: {hint(name)}")
        lines.append("ツールの場所は設定ファイルの tools: で指定することもできます。")
        raise ToolError("\n".join(lines))
    return found


def describe_tools(cfg: dict) -> list[tuple[str, str, str, str]]:
    """(name, status, path/version, hint) for the ``check-tools`` command."""
    rows = []
    for name in CANDIDATES:
        path = locate(name, cfg["tools"].get(name))
        if path:
            rows.append((name, "OK", f"{path}  [{probe_version(name, path)}]", PURPOSE[name]))
        else:
            rows.append((name, "見つかりません", hint(name), PURPOSE[name]))
    return rows
