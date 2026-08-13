#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
issue_improver.py — JP Core Issue Improver（SWG別 Issue改善支援ツール / 1コマンド統合版）

1回の実行で次を行う:
  (1) 所属SWG(ラベル)のオープンIssueを取得し、深刻度・対象ファイル・
      ケース(A/B/C/D)・推奨着手順を付けて一覧表示する
  (2) 着手順に各Issueのケースを判定し、
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

# ケース分類（スライドの ケースA/B/C/D に対応）。判定優先度 D > C > B > A(既定)
CASE_KW = {
    "D": ["矛盾", "食い違い", "食違い", "不一致", "齟齬", "相違", "conflict", "inconsistent"],
    "C": ["マイナ", "資格確認", "保険者番号", "保険証", "後期高齢者", "共済", "船員", "点数表", "制度"],
    "B": ["slic", "cardinality", "discriminator", "binding", "invariant", "partOf",
          "associatedEncounter", "ConceptMap", "CodeSystem", "Extension", "構造", "体系"],
}
# ケースの説明（A/B/C/D）
CASE_DESC = {
    "A": "タイポ・文言・コメント等の単純修正（clarify/analyze 省略）",
    "B": "構造・cardinality など仕様変更（破壊的注意）",
    "C": "保険制度などの設計（clarify で PM が回答）",
    "D": "記述の矛盾・食い違いの解消（先に『正』を1つ決める）",
}
CASE_ORDER = ["A", "B", "C", "D"]

# clarify/analyze（と手動ゲート）を伴うケース。A/D はそれらを省く。
FULL_CASES = ("B", "C")


def is_full(case):
    """ケースが clarify/analyze と手動ゲートを伴うか（B/C）。"""
    return case in FULL_CASES


def rest_steps(case):
    """specify の後に回す spec-kit 工程（ケースで決まる）。"""
    return ["plan", "tasks", "analyze", "implement"] if is_full(case) else ["plan", "tasks", "implement"]


def steps_overview(case):
    """工程概要（表示用）。ケースから生成する。"""
    if is_full(case):
        return "specify → clarify → plan → tasks → analyze → implement → sushi build → checklist →〔人間レビュー/コミット〕"
    return "specify → plan → tasks → implement → sushi build → checklist →〔人間レビュー/コミット〕 (clarify/analyze 省略)"


PATH_PATTERN = re.compile(r"input/[\w/\.\-]+\.(?:fsh|md)")
PROFILE_PATTERN = re.compile(r"JP_[A-Za-z]+")


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
                     "case": case,
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
    w.writerow(["order", "num", "severity", "case", "files", "title", "url"])
    for r in rows:
        w.writerow([r["order"], r["num"], r["severity"], r["case"],
                    " ; ".join(r["files"]), r["title"], r["url"]])


def out_json(rows):
    slim = [{k: r[k] for k in ("order", "num", "severity", "case", "files", "title", "url")} for r in rows]
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
    # ケースD で人間が決めた『正』があれば specify に注入し、それに従わせる
    decision = r.get("decision")
    extra = (f"\n【確定した正】{decision}\nこの決定に従って記述を統一し、矛盾を解消すること。"
             if decision else "")
    return f"""/speckit.specify

Issue #{r['num']} の指摘を解消する最小変更の仕様を策定する。着手前に Issue 本文を必ず参照すること（`gh issue view {r['num']}`、または下記URL）。
{r['url']}{extra}
"""


# 工程名 → 表示ラベル
STEP_LABEL = {"plan": "設計", "tasks": "タスク化", "analyze": "整合性確認", "implement": "実装"}


def build_steps(r):
    """specify 〜 checklist まで、Claude Code に手で貼る全プロンプトを順に組み立てて返す。

    手動実行用のため GUARD は付けない（commit/push 等は人が管理）。
    GUARD は自動実行(--run / run_one)でのみ各プロンプトに付与される。

    ケース(A/B/C/D)から決まる工程に従う（run_one の自動実行と同じ並び）:
      - A/D: specify → plan → tasks → implement → sushi build → checklist
      - B/C: specify → clarify(手動) → plan → tasks → analyze → implement → sushi build → checklist
      - ケースD は specify の前に『正』を1つ決める手動ゲートを挟む
    各要素は dict: {no, kind('prompt'|'gate'|'gate-prompt'|'shell'), title, text, hint}
    """
    full = is_full(r["case"])
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
    for st in rest_steps(r["case"]):
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
              f"  {r['title']}\n  工程: {steps_overview(r['case'])}\n{'-'*70}")
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
                f.write(f"- 工程: {steps_overview(r['case'])}\n\n")
                for s in steps:
                    f.write(f"## {s['no']}. {s['title']}\n")
                    if s["hint"]:
                        f.write(f"> {s['hint']}\n\n")
                    if s["text"]:
                        f.write(f"```\n{s['text']}\n```\n\n")
    if out_dir:
        print(f"\n[OK] 各Issueのプロンプトを {out_dir}/ に出力しました。", file=sys.stderr)


