# plan 014：リポジトリを職種で分ける

spec：[spec.md](spec.md)

## モデル（`0007_repository_job_type`）

| モデル | 変更 |
|---|---|
| `MemberRepository` | `job_type`（SE・プログラマー）を足す。既定は SE |

## 処理

- `kit.checklist(job_type)`：プログラマーでは `PROGRAMMER_REPLACED_ITEMS` で差し替え、`PROGRAMMER_ADDED_ITEMS` を足す
- `kit.join_site(..., job_type=None)`：渡せばリポジトリの職種を変える。チェックリストはそのときの職種で写す
- `kit.job_type_of(user)`：リポジトリがない人は SE として扱う
- `growth.ledger()`：行に職種を足す

## 画面

| URL | 画面 |
|---|---|
| `/genba/kit/?job=` | チェックリストを職種で切り替える |
| `/genba/member/repository/` | 職種を変える（POST） |
| `/genba/sites/create/` | 職種を選ぶ |
| `/genba/sites/<id>/?tab=members` | 職種の列 |
| `/genba/company/?job=#ledger` | スキル台帳を職種で分ける |
