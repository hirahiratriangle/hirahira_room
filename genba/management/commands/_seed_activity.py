"""seed_genba の活動データ（週報・確認事項・作業・議事録・用語・設計判断・読めない）を多めに作る。

seed_genba の筋書き（ゲストの危険信号、週報の途切れ、承認待ちなど）は、seed_genba 本体が日付まで決めて作る。
ここでは、その筋書きを壊さないように「過去の活動」を厚くする。

- 週報は2週以上前の週だけ足す。直近の2週（今週・先週）は本体の筋書きに任せる
  （先週の週報がないことが、リーダーの画面の「週報の途切れ」やゲストの危険信号になっているため）
- 先週の週報がない人は、さらに2〜3週前も空けておく。途切れが2週以上続いていることが、
  マネジメントの「『指摘も指示も来ない』状態が続いている人がいる」になっているため
- quiet_weeks を渡した現場は、直近その週数の週報を足さない（「週報が滞っている」の筋書きを残す）
- 確認事項・作業・読めないは、すべて解決済みにする。未解決を足すと、運営指標の滞留日数（平均）が
  下がって「意思決定が止まっている」の兆候が消えたり、リーダーの「詰まりの一覧」が筋書きから外れたりするため
- ゲスト本人（seed_genba の --user）は skip で外す。本人の画面の筋書きを変えないため
- 乱数は現場のリポジトリ名で種を決める。何度流しても同じデータになる
"""
import random
from datetime import timedelta

from genba.models import Document, Question, Term, week_start

