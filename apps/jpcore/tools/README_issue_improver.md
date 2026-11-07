# issue_improver.py — JP Core Issue Improver（SWG別 Issue改善支援ツール）

所属SWG（ラベル）のオープンIssueを GitHub から取得し、内容から **ケースA/B/C/D** を判定して、
着手順の一覧と、各Issueの **spec-kit 処理プロンプト（specify〜checklist）** を生成する単一CLI。
さらに `--run --execute` で、Claude Code（ヘッドレス）経由で spec-kit を**自動実行**できる。

- 一覧化・プロンプト生成のみ … ネットワーク（GitHub公開API）だけで動作
- 自動実行（`--run`）… Claude Code と spec-kit、`sushi` が必要

## 必要環境
- Python 3.8+（標準ライブラリのみ。追加インストール不要）
- インターネット接続（GitHub 公開 API）。任意で `export GITHUB_TOKEN=ghp_xxx`（レート制限緩和）
- 自動実行する場合: Claude Code（`claude`）がインストール＆ログイン済み、`sushi`（FSH ビルド）

## 設定（repo / label / milestone）
解決の優先順（**組込み既定なし**）:
1. コマンド引数 `--repo` / `--label` / `--milestone`
2. 設定ファイル `tools/issue_improver.config.json`（`--config` で別パス指定可）
3. ローカル git の origin から `--repo` を自動検出（`--no-detect` で無効）

`repo` と `label` がどこからも解決できなければエラー停止。`milestone` は任意（未指定で全マイルストーン）。

設定ファイル例:
```json
{ "repo": "jami-fhir-jp-wg/jp-core-v1x", "label": "ｱﾄﾞﾐﾆｽﾄﾚｰｼｮﾝWG", "milestone": "1.3-release" }
```
※ラベル名は GitHub 表記（半角カナ等）と完全一致させること。

## 基本の使い方（一覧＋プロンプト生成）
```bash
# (1)着手順Issue一覧 と (2)各Issueの 全工程プロンプト を出力
python3 tools/issue_improver.py

# 対象を明示
python3 tools/issue_improver.py --repo jami-fhir-jp-wg/jp-core-v1x --label "検査診療WG"

# 一覧だけ / プロンプトだけ
python3 tools/issue_improver.py --list-only --format csv > issues.csv
python3 tools/issue_improver.py --prompts-only

# プロンプトを Issue ごとのファイルに出力（01-issue-XXX.md …）
python3 tools/issue_improver.py --prompts-only --out tools/prompts

# ケースで絞り込み（--case 相当。一覧・プロンプト・自動実行に効く）
python3 tools/issue_improver.py --prompts-only --case A
```

### 生成プロンプトについて
- 各Issueの全工程（**specify → (clarify) → plan → tasks → (analyze) → implement → sushi build → checklist**）を、
  Claude Code に上から順に貼り付けられる形で出力する。
- `specify` は**最小構成**。Issue 本文は貼り込まず、`gh issue view <番号>` または Issue URL で
  **必ず Issue を参照する**よう指示する（タイトル・該当ファイル等は一覧表で確認できるため重複させない）。
- 手で貼る用のプロンプトには GUARD（後述）を付けない。GUARD は `--run` 自動実行時のみ付与される。

## ケース分類（A/B/C/D）
内容のキーワードから判定（優先度 D > C > B > A、`CASE_KW` で調整可）。ケースで工程と手動ゲートが決まる。

| ケース | 内容 | 工程 | 手動ゲート |
|---|---|---|---|
| A | タイポ・文言・コメント等の単純修正 | specify→plan→tasks→implement→build→checklist | なし |
| B | 構造・cardinality・slicing 等の仕様変更 | ＋clarify・analyze | clarify / 実装前 |
| C | 保険制度などの設計（マイナ保険証・保険者番号等） | ＋clarify・analyze | clarify / 実装前 |
| D | 記述の矛盾・食い違いの解消 | A と同じ（clarify/analyze なし） | 『正』決定（specify前） |

## 自動実行（--run --execute）
`--run` で各 `/speckit.*` を **Claude Code ヘッドレス（`claude -p`）** で順に実行する。既定はドライラン（表示のみ）、
`--execute` で実行。ケース種別ごと（A→B→C→D）にまとめて処理し、**エラーは停止せずスキップ**して次へ進む。

```bash
# まずドライラン（実行されるコマンドを表示）
python3 tools/issue_improver.py --run

# 1件だけ実際に試す（推奨）。ログも残す
python3 tools/issue_improver.py --run --execute --only 942 --log tools/run_942.log

# ケースA だけ（手動ゲート無し＝無人で流せる）
python3 tools/issue_improver.py --run --execute --case A
```

### ブランチ / commit / push
- **ブランチ作成 … 自動**（`/speckit.specify` が spec-kit 既定の連番名 `NNN-slug` で作成）
- **commit … 自動**（正常完了時に**ツール自身**が `git add -A && git commit`。`--no-commit` で無効化）
- **push … しない**（人間が別工程で実施。下記の権限で push はハード拒否）

