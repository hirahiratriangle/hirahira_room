#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
issue_improver.py — JP Core Issue Improver（SWG別 Issue改善支援ツール / 1コマンド統合版）

1回の実行で次を行う:
  (1) 所属SWG(ラベル)のオープンIssueを取得し、深刻度・対象ファイル・
      処理パス(フル/軽量)・推奨着手順を付けて一覧表示する
  (2) 着手順に各Issueへ最適なspec-kit処理(パス)を判定し、
      貼り付け用 /speckit.specify プロンプトと工程・ブランチ名を生成する

任意のSWGに対応: --label "<ラベル名>"。
GitHub公開APIを使用。GITHUB_TOKEN 環境変数があればレート制限が緩和される。

使用例:
  python3 tools/issue_improver.py                       # 一覧 + 処理プロンプト
  python3 tools/issue_improver.py --label "検査診療WG"     # 他SWGを対象
  python3 tools/issue_improver.py --out tools/prompts     # プロンプトを個別ファイル出力
  python3 tools/issue_improver.py --list-only --format csv > issues.csv
  python3 tools/issue_improver.py --prompts-only
"""
import argparse, datetime, json, os, re, shlex, subprocess, sys, urllib.parse, urllib.request, urllib.error

CONFIG_NAME = "issue_improver.config.json"   # スクリプトと同じディレクトリに置く設定ファイル

SEV_RANK = {"Critical": 0, "High": 1, "Med": 2, "Low": 3, "-": 4}

FULL_KW = ["slic", "cardinality", "discriminator", "保険者番号", "マイナ", "後期高齢者",
           "共済", "船員", "ConceptMap", "CodeSystem", "partOf", "associatedEncounter",
           "体系", "未整備", "未定義", "未明示", "binding", "invariant", "資格確認"]
LIGHT_KW = ["typo", "タイポ", "ゴミ文字", "孤立行", "用語", "統一", "コメント", "重複",
            "OID", "ケース違い", "誤導", "別人", "同一", "綴り", "short", "矛盾"]

# ケース分類（スライドの ケースA/B/C/D に対応）。判定優先度 D > C > B > A(既定)
CASE_KW = {
    "D": ["矛盾", "食い違い", "食違い", "不一致", "齟齬", "相違", "conflict", "inconsistent"],
    "C": ["マイナ", "資格確認", "保険者番号", "保険証", "後期高齢者", "共済", "船員", "点数表", "制度"],
    "B": ["slic", "cardinality", "discriminator", "binding", "invariant", "partOf",
          "associatedEncounter", "ConceptMap", "CodeSystem", "Extension", "構造", "体系"],
}
# ケース → (処理パス, 説明)
CASE_INFO = {
    "A": ("軽量", "タイポ・文言・コメント等の単純修正（clarify/analyze 省略）"),
    "B": ("フル", "構造・cardinality など仕様変更（破壊的注意）"),
    "C": ("フル", "保険制度などの設計（clarify で PM が回答）"),
    "D": ("軽量", "記述の矛盾・食い違いの解消（先に『正』を1つ決める）"),
}
CASE_ORDER = ["A", "B", "C", "D"]

PATH_PATTERN = re.compile(r"input/[\w/\.\-]+\.(?:fsh|md)")
PROFILE_PATTERN = re.compile(r"JP_[A-Za-z]+")

STEPS = {
    "フル": "specify → clarify → plan → tasks → analyze → implement → sushi build → checklist →〔人間レビュー/コミット〕",
    "軽量": "specify → plan → tasks → implement → sushi build → checklist →〔人間レビュー/コミット〕 (clarify/analyze 省略)",
}


def parse_repo(value):
    """owner/name / https://github.com/owner/name(.git) / git@github.com:owner/name.git → 'owner/name'"""
    if not value:
        return None
    v = value.strip()
    m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$", v)
    if m:
        return m.group(1)
    if re.fullmatch(r"[^/\s]+/[^/\s]+", v):   # 既に owner/name 形式
        return v
    return None


def detect_origin_repo():
    """カレントのローカルgitの origin から owner/name を推定（取得できなければ None）"""
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return parse_repo(url)
    except Exception:
        return None


