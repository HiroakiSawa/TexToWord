# TexToWord（tex2docx）

XeLaTeX（polyglossia ＋ fontspec）で書かれた人文学の論文・書籍原稿を、共著者や編集者が **Word の変更履歴とコメントで校閲できる .docx** に一括変換するコマンドラインツールです。アラビア語・ヘブライ語・ギリシア語を含む本文、reledmac の校訂注、biblatex の引用を扱います。

- 変換は TeX → Word の一方向だけです。
- 目的は校閲です。PDF の見た目を再現することより、**本文・注・引用を落とさず正確に、Word で編集しやすく**することを優先します。
- macOS と Windows で同じように動きます（Python 3.10 以上）。

## 主な機能

| 対象 | Word 側の結果 |
|---|---|
| `\part`〜`\subsubsection` | 見出し 1〜n（PDF と同じ番号を見出しの先頭に入れる） |
| `\footnote` | Word の本物の脚注 |
| reledmac `\edtext{lemma}{\Afootnote{…}\Bfootnote{…}}` | lemma の直後に置く通常の脚注 `[A] lemma] 異読 P`。入れ子と `\lemma{…}` にも対応 |
| reledpar の対訳 | 原文段落 → 訳文段落の交互（設定で 2 列の表、または原文全体 → 訳文全体） |
| アラビア語・ヘブライ語 | run に `w:rtl`・`w:lang w:bidi`・cs フォント、RTL 段落は `w:bidi` のスタイル |
| ギリシア語・転写記号 | Unicode（NFC）。`\d{h}`・`\={\i}`・semtrans の `\U{h}`（ḫ）、`` ` ``/`'` によるアイン・ハムザ |
| biblatex の引用・文献目録 | biblatex が実際に組んだ整形済みテキスト（PDF と同一）。`\footcite` は本物の脚注 |
| `\ref`・`\cref`・`\eqref` | PDF と同じ番号（実際にコンパイルした `.aux` から取得） |
| 数式 | Word のネイティブ数式（OMML）。式番号を付ける |
| 表 | Word の表（`\multirow`・`\multicolumn` のセル結合を含む） |
| 図 | PDF/EPS/SVG は PNG に変換。TikZ/tikz-cd は単独でコンパイルして画像化。キャプションは「図表番号（Caption）」スタイル |
| 解釈できないマクロ・環境 | **削除しない**。黄色ハイライトの `[[未変換: \macro{…}]]` として残し、ログに記録する |

## インストール

### 1. tex2docx 本体

```bash
pipx install git+https://github.com/HiroakiSawa/TexToWord.git
```

pipx を使わない場合は、仮想環境に入れます。

```bash
python -m pip install git+https://github.com/HiroakiSawa/TexToWord.git
```

### 2. 外部ツール

| ツール | 必須 | 用途 | macOS（Homebrew） | Windows |
|---|---|---|---|---|
| pandoc 3.1 以上 | 必須 | LaTeX → docx の変換本体 | `brew install pandoc` | `winget install --id JohnMacFarlane.Pandoc -e` または `scoop install pandoc` |
| XeLaTeX・biber | 推奨 | 相互参照の番号と比較用 PDF の取得、TikZ 図の画像化 | `brew install --cask mactex` | TeX Live の公式インストーラ（<https://tug.org/texlive/windows.html>） |
| make4ht（tex4ht） | 推奨 | biblatex による引用の整形 | MacTeX に同梱 | TeX Live に同梱 |
| pdftotext（poppler） | 任意 | PDF とのテキスト網羅性の検証 | `brew install poppler` | `scoop install poppler`（Git for Windows にも同梱） |
| Ghostscript | 任意 | EPS 図の変換 | `brew install ghostscript` | `winget install --id ArtifexSoftware.GhostScript -e`（TeX Live の `rungs` でも代用可） |
| Inkscape | 任意 | SVG 図の変換 | `brew install --cask inkscape` | `winget install --id Inkscape.Inkscape -e` |

TeX Live に make4ht や biber が入っていなければ、`tlmgr install make4ht tex4ht biber` で追加できます（macOS では `sudo` が必要な場合があります）。

導入できたかは次で確認できます。見つからないツールは、OS に合った導入方法が表示されます。

```bash
tex2docx check-tools
```

ツールが PATH に無い場合は、設定ファイルの `tools:` でパスを指定できます。

## 使い方

```bash
tex2docx convert 原稿フォルダ -o 出力フォルダ --config config.yaml --jobs 4
```

