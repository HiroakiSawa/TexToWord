"""Figures: PDF/EPS/SVG graphics to PNG, TikZ pictures compiled stand-alone and rasterised;
box wrappers around tables are removed so pandoc sees the tabular itself."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .citations import CS_FIRST, _consume_args
from .diagnostics import Diagnostics, context_of
from .markers import Markers
from .texscan import (ScanError, find_environments, iter_control_sequences, protected_spans,
                      read_arg, read_group, read_optional, replace_spans, skip_spaces)
from .tools import Tool, run

GRAPHIC_EXT = [".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg", ".gif", ".bmp", ".tif", ".tiff"]
RASTER_OK = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
TIKZ_ENVS = ("tikzpicture", "tikzcd", "forest", "circuitikz", "pgfpicture")
TIKZ_PACKAGES = {"tikz", "tikz-cd", "pgfplots", "forest", "circuitikz", "pgf", "amsmath", "amssymb",
                 "fontspec", "polyglossia", "xcolor", "color", "graphicx", "semtrans", "xspace",
                 "tikzpeople", "tkz-euclide", "chemfig"}
TIKZ_COMMANDS = re.compile(
    r"^(?:usetikzlibrary|usepgfplotslibrary|pgfplotsset|tikzset|tikzstyle|tikzcdset|forestset|"
    r"setmainlanguage|setotherlanguages?|setdefaultlanguage|setmainfont|newfontfamily|setfontfamily|"
    r"newfontface|defaultfontfeatures|newcommand|providecommand|DeclareRobustCommand|definecolor|"
    r"colorlet|pgfdeclarelayer|pgfsetlayers)$")
# commands whose last mandatory argument is content: (name, mandatory args before content, optional first)
WRAPPERS = {"resizebox": 2, "scalebox": 1, "rotatebox": 1, "adjustbox": 1, "reflectbox": 0}
KEEP_GRAPHIC_OPTS = re.compile(r"^\s*(width|height|scale|angle)\s*=")


def _short_hash(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
    return h.hexdigest()[:10]


def graphics_paths(preamble: str, root: Path) -> list[Path]:
    paths = [root]
    for m in re.finditer(r"\\graphicspath\s*\{((?:\{[^}]*\}\s*)+)\}", preamble):
        for d in re.findall(r"\{([^}]*)\}", m.group(1)):
            paths.append((root / d).resolve())
    return paths


def resolve_graphic(name: str, dirs: list[Path]) -> Path | None:
    name = name.strip().strip('"')
    p = Path(name)
    exts = [p.suffix.lower()] if p.suffix.lower() in GRAPHIC_EXT else [""] + GRAPHIC_EXT
    for d in dirs:
        for ext in exts:
            cand = (d / (name + ext)) if ext and not name.lower().endswith(ext) else d / name
            if cand.is_file():
                return cand.resolve()
    return None


# ------------------------------------------------------------------ converters
def pdf_to_png(src: Path, dst: Path, dpi: int, page: int, tools: dict[str, Tool], order: list[str],
               diag: Diagnostics) -> bool:
    for method in order:
        try:
            if method == "pypdfium2":
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(str(src))
                if page >= len(pdf):
                    diag.warning("figure", f"{src.name} に {page + 1} ページ目がありません")
                    page = 0
                pdf[page].render(scale=dpi / 72).to_pil().save(dst, dpi=(dpi, dpi))
                pdf.close()
                return True
            if method == "ghostscript" and "ghostscript" in tools:
                proc = run(tools["ghostscript"].cmd("-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m",
                                                    f"-r{dpi}", f"-dFirstPage={page + 1}",
                                                    f"-dLastPage={page + 1}", f"-sOutputFile={dst}", str(src)),
                           timeout=300)
                if proc.returncode == 0 and dst.exists():
                    return True
            if method == "inkscape" and "inkscape" in tools:
                proc = run(tools["inkscape"].cmd(str(src), "--export-type=png", f"--export-dpi={dpi}",
                                                 f"--export-filename={dst}"), timeout=300)
                if proc.returncode == 0 and dst.exists():
                    return True
        except Exception as exc:  # try the next converter
            diag.info("figure", f"{method} で {src.name} を変換できませんでした: {exc}")
    return False


def eps_to_png(src: Path, dst: Path, dpi: int, tools: dict[str, Tool], diag: Diagnostics) -> bool:
    if "ghostscript" not in tools:
        diag.error("figure", f"EPS 図 {src.name} の変換に Ghostscript が必要です")
        return False
    proc = run(tools["ghostscript"].cmd("-dSAFER", "-dBATCH", "-dNOPAUSE", "-dEPSCrop", "-sDEVICE=png16m",
                                        f"-r{dpi}", f"-sOutputFile={dst}", str(src)), timeout=300)
    return proc.returncode == 0 and dst.exists()


def svg_to_png(src: Path, dst: Path, dpi: int, tools: dict[str, Tool], diag: Diagnostics) -> bool:
    if "inkscape" in tools:
        proc = run(tools["inkscape"].cmd(str(src), "--export-type=png", f"--export-dpi={dpi}",
                                         f"--export-filename={dst}"), timeout=300)
        if proc.returncode == 0 and dst.exists():
            return True
    try:
        import cairosvg  # optional
        cairosvg.svg2png(url=str(src), write_to=str(dst), dpi=dpi)
        return dst.exists()
    except Exception:
        pass
    return False


class Figures:
    def __init__(self, doc_root: Path, preamble: str, build: Path, tools: dict[str, Tool], cfg: dict,
                 markers: Markers, diag: Diagnostics):
        self.root = doc_root
        self.preamble = preamble
        self.out = build / "figures"
        self.out.mkdir(parents=True, exist_ok=True)
        self.tools = tools
        self.cfg = cfg
        self.fcfg = cfg["figures"]
        self.dpi = int(self.fcfg.get("dpi", 300))
        self.markers = markers
        self.diag = diag
        self.dirs = graphics_paths(preamble, doc_root)
        self.stats: Counter = Counter()
        self.deps: list[Path] = []

    def _unconverted(self, text: str, why: str) -> str:
        from .texscan import escape_latex
        return self.markers.char(escape_latex(f"[[未変換: {why}: {text}]]"), self.cfg["styles"]["unconverted"],
                                 highlight=True, kind="figure")

    # -------------------------------------------------------------- graphics
    def _convert_file(self, src: Path, page: int) -> Path | None:
        ext = src.suffix.lower()
        if ext in RASTER_OK:
            return src
        dst = self.out / f"{src.stem}-{_short_hash(str(src), str(page))}.png"
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            return dst
        ok = False
        if ext == ".pdf":
            ok = pdf_to_png(src, dst, self.dpi, page, self.tools, self.fcfg.get("pdf_converters", ["pypdfium2"]),
                            self.diag)
        elif ext == ".eps":
            ok = eps_to_png(src, dst, self.dpi, self.tools, self.diag)
        elif ext == ".svg":
            ok = svg_to_png(src, dst, self.dpi, self.tools, self.diag)
            if not ok:
                self.diag.warning("figure", f"SVG 図 {src.name} を PNG にできないため SVG のまま埋め込みます")
                return src
        elif ext in (".tif", ".tiff"):
            try:
                from PIL import Image
                Image.open(src).save(dst, dpi=(self.dpi, self.dpi))
                ok = True
            except Exception:
                ok = False
        return dst if ok else None

    def graphics(self, text: str) -> str:
        repl = []
        for name, start, end in iter_control_sequences(text):
            if name != "includegraphics":
                continue
            try:
                i = end + 1 if text[end:end + 1] == "*" else end
                opts, i = read_optional(text, i)
                fname, stop = read_arg(text, i)
            except ScanError:
                continue
            src = resolve_graphic(fname, self.dirs)
            if src is None:
                self.diag.error("figure", f"図ファイルが見つかりません: {fname}", context_of(text, start))
                repl.append((start, stop, self._unconverted(fname, "図ファイルが見つかりません")))
                continue
            self.deps.append(src)
            page = 0
            kept = []
            for opt in (opts or "").split(","):
                m = re.match(r"\s*page\s*=\s*(\d+)", opt)
                if m:
                    page = int(m.group(1)) - 1
                elif KEEP_GRAPHIC_OPTS.match(opt):
                    kept.append(opt.strip())
            png = self._convert_file(src, page)
            if png is None:
                self.diag.error("figure", f"図 {src.name} を画像に変換できませんでした")
                repl.append((start, stop, self._unconverted(fname, "図を変換できません")))
                continue
            self.stats["図"] += 1
            optstr = f"[{','.join(kept)}]" if kept else ""
            repl.append((start, stop, f"\\includegraphics{optstr}{{{png.as_posix()}}}"))
        return replace_spans(text, repl)

    # -------------------------------------------------------------- tikz
    def _tikz_preamble(self) -> str:
        keep = []
        last = -1
        pre = self.preamble
        for name, start, end in iter_control_sequences(pre):
            if start < last:
                continue
            try:
                if name == "usepackage":
                    stop = _consume_args(pre, end, 3)
                    m = re.search(r"\{([^}]*)\}\s*$", pre[start:stop].strip())
                    pkgs = {p.strip() for p in m.group(1).split(",")} if m else set()
                    if pkgs & TIKZ_PACKAGES:
                        keep.append(pre[start:stop] if pkgs <= TIKZ_PACKAGES else
                                    "\\usepackage{" + ",".join(sorted(pkgs & TIKZ_PACKAGES)) + "}")
                    last = stop
                elif TIKZ_COMMANDS.match(name):
                    stop = _consume_args(pre, end, cs_first=name in CS_FIRST)
                    keep.append(pre[start:stop])
                    last = stop
            except ScanError:
                continue
        text = "\n".join(keep)
        font_root = self.root.as_posix().rstrip("/") + "/"
        return re.sub(r"Path\s*=\s*(?!/|[A-Za-z]:)([^,\]]+)", lambda m: "Path=" + font_root + m.group(1).strip(), text)

    def _compile_tikz(self, envs: list[str], tag: str) -> list[Path | None]:
        if "xelatex" not in self.tools:
            self.diag.error("figure", "tikz 図の画像化に xelatex が必要です")
            return [None] * len(envs)
        names = ",".join(TIKZ_ENVS)
        tex = (f"\\documentclass[multi={{{names}}},border=4pt]{{standalone}}\n{self._tikz_preamble()}\n"
               "\\begin{document}\n" + "\n".join(envs) + "\n\\end{document}\n")
        work = self.out / f"tikz-{tag}"
        work.mkdir(parents=True, exist_ok=True)
        texfile = work / "figs.tex"
        texfile.write_text(tex, encoding="utf-8", newline="\n")
        env = dict(os.environ)
        env["TEXINPUTS"] = os.pathsep.join([str(self.root), ".", env.get("TEXINPUTS", "")])
        try:
            run(self.tools["xelatex"].cmd("-interaction=nonstopmode", f"-output-directory={work}", str(texfile)),
                cwd=self.root, timeout=int(self.cfg["compile"].get("timeout", 1200)), env=env)
        except subprocess.TimeoutExpired:
            self.diag.error("figure", "tikz 図のコンパイルがタイムアウトしました")
            return [None] * len(envs)
        pdf = work / "figs.pdf"
        if not pdf.exists():
            return [None] * len(envs)
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf))
        n = len(doc)
        doc.close()
        if n != len(envs):
            return []
        out = []
        for i in range(len(envs)):
            png = work / f"fig{i + 1}.png"
            ok = pdf_to_png(pdf, png, self.dpi, i, self.tools, self.fcfg.get("pdf_converters", ["pypdfium2"]),
                            self.diag)
            out.append(png if ok else None)
        return out

    def tikz(self, text: str) -> str:
        if not self.fcfg.get("render_tikz", True):
            return text
        math = protected_spans(text, commands=())
        found = []
        for env in TIKZ_ENVS:
            try:
                for b, _, _, e in find_environments(text, env):
                    if any(a <= b < z for a, z in math):
                        continue
                    found.append((b, e))
            except ScanError:
                continue
        found.sort()
        outer = []
        for b, e in found:
            if outer and b < outer[-1][1]:
                continue
            outer.append((b, e))
        if not outer:
            return text
        envs = [text[b:e] for b, e in outer]
        tag = _short_hash(self._tikz_preamble(), *envs)
        pngs = self._compile_tikz(envs, tag)
        if not pngs:
            self.diag.info("figure", "tikz 図を 1 つずつコンパイルし直します")
            pngs = []
            for i, env_text in enumerate(envs):
                one = self._compile_tikz([env_text], _short_hash(tag, str(i)))
                pngs.append(one[0] if one else None)
        repl = []
        for (b, e), png, env_text in zip(outer, pngs, envs):
            if png is None:
                self.diag.error("figure", "tikz 図を画像にできませんでした（未変換として残します）", env_text[:120])
                continue
            self.stats["tikz 図"] += 1
            repl.append((b, e, f"\\includegraphics{{{png.as_posix()}}}"))
        return replace_spans(text, repl)


def unwrap_boxes(text: str, diag: Diagnostics) -> str:
    """\\resizebox{w}{h}{X} etc. -> X (they only scale content)."""
    for _ in range(5):
        repl = []
        last = -1
        for name, start, end in iter_control_sequences(text):
            if start < last or name not in WRAPPERS:
                continue
            try:
                i = end
                if text[i:i + 1] == "*":
                    i += 1
                _, i = read_optional(text, i)
                for _ in range(WRAPPERS[name]):
                    _, i = read_arg(text, i)
                _, i = read_optional(text, i)
                content, stop = read_arg(text, i)
            except ScanError:
                continue
            repl.append((start, stop, content))
            last = stop
        if not repl:
            break
        text = replace_spans(text, repl)
    return text


TABLE_RULES = re.compile(
    r"\\(?:cmidrule|cline|hhline|addlinespace|specialrule|morecmidrules|cdashline|hdashline)\*?"
    r"(?:\s*\([^)]*\))?(?:\s*\[[^\]]*\])?(?:\s*\{[^{}]*\})*")


def clean_table_rules(text: str) -> str:
    """Partial rules (\\cmidrule(lr){2-3}, \\cline, ...) confuse pandoc's table parser."""
    return TABLE_RULES.sub("", text)


def process_figures(body: str, doc_root: Path, preamble: str, build: Path, tools: dict[str, Tool],
                    cfg: dict, markers: Markers, diag: Diagnostics) -> tuple[str, Counter, list[Path]]:
    fig = Figures(doc_root, preamble, build, tools, cfg, markers, diag)
    body = unwrap_boxes(body, diag)
    body = clean_table_rules(body)
    body = fig.tikz(body)
    body = fig.graphics(body)
    return body, fig.stats, fig.deps
