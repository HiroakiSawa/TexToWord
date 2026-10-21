"""Conversion of a single main document: TeX -> preprocessed TeX -> pandoc -> post-processed docx."""

from __future__ import annotations

import json
import os
import re
import time
import traceback
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import citations, figures, langs, macros, refs, reledmac, translit
from .compile import compile_document
from .diagnostics import Diagnostics
from .flatten import FlatDocument, flatten
from .fonts import plan_fonts
from .markers import Markers
from .postprocess import postprocess_docx
from .report import overall_status, write_document_report
from .texscan import ScanError, iter_control_sequences, read_arg, read_optional, replace_spans, skip_spaces
from .tools import Tool, run
from . import verify


@dataclass
class DocResult:
    main: Path
    rel: str
    output: Path | None = None
    status: str = "fail"
    checks: list = field(default_factory=list)
    diag: Diagnostics = field(default_factory=Diagnostics)
    seconds: float = 0.0
    error: str | None = None
    report_path: Path | None = None
    deps: list[Path] = field(default_factory=list)


ENV_MANDATORY_ARGS = {"minipage": 1, "spacing": 1, "adjustwidth": 2}


def data_path(name: str) -> Path:
    return Path(str(resources.files("tex2docx").joinpath(name)))


def remove_transparent_envs(text: str, envs: list[str]) -> str:
    repl = []
    names = set(envs)
    for name, start, end in iter_control_sequences(text):
        if name not in ("begin", "end"):
            continue
        try:
            env, i = read_arg(text, skip_spaces(text, end))
        except ScanError:
            continue
        if env not in names:
            continue
        stop = i
        if name == "begin":
            for _ in range(2):
                _, j = read_optional(text, stop)
                if j == stop:
                    break
                stop = j
            for _ in range(ENV_MANDATORY_ARGS.get(env, 0)):
                try:
                    _, stop = read_arg(text, stop)
                except ScanError:
                    break
        repl.append((start, stop, "\n" if env in ("center", "flushleft", "flushright") else ""))
    return replace_spans(text, repl)


def extract_title_fields(preamble: str) -> dict[str, str]:
    out = {}
    for name, start, end in iter_control_sequences(preamble):
        if name in ("title", "author", "date", "subtitle") and name not in out:
            try:
                _, i = read_optional(preamble, end)
                arg, _ = read_arg(preamble, i)
            except ScanError:
                continue
            arg = re.sub(r"\\(?:empty|today)\b", "", arg).strip()
            if arg:
                out[name] = arg
    return out


def main_language(preamble: str, cfg: dict) -> str:
    main, _ = langs.declared_languages(preamble)
    return cfg["languages"].get((main or "english").lower(), "en-US")