- `\documentclass` を含み、`subfiles` / `standalone` クラスでない `.tex` を**メイン文書として自動で検出**します。`\input`・`\include`・`\subfile`・`\import` で読み込まれる章ファイルは、メイン文書の一部として結合されます。
- `--jobs N`：N 文書を並列に変換します。1 文書が失敗しても、他の文書の変換は続きます。
- 再実行すると、**内容に変更のない文書はスキップ**します（章ファイル・`.bib`・図・設定・ツールのバージョンまで含めて判定）。`--force` で強制的に再変換します。
- `--no-compile`：XeLaTeX でのコンパイルを省きます。速くなりますが、相互参照の番号は解決されず、PDF との比較も行いません。
- `--no-pdf-check`：PDF とのテキスト比較だけを省きます。

その他のコマンドは次のとおりです。

```bash
tex2docx init-config tex2docx.yaml     # 既定の設定ファイルを書き出す（これを編集して --config に渡す）
tex2docx make-reference reference.docx # Word のスタイルテンプレートを書き出す（Word で編集して reference_docx に指定できる）
```

### 出力

```
出力フォルダ/
  summary.html / summary.md       … バッチ全体のサマリー（判定、脚注数、PDF 網羅率、未変換・要確認の件数）
  <原稿と同じ相対パス>.docx
  <原稿と同じ相対パス>.report.md  … 文書ごとの検証結果と詳細ログ
  <原稿と同じ相対パス>.log.json   … 同じ内容の機械可読版
  .tex2docx/                      … 作業フォルダ（コンパイル結果、引用整形用の文書、画像）と再実行判定用の状態
```

原稿フォルダには何も書き込みません（XeLaTeX の中間ファイルも出力フォルダ側に作ります）。

## 変換の仕組み

```
.tex 群 ─ 結合・コメント除去 ─ 自作マクロ展開 ─ 転写記号→Unicode ─ 言語切替の正規化
       ─ 相互参照・見出し・図表・式番号（.aux から） ─ reledmac → 脚注 ─ 引用（biblatex の整形結果）
       ─ 図の画像化 ─ pandoc（latex+raw_tex ＋ Lua フィルタ） ─ docx 後処理（RTL・言語・フォント・スタイル）
       ─ 検証 ─ レポート
```

- 元の文書を XeLaTeX でそのままコンパイルし（出力フォルダの作業フォルダへ）、`.aux` から番号を、PDF を比較用に使います。
- pandoc は `latex+raw_tex` で動かします。pandoc が解釈できなかった TeX は生のまま AST に残るので、Lua フィルタがそれをすべて「未変換」として黄色で残します。**pandoc に黙って捨てさせない**ための仕組みです。
- 書式は `reference.docx`（スタイルテンプレート）で一元管理します。本文、見出し、脚注、RTL 用の本文・脚注・ブロック引用、文献目録（ぶら下げインデント）、図表番号、未変換・要確認の文字スタイル、アラビア語・ヘブライ語・ギリシア語の文字スタイルを定義しています。出版社のテンプレートを `reference_docx:` に指定することもできます（足りないスタイルは自動で追加されます）。

## 変換方針と根拠

実装の前に、polyglossia・bidi・reledmac/reledpar・semtrans・biblatex（`bibstyle=manuscripts, citestyle=authoryear`）・subfiles を使う合成サンプル（`samples/synthetic`）で、次の 3 方式を比較しました。

| 観点 | pandoc 単体 | make4ht（tex4ht） | 採用した構成 |
|---|---|---|---|
| ギリシア語 | ○ | ✗ 全部消えた | ○ |
| アラビア語ブロック（bidi） | 文字は残るが言語タグが誤り | ✗ 文字順が逆転、ゴミ文字混入 | ○ |
| docx の RTL 属性 | ✗ `w:rtl`・`w:bidi` が一つもない | — | ○ 後処理で付与 |
| reledmac 校訂注 | ✗ **lemma ごと黙って消える** | ✗ 崩れる | ○ 前処理で脚注化 |
| 未知のマクロ | ✗ 引数ごと黙って消える | 引数だけ残る | ○ ハイライトして残す |
| `\U{h}`・`\={\i}` | ✗ h̎・ı̄ に化ける | ✗ 画像・ı になる | ○ ḫ・ī |
| 引用・文献目録 | CSL 方式は PDF と不一致が多い | ◎ PDF と完全に一致 | ◎ make4ht で整形させる |

### 引用（biblatex）：tex4ht による整形結果を流用（方針 b）

