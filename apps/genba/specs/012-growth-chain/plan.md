# plan 012：成長の連鎖

spec：[spec.md](spec.md)

## モデル

`Grade.required_level` と `GradeReview`（本人・元になった成長・今の階級・提案する階級・状態・提案した人・決めた人）を足す。

## 処理

- `grades.py`：候補・提案・決定
- `growth.own_records()`：終えた作業を証拠の候補に足す
- `money.growth_share()`：標準売上のうち階級が上がった分
- `kpi.py`：会社の順序、成長の寄与、作業を証拠にした数字

## 画面

| URL | 画面 |
|---|---|
| `/genba/management/` | 階級の見直しの候補と、会社の決定待ち |
| `/genba/company/kpi/` | 人材開発から始まる順序、成長の寄与、階級の見直しの決定 |
| `/genba/kpi/` | メンバーに証拠にした作業と階級、リーダーとマネジメントに作業の集計 |