def preprocess(doc: FlatDocument, cfg: dict, aux: refs.AuxData, markers: Markers,
               diag: Diagnostics, build: Path | None = None,
               tools: dict[str, Tool] | None = None,
               extra_deps: list[Path] | None = None) -> tuple[str, dict, str, str]:
    """Return (pandoc_body, title_fields, source_text_for_checks, expanded_body)."""
    stats = diag.stats
    table = macros.MacroTable()
    macros.add_replacements(table, cfg["macros"].get("replace", {}))
    expand_renewed = set(cfg["macros"].get("expand_renewed") or [])
    preamble = macros.collect_definitions(doc.preamble, table, diag, expand_renewed)
    body = macros.collect_definitions(doc.body, table, diag, expand_renewed)
    if cfg["macros"].get("expand_user_macros", True):
        body, n = macros.expand(body, table, diag)
        stats["マクロ展開"] = n
    titles = extract_title_fields(preamble)
    for k, v in list(titles.items()):
        v, _ = macros.expand(v, table, diag)
        titles[k] = v
    body = remove_transparent_envs(body, cfg["macros"].get("transparent_environments", []))
    expanded = body

    extra = cfg["transliteration"].get("extra_accents") or {}
    body, acc = translit.convert_accents(body, diag, extra)
    stats["アクセント記号の変換"] = sum(acc.values())
    for k in titles:
        titles[k], _ = translit.convert_accents(titles[k], diag, extra)
    if cfg["transliteration"].get("ain_hamza", "convert") == "convert":
        ain, hamza = cfg["transliteration"]["ain"], cfg["transliteration"]["hamza"]
        body, ah = translit.convert_ain_hamza(body, diag, ain, hamza)
        stats["アイン"] = ah.get("ain", 0)
        stats["ハムザ"] = ah.get("hamza", 0)
        stats["アポストロフィとして保持"] = ah.get("apostrophe", 0)
        for k in titles:
            titles[k], _ = translit.convert_ain_hamza(titles[k], diag, ain, hamza)
    body = translit.nfc(body)

    _, declared = langs.declared_languages(doc.preamble)
    body, lstats = langs.normalize_languages(body, declared, diag)
    stats["言語切替"] = dict(lstats)
    source_text = body

    structure = refs.Structure(body, aux, cfg, doc.preamble, markers, diag, doc.has_chapters)
    body = structure.apply()
    body, astats = reledmac.transform_apparatus(body, cfg, markers, diag)
    if astats:
        stats["校訂注"] = dict(astats)
    if build is not None and tools is not None:
        body, cstats = citations.apply_citations(body, doc.documentclass, doc.preamble, doc.root,
                                                 doc.bib_files, build, tools, cfg, markers, diag)
        if cstats:
            stats["引用"] = dict(cstats)
        body, fstats, fdeps = figures.process_figures(body, doc.root, doc.preamble, build, tools, cfg,
                                                      markers, diag)
        if fstats:
            stats["図"] = dict(fstats)
        if extra_deps is not None:
            extra_deps.extend(fdeps)
    return body, titles, source_text, expanded


def pandoc_document(doc: FlatDocument, body: str, titles: dict[str, str]) -> str:
    head = [f"\\documentclass{{{doc.documentclass}}}"]
    for k in ("title", "subtitle", "author", "date"):
        if k in titles:
            head.append(f"\\{k}{{{titles[k]}}}")
    return "\n".join(head) + "\n\\begin{document}\n" + body + "\n\\end{document}\n"


def run_pandoc(pandoc: Tool, build: Path, src_root: Path, reference: Path, diag: Diagnostics,
               out_name: str = "pandoc.docx") -> Path:
    lua = data_path("filters/tex2docx.lua")
    resource = f"{src_root}{os.pathsep}{build}"
    proc = run(pandoc.cmd("input.tex", "-f", "latex+raw_tex", "-t", "docx", "--wrap=none",
                          f"--reference-doc={reference}", f"--lua-filter={lua}",
                          f"--resource-path={resource}", "-o", out_name),
               cwd=build, timeout=1800)
    ignored: dict[str, int] = {}
    for line in proc.stderr.splitlines():
        if line.startswith("TDXLOG\t"):
            _, kind, msg = (line.split("\t", 2) + ["", ""])[:3]
            if kind == "ignored":
                m = re.match(r"\\(?:begin\{)?([A-Za-z@]+)", msg)
                key = m.group(1) if m else msg[:20]
                ignored[key] = ignored.get(key, 0) + 1
            else:
                diag.warning("unconverted", "pandoc が解釈できなかった TeX をハイライトして残しました", msg)
        elif line.strip():
            level = "warning" if "[WARNING]" in line else "info"
            diag.add(level, "pandoc", line.strip())
    for k, n in sorted(ignored.items()):
        diag.info("ignored", f"レイアウト用の \\{k} を {n} 件取り除きました")
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc が失敗しました（終了コード {proc.returncode}）: {proc.stderr[-500:]}")
    return build / out_name


def build_dir_for(out_dir: Path, rel: str) -> Path:
    import hashlib
    h = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(rel).stem)[:24]
    return out_dir / ".tex2docx" / f"{stem}-{h}"