# ---------- 自動実行（Claude Code ヘッドレス経由で spec-kit を回す） ----------
# specify の後に実行する /speckit ステップはケースで決まる（rest_steps()）。
# clarify は手動ゲートで人間が対応。

# 全プロンプトに付与するガード（claude には commit/push をさせない。コミットは正常完了時にツールが実施）
GUARD = ("【厳守】git の commit / push は実行しないこと（コミットは正常完了時にツールが行う）。"
         "ブランチは /speckit.specify が作成する。ファイル編集と spec-kit 工程のみ行う。"
         "PR／マージは人間が別工程で実施する。")


# 自動承認するツール（方針: bash・Serena は広く自動YES。ブランチ作成/commit も自動）。
# push だけは人間が行うため、下の DISALLOW_TOOLS でハード拒否する。
ALLOW_TOOLS = [
    "Edit", "Write", "MultiEdit",
    "Bash",          # 任意の bash を自動承認（検証・ビルド・git等。push は下で拒否）
    "mcp__serena",   # Serena の全ツールを自動承認（探索・編集・シェル含む）
]

# 自動承認しないツール（許可より優先）。push は人間の別工程なのでハード拒否する。
# ※ Serena の execute_shell_command 経由の push までは塞げないため、最終防壁として
#   GitHub 側で develop/main のブランチ保護を併用すること。
DISALLOW_TOOLS = [
    "Bash(git push:*)", "Bash(git push)",
]


# ---- 計測（各工程の所要時間を記録して原因切り分けに使う）----
import time as _time
TIMING_LOG = []            # [{"num":int|None, "step":str, "sec":float, "ok":bool}]
_TIMING_CTX = {"num": None}  # 現在処理中のIssue番号（run_one が設定）
_CAPTURED = {"text": ""}     # capture=True で実行した工程の出力（analyze ゲート再表示用）


# ---- 画面ログ（--log）。ツールの print 出力をファイルへも複製する ----
class _Tee:
    """sys.stdout/stderr をラップし、元ストリームとログファイルの両方へ書く。"""
    def __init__(self, stream, fh):
        self._stream = stream
        self._fh = fh

    def write(self, s):
        self._stream.write(s)
        try:
            self._fh.write(s)
            self._fh.flush()
        except Exception:
            pass
        return len(s)

    def flush(self):
        self._stream.flush()
        try:
            self._fh.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _start_logging(path):
    """以降の stdout/stderr を path にも追記する（claude本体のライブ出力は対象外）。"""
    fh = open(path, "a", encoding="utf-8")
    fh.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} issue_improver 実行ログ =====\n")
    fh.write("$ " + " ".join(shlex.quote(c) for c in sys.argv) + "\n\n")
    fh.flush()
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)
    print(f"[ログ] 画面出力を {path} に追記します。", file=sys.stderr)


def _gate_record(label, value):
    """ゲートで確定した入力をログに残すためエコーバックする（空なら何もしない）。"""
    if value:
        print(f"  → 記録【{label}】{value}")


def _record(step, sec, ok):
    TIMING_LOG.append({"num": _TIMING_CTX["num"], "step": step, "sec": round(sec, 1), "ok": ok})
    print(f"  ⏱ {step}: {sec:.1f}s", file=sys.stderr)


