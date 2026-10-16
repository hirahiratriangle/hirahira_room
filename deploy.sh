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
# マイグレーションは別に要る（deploy.txt の「マイグレーション」）。

set -eu
cd "$(dirname "$0")"

if [ -n "$(git status --porcelain)" ]; then
    echo "未コミットの変更があります。コミットしてからデプロイしてください（zip にはコミット済みの内容しか入りません）。" >&2
    git status --short >&2
    exit 1
fi

ZIP="$(mktemp -d)/hirahira_room.zip"
trap 'rm -rf "$(dirname "$ZIP")"' EXIT

git archive --format=zip -o "$ZIP" HEAD

# 入ってはいけないものが無いか、送る前に確かめる
BAD="$(unzip -Z1 "$ZIP" | grep -E '(^|/)(db\.sqlite3|\.env[^/]*|\.git/|\.claude/|venv/)' || true)"
if [ -n "$BAD" ]; then
    echo "zip に入れてはいけないファイルがあります。デプロイを止めます。" >&2
    echo "$BAD" >&2
    exit 1
fi

echo "デプロイするコミット: $(git log --oneline -1)"
echo "zip のファイル数: $(unzip -Z1 "$ZIP" | wc -l | tr -d ' ')"
az webapp deploy --name hirahira-room --resource-group hirahira --src-path "$ZIP" --type zip
