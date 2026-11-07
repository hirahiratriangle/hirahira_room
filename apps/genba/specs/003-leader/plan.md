# plan 003：リーダー画面

spec：[spec.md](spec.md)

## モデル（`0001_initial` を作り直す）

| モデル | 追加 |
|---|---|
| `Question` | 読めない：`support_on`（説明の時間）・`support_note`／懸念：`received_at`・`response` |
| `Summary` | `feedback`（差と次の作業）・`feedback_by`・`feedback_at` |
| `Membership` | 裁量の線：`line_self`（自分で決める範囲）・`line_consult`（相談して決める範囲）・`line_updated_at` |

## 画面

| URL | 画面 |
|---|---|
| `/genba/leader/` | 担当現場ごとに：詰まり（メンバーごと・週報の途切れ）、読めない、まとめ確認、週次共有へのリンク、裁量の線、懸念、ナレッジ候補 |
| `/genba/issues/<id>/` | リーダーなら、読めないに説明の時間、課題に返事を書くフォーム。メンバーには結果が見える |
| `/genba/sites/<id>/summary/?user=<id>` | リーダーなら、差と次の作業を返すフォーム。メンバーの目標と具体記述を横に出す |
| `/genba/sites/<id>/weekly/?week=YYYY-MM-DD` | 週次共有の一覧（現場チーム） |
| `/genba/sites/<id>/lines/` | 裁量の線（本人とリーダーが書く） |

## 判定

- 週報の途切れ：入場から1週以上たって、前週の週報がない（危険信号の「指摘も指示も来ない」と同じ判定を `signals` から使う）

## テスト

- リーダー画面の6欄、週報の途切れ
- 説明の時間・まとめへの返し・懸念への返事：リーダーだけが書け、メンバーに見える
- 週次共有の一覧：週の切り替え、他の現場の週報は出ない
- 裁量の線：本人とリーダーだけが書ける
- 件数・進み具合を出していない
