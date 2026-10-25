#!/bin/sh
# 本番（hirahira-room）へのデプロイ。コミット済みのファイルだけを zip にして送る。
#
#   ./deploy.sh
#
# az webapp up はフォルダをまるごと zip にする。除くのは名前に env を含むフォルダと
# 「.env」という名前のファイルだけで、.gitignore は読まない。そのため手元の
# db.sqlite3（開発用 DB。転記した問題が入ることがある）、.env.local（Gmail の
# アプリパスワード）、.git まで本番に上がっていた。
#
# ここでは git archive で HEAD の中身だけを zip にするので、.gitignore にあるものも、
# まだコミットしていないものも入らない。本番で要るものは zip に頼っていない。
#   ・media と iris_classifier/models は /home/site/wwwroot の下（永続領域）にあり、
#     デプロイで消えない
#   ・staticfiles はビルド（Oryx）が collectstatic で作り直す
# ビルドは、アプリ設定 SCM_DO_BUILD_DURING_DEPLOYMENT=True により az webapp up と同じく走る。
#
# デプロイが終わっても、動いているコンテナが古いまま入れ替わらないことがある（2026-10-25・26 に2回。
# az webapp restart で入れ替わった）。そこで zip に版の印 static/revision.txt（コミットの短いハッシュ）を
# 入れ、/static/revision.txt が今回のコミットを返すまで確かめる。返さなければ再起動する。
# WhiteNoise は起動したときの静的ファイルを配るので、この印は「いま動いているコンテナの版」になる。
#
# マイグレーションは別に要る（deploy.txt の「マイグレーション」）。

set -eu
cd "$(dirname "$0")"

if [ -n "$(git status --porcelain)" ]; then
    echo "未コミットの変更があります。コミットしてからデプロイしてください（zip にはコミット済みの内容しか入りません）。" >&2
    git status --short >&2
    exit 1
fi

ZIP="$(mktemp -d)/hirahira_room.zip"
trap 'rm -rf "$(dirname "$ZIP")"' EXIT   # az を裏で動かすところで、止める処理も足して張り直す

git archive --format=zip -o "$ZIP" HEAD

# 版の印を zip に足す（リポジトリには置かない。collectstatic で staticfiles に入り、WhiteNoise が配る）
REV="$(git rev-parse --short HEAD)"
mkdir -p "$(dirname "$ZIP")/rev/static"
echo "$REV" > "$(dirname "$ZIP")/rev/static/revision.txt"
(cd "$(dirname "$ZIP")/rev" && zip -q "$ZIP" static/revision.txt)

# 入ってはいけないものが無いか、送る前に確かめる
BAD="$(unzip -Z1 "$ZIP" | grep -E '(^|/)(db\.sqlite3|\.env[^/]*|\.git/|\.claude/|venv/)' || true)"
if [ -n "$BAD" ]; then
    echo "zip に入れてはいけないファイルがあります。デプロイを止めます。" >&2
    echo "$BAD" >&2
    exit 1
fi

echo "デプロイするコミット: $(git log --oneline -1)"
echo "zip のファイル数: $(unzip -Z1 "$ZIP" | wc -l | tr -d ' ')"
# az webapp deploy は、Azure 側でデプロイが終わっても戻らないことがある。
# 同期でも、--track-status false でも、--async true でも同じだった（2026-10-16 に3回。
# どれも Azure 側は約3分で完了していた）。そこで az は裏で動かし、完了は Azure の
# デプロイ記録で確かめ、終わったら固まった az を止める。
APP="--name hirahira-room --resource-group hirahira"
LOG="$(dirname "$ZIP")/az.log"
SENT_AT="$(date -u +%Y-%m-%dT%H:%M:%S)"
az webapp deploy $APP --src-path "$ZIP" --type zip --async true --track-status false >"$LOG" 2>&1 &
AZ_PID=$!
# az はシェルの包みで、中で python が動く。子から止めてから親を止める
stop_az() { pkill -P "$AZ_PID" 2>/dev/null || true; kill "$AZ_PID" 2>/dev/null || true; }
trap 'stop_az; rm -rf "$(dirname "$ZIP")"' EXIT

