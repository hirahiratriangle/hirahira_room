#!/bin/bash
# 基本情報の X 投稿を起こす Logic App（hirahira-fe-x-post）を作る・作り直す。
# 日本時間の7時・12時・19時に /fe/tasks/post-x/ を、時間帯（slot）と予定の日付（date）付きで呼ぶ。
#
# トークンは Azure のアプリ設定 FE_X_TOKEN から読んで渡すので、画面には出ない。
# Logic App の中では「安全なパラメーター」に置き、実行の履歴にも入力を出さない。
# 何度流してもよい（同じ名前の Logic App を上書きする）。
#
# 使い方: リポジトリのフォルダで ./azure/create-fe-x-post-logicapp.sh
set -euo pipefail
cd "$(dirname "$0")"

RG=hirahira
APP=hirahira-room

# Logic Apps を初めて使うサブスクリプションでは、提供元の登録が要る
if [ "$(az provider show -n Microsoft.Logic --query registrationState -o tsv)" != "Registered" ]; then
    echo "Logic Apps（Microsoft.Logic）を登録しています…"
    az provider register -n Microsoft.Logic --wait
fi

TOKEN=$(az webapp config appsettings list --name "$APP" --resource-group "$RG" \
    --query "[?name=='FE_X_TOKEN'].value | [0]" -o tsv)
if [ -z "$TOKEN" ]; then
    echo "アプリ設定に FE_X_TOKEN がありません。" >&2
    exit 1
fi

# トークンはコマンドの引数に出さず、一時ファイル（自分だけが読める）で渡す
PARAMS=$(mktemp)
trap 'rm -f "$PARAMS"' EXIT
chmod 600 "$PARAMS"
printf '{"feXToken": {"value": "%s"}}' "$TOKEN" > "$PARAMS"

az deployment group create --resource-group "$RG" --name fe-x-post-logicapp \
    --template-file fe-x-post-logicapp.json --parameters @"$PARAMS" \
    --query properties.provisioningState -o tsv
echo "Logic App hirahira-fe-x-post を作りました。"
