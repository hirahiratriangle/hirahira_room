# -*- coding: utf-8 -*-
"""JP Core 導入〜運用 解説スライドのメタ情報。

実ファイル(PPTX)は jpcore/materials/ に置く。閲覧は Office Online ビューア、
ダウンロードはログイン保護付きの配信ビュー経由で提供する。
"""

# slug: URL/識別子, pptx: jpcore/materials/ 配下のファイル名
DECKS = [
    {
        "slug": "0-claude",
        "no": "0",
        "title": "Claude の取得",
        "subtitle": "Claude Code を使うための準備（アカウント作成と有料プランへの加入）",
        "pptx": "0_Claudeの取得.pptx",
    },
    {
        "slug": "1-speckit",
        "no": "1",
        "title": "GitHub Spec Kit のインストール",
        "subtitle": "Windows + WSL（Ubuntu）。前提ツール → uv → specify CLI → 初期化",
        "pptx": "1_SpecKitインストール.pptx",
    },
    {
        "slug": "2-vscode",
        "no": "2",
        "title": "VS Code 開発環境（Claude Code 実行環境）の構築",
        "subtitle": "Windows の VS Code から Remote-WSL で WSL に接続し、Claude Code を GUI で使う",
        "pptx": "2_VSCode_ClaudeCode環境.pptx",
    },
    {
        "slug": "3-issue-manual",
        "no": "3",
        "title": "Issue の手動改善 手順書（ケース別）",
        "subtitle": "spec-kit で 1 Issue = 1 サイクル。ケース A/B/C/D に応じて工程を使い分ける",
        "pptx": "3_Issue手動改善_ケース別.pptx",
    },
    {
        "slug": "4-batch-tool",
        "no": "4",
        "title": "一括処理ツール（issue_improver）の使い方",
        "subtitle": "所属SWGのIssue一覧化 ＋ 着手順の処理プロンプト生成 / 自動実行",
        "pptx": "4_一括処理ツール説明.pptx",
    },
]

DECKS_BY_SLUG = {d["slug"]: d for d in DECKS}
