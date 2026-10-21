"""Command line interface: tex2docx convert | check-tools | init-config | make-reference."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, default_config_text, load_config
from .tools import ToolError, describe_tools, find_tools


def _console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_convert(args) -> int:
    from .batch import run_batch
    cfg = load_config(args.config)
    if args.no_compile:
        cfg["compile"]["enabled"] = False
    if args.no_pdf_check:
        cfg["verify"]["pdf_compare"] = False
    required = {"pandoc"}
    optional = {"pdftotext", "ghostscript", "inkscape"}
    if cfg["compile"].get("enabled", True):
        required |= {"xelatex"}
        optional |= {"biber"}
    if cfg["citations"].get("method") == "oracle":
        optional |= {"make4ht", "biber"}
    tools = find_tools(cfg, required, optional)
    results = run_batch(Path(args.input), Path(args.output), cfg, tools, jobs=args.jobs, force=args.force)
    failed = [r for r in results if r.status == "fail"]
    return 1 if failed or not results else 0


def cmd_check_tools(args) -> int:
    cfg = load_config(args.config)
    for name, status, info, purpose in describe_tools(cfg):
        print(f"{name:12} {status:8} {purpose}\n             {info}")
    return 0


def cmd_init_config(args) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} は既に存在します（上書きするには --force）", file=sys.stderr)
        return 1
    path.write_text(default_config_text(), encoding="utf-8")
    print(f"既定の設定を書き出しました: {path}")
    return 0


def cmd_make_reference(args) -> int:
    from .reference import build_reference_docx
    cfg = load_config(args.config)
    tools = find_tools(cfg, {"pandoc"})
    out = build_reference_docx(tools["pandoc"], Path(args.path), cfg)
    print(f"reference.docx を作成しました: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tex2docx", description="XeLaTeX 文書を校閲用 .docx に一括変換します")
    p.add_argument("--version", action="version", version=f"tex2docx {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="ディレクトリ（または .tex ファイル）を変換する")
    c.add_argument("input", help="入力ディレクトリまたはメインの .tex ファイル")
    c.add_argument("-o", "--output", required=True, help="出力ディレクトリ")
    c.add_argument("--config", type=Path, default=None, help="設定 YAML")
    c.add_argument("--jobs", "-j", type=int, default=1, help="並列数")
    c.add_argument("--force", action="store_true", help="変更がなくても再変換する")
    c.add_argument("--no-compile", action="store_true", help="xelatex でのコンパイルを行わない")
    c.add_argument("--no-pdf-check", action="store_true", help="PDF とのテキスト比較を行わない")
    c.set_defaults(func=cmd_convert)

    t = sub.add_parser("check-tools", help="外部ツールの有無とバージョンを表示する")
    t.add_argument("--config", type=Path, default=None)
    t.set_defaults(func=cmd_check_tools)

    i = sub.add_parser("init-config", help="既定の設定ファイルを書き出す")
    i.add_argument("path", nargs="?", default="tex2docx.yaml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init_config)

    r = sub.add_parser("make-reference", help="スタイルテンプレート reference.docx を作る")
    r.add_argument("path", nargs="?", default="reference.docx")
    r.add_argument("--config", type=Path, default=None)
    r.set_defaults(func=cmd_make_reference)
    return p


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ToolError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