def load_config(path):
    """設定ファイル(JSON)を読む。{'repo': '...', 'label': '...', 'milestone': '...'}"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[warn] 設定ファイル読込失敗 ({path}): {e}", file=sys.stderr)
        return {}


def check_location(repo, disabled):
    """実行位置(カレントのローカルgit)が対象リポジトリと一致するか検証する。
    不一致なら停止。Gitリポジトリ外なら警告のみ（一覧取得は可能なため）。"""
    if disabled:
        return
    local = detect_origin_repo()
    if local is None:
        sys.exit("[実行位置エラー] カレントディレクトリはGitリポジトリ外です。"
                 "対象リポジトリのローカルクローン内で実行してください（意図的なら --no-location-check で無視可）。")
    if repo and local.lower() != repo.lower():
        sys.exit(f"[実行位置エラー] 現在地のリポジトリ(origin={local})が対象(repo={repo})と一致しません。\n"
                 f"  対象 {repo} のローカルクローン内で実行してください（意図的なら --no-location-check で無視可）。")


def api_get(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "swg-tool"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_issues(repo, label, milestone, state):
    q = f'repo:{repo} is:issue state:{state} label:"{label}"'
    if milestone:
        q += f' milestone:"{milestone}"'
    items, page = [], 1
    while True:
        url = ("https://api.github.com/search/issues?q="
               + urllib.parse.quote(q) + f"&per_page=100&page={page}")
        data = api_get(url)
        batch = data.get("items", [])
        items.extend(batch)
        if len(items) >= data.get("total_count", 0) or not batch:
            break
        page += 1
    return items


def severity(title):
    m = re.match(r"\s*\[(Critical|High|Med|Low)\]", title)
    return m.group(1) if m else "-"


def target_files(body, title):
    body = body or ""
    files = []
    for m in PATH_PATTERN.findall(body):
        if m not in files:
            files.append(m)
    if not files:
        profs = []
        for m in PROFILE_PATTERN.findall(title + " " + body):
            if m not in profs:
                profs.append(m)
        files = profs[:2]
    return files[:3]


def classify_path(title, body, sev):
    hay = title + " " + (body or "")
    low = hay.lower()
    if any(k.lower() in low or k in hay for k in FULL_KW):
        return "フル"
    if any(k.lower() in low or k in hay for k in LIGHT_KW):
        return "軽量"
    return "フル" if sev in ("High", "Med") else "軽量"


def classify_case(title, body):
    """スライドの ケースA/B/C/D に分類。判定優先度 D > C > B > A(既定)。"""
    hay = title + " " + (body or "")
    low = hay.lower()
    for case in ("D", "C", "B"):
        if any(k.lower() in low or k in hay for k in CASE_KW[case]):
            return case
    return "A"


def enrich(items):
    rows = []
    for it in items:
        title, body = it["title"], (it.get("body") or "")
        sev = severity(title)
        case = classify_case(title, body)
        rows.append({"num": it["number"], "title": title, "severity": sev,
                     "files": target_files(body, title),
                     "case": case, "path": CASE_INFO[case][0],
                     "url": it["html_url"], "body": body})
    return rows


def filter_case(rows, case):
    """ケース(A/B/C/D)で絞り込む（--case 相当）。None/空なら全件。"""
    if case in ("A", "B", "C", "D"):
        return [r for r in rows if r["case"] == case]
    return rows


def order_rows(rows, mode):
    if mode == "number":
        key = lambda r: r["num"]
    elif mode == "file":
        key = lambda r: ((r["files"][0] if r["files"] else "zzz"), SEV_RANK[r["severity"]], r["num"])
    else:
        key = lambda r: (SEV_RANK[r["severity"]], (r["files"][0] if r["files"] else "zzz"), r["num"])
    ordered = sorted(rows, key=key)
    for i, r in enumerate(ordered, 1):
        r["order"] = i
    return ordered


# ---------- (1) 一覧出力 ----------
def out_md(rows, label):
    out = [f"# {label} — 着手順Issue一覧 ({len(rows)}件)\n",
           "| 順 | Issue | 深刻度 | ケース | 対象ファイル | タイトル |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        files = "<br>".join(r["files"]) or "-"
        out.append(f"| {r['order']} | [#{r['num']}]({r['url']}) | {r['severity']} | {r['case']} | {files} | {r['title']} |")
    print("\n".join(out))


def out_csv(rows):
    import csv
    w = csv.writer(sys.stdout)
    w.writerow(["order", "num", "severity", "path", "files", "title", "url"])
    for r in rows:
        w.writerow([r["order"], r["num"], r["severity"], r["path"],
                    " ; ".join(r["files"]), r["title"], r["url"]])


def out_json(rows):
    slim = [{k: r[k] for k in ("order", "num", "severity", "path", "files", "title", "url")} for r in rows]
    print(json.dumps(slim, ensure_ascii=False, indent=2))


# ---------- (2) 処理プロンプト生成 ----------
def short_name(title):
    t = re.sub(r"^\s*\[(Critical|High|Med|Low)\]\s*", "", title)
    t = re.sub(r"[^0-9A-Za-z_]+", "-", t).strip("-").lower()
    return (t[:30] or "fix").rstrip("-")


def specify_prompt(r):
    """最小構成の /speckit.specify（貼り付け用）。

    Issue に書かれている情報（タイトル・該当ファイル・深刻度等）は重複出力しない。
    Claude Code に Issue を必ず参照させる前提で、プロンプトは「どの Issue か＋必読」のみ。
    タイトル/ケース等は一覧表で人間が確認できる。注意書き・GUARD は付けない
    （JP Core 規約は repo の constitution、Serena は MCP 接続が担保。GUARD は --run のみ）。
    """
    return f"""/speckit.specify