def _invoke(claude_bin, perm, label, prompt, execute, allow_tools=None, skip=False, capture=False):
    """1 工程を claude -p で実行。capture=True のときは出力を取り込み _CAPTURED に保持し
    （ライブ表示はせず）、ゲートでの再表示に使う。それ以外は従来どおりストリーム表示。"""
    cmd = [claude_bin, "-p", prompt]
    if skip:
        cmd += ["--dangerously-skip-permissions"]   # 全プロンプトを自動承認（--yes）
    else:
        cmd += ["--permission-mode", perm]          # 編集を自動承認
        if allow_tools:
            cmd += ["--allowedTools", *allow_tools]  # bash・Serena 等を自動承認
            cmd += ["--disallowedTools", *DISALLOW_TOOLS]  # push だけは拒否（許可より優先）
    if not execute:
        shown = " ".join(shlex.quote(c) for c in cmd)
        print("DRY-RUN >", (shown[:150] + " …") if len(shown) > 150 else shown)
        return True
    print(f"  ▶ {label} …")
    t0 = _time.monotonic()
    if capture:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        rc = proc.returncode
        _CAPTURED["text"] = proc.stdout or ""
        if rc != 0 and proc.stderr:
            print(proc.stderr, file=sys.stderr, end="")
    else:
        rc = subprocess.run(cmd).returncode
    _record(label, _time.monotonic() - t0, rc == 0)
    if rc != 0:
        print(f"  ✖ {label} が異常終了。このIssueをスキップして次へ進みます。", file=sys.stderr)
        return False
    return True


def _show_issue(r):
    """ゲートで判断材料を出すため、Issue のタイトル・本文・URL を表示する。
    （矛盾の選択肢はツールが自動抽出できないため、本文を見て人間が判断する）"""
    print(f"\n  Issue #{r['num']}: {r.get('title','')}")
    if r.get("files"):
        print(f"  対象ファイル: {', '.join(r['files'])}")
    print(f"  URL: {r.get('url','')}")
    body = (r.get("body") or "").strip()
    if body:
        limit = 2000
        excerpt = body if len(body) <= limit else body[:limit] + "\n  …（以下省略。全文は上記URL参照）"
        print("  ── Issue本文 ──")
        for line in excerpt.splitlines():
            print(f"  | {line}")
    print()


def _latest_specfile(name):
    """直近に更新された specs/*/<name> を返す（無ければ None）。"""
    import glob
    if not os.path.isdir("specs"):
        return None
    files = glob.glob(f"specs/*/{name}")
    if not files:
        return None
    return max(files, key=lambda p: os.path.getmtime(p))


def _show_spec(r):
    """clarify ゲートで specify の成果物 spec.md を表示する（レビュー対象）。"""
    p = _latest_specfile("spec.md")
    if not p:
        print("  （spec.md が見つかりません。specify の出力／直前ログを確認してください）\n")
        return
    try:
        txt = open(p, encoding="utf-8").read().strip()
    except Exception:
        print(f"  （{p} を読めませんでした）\n")
        return
    ncl = txt.count("[NEEDS CLARIFICATION")
    print(f"  ── specify 生成の spec（{p}） ──"
          + (f"  ※[NEEDS CLARIFICATION] {ncl}件" if ncl else ""))
    limit = 3000
    if len(txt) > limit:
        txt = txt[:limit] + "\n  …（省略。全文は上記ファイル）"
    for line in txt.splitlines():
        print(f"  | {line}")
    print()


def manual_gate(r, execute):
    """ケースB/C（複雑）Issueで人間の手動入力を求める。Enterで続行 / s でスキップ。

    続行時は clarify での確定事項をテキスト入力させ、r['clarification'] に保持して
    plan 以降の工程プロンプトに注入する（空Enterなら未指定）。"""
    if not execute:
        print("DRY-RUN > ［手動ゲート/要件確認］確定事項を入力 → plan 以降に注入（clarifyの手動実行は任意）。")
        return "go"
    print("\n  ── 複雑なIssueです（ケースB/C：要件確認）──")
    _show_issue(r)
    _show_spec(r)   # specify の成果物（レビュー対象）
    print("  ↑ Issue と spec を確認し、実装方針として確定すべき事項を下に入力してください（plan 以降に反映されます）。")
    print("  ※ AIの質問を見たい場合のみ、別途 Claude Code で /speckit.clarify を回し、その結論をここに記入（任意・必須ではありません）。")
    ans = input("  続行する=Enter / このIssueをスキップ=s / 全体を中止=q : ").strip().lower()
    if ans == "s":
        return "skip"
    if ans == "q":
        return "quit"
    # 続行 → clarify での確定事項を入力させ、plan 以降に渡す
    notes = input("  clarify での確定事項（plan以降に反映。空Enter=なし）:\n  > ").strip()
    if notes:
        r["clarification"] = notes
        _gate_record("clarify確定事項", notes)
    return "go"


