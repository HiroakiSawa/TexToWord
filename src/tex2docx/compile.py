"""Run the original document through XeLaTeX (+ biber) to obtain .aux data and a reference PDF."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .diagnostics import Diagnostics
from .tools import Tool, run


@dataclass
class CompileResult:
    pdf: Path | None
    aux: Path | None
    log: Path | None
    ran: bool
    ok: bool


def _newest_mtime(paths: list[Path]) -> float:
    return max((p.stat().st_mtime for p in paths if p.exists()), default=0.0)


def compile_document(main: Path, build: Path, deps: list[Path], tools: dict[str, Tool], cfg: dict,
                     diag: Diagnostics, force: bool = False) -> CompileResult:
    build.mkdir(parents=True, exist_ok=True)
    stem = main.stem
    pdf, aux, log = build / f"{stem}.pdf", build / f"{stem}.aux", build / f"{stem}.log"
    if not force and pdf.exists() and aux.exists() and pdf.stat().st_mtime >= _newest_mtime(deps):
        diag.info("compile", "前回のコンパイル結果が最新なので再利用しました")
        return CompileResult(pdf, aux, log, False, True)
    xelatex = tools["xelatex"]
    timeout = int(cfg["compile"].get("timeout", 1200))
    runs = max(1, int(cfg["compile"].get("runs", 3)))

    def latex_once() -> None:
        try:
            run(xelatex.cmd("-interaction=nonstopmode", "-file-line-error",
                            f"-output-directory={build}", main.name), cwd=main.parent, timeout=timeout)
        except subprocess.TimeoutExpired:
            diag.error("compile", f"xelatex が {timeout} 秒以内に終了しませんでした")

    latex_once()
    bcf = build / f"{stem}.bcf"
    if bcf.exists():
        if "biber" in tools:
            proc = run(tools["biber"].cmd(f"--input-directory={build}", f"--output-directory={build}",
                                          stem), cwd=main.parent, timeout=timeout)
            if proc.returncode != 0:
                diag.error("compile", "biber がエラーで終了しました",
                           (proc.stdout + proc.stderr)[-300:])
        else:
            diag.error("compile", "biber が見つからないため文献を処理できません")
    for _ in range(runs - 1):
        latex_once()
    extra = 0
    while log.exists() and extra < 2 and re.search(r"Rerun to get|Label\(s\) may have changed|rerun",
                                                   log.read_text(encoding="utf-8", errors="replace")):
        latex_once()
        extra += 1
    ok = pdf.exists()
    if log.exists():
        errors = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()
                  if l.startswith("!") or re.match(r"^[^:\n]+:\d+: ", l)]
        if errors:
            diag.warning("compile", f"LaTeX のログにエラーが {len(errors)} 件あります（PDF は {'生成' if ok else '未生成'}）",
                         " / ".join(errors[:5]))
    if not ok:
        diag.error("compile", "PDF が生成されませんでした。相互参照と PDF 比較は行えません")
    return CompileResult(pdf if ok else None, aux if aux.exists() else None,
                         log if log.exists() else None, True, ok)
