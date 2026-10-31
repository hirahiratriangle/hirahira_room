# plan 013：ビジョンの統合

spec：[spec.md](spec.md)

## モデル（`0006_member_repository`）

| モデル | 変更 |
|---|---|
| `Site` | `repo_name` を `team_slug` に改名。`template_version` を外す |
| `MemberRepository`（新規） | 人に1つ。既に現場に入っている人の分は移行で作る |
| `FocusSkill`（新規） | 会社の重点スキル項目 |
| `Assignment`（新規） | 元になった成長・現場・次の目標に合うか・決めた人 |

## 画面

| URL | 画面 |
|---|---|
| `/genba/kpi/`・`/genba/company/kpi/` | 先頭にビジョンの階層（`_vision.html`） |
| `/genba/company/kpi/` | 会社の重点を決める |
| `/genba/sites/<id>/goals/create/` | 重点に★を付ける |
| `/genba/management/` | 次の目標に合う現場へアサイン |