def decide_gate(r, execute):
    """ケースD（記述の矛盾）で、specifyの前に『正』を1つ決める人間ゲート。

    続行時は『正』の内容をテキスト入力させ、r['decision'] に保持して specify に渡す
    （空Enterなら未指定＝AI判断）。"""
    if not execute:
        print("DRY-RUN > ［手動ゲート/ケースD］食い違いを確認し『正』を入力 → specify に注入。")
        return "go"
    print("\n  ── ケースD（記述の矛盾・食い違い）──")
    _show_issue(r)
    print("  ↑ 本文の食い違いを確認し、どれを『正』にするかを決めてください（必要ならPM確認）。")
    ans = input("  続行=Enter / このIssueをスキップ=s / 全体を中止=q : ").strip().lower()
    if ans == "s":
        return "skip"
    if ans == "q":
        return "quit"
    # 続行 → 『正』の内容を入力させ、specify プロンプトへ渡す
    decision = input("  『正』の内容（どちらを正とし、どう統一するか。空Enter=AI判断）:\n  > ").strip()
    if decision:
        r["decision"] = decision
        _gate_record("確定した正", decision)
    return "go"


def _show_analysis(r):
    """analyze の結果を実装前レビュー用に表示する。
    優先: analyze 工程の取り込み出力(r['analysis_output']) → specs/*/analysis*.md → 案内。"""
    import glob
    txt = (r.get("analysis_output") or "").strip()
    src = "analyze 出力"
    if not txt:
        files = (sorted(glob.glob("specs/*/analysis*.md") + glob.glob("specs/*/analysis*.txt"),
                        key=lambda p: os.path.getmtime(p), reverse=True)
                 if os.path.isdir("specs") else [])
        if files:
            src = files[0]
            try:
                txt = open(files[0], encoding="utf-8").read().strip()
            except Exception:
                txt = ""
    if txt:
        print(f"  ── analyze 結果（{src}） ──")
        limit = 3500
        if len(txt) > limit:
            txt = txt[:limit] + "\n  …（省略）"
        for line in txt.splitlines():
            print(f"  | {line}")
    else:
        print("  （analyze の出力は直前のログを確認してください）")
    print()


def analyze_gate(r, execute):
    """ケースB/C で analyze の後、implement の前に置く実装前レビューゲート。

    続行時は『実装前の修正指示』をテキスト入力させ、r['impl_note'] に保持して
    implement プロンプトに注入する（空Enterなら未指定）。"""
    if not execute:
        print("DRY-RUN > ［手動ゲート/analyze後］整合性チェック結果を確認し、実装に進むか判断 → implement に注入。")
        return "go"
    print("\n  ── analyze 結果レビュー（実装前ゲート）──")
    _show_analysis(r)
    print("  ↑ analyze の指摘を確認し、実装に進めてよいか判断してください。")
    ans = input("  実装へ進む=Enter / このIssueをスキップ=s / 全体を中止=q : ").strip().lower()
    if ans == "s":
        return "skip"
    if ans == "q":
        return "quit"
    note = input("  実装前の修正指示（implementに反映。空Enter=なし）:\n  > ").strip()
    if note:
        r["impl_note"] = note
        _gate_record("実装前の修正指示", note)
    return "go"


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
    """失敗/スキップIssueの未完変更を破棄してツリーをクリーンにする（次Issueを汚さない）。

    重要: `git clean -fd` は未追跡ファイル/ディレクトリを削除する。ツール自身が
    対象repo内の未追跡 `tools/` 等に置かれていると、これがツール本体やレポートを
    巻き込んで消してしまう。そこでツールのあるディレクトリを -e で除外し、自滅を防ぐ。
    （gitignore 済みファイルは元々 git clean の対象外。）
    """
    tool_dir = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
    if not execute:
        print(f"DRY-RUN > git reset --hard && git clean -fd -e {tool_dir} （未完変更を破棄。ツール自身のディレクトリは除外）")
        return
    subprocess.run(["git", "reset", "--hard"])
    subprocess.run(["git", "clean", "-fd", "-e", tool_dir])