# 現場ごとの話題。業界が違えば、聞く相手も用語も違う
TOPICS = {
    'order': {
        'ask_to': ['業務部 課長', '情シス', '倉庫担当', '経理', '運用チーム', '営業管理'],
        'questions': [
            '受注取消の締め時刻', '分納のときの引当単位', '得意先コードの桁数', '単価改定の反映日', '返品伝票の起票者',
            '直送品の在庫計上', '受注明細の上限行数', '与信超過のときの扱い', '納期回答の担当', '出荷保留の解除権限',
            'セット品の引当方法', '見積から受注への引継ぎ項目', '値引きの按分方法', '請求書の再発行条件',
        ],
        'tasks': [
            '受注明細画面の項目定義', '引当処理のフロー図', '帳票一覧の整理', 'エラーメッセージ一覧', '得意先マスタ画面の設計',
            '出荷指示書のレイアウト', '締め処理のバッチ設計', 'IF一覧（倉庫システム）', '権限マトリクスの初版', '画面遷移図の更新',
        ],
        'terms': [
            ('引当', '受注に対して在庫を確保すること。出荷指示の前に行う。'),
            ('分納', '1つの受注を複数回に分けて出荷すること。'),
            ('直送', '仕入先から得意先へ直接届けること。自社倉庫を通らない。'),
            ('与信', '得意先ごとの取引上限額。超えると受注を保留する。'),
            ('売上計上', 'この現場では得意先受領の時点で計上する。'),
        ],
        'designs': [
            ('受注明細は1伝票100行までにする', '案: 上限なし／100行\n決定: 100行\n理由: 帳票1枚に収まり、超える受注は実績がないため。'),
            ('与信チェックは受注確定時に行う', '案: 入力時／確定時\n決定: 確定時\n理由: 入力途中の金額で止めると入力が進まないため。'),
            ('分納は明細単位で管理する', '案: 伝票単位／明細単位\n決定: 明細単位\n理由: 品目ごとに出荷日が違うため。'),
        ],
        'sources': ['受注仕様書 v4', '帳票定義書 v3', '業務マニュアル（受注編）', 'DB定義書', 'IF仕様書（倉庫）'],
        'minutes': ['受注画面の項目並び', '引当ルールの確認', '帳票の出力形式', '締め処理の時刻', '権限の整理', '分納の扱い'],
    },
    'logistics': {
        'ask_to': ['元請PM', '倉庫の現場リーダー', '運用担当', '顧客の物流部'],
        'questions': [
            '締め時刻を倉庫ごとに変えるか', '出荷指示の再送条件', '夜間バッチの再実行手順', '欠品時の出荷指示の扱い',
            '休日の締め処理', 'バッチの実行ログの保管期間', '出荷実績の連携タイミング', '帳票の出力先プリンタ',
        ],
        'tasks': [
            '出荷指示バッチの処理フロー', '締め時刻の設定値一覧', '影響範囲一覧（在庫引当・帳票）', 'テスト観点の洗い出し',
            '運用手順書の改訂案', 'ジョブネットの変更案',
        ],
        'terms': [
            ('締め時刻', 'その日の出荷指示に含める受注の締切。倉庫ごとに違う。'),
            ('ピッキング', '出荷のために棚から品物を取り出す作業。'),
            ('ジョブネット', 'バッチ処理の実行順と依存関係をまとめたもの。'),
        ],
        'designs': [
            ('再実行は差分だけ行う', '案: 全件再実行／差分だけ\n決定: 差分だけ\n理由: 全件だと締め後の出荷指示が二重になるため。'),
        ],
        'sources': ['バッチ設計書', '運用手順書', 'ジョブネット定義'],
        'minutes': ['締め時刻の変更範囲', 'バッチの再実行手順', 'テスト計画'],
    },
    'claims': {
        'ask_to': ['査定部', '支払担当', '元請PM', '顧客情シス', 'コールセンター'],
        'questions': [
            '事故受付と請求受付の紐づけ', '査定保留の期限', '支払方法の変更可否', '必要書類の不足時の扱い', '高額案件の承認経路',
            '代理請求の受付条件', '支払通知書の送付方法', '査定結果の通知タイミング', '重複請求の判定', '受付番号の採番ルール',
            '免責金額の計算順序', '再請求の受付期限',
        ],
        'tasks': [
            '事故受付画面の項目定義', '査定フローの図', '支払通知書のレイアウト', '必要書類チェック画面の設計', 'IF一覧（基幹）',
            '画面遷移図（請求受付）', 'エラー一覧', '権限マトリクス', '帳票一覧', '業務ルール一覧',
        ],
        'terms': [
            ('査定', '請求内容と約款を照らして、支払うかどうかと金額を決めること。'),
            ('免責金額', '保険金から差し引く自己負担の額。'),
            ('約款', '保険契約の条件を定めた文書。査定の根拠になる。'),
            ('代理請求', '契約者本人以外が請求すること。条件がある。'),
            ('支払保留', '書類不足などで支払を止めている状態。'),
        ],
        'designs': [
            ('高額案件は二段階承認にする', '案: 一段階／二段階\n決定: 二段階（100万円以上）\n理由: 内部監査の指摘による。'),
            ('受付番号は事故単位で採番する', '案: 請求単位／事故単位\n決定: 事故単位\n理由: 1事故に複数請求が紐づくため。'),
            ('必要書類はチェックリストで管理する', '案: 自由記述／チェックリスト\n決定: チェックリスト\n理由: 不足の判定を揃えるため。'),
        ],
        'sources': ['支払業務説明書', '査定マニュアル', '約款（自動車）', '画面一覧', 'IF仕様書（基幹）'],
        'minutes': ['請求受付画面の項目', '査定フローの確認', '支払通知書の形式', '高額案件の承認', '必要書類の一覧', 'IFの方式'],
    },
    'stock': {
        'ask_to': ['元請PM', '店舗運営部', '店舗担当者', '顧客情シス'],
        'questions': [
            '在庫の更新頻度', '棚卸し差異の表示方法', '店舗ごとの閲覧権限', '移動中在庫の扱い', '可視化画面の対象店舗',
            '過去在庫の保持期間', '発注点の表示',
        ],
        'tasks': [
            '在庫一覧画面の設計', '店舗別グラフの設計', '権限一覧', 'データ項目一覧', '画面遷移図',
        ],
        'terms': [
            ('実在庫', '棚卸しで数えた在庫。理論在庫と比べて差異を見る。'),
            ('発注点', '在庫がこの数を下回ったら発注する目安。'),
            ('移動中在庫', '店舗間で移動している途中の在庫。'),
        ],
        'designs': [
            ('在庫は1時間ごとに更新する', '案: リアルタイム／1時間ごと\n決定: 1時間ごと\n理由: POSの連携が1時間ごとのため。'),
        ],
        'sources': ['POS連携仕様書', '在庫管理マニュアル', '店舗運営規程'],
        'minutes': ['可視化画面のレビュー', '対象店舗の確認', '権限の整理'],
    },
}

WEEKLY = {
    'done': ['担当画面の項目定義を進めた。', 'レビュー指摘を反映した。', '業務フローの例外を整理した。', '確認事項を3件解消した。',
             '設計書の初版を出した。', '帳票のレイアウトを固めた。', 'IFの項目を突き合わせた。', 'テスト観点を洗い出した。'],
    'learned': ['決裁は課長ではなく部長が出している。', '業務ルールの例外は口頭で伝わっている。', '用語の意味が部署で違う。',
                'レビューは前日までに資料を送る決まり。', '', '', ''],
    'stuck': ['回答待ちで画面遷移が決められない。', '資料の最新版がどれか分からない。', '', '', '', ''],
    'next': ['レビュー指摘を反映する。', '確認事項の回答を設計に入れる。', '次の画面の項目定義に入る。', '初版をレビューに出す。'],
}