### 権限（自動承認の範囲）
既定（`--yes` なし）は、許可リスト＋拒否リストで安全に自動承認する:
- 自動承認: `Edit` / `Write` / `MultiEdit` / `Bash`（任意）/ `mcp__serena`（Serena 全ツール）
- **拒否（許可より優先）: `Bash(git push:*)`** … push は実行させない
- `--yes` を付けると `--dangerously-skip-permissions` になり**全許可（push 拒否も無効）**。**非推奨**。
- 最終防壁として GitHub 側で develop/main の**ブランチ保護**を併用すること
  （Serena のシェル経由 push までは拒否リストで塞げないため）。

## 人間ゲート（3か所。`--auto-complex` で全無効化）
ケースに応じて停止し、**レビュー対象を表示**してから、確定事項をテキスト入力させる。
入力は後続工程のプロンプトに注入され、Claude がそれに従う（空Enter＝AI判断）。

| ゲート | ケース | 位置 | 表示するもの | 入力の注入先 |
|---|---|---|---|---|
| 正の決定 | D | specify の前 | Issue 本文 | **specify**（`【確定した正】…`） |
| 要件確認（clarify） | B/C | specify の後 | Issue 本文 ＋ **spec.md** | **plan 以降**（`【clarifyでの確定事項】…`） |
| 実装前レビュー | B/C | analyze の後 | **analyze レポート** | **implement**（`【analyze後の修正指示】…`） |

- 各ゲートの選択肢: `Enter=続行 / s=このIssueをスキップ / q=全体中止`。
- clarify は**ツール内の人手ゲート**で、`/speckit.clarify` をツールが自動実行するわけではない
  （対話Q&Aのため。確定事項をここに入力すれば足りる。AIの質問を見たい場合のみ別途手動実行も可）。
- 入力の経緯はファイルには記録されない。残したい場合は `--log`（後述）か PR 説明に記す。

## ログ・計測
- `--log <ファイル>` … ツールの画面出力（設定・各ゲート表示・**入力したゲート内容**・analyzeレポート・計測・サマリ）を
  追記する。※ claude 本体のライブ出力（specify/plan/implement のストリーミング）は対象外。
  丸ごと残すなら外側で `script -q run.log <コマンド>` を併用。
- 実行後に **計測**（各工程の所要秒）を画面表示し、`tools/issue_improver_timing_*.csv` に保存。
- 実行結果は `tools/issue_improver_report_*.md`（成功/スキップ/失敗・ブランチ）に保存。
- `--probe` … 実行前に自明プロンプトを1回流し、Claude Code＋MCP の素の起動コストを計測（切り分け用）。

## 実行位置の不正チェック
カレントのローカル git（origin）が対象 `repo` と一致するか検証する。不一致やリポジトリ外なら停止
（対象リポジトリのクローン内で実行する想定）。`--no-location-check` で無効化。
※失敗時のクリーンアップ `git clean` はツール自身のディレクトリを除外するため、自分（tools/）を消さない。

## オプション一覧（主なもの）
| オプション | 既定 | 説明 |
|---|---|---|
| `--repo` | （必須・自動検出可） | 対象リポジトリ owner/name または GitHub URL |
| `--label` | （必須） | 対象SWGのラベル名（GitHub 表記と完全一致） |
| `--milestone` | （任意） | 対象マイルストーン |
| `--config` | tools/issue_improver.config.json | 設定ファイルのパス |
| `--no-detect` | off | git origin からの repo 自動検出を無効化 |
| `--no-location-check` | off | 実行位置と対象 repo の一致チェックを無効化 |
| `--state` | open | open / closed / all |
| `--order` | severity | severity / file / number |
| `--format` | md | 一覧形式 md / csv / json |
| `--out` | （なし） | プロンプトを Issue ごとにファイル出力するディレクトリ |
| `--list-only` / `--prompts-only` | off | 一覧のみ / プロンプトのみ |
| `--case` | （全ケース） | 指定ケースのみ（A/B/C/D） |
| `--run` | off | spec-kit を自動実行（既定ドライラン） |
| `--execute` | off | `--run` 時に実際に実行 |
| `--only <番号>` / `--limit N` | （なし） | 指定Issueのみ / 件数上限 |
| `--auto-complex` | off | 手動ゲート（D=正の決定／B/C=clarify・実装前）を無効化し全自動 |
| `--yes` | off | 権限を全自動承認（`--dangerously-skip-permissions`。push 拒否も無効。非推奨） |
| `--no-commit` | off | 正常完了してもコミットしない |
| `--no-build` | off | implement 後の `sushi build` を行わない |
| `--probe` | off | 起動コスト計測（自明プロンプトを1回実行） |
| `--log <ファイル>` | （なし） | 画面出力をファイルへ追記 |
| `--claude-bin` | claude | claude 実行ファイル |
| `--permission-mode` | acceptEdits | Claude Code の権限モード |

## 注意
- 実際の FSH 修正・git・ビルドは Claude Code 側で行う（本ツールは一覧化・プロンプト生成・自動実行の制御）。
- push / PR / マージは**人間が別工程**で実施する。
- 複数Issueを連続自動実行すると、`/speckit.specify` が直前のブランチ上に次のブランチを作り履歴が積み重なる
  ことがある。各Issueを分離したい場合は、Issue ごとにベース（develop 等）へ戻してから実行する。
- 判定キーワードは `issue_improver.py` 冒頭の `CASE_KW` で調整可能。