def run_one(r, claude_bin, perm, do_build, execute, auto_complex, do_commit, allow_tools, skip):
    """1 Issueを Claude Code ヘッドレスで spec-kit 実行。
    ブランチは /speckit.specify が作成。正常完了時のみツールがコミット（push はしない）。"""
    print(f"\n{'='*70}\n[{r['order']:02d}] #{r['num']} (ケース{r['case']}/{r['severity']}) {r['title']}\n{'-'*70}")
    _TIMING_CTX["num"] = r["num"]
    _issue_t0 = _time.monotonic()
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
    # 2) ケースB/C は clarify 手動ゲート（--auto-complex で無効化）
    if is_full(r["case"]) and not auto_complex:
        g = manual_gate(r, execute)
        if g == "skip":
            print("  → スキップ"); return {"status": "skip", "step": "clarify", "branch": None}
        if g == "quit":
            return {"status": "quit"}
    # clarify ゲートで入力された確定事項を plan 以降の各工程プロンプトに注入する
    clar = r.get("clarification")
    clar_block = (f"\n\n【clarifyでの確定事項】{clar}\nこの内容に従って進めること。" if clar else "")
    # 3) 残り工程
    for st in rest_steps(r["case"]):
        extra = clar_block
        if st == "implement" and r.get("impl_note"):
            extra += f"\n\n【analyze後の修正指示】{r['impl_note']}\nこれを反映して実装すること。"
        # analyze はゲート再表示用に出力を取り込む（ライブ表示せず、ゲートで再掲）
        cap = (st == "analyze" and not auto_complex)
        if not _invoke(claude_bin, perm, f"/speckit.{st}", f"/speckit.{st}\n\n{GUARD}{extra}",
                       execute, allow_tools, skip, capture=cap):
            return fail(st)
        # analyze の後に実装前レビューゲート（ケースB/C・--auto-complex なし）
        if st == "analyze" and not auto_complex:
            r["analysis_output"] = _CAPTURED["text"]
            g = analyze_gate(r, execute)
            if g == "skip":
                print("  → スキップ"); return {"status": "skip", "step": "analyze-review", "branch": None}
            if g == "quit":
                return {"status": "quit"}
    # 4) ビルド検証
    if do_build:
        if not execute:
            print("DRY-RUN > sushi build （FSH検証）")
        else:
            print("  ▶ sushi build …")
            t0 = _time.monotonic()
            rc = subprocess.run(["sushi", "build"]).returncode
            _record("sushi build", _time.monotonic() - t0, rc == 0)
            if rc != 0:
                print("  ✖ sushi build 失敗。", file=sys.stderr)
                return fail("sushi build")
    # 5) checklist（品質チェックリスト生成）
    if not _invoke(claude_bin, perm, "/speckit.checklist", "/speckit.checklist\n\n" + GUARD + clar_block,
                   execute, allow_tools, skip):
        return fail("checklist")
    if execute:
        _record(f"#{r['num']} 合計", _time.monotonic() - _issue_t0, True)
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
    out_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)  # ディレクトリが消えていても落ちないように
    path = os.path.join(out_dir, f"issue_improver_report_{ts}.md")
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


def write_timing(mode):
    """各工程の所要時間を CSV に書き出し、工程種別ごとの集計を表示する（原因切り分け用）。"""
    if not TIMING_LOG:
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"issue_improver_timing_{ts}.csv")
    import csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["issue", "step", "seconds", "ok"])
        for t in TIMING_LOG:
            w.writerow([t["num"] if t["num"] is not None else "-", t["step"], t["sec"], t["ok"]])
    # 工程種別ごとの集計（合計行は除く）
    agg = {}
    for t in TIMING_LOG:
        if str(t["step"]).endswith("合計"):
            continue
        a = agg.setdefault(t["step"], [0, 0.0])
        a[0] += 1
        a[1] += t["sec"]
    print("\n[計測] 工程別 所要時間（回数 / 合計s / 平均s）", file=sys.stderr)
    for step, (n, tot) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print(f"  {step:28s} {n:3d}回  合計{tot:7.1f}s  平均{tot/n:6.1f}s", file=sys.stderr)
    return path


def probe_startup(claude_bin, skip):
    """Claude Code（+MCP/Serena）の素の起動コストを計測する。

    自明なプロンプトを 1 回 `claude -p` するだけ。生成はほぼ無いので、所要時間は
    おおむね「セッション起動 + MCP サーバ起動（Serena の activate/index 等）」に相当。
    実工程の時間からこれを引くと、起動オーバーヘッドと実作業を切り分けられる。
    """
    cmd = [claude_bin, "-p", "OK とだけ返答してください。"]
    cmd += ["--dangerously-skip-permissions"] if skip else []
    print("[probe] 起動コスト計測のため自明プロンプトを1回実行します …", file=sys.stderr)
    t0 = _time.monotonic()
    subprocess.run(cmd, stdout=subprocess.DEVNULL)
    dt = _time.monotonic() - t0
    print(f"[probe] 起動+MCP(Serena含む)のおおよその素コスト: {dt:.1f}s", file=sys.stderr)
    print("       （各工程の時間からこの値を引いた分が概ね実作業時間）", file=sys.stderr)
    return dt


