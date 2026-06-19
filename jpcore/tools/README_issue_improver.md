# issue_improver.py — JP Core Issue Improver（SWG別 Issue改善ツール / 1コマンド統合版）

所属SWGのIssueを一覧化し、各Issueに最適なspec-kit処理を割り当てる単一CLI。
**1回の実行で「一覧」と「処理プロンプト」を一括出力**する。任意のSWGに `--label` で対応。

## 必要環境
- Python 3.8+（標準ライブラリのみ。追加インストール不要）
- インターネット接続（GitHub公開APIを使用）
- 任意: `export GITHUB_TOKEN=ghp_xxx` でレート制限を緩和

## 基本の使い方
```bash
# (1)着手順Issue一覧 と (2)各Issueの /speckit.specify プロンプト を一括出力
python3 tools/issue_improver.py

# 対象リポジトリとSWGを指定（URL/slugどちらでも可）
python3 tools/issue_improver.py --repo https://github.com/jami-fhir-jp-wg/jp-core-v1x --label "検査診療WG"
python3 tools/issue_improver.py --repo jami-fhir-jp-wg/jp-core-v1x --label "ﾀｰﾐﾉﾛｼﾞｰWG"

# 処理プロンプトをIssueごとの個別ファイルにも出力（01-issue-925.md ...）
python3 tools/issue_improver.py --out tools/prompts
```

## 設定（リポジトリURL と SWG）
`--repo`（リモートリポジトリ）と `--label`（SWG）は、次の優先順で解決される（**組込み既定なし**）:

1. **コマンド引数** `--repo` / `--label` / `--milestone`
2. **設定ファイル** `tools/issue_improver.config.json`（スクリプトと同じ場所。`--config` で別パス指定可）
3. **ローカルgitの origin から自動検出**（repoのみ。`--no-detect` で無効化）

`repo` と `label` がどこからも解決できない場合はエラーで停止する（何を設定すべきかを表示）。
`milestone` は任意で、未指定なら全マイルストーンを対象にする。

`--repo` は `owner/name` / `https://github.com/owner/name(.git)` / `git@github.com:owner/name.git` のいずれの形式でも受け付ける。
実行時に解決結果が `[設定] repo=... / label=... / milestone=...` として表示される。

### 設定ファイル例（`tools/issue_improver.config.json`）
```json
{
  "repo": "jami-fhir-jp-wg/jp-core-v1x",
  "label": "ｱﾄﾞﾐﾆｽﾄﾚｰｼｮﾝWG",
  "milestone": "1.3-release"
}
```
普段使うリポジトリ／SWGをここに書いておけば、引数なしの `python3 tools/issue_improver.py` だけで動く。
別SWGを一時的に見たいときは `--label` で上書きする。

出力は2部構成:
- **(1) 着手順Issue一覧** … 深刻度・ケース(A/B/C/D)・処理パス(フル/軽量)・対象ファイル・着手順
- **(2) 処理プロンプト** … 各Issueの工程・貼り付け用 `/speckit.specify` 文

`--out` で書き出した `tools/prompts/NN-issue-XXX.md` には「`/speckit.specify` 貼り付け文」が入っており、
Claude Code に流せば spec-kit サイクルを開始できる（ブランチは specify が作成する）。

## 一覧だけ / プロンプトだけ
```bash
python3 tools/issue_improver.py --list-only --format csv > issues.csv   # 一覧のみ(CSV)
python3 tools/issue_improver.py --list-only --format json > issues.json # 一覧のみ(JSON)
python3 tools/issue_improver.py --prompts-only                          # 処理プロンプトのみ
```

## 自動実行（--run）— spec-kit を自動で回す
`--run` を付けると、各Issueを **Claude Code のヘッドレスモード（`claude -p`）経由で spec-kit を自動実行**する。
`/speckit.*` は Claude Code のコマンドのため、この方式が唯一の自動化手段。

```bash
# まずドライラン（実行されるコマンドを表示するだけ。安全）
python3 tools/issue_improver.py --run

# 1件だけ実際に実行して試す（推奨）
python3 tools/issue_improver.py --run --execute --only 925

# 全件を実際に自動実行
python3 tools/issue_improver.py --run --execute
```

実行される工程（ケースごと、Issueごとに順番に）:
- 軽量（A）: `specify → plan → tasks → implement → sushi build → checklist`（停止せず自動）
- フル（B/C）: `specify →〔clarify 手動ゲートで停止〕→ plan → tasks → analyze → implement → sushi build → checklist`
- 矛盾（D）: `〔『正』を決める手動ゲート〕→ specify → plan → tasks → implement → sushi build → checklist`

※ `tasks` は implement の前提のため軽量でも省略しない。`checklist` は実行の最後に生成する。

方針（設計上の約束）:
- **ブランチは `/speckit.specify` が作成する**（spec-kit 既定の連番命名 `NNN-slug`）。ツールは自前でブランチを切らない。各工程のプロンプトに「commit・push は claude にさせない」ガードを付与し、ファイル編集と spec-kit 工程のみ行わせる。
- **正常完了（全工程＋`sushi build` 成功）したIssueだけツールがコミットする**（`git add -A && git commit -m "#NNN …"`）。**push／PR／マージは別工程の人間**が実施する（`--no-commit` でコミットも無効化）。
- **エラーは停止しない**。失敗したIssueは未完変更を破棄して**スキップ**し、次へ進む。どのIssueが成功/スキップしたかは末尾の**レポート（`tools/issue_improver_report_*.md`）**で後から分かる。
- **手動ゲート**：フル（B/C）は specify 後に `/speckit.clarify` で一旦停止（PMが回答）、ケースDは specify 前に『正』を1つ決める。`--auto-complex` で両ゲートを無効化（全自動。ただし制度判断もAI任せ）。
- 既定は**ドライラン**。実際に動かすのは `--execute` を付けたときだけ。
- 前提: Claude Code がインストール＆ログイン済み。`--execute` 時はファイルを編集し、プラン利用量を消費する。

