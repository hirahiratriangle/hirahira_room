# plan 008：標準値の積み上げと立場ごとの公開範囲

spec：[spec.md](spec.md)

## モデル（`0004_grade_and_work`）

| モデル | 内容 |
|---|---|
| `Grade` | 階級・並び順・月額の標準単価・月額の標準原価 |
| `GradeAssignment` | 人の今の階級 |
| `MonthlyWork` | 現場メンバー・対象月・その月の階級・人月 |
| `CompanyMonth` | 月ごとの全社の在籍人数 |

## 画面

| URL | 画面 |
|---|---|
| `/genba/sites/<id>/work/` | 階級と人月。本人が自分の人月を入れ、チームが全員の行を読む |
| `/genba/kpi/` | 経営の数字（メンバー・リーダー・マネジメント）。会社は `/genba/company/kpi/` へ移す |
| `/genba/management/` | 現場ごとの標準売上と標準粗利 |
| `/genba/company/kpi/` | 経営の結果（全社・業界ごと）を先頭に足す |

階級表・人の階級・在籍人数は、プロトタイプでは管理画面で直す。

## 集計

`money.rollup()` が1回の走査で人・現場・業界・全社を同時に足し上げる。`kpi.for_role()` が立場ごとに絞る。