# デプロイの完了を待つ（最大15分）。送った時刻以後に受け付けた記録が complete になるまで。
# status は Kudu の値で、4 が成功、3 が失敗。
echo "ビルドとデプロイの完了を待っています…"
i=0
while :; do
    # 1行に「受け付け時刻 完了したか 状態」を並べて読む（-o tsv は1項目1行で出るので使わない）
    ROW="$(az webapp log deployment list $APP --query "[0]" -o json 2>/dev/null \
        | python3 -c 'import json,sys; d=json.load(sys.stdin) or {}; print(str(d.get("received_time",""))[:19], str(d.get("complete","")).lower(), d.get("status",""))' \
        2>/dev/null || true)"
    RECEIVED="$(echo "$ROW" | cut -d' ' -f1)"
    COMPLETE="$(echo "$ROW" | cut -d' ' -f2)"
    STATUS="$(echo "$ROW" | cut -d' ' -f3)"
    # 送った時刻以後に受け付けた記録か（ISO 形式なので文字列の並びで前後が決まる）
    LATER="$(printf '%s\n%s\n' "$SENT_AT" "$RECEIVED" | sort | tail -1)"
    if [ -n "$RECEIVED" ] && [ "$LATER" = "$RECEIVED" ] && [ "$COMPLETE" = "true" ]; then
        stop_az
        if [ "$STATUS" = "4" ]; then
            echo "デプロイが完了しました（受け付け ${RECEIVED}Z）。"
            break
        fi
        echo "デプロイが失敗しました（status ${STATUS}）。az webapp log deployment show $APP で確かめてください。" >&2
        exit 1
    fi
    # az が自分で終わったら、終了コードを一度だけ受け取る。失敗して終わった
    # （ログインが切れている、など）なら、記録を待っても来ないので止める
    if [ -z "${AZ_RC:-}" ] && ! kill -0 "$AZ_PID" 2>/dev/null; then
        if wait "$AZ_PID"; then AZ_RC=0; else AZ_RC=$?; fi
        if [ "$AZ_RC" != 0 ]; then
            echo "az webapp deploy が失敗しました（終了コード ${AZ_RC}）。" >&2
            cat "$LOG" >&2
            exit 1
        fi
    fi
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "15分待ってもデプロイが終わりません。az webapp log deployment list $APP で確かめてください。" >&2
        exit 1
    fi
    sleep 15
done

# 今回のコミットで動いているか。/static/revision.txt が今回の版を返すまで待つ（最大10分）。
# 3分たっても古いままなら、コンテナが入れ替わっていないとみて一度だけ再起動する
running_rev() { curl -s -m 30 "https://hirahira-room.net/static/revision.txt?t=$(date +%s)" | tr -d '[:space:]'; }
i=0
RESTARTED=""
until [ "$(running_rev)" = "$REV" ]; do
    i=$((i + 1))
    if [ -z "$RESTARTED" ] && [ "$i" -ge 12 ]; then
        echo "まだ古い版で動いています（$(running_rev || true)）。アプリを再起動します…"
        az webapp restart $APP
        RESTARTED=1
        i=0
    fi
    if [ "$i" -ge 40 ]; then
        echo "10分待っても今回の版（${REV}）になりません。az webapp log tail $APP で確かめてください。" >&2
        exit 1
    fi
    sleep 15
done
echo "今回の版（${REV}）で動いています。"

# 起動を待つ。ログイン画面が 200 を返せば動いている（最大10分）
URL="https://hirahira-room.net/accounts/login/"
i=0
until [ "$(curl -s -o /dev/null -m 30 -w '%{http_code}' "$URL")" = "200" ]; do
    i=$((i + 1))
    if [ "$i" -ge 40 ]; then
        echo "10分待ってもサイトが応答しません。az webapp log tail $APP で確かめてください。" >&2
        exit 1
    fi
    sleep 15
done
echo "デプロイしました。サイトは応答しています。"