def convert_document(main: Path, input_root: Path, out_dir: Path, cfg: dict, tools: dict[str, Tool],
                     force: bool = False) -> DocResult:
    t0 = time.time()
    main = main.resolve()
    try:
        rel = main.relative_to(input_root.resolve()).as_posix()
    except ValueError:
        rel = main.name
    result = DocResult(main=main, rel=rel)
    diag = result.diag
    out_docx = out_dir / Path(rel).with_suffix(".docx")
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    result.report_path = out_docx.with_suffix(".report.md")
    build = build_dir_for(out_dir, rel)
    build.mkdir(parents=True, exist_ok=True)
    try:
        doc = flatten(main, diag)
        result.deps = doc.tex_files + doc.bib_files
        aux_data = refs.AuxData()
        pdf = None
        if cfg["compile"].get("enabled", True) and "xelatex" in tools:
            comp = compile_document(main, build / "latex", result.deps, tools, cfg, diag, force)
            if comp.aux:
                aux_data = refs.parse_aux(comp.aux)
            pdf = comp.pdf
        else:
            diag.warning("compile", "コンパイルを行わないため、相互参照番号は解決されません")

        markers = Markers()
        body, titles, source_text, expanded = preprocess(doc, cfg, aux_data, markers, diag, build, tools,
                                                         result.deps)
        autocite_fn = bool(re.search(r"autocite\s*=\s*footnote|style\s*=\s*[\w-]*(?:verbose|notes)",
                                     doc.preamble))
        expected = verify.expected_footnotes(expanded, autocite_fn)

        (build / "input.tex").write_text(pandoc_document(doc, body, titles), encoding="utf-8", newline="\n")
        filter_cfg = {
            "markers": markers.table,
            "ignore": cfg["macros"].get("ignore", []),
            "styles": {"unconverted": cfg["styles"]["unconverted"]},
        }
        (build / "tdx-filter.json").write_text(json.dumps(filter_cfg, ensure_ascii=False), encoding="utf-8")
        reference = Path(cfg["reference_docx"]).resolve() if cfg.get("reference_docx") else data_path("data/reference.docx")
        raw_docx = run_pandoc(tools["pandoc"], build, doc.root, reference, diag)

        plan = plan_fonts(cfg, doc.preamble, doc.root, diag)
        stats = postprocess_docx(raw_docx, out_docx, cfg, plan, main_language(doc.preamble, cfg), diag)
        diag.stats.update({
            "RTL 段落": stats.rtl_paragraphs, "RTL run": stats.rtl_runs, "ギリシア語 run": stats.greek_runs,
            "分割した run": stats.split_runs, "未変換（ハイライト）": stats.unconverted,
            "要確認（ハイライト）": stats.needs_review,
            "フォント": f"本文 {plan.main} / " + ", ".join(f"{k} {v}" for k, v in plan.scripts.items()),
        })
        result.output = out_docx

        limit = int(cfg["verify"].get("max_listed", 50))
        content = verify.read_docx(out_docx)
        checks = [
            verify.check_footnotes(content, expected),
            verify.check_rtl(content, limit),
            verify.check_rtl_coverage(content, source_text, limit),
            verify.check_bad_chars(content, limit),
            verify.check_glyphs(content, plan, limit),
            verify.check_codepoints(content, source_text, limit),
            verify.check_unconverted(content, cfg, limit),
            verify.check_citations(content, verify.bib_keys(doc.bib_files), limit),
            verify.check_cell_spans(out_docx, expanded),
        ]
        if cfg["verify"].get("pdf_compare", True):
            if pdf and "pdftotext" in tools:
                checks.append(verify.check_pdf_coverage(content, verify.pdf_text(pdf, tools["pdftotext"]), limit))
            else:
                checks.append(verify.Check("PDF とのテキスト網羅性", verify.SKIP,
                                           "PDF または pdftotext が無いため未実施"))
        result.checks = checks
        result.status = overall_status(checks, diag.count("error"))
    except Exception as exc:  # isolate failures per document
        result.error = f"{type(exc).__name__}: {exc}"
        diag.error("fatal", result.error, traceback.format_exc()[-1500:])
        result.status = "fail"
    result.seconds = time.time() - t0
    try:
        write_document_report(result.report_path, result)
        diag.write_json(out_docx.with_suffix(".log.json"))
    except OSError as exc:
        result.error = (result.error or "") + f" / レポート書き込み失敗: {exc}"
    return result
