# FE 基本情報技術者試験 対策アプリ

科目A・科目Bの1問1答演習と，分野ごとの正答率にもとづく重点出題を行う Django アプリ。

## 試験の前提（試験要綱 Ver5.6 より）

| | 科目A | 科目B |
|---|---|---|
| 出題数／解答数 | 60問／60問 | 20問／20問 |
| 試験時間 | 90分 | 100分 |
| 形式 | 多肢選択式（四肢択一） | 多肢選択式 |
| 基準点 | 600点／1,000点（IRT） | 600点／1,000点（IRT） |

両科目とも基準点以上で合格。科目Bの分野別出題数は要綱に明記があり，
アルゴリズムとプログラミング16問／情報セキュリティ4問。

## 構成

| ファイル | 役割 |
|---|---|
| `data/categories.py` | 分野・大分類・中分類（全23）のマスタと，科目Aでの想定出題数 |
| `data/questions_*.json` | 固定問題のバンク |
| `generators.py` | 数値を振り直して作る計算問題のテンプレート |
| `selection.py` | 出題する1問の選定（重点出題のロジック） |
| `stats.py` | 正答率の集計と苦手度の算出 |
| `views.py` | ダッシュボード／演習／分野別正答率／履歴 |

## 初期セットアップ

`db.sqlite3` はリポジトリに含めていないので，開発環境では作り直す。

```bash
python manage.py migrate --settings=config.settings_dev
python manage.py seed_fe --settings=config.settings_dev
python manage.py createsuperuser --settings=config.settings_dev
```

ログイン画面が案内しているお試しアカウント（`testuser` / `tu20251025`）を使う場合は，
`createsuperuser` の代わりに次を実行する。

```bash
python manage.py shell --settings=config.settings_dev -c "
from accounts.models import CustomUser
from allauth.account.models import EmailAddress
u, _ = CustomUser.objects.get_or_create(username='testuser', defaults={'email': 'testuser@example.com'})
u.set_password('tu20251025'); u.save()
EmailAddress.objects.update_or_create(user=u, email=u.email, defaults={'verified': True, 'primary': True})
"
```

## 投入・更新

```bash
python manage.py seed_fe --settings=config.settings_dev
```

問題キー（`key`）で突き合わせて更新するので，何度実行しても重複しない。
本番環境では `--settings` を外して実行する。

`--prune` を付けると，JSON から消えた固定問題を無効化する（生成済みの問題は対象外）。

## 問題を追加する

`data/questions_*.json` に次の形式で追記して `seed_fe` を再実行する。

```json
{
  "key": "FE-A-11-025",
  "category": 11,
  "subject": "A",
  "topic": "情報セキュリティ",
  "difficulty": 2,
  "stem": "問題文",
  "choices": ["ア の本文", "イ の本文", "ウ の本文", "エ の本文"],
  "answer": 0,
  "explanation": "解説",
  "source": "出典・根拠"
}
```

- `key` は一意。`FE-<科目>-<中分類2桁>-<連番>` の形にそろえている。
- `choices` にア／イ／ウ／エの記号は含めない（表示側で付ける）。
- `answer` は 0 始まりの添字。
- 擬似言語のプログラムは `stem` に改行と全角スペースの字下げをそのまま書く
  （科目Bは等幅フォント＋`white-space: pre-wrap` で表示される）。

計算問題を増やす場合は `generators.py` の `TEMPLATE_SPECS` に
`(キー, 題材, 中分類番号, 小分類, 生成関数)` を追加する。
生成関数は `stem` / `correct` / `distractors` / `explanation` / `params` を返す。
誤答は3個そろわないと出題されないので，正解と衝突しない候補を多めに並べておく。

## 重点出題の考え方

1. **中分類を重み付き抽選で選ぶ。** 重みは本番の出題比率（`exam_weight`）を土台に，
   苦手なほど増幅する（`exam_weight × (0.3 + 2.0 × 苦手度)`）。
   本番で8問出るセキュリティを得意になったからといって，
   1問しか出ない法務ばかり出題されては困るため，苦手度だけでは決めない。
2. **苦手度は平滑化した正答率から求める。** 素の正答率だと1〜2問しか解いていない
   中分類が「正答率0％の最重要苦手分野」として暴れるので，事前分布 0.6（基準点相当）へ
   5問ぶん寄せてから比較する（`stats.smoothed_rate`）。
3. **中分類の中では，未出題 → 前回まちがえた問題 → しばらく解いていない問題 の順に選ぶ。**
   計算問題のテンプレートがある中分類では，未出題が尽きたら新しい数値で生成する。

## テスト

```bash
python manage.py test fe --settings=config.settings_dev
```

重点出題が実際に苦手分野へ寄るかどうかは統計的に検証している（`SelectionTests`）。