def run_speckit(rows, a):
    # rows は main で --case 適用済み。ここでは --only / --limit のみ。
    targets = [r for r in rows if (a.only is None or r["num"] == a.only)]
    if a.limit:
        targets = targets[:a.limit]
    if not targets:
        sys.exit("対象Issueなし（--only / --case の条件に一致するIssueがありません）")
    # ケース種別でまとめる（A→B→C→D）。同ケース内は着手順。
    targets.sort(key=lambda r: (CASE_ORDER.index(r["case"]), r["order"]))
    mode = "実行(EXECUTE)" if a.execute else "ドライラン(表示のみ)"
    gate = ("全自動（ケースD/B/Cも停止しない）" if a.auto_complex
            else "ケースD=正の決定（specify前）／ケースB/C=clarify（specify後）＋analyze後の実装前レビュー で手動ゲート停止")
    perm_desc = "全プロンプト自動承認（--yes）" if a.yes else f"編集は自動承認＋安全な許可リスト（permission={a.permission_mode}）"
    commit_desc = "コミットしない（--no-commit）" if a.no_commit else "正常完了時はツールが自動コミット"
    print(f"\n[自動実行モード] {mode} / 対象 {len(targets)}件 / claude='{a.claude_bin}'", file=sys.stderr)
    print(f"[権限] {perm_desc}。{commit_desc}。push/PR/マージは別工程の人間。", file=sys.stderr)
    print(f"[方針] ブランチは /speckit.specify が作成。ケース種別ごと(A→B→C→D)にまとめて実行。{gate}。エラーは停止せずスキップ。", file=sys.stderr)
    if a.execute:
        print("[注意] Claude Code を実際に起動し、ファイルを編集します（プラン利用量を消費）。", file=sys.stderr)
    if a.probe and a.execute:
        probe_startup(a.claude_bin, a.yes)
    allow = None if a.yes else ALLOW_TOOLS
    results = []
    cur_case = None
    for r in targets:
        if r["case"] != cur_case:
            cur_case = r["case"]
            cnt = sum(1 for x in targets if x["case"] == cur_case)
            print(f"\n{'#'*70}\n# ケース{cur_case}（{CASE_DESC[cur_case]}） {cnt}件 — まとめて実行\n{'#'*70}", file=sys.stderr)
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
    tpath = write_timing(mode)
    if tpath:
        print(f"[計測CSV] {tpath}", file=sys.stderr)
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
    ap.add_argument("--auto-complex", action="store_true", help="手動ゲート(ケースD=正の決定／ケースB/C=clarify・実装前)を無効化し全自動にする")
    ap.add_argument("--case", choices=["A", "B", "C", "D"], default=None, help="指定ケースのみ実行（A=タイポ等/B=構造/C=制度/D=矛盾）")
    ap.add_argument("--yes", action="store_true", help="権限プロンプトを全て自動承認(--dangerously-skip-permissions)。既定は編集+安全な許可リストのみ")
    ap.add_argument("--no-commit", action="store_true", help="正常完了してもコミットしない（既定はツールが自動コミット）")
    ap.add_argument("--no-build", action="store_true", help="implement後の sushi build を行わない")
    ap.add_argument("--probe", action="store_true", help="実行前に自明プロンプトを1回流し、Claude Code+MCP(Serena)の素の起動コストを計測する")
    ap.add_argument("--log", default=None, help="画面出力（設定・ゲート表示・入力・analyzeレポート・計測・サマリ）をこのファイルにも追記する")
    ap.add_argument("--claude-bin", default="claude", help="claude 実行ファイル（既定: claude）")
    ap.add_argument("--permission-mode", default="acceptEdits", help="Claude Codeの権限モード（既定: acceptEdits）")
    a = ap.parse_args()

    # 画面ログ: ツールの出力(stdout/stderr)を指定ファイルへも複製する（claude本体のライブ出力は対象外）
    if a.log:
        _start_logging(a.log)

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