- **CSL 方式（a）は採用しませんでした。** pandoc citeproc に Chicago author-date の CSL を使うと、`p. 12` が `12` になる、`pp. 123–125` が `123–25` に省略される、写本エントリ（shorthand `P`）が `(1200)` になる、文献目録でアラビア語転写のタイトルが `Al-Anbāʾ Fī` のように勝手に大文字化される、といった違いが出ました。そもそも `biblatex-manuscripts-philology` に相当する CSL がありません。
- **採用した方式（b）**：文書中の引用コマンドを出現順に、脚注の中かどうかという文脈も含めて小さな文書にコピーし、同じ biblatex の設定と `.bib` で make4ht にかけます。biblatex が組んだ結果を元の位置に戻します。イタリックや脚注の境界は ASCII の目印を使って受け渡します。このため ibid./op. cit. の追跡、`\footcite` の文頭の大文字化と末尾のピリオド、manuscripts スタイルまで PDF と同じになります。
- 本体の変換に make4ht を使わないのは、上の表のとおりギリシア語・bidi・reledmac を扱えないためです。引用の整形だけなら問題なく動きます。

### 判断した点（2026-10-20 に確認済み）

- **アイン・ハムザ**：生の `` ` `` と `'` は ʿ（U+02BF）と ʾ（U+02BE）に変換します（PDF では ‘ ’ と組まれています）。英語の所有格・縮約（`'s`、`'t` など）、`` `` `` と `''`、空白をはさむ `` `…' `` の対はそのまま残します。どう判定したかはレポートに記録します。`transliteration.ain_hamza: keep` で変換を止められます。
- **`\pageref`**：Word のページは PDF と一致しないので、**ラベルのある節の番号**（例：`§1.3`）にします。直前の `p.~` は取り除きます（`references.pageref_absorb_prefix`）。
- **`\edlineref`**（校訂本文の行番号の参照）：Word では行番号を再現しないので、PDF の行番号を「PDF l. 12」という固定の文字で入れ、**要確認のハイライト**を付けてログに出します。
- **校訂注の接頭辞**：既定は `[A]` / `[B]` …です（`apparatus.prefixes` で変更可）。
- **連続する注番号**：同じ lemma に A 層と B 層の注が付くと「¹¹¹²」と読み分けられないため、間に上付きの「,」を入れます（`footnotes.adjacent_separator: ""` で無効化）。RTL 段落でも右から順に読めます。
- **見出し・図表・式の番号**：PDF と同じ番号を固定の文字として入れます（`headings.numbering: none` で見出し番号を入れないこともできます）。式番号は、texmath が `\tag` を黙って捨てるため、`(1.1)` を式の右側に置きます。

## 検証

文書ごとに次を自動でチェックし、`*.report.md` とサマリーに載せます。

