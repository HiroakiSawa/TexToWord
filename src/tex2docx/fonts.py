"""Font planning: fontspec settings -> Word font names, installed-font lookup, glyph coverage."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import current_os, target_os
from .diagnostics import Diagnostics
from .texscan import ScanError, iter_control_sequences, read_arg, read_optional

FONT_EXT = (".ttf", ".otf", ".ttc", ".otc")
SCRIPT_FAMILY_COMMANDS = {
    "arabicfont": "arabic", "hebrewfont": "hebrew", "greekfont": "greek",
    "syriacfont": "syriac", "persianfont": "arabic", "urdufont": "arabic",
}


def font_dirs() -> list[Path]:
    os_name = current_os()
    if os_name == "windows":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        local = os.environ.get("LOCALAPPDATA", "")
        return [Path(windir) / "Fonts", Path(local) / "Microsoft" / "Windows" / "Fonts"]
    if os_name == "macos":
        home = Path.home()
        return [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"), home / "Library" / "Fonts"]
    home = Path.home()
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts",
            home / ".local" / "share" / "fonts"]


def _family_names(path: Path) -> list[str]:
    from fontTools.ttLib import TTCollection, TTFont
    names: list[str] = []
    try:
        fonts = TTCollection(str(path), lazy=True).fonts if path.suffix.lower() in (".ttc", ".otc") \
            else [TTFont(str(path), lazy=True)]
    except Exception:
        return names
    for f in fonts:
        try:
            table = f["name"]
        except Exception:
            continue
        for rec in table.names:
            if rec.nameID in (1, 16):
                try:
                    n = rec.toUnicode().strip()
                except Exception:
                    continue
                if n and n not in names:
                    names.append(n)
    return names


@lru_cache(maxsize=1)
def installed_fonts() -> dict[str, Path]:
    """Lower-cased family name -> font file, for fonts installed on this machine."""
    index: dict[str, Path] = {}
    for d in font_dirs():
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() not in FONT_EXT:
                continue
            for name in _family_names(p):
                key = name.lower()
                if key not in index or "regular" in p.stem.lower():
                    index[key] = p
    return index


def is_installed(family: str) -> bool:
    return family.lower() in installed_fonts()


def guess_family_from_file(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.split(r"[-_](?:Regular|Bold|Italic|BoldItalic|VariableFont.*|Medium|Book|Roman)$", stem)[0]
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    return stem.replace("_", " ").strip()


@dataclass
class FontSpecFont:
    name: str
    path: Path | None = None


@dataclass
class FontPlan:
    main: str
    scripts: dict[str, str] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)

    def for_script(self, script: str) -> str:
        return self.scripts.get(script, self.main)


def _fontspec_font(arg: str, options: str | None, root: Path) -> FontSpecFont:
    name = arg.strip()
    path = None
    if name.lower().endswith(FONT_EXT):
        base = root
        if options:
            m = re.search(r"Path\s*=\s*([^,\]]+)", options)
            if m:
                base = root / m.group(1).strip().strip("{}")
        cand = base / name
        if cand.is_file():
            path = cand
            fam = _family_names(cand)
            name = fam[0] if fam else guess_family_from_file(name)
        else:
            name = guess_family_from_file(name)
    return FontSpecFont(name, path)


def read_fontspec(preamble: str, root: Path) -> dict[str, FontSpecFont]:
    """Return {'main': ..., 'arabic': ..., ...} from \\setmainfont / \\newfontfamily."""
    out: dict[str, FontSpecFont] = {}
    for name, start, end in iter_control_sequences(preamble):
        try:
            if name == "setmainfont":
                opts, i = read_optional(preamble, end)
                arg, i = read_arg(preamble, i)
                opts2, _ = read_optional(preamble, i)
                out["main"] = _fontspec_font(arg, (opts or "") + "," + (opts2 or ""), root)
            elif name in ("newfontfamily", "setfontfamily", "renewfontfamily"):
                cs, i = read_arg(preamble, end)
                cs = cs.lstrip("\\").strip()
                opts, i = read_optional(preamble, i)
                arg, i = read_arg(preamble, i)
                opts2, _ = read_optional(preamble, i)
                script = SCRIPT_FAMILY_COMMANDS.get(cs)
                if script:
                    out[script] = _fontspec_font(arg, (opts or "") + "," + (opts2 or ""), root)
        except ScanError:
            continue
    return out


def _choose(cfg: dict, wanted: str, os_name: str, diag: Diagnostics, role: str,
            local_file: Path | None) -> str:
    subs = cfg["fonts"].get("substitutes", {}) or {}
    available = (os_name == current_os() and is_installed(wanted))
    if available:
        return wanted
    sub = subs.get(wanted, {})
    replacement = sub.get(os_name) if isinstance(sub, dict) else sub
    if replacement:
        why = "この OS にインストールされていない" if os_name == current_os() else f"{os_name} 向けの代替指定がある"
        diag.warning("font", f"{role}: フォント「{wanted}」は{why}ため、Word では「{replacement}」を指定します")
        return replacement
    if os_name == current_os():
        note = "（TeX 側ではフォントファイルを直接参照しています）" if local_file else ""
        diag.warning("font", f"{role}: フォント「{wanted}」がこの OS に見つかりません{note}。"
                              "校閲者の環境で別のフォントに置き換わる可能性があります")
    return wanted


def plan_fonts(cfg: dict, preamble: str, root: Path, diag: Diagnostics) -> FontPlan:
    os_name = target_os(cfg)
    spec = read_fontspec(preamble, root)
    fcfg = cfg["fonts"]
    defaults = fcfg.get("defaults", {})

    def default_for(script: str) -> str:
        d = defaults.get(script)
        if isinstance(d, dict):
            return d.get(os_name) or next(iter(d.values()))
        return d or defaults.get("main", "Times New Roman")

    main_req = fcfg.get("main") or (spec["main"].name if "main" in spec else defaults.get("main", "Times New Roman"))
    plan = FontPlan(main=_choose(cfg, main_req, os_name, diag, "本文",
                                 spec["main"].path if "main" in spec else None))
    if "main" in spec and spec["main"].path:
        plan.files["main"] = spec["main"].path
    for script in ("arabic", "hebrew", "greek", "syriac"):
        configured = (fcfg.get("scripts") or {}).get(script)
        if configured:
            plan.scripts[script] = configured
            continue
        if script in spec:
            plan.scripts[script] = _choose(cfg, spec[script].name, os_name, diag,
                                           f"{script} 用", spec[script].path)
            if spec[script].path:
                plan.files[script] = spec[script].path
        elif script == "greek":
            plan.scripts[script] = plan.main
        elif script != "syriac":
            plan.scripts[script] = default_for(script)
    plan.scripts["east_asian"] = (fcfg.get("scripts") or {}).get("east_asian") or default_for("east_asian")
    return plan


def is_east_asian(ch: str) -> bool:
    import unicodedata
    return unicodedata.east_asian_width(ch) in ("W", "F")


@lru_cache(maxsize=64)
def _cmap(path: Path) -> frozenset[int]:
    from fontTools.ttLib import TTCollection, TTFont
    cps: set[int] = set()
    try:
        fonts = TTCollection(str(path)).fonts if path.suffix.lower() in (".ttc", ".otc") \
            else [TTFont(str(path))]
        for f in fonts:
            cmap = f.getBestCmap() or {}
            cps.update(cmap.keys())
    except Exception:
        return frozenset()
    return frozenset(cps)


def font_file(family: str, plan: FontPlan) -> Path | None:
    for role, p in plan.files.items():
        if (role == "main" and family == plan.main) or plan.scripts.get(role) == family:
            return p
    return installed_fonts().get(family.lower())


def missing_glyphs(family: str, chars: set[str], plan: FontPlan) -> set[str] | None:
    """Characters not in the font's cmap, or None if the font file is unavailable."""
    path = font_file(family, plan)
    if path is None:
        return None
    cmap = _cmap(path)
    if not cmap:
        return None
    return {c for c in chars if ord(c) not in cmap and not c.isspace()}