def seed_activity(cmd, site, key, members, skip=(), quiet_weeks=0):
    """site に、members（その現場に入っている人）の過去の活動を足す。

    cmd  ：seed_genba の Command（now・today・ago を使う）
    key  ：TOPICS のキー（現場の話題）
    skip ：活動を足さない人（ゲスト本人など）
    quiet_weeks：直近この週数は週報を足さない（0 なら2週前から足す）
    """
    rnd = random.Random(site.repo_name)
    topic = TOPICS[key]
    people = [m for m in members if m not in skip]
    if not people:
        return
    this_week = week_start(cmd.today)

    # 週報：入場した週から、2週前の週まで。ときどき出していない週がある（1割）
    for m in people:
        joined = site.memberships.get(user=m).joined_on
        start = max(2, quiet_weeks)
        last_week = this_week - timedelta(weeks=1)
        if not site.documents.filter(author=m, kind=Document.KIND_WEEKLY, week_of=last_week).exists():
            start = max(start, 4)
        w = this_week - timedelta(weeks=start)
        while w >= week_start(joined):
            exists = site.documents.filter(author=m, kind=Document.KIND_WEEKLY, week_of=w).exists()
            if not exists and rnd.random() > 0.1:
                Document.objects.create(
                    site=site, author=m, kind=Document.KIND_WEEKLY, title=f'週報 {w.month}/{w.day}の週', week_of=w,
                    done=rnd.choice(WEEKLY['done']), learned=rnd.choice(WEEKLY['learned']),
                    stuck=rnd.choice(WEEKLY['stuck']), next_step=rnd.choice(WEEKLY['next']),
                    created_at=cmd.now - timedelta(days=(cmd.today - w).days - 4))
            w -= timedelta(weeks=1)

    days_since = max((cmd.today - site.started_on).days, 7)

    # 確認事項：すべて解決済み（聞いてから1〜5日で回答が来た）
    for i, title in enumerate(topic['questions']):
        author = people[i % len(people)]
        opened = rnd.randint(7, days_since)
        Question.objects.create(site=site, author=author, title=title, ask_to=rnd.choice(topic['ask_to']),
                                created_at=cmd.ago(opened), is_open=False,
                                closed_at=cmd.ago(max(opened - rnd.randint(1, 5), 1)))

    # 作業：予定と実績。ほとんど完了、一部は遅れてから完了（遅れの理由つき）
    for i, title in enumerate(topic['tasks']):
        author = people[i % len(people)]
        start = rnd.randint(10, days_since)
        due = cmd.today - timedelta(days=start - rnd.randint(3, 7))
        late = rnd.random() < 0.3
        done = due + timedelta(days=rnd.randint(1, 4)) if late else due - timedelta(days=rnd.randint(0, 2))
        Question.objects.create(
            site=site, author=author, kind=Question.KIND_TASK, title=title, created_at=cmd.ago(start), due_on=due,
            done_on=min(done, cmd.today), is_open=False, closed_at=cmd.ago(max((cmd.today - done).days, 0)),
            delay_note='回答待ちで着手が遅れた。回答の翌日から2日で追いついた。' if late else '')

    # 読めない：解決済み（説明の時間をもらって読めるようになった）
    for i, src in enumerate(topic['sources']):
        author = people[i % len(people)]
        opened = rnd.randint(7, days_since)
        Question.objects.create(
            site=site, author=author, unreadable=True, title=f'{src}の前提が分からない', source=src,
            location=f'{rnd.randint(1, 6)}章', reason=rnd.choice(['term', 'premise', 'figure']), created_at=cmd.ago(opened),
            is_open=False, closed_at=cmd.ago(max(opened - rnd.randint(1, 3), 1)))

    # 用語：既にある語は足さない（本体の筋書きで作った用語と重ねない）
    for i, (term, meaning) in enumerate(topic['terms']):
        if not site.terms.filter(term=term).exists():
            Term.objects.create(site=site, author=people[i % len(people)], term=term, meaning=meaning,
                                created_at=cmd.ago(rnd.randint(3, days_since)))

    # 設計判断（04_設計）
    for i, (title, body) in enumerate(topic['designs']):
        Document.objects.create(site=site, author=people[i % len(people)], kind=Document.KIND_DESIGN, title=title, body=body,
                                created_at=cmd.ago(rnd.randint(5, days_since)))

    # 議事録（05_議事録）：週1回の定例。書くのは持ち回り
    n_weeks = min(days_since // 7, 12)
    for n in range(2, n_weeks):
        Document.objects.create(
            site=site, author=people[n % len(people)], kind=Document.KIND_MINUTES,
            title=f'第{n_weeks - n}回 定例', body=f'・{rnd.choice(topic["minutes"])}を確認\n・確認事項の回答を共有\n・次回までの宿題を確認',
            created_at=cmd.ago(n * 7))