| 項目 | 内容 |
|---|---|
| 脚注数 | ソースの `\footnote`・`\footcite`・校訂注（`\Afootnote` など）の数と、docx の脚注数が一致するか |
| RTL 属性 | アラビア文字・ヘブライ文字を含む run すべてに `w:rtl` と bidi 言語があるか |
| アラビア語・ヘブライ語の網羅性 | ソースの RTL の語がすべて docx にあるか（XeTeX の PDF からはアラビア語を復元できないため、ソースと比較する） |
| PDF とのテキスト網羅性 | pdftotext で抽出した PDF の語（ラテン文字・ギリシア文字）と docx（本文＋脚注＋数式）を比較し、欠落・重複の疑いを列挙する。柱・ページ番号・行末のハイフンは除いて比較する |
| 引用 | `??` や素の引用キーが残っていないか |
| 不正文字 | U+FFFD・私用領域の文字がないか |
| グリフ | 指定フォントで表示できない文字の一覧 |
| 特殊文字の個数 | 転写記号などの非 ASCII 文字が、ソースより少なくなっていないか |
| 未変換マクロ | ハイライトした未変換箇所と、ハイライト外に残った `\` で始まる文字列の一覧 |
| 表のセル結合 | `\multicolumn` / `\multirow` の数と docx の結合セルの数の比較 |

## 設定ファイル

`tex2docx init-config` で、既定値とコメント付きの設定ファイルを書き出せます。指定した項目だけが既定値に上書きされます。主な項目は次のとおりです。

| キー | 内容 |
|---|---|
| `reference_docx` | Word のスタイルテンプレート（出版社のテンプレートなど） |
| `tools.*` | 外部ツールのパス |
| `compile.enabled` / `runs` / `timeout` | 元の文書のコンパイル |
| `fonts.main` / `fonts.scripts.{arabic,hebrew,greek,east_asian}` | Word で使うフォント（未指定なら fontspec の設定から推定） |
| `fonts.substitutes` | フォントが無い場合の OS 別の代替（例：`"Scheherazade New": {windows: "Traditional Arabic", macos: "Geeza Pro"}`） |
| `fonts.target_os` | 校閲者の OS を想定して代替を選ぶ（`auto` / `windows` / `macos`） |
| `fonts.size_percent` | RTL 文字の表示倍率（アラビア文字は同じポイント数だと小さく見えるため、既定 125%） |
| `languages` | polyglossia の言語名 → Word の言語コード |
| `transliteration.*` | アイン・ハムザの扱い、追加のアクセントマクロ |
| `apparatus.prefixes` / `format` / `direction` | 校訂注の接頭辞と書式、注の段落の向き |
| `footnotes.adjacent_separator` | 連続する注番号の区切り |
| `parallel.layout` | 対訳の配置（`interleave` / `table` / `sequential`） |
| `references.*` | `\pageref`・`\edlineref`・未解決参照の表示 |
| `headings.*` / `captions.*` / `equations.numbering` | 番号の入れ方 |
| `citations.method` | `oracle`（biblatex で整形）/ `none` |
| `figures.dpi` / `pdf_converters` / `render_tikz` | 図の変換 |
| `macros.replace` / `ignore` / `transparent_environments` / `expand_renewed` | マクロの置換規則、出力から黙って除いてよいレイアウト用マクロ、外枠だけ外す環境 |
| `styles.*` | docx 側のスタイル名、RTL 段落とみなす比率 |

## フォントについての注意

- docx に入るのはフォントの**名前だけ**です。校閲者の PC にそのフォントが無ければ、Word が別のフォントで表示します。
- fontspec で `Path=Font/` のようにフォントファイルを直接指定している場合（例：Scheherazade New）、変換する PC にインストールされていなければ `fonts.substitutes` の代替フォントを指定し、レポートで警告します。校閲者全員に同じフォントを入れてもらうか、Windows と macOS の両方にある書体（例：Times New Roman）を `fonts.scripts` で指定してください。
- Word はアラビア語・ヘブライ語に**複合文字用（cs）のフォント**を使います。tex2docx は `w:rFonts w:cs` を設定し、「Arabic Text」「Hebrew Text」の文字スタイルにまとめているので、Word のスタイルを変えるだけで一括して変更できます。
- 「未変換」の目印に含まれる日本語は東アジア用フォント（Windows: 游明朝、macOS: ヒラギノ明朝）で表示されます。

## 既知の制限

- `\selectlanguage` による切り替えは言語情報を引き継ぎません（RTL の判定は文字種で行うので、表示の向きには影響しません）。
- `align` 系の環境の式番号は、行ごとに数えた近似です（`\nonumber`・`\notag`・`\tag` は考慮します）。`\numberwithin` は節・章単位のみ対応しています。
- `\cref` の範囲の圧縮（1.1–1.3）は行わず、「sections 1.1, 1.2 and 1.3」のように列挙します。
- 引用の整形では、引用だけを抜き出した文書を組むので、**ページに依存する biblatex の追跡**（ページが変わると ibid. をやめる、など）は PDF と異なる場合があります。
- `\textcolor` の文字色は Word に引き継ぎません（文字は残ります）。`\hl` はハイライトになります。
- 行内の `\tikz …;` は画像化されず、未変換として残ります。
- Word で `\pageref` のページ番号は再現しません（節番号で代替）。reledmac の行番号も再現しません。
- Windows ではパスの長さに制限があります。出力フォルダは浅い場所（例：`C:\out`）にすることを勧めます。OneDrive などの同期フォルダでは、同期中のファイルロックで失敗することがあります。

## 開発

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

`tests/golden/` に、機能ごとの最小の .tex を置いています（多言語段落、入れ子の校訂注、対訳、biblatex の引用、数式、表、TikZ）。テストではそれを変換し、docx の XML に期待する要素（`w:rtl`、bidi 言語、本物の脚注、スタイル、セル結合、OMML など）があるかを確かめます。外部ツールが無い環境では、該当するテストをスキップします。GitHub Actions で Windows と macOS（Python 3.10 / 3.12）のマトリクステストを実行しています。

`samples/synthetic/` は、実原稿のプリアンブルを模した合成サンプルです（フォントは TeX Live 同梱の Amiri とシステムの Noto Serif に置き換えています）。
