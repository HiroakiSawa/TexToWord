"""Per-document Markdown reports and batch summaries (Markdown + HTML)."""

from __future__ import annotations

import html
from collections import Counter
from pathlib import Path

from .verify import FAIL, OK, SKIP, WARN, Check

ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌", SKIP: "—"}
LEVEL_JA = {"error": "エラー", "warning": "警告", "info": "情報"}


def overall_status(checks: list[Check], errors: int) -> str:
    if errors or any(c.status == FAIL for c in checks):
        return FAIL
    if any(c.status == WARN for c in checks):
        return WARN
    return OK


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def write_document_report(path: Path, result) -> None:
    diag = result.diag
    L: list[str] = []
    w = L.append
    w(f"# 変換レポート: {result.rel}\n")
    w(f"- 入力: `{result.main}`")
    w(f"- 出力: `{result.output}`" if result.output else "- 出力: なし")
    w(f"- 判定: {ICON[result.status]} {result.status}")
    w(f"- 所要時間: {result.seconds:.1f} 秒")
    if result.error:
        w(f"- **中断の原因**: {result.error}")
    w("")
    if result.checks:
        w("## 検証結果\n")
        w("| 項目 | 判定 | 概要 |")
        w("|---|---|---|")
        for c in result.checks:
            w(f"| {c.name} | {ICON[c.status]} | {_md_escape(c.summary)} |")
        w("")
        for c in result.checks:
            if c.details:
                w(f"### {c.name}\n")
                for d in c.details:
                    w(f"- {_md_escape(d)}")
                w("")
    if diag.stats:
        w("## 処理の統計\n")
        for k, v in diag.stats.items():
            w(f"- {k}: {v}")
        w("")
    w("## ログ\n")
    if not diag.entries:
        w("（なし）")
    for level in ("error", "warning", "info"):
        entries = [e for e in diag.entries if e.level == level]
        if not entries:
            continue
        w(f"### {LEVEL_JA[level]}（{len(entries)} 件）\n")
        by_cat = Counter(e.category for e in entries)
        for cat, n in by_cat.most_common():
            w(f"#### {cat}（{n} 件）\n")
            for e in [e for e in entries if e.category == cat][:200]:
                ctx = f" — `{_md_escape(e.context)}`" if e.context else ""
                w(f"- {_md_escape(e.message)}{ctx}")
            w("")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def _row(result) -> dict:
    checks = {c.name: c for c in result.checks}
    fn = checks.get("脚注数")
    cov = checks.get("PDF とのテキスト網羅性")
    return {
        "file": result.rel,
        "status": result.status,
        "footnotes": fn.summary if fn else "—",
        "coverage": cov.summary.split("（")[1].split("）")[0] if cov and "（" in cov.summary else "—",
        "unconverted": result.diag.stats.get("未変換（ハイライト）", 0),
        "review": result.diag.stats.get("要確認（ハイライト）", 0),
        "warnings": result.diag.count("warning"),
        "errors": result.diag.count("error"),
        "seconds": f"{result.seconds:.1f}",
        "report": result.report_path.name if result.report_path else "",
        "error": result.error or "",
    }


def write_batch_summary(out_dir: Path, results: list) -> tuple[Path, Path]:
    rows = [_row(r) for r in results]
    counts = Counter(r["status"] for r in rows)
    md = ["# tex2docx 一括変換サマリー\n",
          f"- 文書数: {len(rows)}（✅ {counts.get(OK, 0)} / ⚠️ {counts.get(WARN, 0)} / ❌ {counts.get(FAIL, 0)} / "
          f"スキップ {counts.get('skipped', 0)}）\n",
          "| 文書 | 判定 | 脚注（ソース/docx） | PDF 網羅率 | 未変換 | 要確認 | 警告 | エラー | 秒 | レポート |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        icon = ICON.get(r["status"], "⏭️")
        md.append(f"| {_md_escape(r['file'])} | {icon} | {_md_escape(r['footnotes'])} | {r['coverage']} | "
                  f"{r['unconverted']} | {r['review']} | {r['warnings']} | {r['errors']} | {r['seconds']} | "
                  f"{r['report']} |")
    for r in rows:
        if r["error"]:
            md.append(f"\n- **{r['file']}**: {r['error']}")
    md_path = out_dir / "summary.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    css = """
    :root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--head:#f6f8fa;--ok:#1a7f37;--warn:#9a6700;--fail:#d1242f}
    @media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#3d444d;--head:#151b23;--ok:#3fb950;--warn:#d29922;--fail:#f85149}}
    body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:24px}
    table{border-collapse:collapse;width:100%}th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
    th{background:var(--head)}td.num{text-align:right;font-variant-numeric:tabular-nums}
    .ok{color:var(--ok)}.warn{color:var(--warn)}.fail{color:var(--fail)}.muted{color:var(--muted)}
    """
    status_cls = {OK: "ok", WARN: "warn", FAIL: "fail"}
    trs = []
    for r in rows:
        cls = status_cls.get(r["status"], "muted")
        link = f'<a href="{html.escape(r["report"])}">{html.escape(r["report"])}</a>' if r["report"] else ""
        trs.append(
            f'<tr><td>{html.escape(r["file"])}</td><td class="{cls}">{html.escape(r["status"])}</td>'
            f'<td>{html.escape(r["footnotes"])}</td><td class="num">{html.escape(r["coverage"])}</td>'
            f'<td class="num">{r["unconverted"]}</td><td class="num">{r["review"]}</td>'
            f'<td class="num">{r["warnings"]}</td><td class="num">{r["errors"]}</td>'
            f'<td class="num">{r["seconds"]}</td><td>{link}<div class="fail">{html.escape(r["error"])}</div></td></tr>')
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>tex2docx サマリー</title>
<style>{css}</style></head><body><h1>tex2docx 一括変換サマリー</h1>
<p class="muted">文書数 {len(rows)} — ok {counts.get(OK, 0)} / warn {counts.get(WARN, 0)} / fail {counts.get(FAIL, 0)} / skipped {counts.get('skipped', 0)}</p>
<table><thead><tr><th>文書</th><th>判定</th><th>脚注（ソース/docx）</th><th>PDF 網羅率</th><th>未変換</th><th>要確認</th><th>警告</th><th>エラー</th><th>秒</th><th>レポート</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table></body></html>"""
    html_path = out_dir / "summary.html"
    html_path.write_text(page, encoding="utf-8")
    return md_path, html_path