主なオプション: `--only <番号>` / `--case A|B|C|D`（指定ケースのみ）/ `--limit N` / `--auto-complex`（手動ゲートも全自動）/ `--yes`（権限プロンプトを全自動承認）/ `--no-commit`（自動コミットしない）/ `--no-build` / `--claude-bin` / `--permission-mode`（既定 acceptEdits）。

### ケース種別ごとにまとめて実行
`--run` はIssueを**ケースA/B/C/Dの種別ごと(A→B→C→D)にまとめて実行**する。各グループの先頭に見出しを表示し、同種をバッチで処理する。`--case B` のように1ケースだけ回すこともできる。
- **A（軽量）**: タイポ・文言・コメント等。clarify/analyze 省略、止まらず自動。
- **B（構造）**: cardinality・slicing 等の仕様変更。フル工程＋ clarify 手動ゲート。
- **C（制度）**: マイナ保険証・資格確認等の制度設計。フル工程＋ clarify は **PM が回答**。
- **D（矛盾）**: 記述の食い違い解消。**specify の前に『正』を1つ決める人間ゲート**を挟み、以降は軽量工程。
- 分類キーワードは `issue_improver.py` 冒頭の `CASE_KW` で調整可能（判定優先度 D > C > B > A）。

### 権限（自動YES）
mechanical な許可（編集の適用・`sushi build` 等）は自動承認する。
- 既定: `--permission-mode acceptEdits`（編集を自動承認）＋ `--allowedTools`（`ALLOW_TOOLS` の安全な許可リストのみ）。
- `--yes`: `--dangerously-skip-permissions` で**全プロンプトを自動承認**（bashも含む。利便性は高いが注意）。
- なお claude には GUARD で commit/push をさせない。コミットは**正常完了時にツール本体**が行う（push はしない）。
- 業務・設計の中身の質問（clarify／ケースDの正の決定）は自動YESにせず、手動ゲートで人間が判断する（`--auto-complex` で無効化可、ただし制度判断もAI任せになる）。

> 注意: ブランチは `/speckit.specify` が作る（spec-kit 既定の連番）。手作業・ツールとも同じ命名になる。
> 複数Issueを連続実行すると、specify が直前のブランチ上に次のブランチを作るため履歴が積み重なる場合がある。Issue を完全に分離したい場合は、各 Issue の前に手動でベース（develop 等）へ戻ってから回す。
> commit はツールが正常時に行うのみ。push／PR／マージは別工程の人間が実施する。

## 実行位置の不正チェック
実行したカレントディレクトリのローカルgit（`origin`）が、対象 `repo` と一致するかを検証する。

- **一致** → そのまま続行。
- **不一致**（例: 対象は jp-core-v1x なのに fhir-jp-core-r4 の中で実行）→ **エラーで停止**。
  間違ったクローンでブランチ作成・spec-kit手順を実行する事故を防ぐ。
- **Gitリポジトリ外** → **エラーで停止**（対象リポジトリのローカルクローン内で実行する必要があるため）。

意図的に別の場所で実行したい場合は `--no-location-check` で無効化できる。

## ケース／処理パスの判定ルール
ケース（A/B/C/D）を `CASE_KW`（優先度 D>C>B>A）で判定し、ケースから処理パスを決める。
- **A（軽量）** typo・用語統一・コメント・重複・OID 等 → specify→plan→tasks→implement→build→checklist（clarify/analyze 省略）
- **B（フル）** slicing・cardinality・discriminator・binding 等の構造変更 → 全工程＋clarify手動ゲート
- **C（フル）** マイナ保険証・資格確認・保険者番号・後期高齢者・共済/船員 等の制度設計 → 全工程＋clarify は PM 回答
- **D（軽量＋正の決定）** 矛盾・食い違い・不一致 等 → 先に『正』を1つ決める人間ゲート→軽量工程

判定キーワードは `issue_improver.py` 冒頭の `CASE_KW` で調整可能。

## オプション一覧
| オプション | 既定 | 説明 |
|---|---|---|
| `--repo` | （CLI/設定/自動検出で必須） | 対象リモートリポジトリ owner/name または GitHub URL |
| `--label` | （CLI/設定で必須） | 対象SWGのラベル名（GitHub表記と完全一致） |
| `--milestone` | （任意。未指定で全マイルストーン） | 対象マイルストーン |
| `--config` | tools/issue_improver.config.json | 設定ファイルのパス |
| `--no-detect` | off | git originからのrepo自動検出を無効化 |
| `--no-location-check` | off | 実行位置(ローカルrepo)と対象repoの一致チェックを無効化 |
| `--state` | open | open / closed / all |
| `--order` | severity | severity / file / number |
| `--format` | md | 一覧の形式 md / csv / json |
| `--out` | （なし） | 処理プロンプトをファイル出力するディレクトリ |
| `--list-only` | off | 一覧のみ出力 |
| `--prompts-only` | off | 処理プロンプトのみ出力 |
| `--case` | （全ケース） | 指定ケースのみ実行（A=軽量 / B=構造 / C=制度 / D=矛盾） |
| `--yes` | off | 権限プロンプトを全自動承認（--dangerously-skip-permissions） |
| `--no-commit` | off | 正常完了してもコミットしない（既定はツールが自動コミット） |

## 注意
- 実際のFSH修正・git・ビルド・PRは Claude Code 側で行う（本ツールは一覧化と処理プロンプト生成まで）。
- ラベル名は GitHub 上の表記（半角カナ等）と完全一致させること。
