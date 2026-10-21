"""Batch conversion: discovery of main documents, parallelism, skip-if-unchanged, summary."""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import __version__
from .config import config_fingerprint
from .flatten import clean_source, read_text
from .pipeline import DocResult, convert_document
from .report import summary_row, write_batch_summary
from .tools import Tool

NON_MAIN_CLASSES = {"subfiles", "standalone"}
DOCCLASS = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def is_main_document(path: Path) -> bool:
    try:
        text = clean_source(read_text(path))
    except (OSError, UnicodeDecodeError):
        return False
    m = DOCCLASS.search(text)
    return bool(m) and m.group(1).strip() not in NON_MAIN_CLASSES and "\\begin{document}" in text


def discover(input_path: Path, out_dir: Path, exclude_dirs: list[str]) -> list[Path]:
    if input_path.is_file():
        return [input_path.resolve()]
    out_dir = out_dir.resolve()
    found = []
    for p in sorted(input_path.rglob("*.tex")):
        rp = p.resolve()
        if any(part in exclude_dirs for part in p.relative_to(input_path).parts[:-1]):
            continue
        if out_dir == rp.parent or out_dir in rp.parents:
            continue
        if is_main_document(p):
            found.append(rp)
    return found


def _file_hash(p: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(p.read_bytes())
    except OSError:
        h.update(b"<missing>")
    return h.hexdigest()


def fingerprint(deps: list[str], cfg: dict, tools: dict[str, Tool]) -> str:
    h = hashlib.sha256()
    h.update(__version__.encode())
    h.update(config_fingerprint(cfg).encode())
    for name in sorted(tools):
        h.update(f"{name}={tools[name].version}".encode("utf-8"))
    for d in sorted(deps):
        h.update(d.encode("utf-8"))
        h.update(_file_hash(Path(d)).encode())
    return h.hexdigest()


def _state_path(out_dir: Path) -> Path:
    return out_dir / ".tex2docx" / "state.json"


def load_state(out_dir: Path) -> dict:
    p = _state_path(out_dir)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(out_dir: Path, state: dict) -> None:
    p = _state_path(out_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _skipped(main: Path, rel: str, out_dir: Path, prev: dict) -> DocResult:
    r = DocResult(main=main, rel=rel, status="skipped")
    r.output = out_dir / Path(rel).with_suffix(".docx")
    r.report_path = r.output.with_suffix(".report.md")
    r.error = None
    r.previous = prev.get("row")
    return r


def run_batch(input_path: Path, out_dir: Path, cfg: dict, tools: dict[str, Tool], jobs: int = 1,
              force: bool = False, progress=print) -> list[DocResult]:
    input_path = input_path.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    root = input_path if input_path.is_dir() else input_path.parent
    mains = discover(input_path, out_dir, cfg["batch"].get("exclude_dirs", []))
    if not mains:
        progress("メイン文書（\\documentclass を含む .tex）が見つかりませんでした。")
        return []
    state = load_state(out_dir)
    todo: list[Path] = []
    results: list[DocResult] = []
    for m in mains:
        rel = m.relative_to(root).as_posix()
        prev = state.get(rel)
        out_docx = out_dir / Path(rel).with_suffix(".docx")
        if not force and prev and out_docx.exists() and prev.get("status") != "fail" and \
                prev.get("fingerprint") == fingerprint(prev.get("deps", []), cfg, tools):
            progress(f"[スキップ] {rel}（変更なし）")
            results.append(_skipped(m, rel, out_dir, prev))
        else:
            todo.append(m)
    progress(f"{len(mains)} 文書中 {len(todo)} 文書を変換します（並列数 {jobs}）")

    def record(res: DocResult) -> None:
        deps = [str(p) for p in res.deps]
        state[res.rel] = {"fingerprint": fingerprint(deps, cfg, tools), "deps": deps,
                          "status": res.status, "row": summary_row(res)}
        save_state(out_dir, state)
        progress(f"[{res.status}] {res.rel}（{res.seconds:.1f} 秒）" + (f" — {res.error}" if res.error else ""))
        results.append(res)

    if jobs <= 1 or len(todo) <= 1:
        for m in todo:
            record(convert_document(m, root, out_dir, cfg, tools, force))
    else:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futures = {ex.submit(convert_document, m, root, out_dir, cfg, tools, force): m for m in todo}
            for fut in as_completed(futures):
                m = futures[fut]
                try:
                    record(fut.result())
                except Exception as exc:  # worker crashed
                    r = DocResult(main=m, rel=m.relative_to(root).as_posix(), error=f"{type(exc).__name__}: {exc}")
                    record(r)
    results.sort(key=lambda r: r.rel)
    md, html = write_batch_summary(out_dir, results)
    progress(f"サマリー: {md} / {html}")
    return results
