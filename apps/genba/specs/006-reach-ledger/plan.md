# plan 006：成長の確定とスキル台帳

spec：[spec.md](spec.md)

## モデル（0002）

`Goal`：合意と、アサインの希望の欄を外し、次の欄を足す。

| 段階 | 欄 |
|---|---|
| 記入 | `submitted_at` |
| 確認 | `confirmed_at`・`confirmed_by`・`confirm_comment`・`customer_voice` |
| 承認 | `approved_on`・`approved_by`・`approved_level`・`approved_grade`・`approval_comment` |

## 画面

| URL | 画面 |
|---|---|
| `/genba/goals/<id>/reach/` | 本人は記入・依頼・取り下げ。リーダーは確認・差し戻し。チームは読む |
| `/genba/goals/<id>/approval/` | マネジメントの承認（ひとり現場は確認も） |
| `/genba/leader/` | 確認待ちの成長 |
| `/genba/management/` | 承認待ちの成長 |
| `/genba/company/` | スキル台帳・スキルシート・台帳の集計 |

## テスト

段階の進み方、誰が操作できるか、公開範囲、台帳に承認したものだけが出ること。
