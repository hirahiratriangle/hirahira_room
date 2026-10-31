# plan 015：資産ベースの KPI

spec：[spec.md](spec.md)

## モデル（`0008_knowledge_use`）

| モデル | 変更 |
|---|---|
| `KnowledgeUse`（新規） | 文書と開かれた日。文書ごと1日1行 |

## 処理

- `DocumentDetailView`：別の現場の人が公開文書を開いたら `KnowledgeUse` に残す
- `kpi.asset_dates()`：6つの資産ごとの増えた日
- `kpi.asset_function()`：残高・育っている資産・活用とグラフ。標準化と成長の寄与のあいだに置く
- `seed_knowledge_use()`：古い公開文書ほど開かれている筋書きのデモ

## 画面

| URL | 画面 |
|---|---|
| `/genba/company/kpi/` | ナレッジ資産のカード、指標の定義 |