Issue #{r['num']} の指摘を解消する最小変更の仕様を策定する。着手前に Issue 本文を必ず参照すること（`gh issue view {r['num']}`、または下記URL）。
{r['url']}
"""


# 工程名 → 表示ラベル
STEP_LABEL = {"plan": "設計", "tasks": "タスク化", "analyze": "整合性確認", "implement": "実装"}


def build_steps(r):
    """specify 〜 checklist まで、Claude Code に手で貼る全プロンプトを順に組み立てて返す。

    手動実行用のため GUARD は付けない（commit/push 等は人が管理）。
    GUARD は自動実行(--run / run_one)でのみ各プロンプトに付与される。

    ケース(A/B/C/D)から決まる工程に従う（run_one の自動実行と同じ並び）:
      - 軽量(A/D): specify → plan → tasks → implement → sushi build → checklist
      - フル(B/C): specify → clarify(手動) → plan → tasks → analyze → implement → sushi build → checklist
      - ケースD は specify の前に『正』を1つ決める手動ゲートを挟む
    各要素は dict: {no, kind('prompt'|'gate'|'gate-prompt'|'shell'), title, text, hint}
    """
    full = (r["path"] == "フル")
    steps = []

    def add(title, text, kind="prompt", hint=""):
        steps.append({"kind": kind, "title": title, "text": text, "hint": hint})

    if r["case"] == "D":
        add("『正』を決める（ケースD）", "", kind="gate",
            hint="specify の前に、食い違う記述のどれを『正』にするかを1つ決める（必要なら PM 確認）。")
    add("仕様策定 /speckit.specify", specify_prompt(r))
    if full:
        add("要件確認 /speckit.clarify（手動ゲート）", "/speckit.clarify",
            kind="gate-prompt", hint="Claude の質問に PM が回答し、内容をレビューしてから次へ。")
    for st in REST_STEPS[r["path"]]:
        add(f"{STEP_LABEL[st]} /speckit.{st}", f"/speckit.{st}")
    add("FSH検証 sushi build", "sushi build", kind="shell",
        hint="FSH をビルドしてエラーが無いか検証する。")
    add("品質チェック /speckit.checklist", "/speckit.checklist")

    for i, s in enumerate(steps, 1):
        s["no"] = i
    return steps


def emit_prompts(rows, out_dir):
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    print("\n# 着手順 処理プロンプト（specify〜checklist 全工程 / ブランチは /speckit.specify が作成 / push は別工程の人間）\n")
    for r in rows:
        steps = build_steps(r)
        print(f"{'='*70}\n[{r['order']:02d}] #{r['num']}  (ケース{r['case']} / {r['severity']})\n"
              f"  {r['title']}\n  工程: {STEPS[r['path']]}\n{'-'*70}")
        for s in steps:
            tag = {"gate": "［手動ゲート］", "gate-prompt": "［手動ゲート］",
                   "shell": "［シェル］", "prompt": ""}[s["kind"]]
            print(f"\n--- {s['no']}. {s['title']} {tag}".rstrip())
            if s["hint"]:
                print(f"# {s['hint']}")
            if s["text"]:
                print(s["text"])
        if out_dir:
            fn = os.path.join(out_dir, f"{r['order']:02d}-issue-{r['num']}.md")
            with open(fn, "w", encoding="utf-8") as f:
                f.write(f"# [{r['order']}] #{r['num']} {r['title']}\n\n")
                f.write(f"- ケース: {r['case']}\n- 深刻度: {r['severity']}\n- URL: {r['url']}\n")
                f.write(f"- 工程: {STEPS[r['path']]}\n\n")
                for s in steps:
                    f.write(f"## {s['no']}. {s['title']}\n")
                    if s["hint"]:
                        f.write(f"> {s['hint']}\n\n")
                    if s["text"]:
                        f.write(f"```\n{s['text']}\n```\n\n")
    if out_dir:
        print(f"\n[OK] 各Issueのプロンプトを {out_dir}/ に出力しました。", file=sys.stderr)


# ---------- 自動実行（Claude Code ヘッドレス経由で spec-kit を回す） ----------
# specify の後に実行する /speckit ステップ（clarify は手動ゲートで人間が対応）
REST_STEPS = {"軽量": ["plan", "tasks", "implement"],
              "フル": ["plan", "tasks", "analyze", "implement"]}

# 全プロンプトに付与するガード（claude には commit/push をさせない。コミットは正常完了時にツールが実施）
GUARD = ("【厳守】git の commit / push は実行しないこと（コミットは正常完了時にツールが行う）。"
         "ブランチは /speckit.specify が作成する。ファイル編集と spec-kit 工程のみ行う。"
         "PR／マージは人間が別工程で実施する。")


# 自動承認してよい安全なツール（編集・ビルド・読取/ブランチ系）。commit/push は含めない
ALLOW_TOOLS = [
    "Edit", "Write", "MultiEdit",
    "Bash(sushi build)", "Bash(sushi:*)",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git switch:*)",
    "Bash(git branch:*)", "Bash(git checkout:*)", "Bash(git rev-parse:*)",
    # Serena（MCP）のコード探索・編集系のみ自動承認。
    # ※ mcp__serena__execute_shell_command は任意シェル実行のため意図的に含めない
    #   （使う場合は対話承認 or --yes）。
    "mcp__serena__get_symbols_overview",
    "mcp__serena__find_symbol",
    "mcp__serena__find_referencing_symbols",
    "mcp__serena__search_for_pattern",
    "mcp__serena__read_file",
    "mcp__serena__list_dir",
    "mcp__serena__find_file",
    "mcp__serena__replace_symbol_body",
    "mcp__serena__insert_after_symbol",
    "mcp__serena__insert_before_symbol",
    "mcp__serena__replace_regex",
    "mcp__serena__write_memory",
    "mcp__serena__read_memory",
    "mcp__serena__list_memories",
]


def _invoke(claude_bin, perm, label, prompt, execute, allow_tools=None, skip=False):
    cmd = [claude_bin, "-p", prompt]
    if skip:
        cmd += ["--dangerously-skip-permissions"]   # 全プロンプトを自動承認（--yes）
    else:
        cmd += ["--permission-mode", perm]          # 編集を自動承認
        if allow_tools:
            cmd += ["--allowedTools", *allow_tools]  # 安全なものだけ自動承認
    if not execute:
        shown = " ".join(shlex.quote(c) for c in cmd)
        print("DRY-RUN >", (shown[:150] + " …") if len(shown) > 150 else shown)
        return True
    print(f"  ▶ {label} …")
    if subprocess.run(cmd).returncode != 0:
        print(f"  ✖ {label} が異常終了。このIssueを中断します。", file=sys.stderr)
        return False
    return True


def manual_gate(r, execute):
    """フル（複雑）Issueで人間の手動入力を求める。Enterで続行 / s でスキップ。"""
    if not execute:
        print("DRY-RUN > ［手動ゲート］複雑Issueのため一旦停止。Claude Codeで /speckit.clarify を手動実行→回答→レビュー後に続行。")
        return "go"
    print("\n  ── 複雑なIssueです（フルパス）──")
    print("  Claude Code で /speckit.clarify を手動実行し、AIの質問にPMが回答 → 内容をレビューしてください。")
    ans = input("  続行する=Enter / このIssueをスキップ=s / 全体を中止=q : ").strip().lower()
    return {"s": "skip", "q": "quit"}.get(ans, "go")


def decide_gate(r, execute):
    """ケースD（記述の矛盾）で、specifyの前に『正』を1つ決める人間ゲート。"""
    if not execute:
        print("DRY-RUN > ［手動ゲート/ケースD］食い違いを確認し『正』を1つ決める（必要ならPM確認）→ specifyへ。")
        return "go"
    print("\n  ── ケースD（記述の矛盾・食い違い）──")
    print("  食い違う箇所を確認し、どれを『正』にするかを先に1つ決めてください（必要ならPM確認）。")
    ans = input("  正を決めた=Enter / このIssueをスキップ=s / 全体を中止=q : ").strip().lower()
    return {"s": "skip", "q": "quit"}.get(ans, "go")


def current_branch():
    out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    return out or "(unknown)"


def git_commit(r, execute):
    """正常完了したIssueの変更を、specifyが作った作業ブランチへコミット（push はしない）。"""
    msg = f"#{r['num']} {r['title']} (spec-kit)"
    if not execute:
        print(f"DRY-RUN > git add -A && git commit -m {shlex.quote(msg)}")
        return None
    subprocess.run(["git", "add", "-A"])
    if subprocess.run(["git", "commit", "-m", msg]).returncode != 0:
        print("  ! コミット対象の変更がない、またはコミット失敗。", file=sys.stderr)
        return None
    return current_branch()


def git_clean(execute):
    """失敗/スキップIssueの未完変更を破棄してツリーをクリーンにする（次Issueを汚さない）。"""
    if not execute:
        print("DRY-RUN > git reset --hard && git clean -fd （未完変更を破棄）")
        return
    subprocess.run(["git", "reset", "--hard"])
    subprocess.run(["git", "clean", "-fd"])


def run_one(r, claude_bin, perm, do_build, execute, auto_complex, do_commit, allow_tools, skip):
    """1 Issueを Claude Code ヘッドレスで spec-kit 実行。
    ブランチは /speckit.specify が作成。正常完了時のみツールがコミット（push はしない）。"""
    print(f"\n{'='*70}\n[{r['order']:02d}] #{r['num']} (ケース{r['case']}/{r['severity']}/{r['path']}) {r['title']}\n{'-'*70}")
    def fail(step): return {"status": "fail", "step": step, "branch": None}
    # 0) ケースD は specify の前に『正』を1つ決める人間ゲート（--auto-complex で無効化）
    if r["case"] == "D" and not auto_complex:
        g = decide_gate(r, execute)
        if g == "skip":
            print("  → スキップ"); return {"status": "skip", "step": "decide", "branch": None}
        if g == "quit":
            return {"status": "quit"}
    # 1) specify（ここで spec-kit がブランチを作成）
    if not _invoke(claude_bin, perm, "/speckit.specify", specify_prompt(r) + "\n" + GUARD,
                   execute, allow_tools, skip):
        return fail("specify")
    # 2) フル（ケースB/C）は clarify 手動ゲート（--auto-complex で無効化）
    if r["path"] == "フル" and not auto_complex:
        g = manual_gate(r, execute)
        if g == "skip":
            print("  → スキップ"); return {"status": "skip", "step": "clarify", "branch": None}
        if g == "quit":
            return {"status": "quit"}
    # 3) 残り工程
    for st in REST_STEPS[r["path"]]:
        if not _invoke(claude_bin, perm, f"/speckit.{st}", f"/speckit.{st}\n\n{GUARD}",
                       execute, allow_tools, skip):
            return fail(st)
    # 4) ビルド検証
    if do_build:
        if not execute:
            print("DRY-RUN > sushi build （FSH検証）")
        else:
            print("  ▶ sushi build …")
            if subprocess.run(["sushi", "build"]).returncode != 0:
                print("  ✖ sushi build 失敗。", file=sys.stderr)
                return fail("sushi build")
    # 5) checklist（品質チェックリスト生成）
    if not _invoke(claude_bin, perm, "/speckit.checklist", "/speckit.checklist\n\n" + GUARD,
                   execute, allow_tools, skip):
        return fail("checklist")
    # 6) 正常完了 → 作業ブランチへコミット（push はしない）
    branch = current_branch() if execute else "(specifyが作成)"
    if do_commit:
        b = git_commit(r, execute)
        if b:
            branch = b
    return {"status": "ok", "step": None, "branch": branch}


def write_report(results, mode):
    """成功/スキップ/失敗を Markdown レポートに書き出す（後から追跡できるように）。"""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"issue_improver_report_{ts}.md")
    label = {"ok": "✅ 成功(コミット済)", "skip": "⏭ スキップ", "fail": "❌ 失敗(スキップ)", "quit": "⛔ 中止"}
    lines = [f"# issue_improver 実行レポート（{mode}） {ts}\n",
             "| Issue | ケース | 結果 | 失敗/停止工程 | ブランチ |",
             "|---|---|---|---|---|"]
    for r, res in results:
        st = label.get(res["status"], res["status"])
        lines.append(f"| #{r['num']} {r['title'][:24]} | {r['case']} | {st} "
                     f"| {res.get('step') or '-'} | {res.get('branch') or '-'} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def run_speckit(rows, a):
    targets = [r for r in rows if (a.only is None or r["num"] == a.only)]
    targets = filter_case(targets, a.case)
    if a.limit:
        targets = targets[:a.limit]
    if not targets:
        sys.exit("対象Issueなし（--only / --case の条件に一致するIssueがありません）")
    # ケース種別でまとめる（A→B→C→D）。同ケース内は着手順。
    targets.sort(key=lambda r: (CASE_ORDER.index(r["case"]), r["order"]))
    mode = "実行(EXECUTE)" if a.execute else "ドライラン(表示のみ)"
    gate = "全自動（D/フルも停止しない）" if a.auto_complex else "ケースD=正の決定／フル(B/C)=clarify で手動ゲート停止"
    perm_desc = "全プロンプト自動承認（--yes）" if a.yes else f"編集は自動承認＋安全な許可リスト（permission={a.permission_mode}）"
    commit_desc = "コミットしない（--no-commit）" if a.no_commit else "正常完了時はツールが自動コミット"
    print(f"\n[自動実行モード] {mode} / 対象 {len(targets)}件 / claude='{a.claude_bin}'", file=sys.stderr)
    print(f"[権限] {perm_desc}。{commit_desc}。push/PR/マージは別工程の人間。", file=sys.stderr)
    print(f"[方針] ブランチは /speckit.specify が作成。ケース種別ごと(A→B→C→D)にまとめて実行。{gate}。エラーは停止せずスキップ。", file=sys.stderr)
    if a.execute:
        print("[注意] Claude Code を実際に起動し、ファイルを編集します（プラン利用量を消費）。", file=sys.stderr)
    allow = None if a.yes else ALLOW_TOOLS
    results = []
    cur_case = None
    for r in targets:
        if r["case"] != cur_case:
            cur_case = r["case"]
            cnt = sum(1 for x in targets if x["case"] == cur_case)
            print(f"\n{'#'*70}\n# ケース{cur_case}（{CASE_INFO[cur_case][1]}） {cnt}件 — まとめて実行\n{'#'*70}", file=sys.stderr)
        res = run_one(r, a.claude_bin, a.permission_mode, not a.no_build, a.execute,
                      a.auto_complex, not a.no_commit, allow, a.yes)
        results.append((r, res))
        if res["status"] == "quit":
            print("[中止] ユーザー操作により全体を中止しました。", file=sys.stderr); break
        if res["status"] in ("fail", "skip"):
            git_clean(a.execute)   # 未完変更を破棄して次Issueを汚さない
            if res["status"] == "fail":
                print(f"  → #{r['num']} は工程『{res.get('step')}』で失敗。スキップして次へ。", file=sys.stderr)
    ok = sum(1 for _, x in results if x["status"] == "ok")
    skip = sum(1 for _, x in results if x["status"] == "skip")
    ng = sum(1 for _, x in results if x["status"] == "fail")
    print(f"\n[完了] 成功 {ok} / スキップ {skip} / 失敗 {ng}（{mode}）", file=sys.stderr)
    print(f"[レポート] {write_report(results, mode)}", file=sys.stderr)
    if not a.execute:
        print("実際に実行するには --execute を付けてください（まず --only <番号> で1件試すのを推奨）。", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="JP Core SWG別 Issue処理支援ツール（一覧+処理プロンプト / 自動実行）")
    ap.add_argument("--repo", default=None, help="対象リモートリポジトリ（owner/name または GitHub URL）")
    ap.add_argument("--label", default=None, help="対象SWGのラベル名")
    ap.add_argument("--milestone", default=None)
    ap.add_argument("--config", default=None, help=f"設定ファイルパス（既定: スクリプトと同じ場所の {CONFIG_NAME}）")
    ap.add_argument("--no-detect", action="store_true", help="ローカルgit originからのrepo自動検出を無効化")
    ap.add_argument("--no-location-check", action="store_true", help="実行位置(ローカルrepo)と対象repoの一致チェックを無効化")
    ap.add_argument("--state", default="open", choices=["open", "closed", "all"])
    ap.add_argument("--order", default="severity", choices=["severity", "file", "number"])
    ap.add_argument("--format", default="md", choices=["md", "csv", "json"], help="一覧の出力形式")
    ap.add_argument("--out", default=None, help="処理プロンプトをファイル出力するディレクトリ")
    ap.add_argument("--list-only", action="store_true", help="一覧のみ出力")
    ap.add_argument("--prompts-only", action="store_true", help="処理プロンプトのみ出力")
    # --- 自動実行（Claude Code 経由で spec-kit を回す） ---
    ap.add_argument("--run", action="store_true", help="spec-kitを自動実行（Claude Codeヘッドレス）。既定はドライラン")
    ap.add_argument("--execute", action="store_true", help="--run時に実際に実行する（未指定はドライラン）")
    ap.add_argument("--only", type=int, default=None, help="指定Issue番号のみ自動実行（まず1件試す用）")
    ap.add_argument("--limit", type=int, default=None, help="自動実行する件数の上限")
    ap.add_argument("--auto-complex", action="store_true", help="手動ゲート(ケースD=正の決定／フル=clarify)を無効化し全自動にする")
    ap.add_argument("--case", choices=["A", "B", "C", "D"], default=None, help="指定ケースのみ実行（A=軽量/B=構造/C=制度/D=矛盾）")
    ap.add_argument("--yes", action="store_true", help="権限プロンプトを全て自動承認(--dangerously-skip-permissions)。既定は編集+安全な許可リストのみ")
    ap.add_argument("--no-commit", action="store_true", help="正常完了してもコミットしない（既定はツールが自動コミット）")
    ap.add_argument("--no-build", action="store_true", help="implement後の sushi build を行わない")
    ap.add_argument("--claude-bin", default="claude", help="claude 実行ファイル（既定: claude）")
    ap.add_argument("--permission-mode", default="acceptEdits", help="Claude Codeの権限モード（既定: acceptEdits）")
    a = ap.parse_args()

    # ---- 設定の解決（優先順: CLI > 設定ファイル > git origin自動検出 > 組込み既定）----
    cfg_path = a.config or os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_NAME)
    cfg = load_config(cfg_path)

    if a.repo and not parse_repo(a.repo):
        sys.exit(f"--repo の形式が不正です: {a.repo}（owner/name か GitHub URL を指定）")
    repo = parse_repo(a.repo) or parse_repo(cfg.get("repo")) \
        or (None if a.no_detect else detect_origin_repo())
    label = a.label or cfg.get("label")
    milestone = a.milestone or cfg.get("milestone")   # 任意（未指定なら全マイルストーン）

    missing = []
    if not repo:
        missing.append("repo（--repo / 設定ファイル / ローカルgit origin のいずれか）")
    if not label:
        missing.append("label（--label / 設定ファイル）")
    if missing:
        sys.exit("必要な設定が不足しています:\n  - " + "\n  - ".join(missing)
                 + f"\n設定ファイル: {cfg_path}（例: {{\"repo\": \"owner/name\", \"label\": \"○○WG\", \"milestone\": \"1.3-release\"}}）")
    print(f"[設定] repo={repo} / label={label} / milestone={milestone or '(全マイルストーン)'}", file=sys.stderr)
    check_location(repo, a.no_location_check)

    try:
        items = fetch_issues(repo, label, milestone, a.state)
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub APIエラー: {e.code} {e.reason}. GITHUB_TOKEN設定でレート制限緩和可。")
    except urllib.error.URLError as e:
        sys.exit(f"ネットワークエラー: {e.reason}")
    if not items:
        sys.exit(f"該当Issueなし (repo='{repo}', label='{label}', milestone='{milestone or 'all'}', state='{a.state}')")

    # --case があれば一覧・プロンプト・自動実行すべてを指定ケースに絞る
    rows = order_rows(filter_case(enrich(items), a.case), a.order)

    if a.run:
        out_md(rows, label)
        run_speckit(rows, a)
        return

    if not a.prompts_only:
        {"md": lambda: out_md(rows, label), "csv": lambda: out_csv(rows),
         "json": lambda: out_json(rows)}[a.format]()
    if not a.list_only:
        emit_prompts(rows, a.out)


if __name__ == "__main__":
    main()
