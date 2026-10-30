"""genba のデモデータを入れる。

    python manage.py seed_genba --user <ユーザー名>

指定したユーザー（本番では未ログインの人が使う genba_guest）に4つの立場を兼ねさせ、
ひとりで全部の画面を見られるようにする。人数は指定したユーザーを含めて次のとおり。

| チーム | リーダー | メンバー |
|---|---|---|
| 1 受発注 | 指定したユーザー | demo_sato・demo_suzuki（受発注システム刷新）、demo_takahashi（ひとり現場）＝3人 |
| 2 保険金請求 | demo_tanaka | 指定したユーザー・demo_ito・demo_watanabe・demo_yamamoto（保険金請求 基本設計）、
|  |  | demo_nakamura（ひとり現場）＝5人 |
| マネジメント | 指定したユーザー（1人） | |
| 会社 | 指定したユーザー（1人） | |

それぞれのチームに、ひとり現場（自社のリーダーが現場にいない）に入っている人を1人ずつ置く。
ひとり現場は、その人だけがメンバーとして入っている現場として作る。確認するリーダーがいないので、
到達はマネジメントが確認と承認を行う（構想書スライド22）。

デモの現場（リポジトリ名が genba-demo- で始まるもの）と、デモのユーザーが出した改訂の提案・質問リストは
毎回作り直す。前の版の seed で作ったデモのユーザー（demo_kacho など）は、立場のグループから外す
（マネジメント・会社が指定したユーザー1人になるように）。

筋書き：
- 受発注（リーダー）：入場から10週。チェックリストはほぼ済み、まとめはリーダーと確認済み。
  demo_sato の到達の確認がリーダーの画面に届いている。demo_suzuki の到達は承認済み。
  自分の目標（プロジェクトマネジメント レベル3→4）は記入中（マネジメントが自分1人なので、自分では承認しない）
- 物流の出荷バッチ改修（ひとり現場・demo_takahashi）：到達はマネジメントが元請の声を聞き取って承認済み
- 保険金請求（メンバー）：現場は12週目、自分は入場から6日。1週目の途中で、3日止まっている「読めない」があり、
  まとめはまだ出していない → 危険信号が2つ出る。目標（デザイン レベル3→4）を立てている。
  demo_watanabe の到達はリーダーが確認済みで、マネジメントの承認待ちに届いている。
  公開された振り返り・用語、質問リスト、改訂の提案も、この現場から出す
- 店舗在庫の可視化（ひとり現場・demo_nakamura）：到達の確認を依頼していて、マネジメントの画面に
  「確認して承認する」で届いている。スコープ外の作業と体制のズレが運営指標に出る
"""
from datetime import timedelta

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import CustomUser
from genba import kit
from genba.models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_PUBLISHED, VISIBILITY_COMPANY,
    Document, Goal, GoalEvidence, InterviewQuestion, KitProposal, Membership, PromiseCheck, Question, Site, StructureGap,
    Summary, Term, week_start,
)
from genba.roles import GROUP_COMPANY, GROUP_MANAGEMENT

from ._seed_activity import seed_activity

DEMO_PREFIX = 'genba-demo-'
DEMO_USERS = ['demo_sato', 'demo_suzuki', 'demo_takahashi',
              'demo_tanaka', 'demo_ito', 'demo_watanabe', 'demo_yamamoto', 'demo_nakamura']

# チェックリストの段階ごとの予定日（入場日からの日数）
STAGE_DUE = {'pre': -1, 'day1': 0, 'week1': 4, 'week2': 14}


class Command(BaseCommand):
    help = 'genba のデモデータ（チームの現場2つ・ひとり現場2つ）を作り直す'

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='4つの立場を割り当てるユーザー名')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            me = CustomUser.objects.get(username=options['user'])
        except CustomUser.DoesNotExist:
            raise CommandError(f'ユーザー {options["user"]} がいません')

        Site.objects.filter(repo_name__startswith=DEMO_PREFIX).delete()
        # 現場に属さない記録（改訂の提案・質問リスト）は、デモのユーザーが出したものだけ消す。
        # 本物の利用者が出したものは残す。作り直すたびに重ならないように
        KitProposal.objects.filter(author__username__startswith='demo_').delete()
        InterviewQuestion.objects.filter(author__username__startswith='demo_').delete()
        users = {}
        for name in DEMO_USERS:
            u, created = CustomUser.objects.get_or_create(username=name, defaults={'email': f'{name}@example.com'})
            if created:
                u.set_unusable_password()
                u.save()
            users[name] = u
        self.me = me
        groups = [Group.objects.get_or_create(name=g)[0] for g in (GROUP_MANAGEMENT, GROUP_COMPANY)]
        me.groups.add(*groups)
        # マネジメント・会社は指定したユーザー1人だけにする。前の版のデモのユーザー（demo_kacho など）が
        # グループに残っていたら外す（デモのユーザーは誰もこの2つの立場を持たない）
        for u in CustomUser.objects.filter(username__startswith='demo_', groups__in=groups).distinct():
            u.groups.remove(*groups)

        self.now = timezone.now()
        self.today = timezone.localdate()
        self.seed_order(me, users['demo_sato'], users['demo_suzuki'])
        self.seed_logistics(users['demo_takahashi'])
        self.seed_claims(me, *(users[n] for n in ('demo_tanaka', 'demo_ito', 'demo_watanabe', 'demo_yamamoto')))
        self.seed_stock(users['demo_nakamura'])
        # 筋書きのあとに、過去の活動（週報・確認事項・作業・議事録など）を多めに足す。
        # ゲスト本人は外す（本人の画面の筋書き：危険信号・出していない週を変えないため）
        for key in ('order', 'logistics', 'claims', 'stock'):
            site = Site.objects.get(repo_name=f'{DEMO_PREFIX}{key}')
            members = [m.user for m in site.memberships.select_related('user')]
            # 店舗在庫（ひとり現場）は直近8週の週報を足さない：見ている人がいないと週報が滞る、という筋書き
            seed_activity(self, site, key, members, skip=(me,), quiet_weeks=8 if key == 'stock' else 0)
        self.stdout.write(self.style.SUCCESS(
            f'デモの現場を4つ（うちひとり現場2つ）作りました（{me.username} が4つの立場を兼ねます）。'))

    # ---- 共通 ----

    def published(self, leader, title, body):
        """リーダーが一般化した版を書いて会社に公開した状態"""
        return dict(visibility=VISIBILITY_COMPANY, knowledge_status=KNOWLEDGE_PUBLISHED, public_title=title,
                    public_body=body, published_by=leader, published_at=self.now - timedelta(days=3))

    def approved(self, level, grade, confirmed_by, days=2, comment='', customer_voice=''):
        """記入・確認・承認が済んだ到達の欄。承認するのはマネジメント（指定したユーザー）。

        days 日前に承認、その前日に確認、さらに前日に確認を依頼した、という順に日付を並べる。
        """
        return dict(submitted_at=self.ago(days + 2), confirmed_at=self.ago(days + 1), confirmed_by=confirmed_by,
                    customer_voice=customer_voice, approved_on=self.today - timedelta(days=days), approved_by=self.me,
                    approved_level=level, approved_grade=grade, approval_comment=comment)

    def ago(self, days):
        return self.now - timedelta(days=days)

    def week(self, n):
        return week_start(self.today) - timedelta(weeks=n)

    def join(self, site, user, role=Membership.ROLE_MEMBER, joined_on=None, duty=''):
        """現場に入る（その人のチェックリストが並ぶ）。予定日は入場日から決める"""
        joined_on = joined_on or site.started_on
        kit.join_site(site, user, role, joined_on, duty)
        for q in site.questions.filter(author=user).exclude(stage=''):
            q.due_on = joined_on + timedelta(days=STAGE_DUE[q.stage])
            q.created_at = self.now - timedelta(days=(self.today - joined_on).days + 1)
            q.save()

    def check_items(self, site, user, stage, sections=None, items=None):
        """その人のチェックリストを済みにする。sections を省くと段階の全部。items で各 Issue の先頭から何項目か"""
        for q in site.questions.filter(author=user, stage=stage).order_by('order'):
            if sections is not None and not any(s in q.title for s in sections):
                continue
            targets = list(q.items.all())[:items] if items else q.items.all()
            for item in targets:
                item.is_done = True
                item.save()
            if not q.items.filter(is_done=False).exists():
                q.is_open = False
                q.done_on = q.due_on
                q.closed_at = self.now
                q.save()

    def weekly(self, site, author, n, done, learned='', stuck='', next_step='', goal_action=''):
        w = self.week(n)
        Document.objects.create(site=site, author=author, kind=Document.KIND_WEEKLY, title=f'週報 {w.month}/{w.day}の週',
                                week_of=w, done=done, learned=learned, stuck=stuck, next_step=next_step,
                                goal_action=goal_action)

    # ---- 現場1: 受発注システム刷新（自分はリーダー） ----

    def seed_order(self, me, sato, suzuki):
        started = self.today - timedelta(weeks=10)
        site = Site.objects.create(
            name='受発注システム刷新', repo_name=f'{DEMO_PREFIX}order', industry='製造業',
            description='既存の受発注システムを刷新する基本設計フェーズ。', started_on=started,
        )
        self.join(site, me, Membership.ROLE_LEADER, duty='基本設計リード')
        self.join(site, sato, duty='画面設計')
        self.join(site, suzuki, duty='帳票・バッチ設計', joined_on=started + timedelta(weeks=2))
        for user in (me, sato, suzuki):
            for stage in ('pre', 'day1', 'week1'):
                self.check_items(site, user, stage)
            self.check_items(site, user, 'week2', items=4)

        Q = Question.objects
        Q.create(site=site, author=sato, title='「確定」ボタン押下後の取消可否', ask_to='業務部 課長',
                 body='確定後に取り消せるのか、赤黒で訂正するのか未定。画面遷移に影響する。', created_at=self.ago(12))
        Q.create(site=site, author=sato, title='得意先マスタの更新権限は誰が持つか', ask_to='情シス', created_at=self.ago(4))
        Q.create(site=site, author=suzuki, title='月末締めバッチの実行時刻', ask_to='運用チーム', created_at=self.ago(9))
        Q.create(site=site, author=suzuki, unreadable=True, title='帳票定義書の「集約区分」が読めない',
                 source='帳票定義書 v3', location='4.2 集約区分', reason='term', ask_to='業務部', created_at=self.ago(1))
        Q.create(site=site, author=suzuki, kind=Question.KIND_TASK, scope_out=True, title='既存帳票の棚卸し（追加依頼）',
                 body='契約は新帳票の設計のみ。旧帳票35本の棚卸しを口頭で頼まれた。', ask_to='課長', created_at=self.ago(6))
        Q.create(site=site, author=me, kind=Question.KIND_PROBLEM, title='旧システムの仕様書が最新でない', created_at=self.ago(15),
                 fact='仕様書は2019年版。実際の画面と項目が5か所違う。',
                 interpretation='改修のたびに仕様書が更新されていない可能性がある。',
                 unconfirmed='最新の仕様をどこで管理しているか（情シスに未確認）。',
                 impact='現行調査の工数が増え、受注画面の設計が1週遅れる見込み。')
        Q.create(site=site, author=sato, kind=Question.KIND_TASK, title='受注画面の画面設計書（初版）',
                 due_on=self.today - timedelta(days=3), created_at=self.ago(14),
                 delay_note='確定後の取消可否の回答待ち。回答が来れば2日で追いつく見込み。')
        Q.create(site=site, author=sato, title='消費税の端数処理', ask_to='経理', created_at=self.ago(20),
                 is_open=False, closed_at=self.ago(17))

        Term.objects.create(site=site, author=sato, term='出荷', meaning='倉庫から出た時点ではなく、得意先が受領した時点を指す。',
                            general_difference='一般には倉庫から出した時点。',
                            **self.published(me, '出荷', '倉庫から出た時点ではなく、得意先が受領した時点を指すことがある。'
                                                        '一般には倉庫から出した時点なので、画面のラベルで確かめる。'))
        Term.objects.create(site=site, author=suzuki, term='赤黒', meaning='確定済み伝票を、マイナス伝票と正しい伝票の2枚で訂正すること。',
                            knowledge_status=KNOWLEDGE_CANDIDATE)
        Term.objects.create(site=site, author=me, term='締め', meaning='得意先ごとの請求締め日。月末とは限らない。',
                            general_difference='社内では「月次締め」を指すことが多い。',
                            **self.published(me, '締め', '得意先ごとの請求締め日。月末とは限らない（社内の「月次締め」と区別する）。'))
        Term.objects.create(site=site, author=suzuki, term='集約区分', meaning='')

        D = Document.objects
        D.create(site=site, author=me, kind=Document.KIND_PRE, title='参画前面談メモ',
                 body='担当工程：基本設計（画面・帳票・バッチ）\n期待値：1週目で現状把握、2週目から受注画面の設計',
                 created_at=self.ago(72))
        D.create(site=site, author=sato, kind=Document.KIND_PRE, title='「出荷」の意味の確認',
                 body='業務部は「出荷」を得意先受領の意味で使っている。画面ラベルを「納品」に寄せる案を相談中。',
                 knowledge_status=KNOWLEDGE_CANDIDATE, created_at=self.ago(60))
        D.create(site=site, author=me, kind=Document.KIND_CONTRACT, title='作業範囲と約束事項',
                 body='基本設計（画面・帳票・バッチ）まで。詳細設計以降は別契約。\nレビューは週1回、木曜。', created_at=self.ago(70))
        D.create(site=site, author=me, kind=Document.KIND_STRUCTURE, title='体制と決裁の流れ',
                 body='PM：顧客情シス課長\n決裁者：業務部長（仕様）／情シス課長（体制）\n日々の指示：業務部 課長\n定例：木曜 14:00 設計レビュー',
                 created_at=self.ago(69))
        D.create(site=site, author=sato, kind=Document.KIND_BUSINESS, title='受注から請求までの業務フロー',
                 body='受注 → 引当 → 出荷指示 → 出荷（得意先受領）→ 締め → 請求', created_at=self.ago(66))
        D.create(site=site, author=me, kind=Document.KIND_DESIGN, title='伝票訂正は赤黒方式にする',
                 body='案: 直接更新／赤黒\n決定: 赤黒\n理由: 監査で訂正履歴を追えることが求められたため。',
                 created_at=self.ago(25),
                 **self.published(me, '確定済み伝票の訂正は赤黒方式にする',
                                  '案: 直接更新／赤黒\n決定: 赤黒（マイナス伝票と正しい伝票の2枚で訂正）\n'
                                  '理由: 監査で訂正履歴を追えることが求められたため。受発注系で同じ判断が要る。'))
        D.create(site=site, author=sato, kind=Document.KIND_MINUTES, title='第8回 設計レビュー',
                 body='・受注画面の項目並びを合意\n・確定後の取消は次回までに業務部が回答', created_at=self.ago(3))
        D.create(site=site, author=suzuki, kind=Document.KIND_MINUTES, title='第7回 設計レビュー',
                 body='・帳票の出力形式はPDFで統一', created_at=self.ago(10))
        for n in range(0, 10):
            if n in (2, 5):
                continue  # 出していない週
            self.weekly(site, me, n, done='受注画面・帳票の設計を進めた。',
                        learned='業務部の「出荷」は得意先受領の意味。' if n == 1 else '',
                        stuck='確定後の取消可否の回答待ちで、受注画面の遷移が決められない。' if n <= 1 else '',
                        next_step='回答を受けて受注画面の遷移を確定する。',
                        goal_action='3人の設計チームの課題とリスクを週次で棚卸しし、レビューに出した。' if n in (0, 3) else '')
        # リーダー画面で見せる：週報の途切れ、読めない、まとめの確認、裁量の線、懸念
        for n in (0, 1):
            self.weekly(site, sato, n, done='受注画面の項目定義を進めた。', learned='引当は受注確定時に行う。',
                        stuck='確定後の取消可否が決まらず、遷移図が描けない。' if n == 0 else '', next_step='遷移図の初版を出す。')
        self.weekly(site, suzuki, 3, done='帳票一覧を作った。', next_step='集約区分の意味を確認する。')
        Question.objects.create(site=site, author=sato, unreadable=True, title='引当仕様書の「仮引当」が読めない',
                                source='引当仕様書 v2', location='2.3 仮引当', reason='premise', created_at=self.ago(3))
        Question.objects.create(site=site, author=sato, unreadable=True, title='在庫テーブルの図がない',
                                source='DB定義書', location='在庫系テーブル', reason='figure', created_at=self.ago(5),
                                support_on=self.today + timedelta(days=1), support_note='木曜の定例後に15分、情シスの担当から')
        Question.objects.create(site=site, author=suzuki, kind=Question.KIND_PROBLEM, title='帳票の本数が契約時の想定を超えている',
                                created_at=self.ago(2), fact='棚卸しで帳票が35本見つかった。契約時の想定は20本。',
                                interpretation='新帳票の設計工数が足りなくなる可能性がある。',
                                unconfirmed='35本すべてが刷新の対象か（業務部に未確認）。',
                                impact='帳票設計が2週遅れる見込み。スコープの再確認が要る。')
        Summary.objects.create(site=site, author=suzuki, submitted_at=self.ago(50),
                               overview='受注〜請求の帳票とバッチの全体像。', functions='担当：帳票。入力＝伝票、出力＝PDF帳票。')
        Membership.objects.filter(site=site, user=sato).update(
            line_self='画面項目の並び、入力チェックの細かいルール', line_consult='画面遷移の変更、業務ルールの解釈、他機能への影響',
            line_updated_at=self.ago(60))
        Summary.objects.create(site=site, author=sato, submitted_at=self.ago(65),
                               feedback='業務システム構築のレベル3「担当機能の業務フローを自分で追い、業務ルールと例外を漏れなく設計する」に対して、'
                                        '業務フローは追えているが、例外（確定後の取消・訂正）がまだ書かれていない。2週目は例外の一覧を作る。',
                               feedback_by=me, feedback_at=self.ago(64),
                               overview='受注〜請求の基幹業務を刷新。利用者は業務部と倉庫。業務フローは 03_業務理解 を参照。',
                               functions='担当：受注画面。入力＝受注明細、処理＝引当、出力＝出荷指示。つながる機能：在庫引当・請求。',
                               review_note='担当範囲は受注・訂正まで。期待値との差は帳票の理解。2週目は帳票一覧を読む。')
        Summary.objects.create(site=site, author=me, submitted_at=self.ago(65),
                               overview='受注〜請求の基幹業務を刷新。帳票とバッチの全体像は 03_業務理解 を参照。',
                               functions='担当：設計全体のリード。受注・帳票・バッチのつながりを整理。',
                               review_note='PMと確認。期待値との差は帳票本数の把握。2週目は帳票の棚卸し範囲を契約と照らす。')
        StructureGap.objects.create(site=site, author=sato, role_kind='instructor', on_chart='顧客情シス課長（PM）',
                                    actual='業務部 課長', note='画面の仕様は業務部 課長が日々決めている。情シス課長は週次で追認するだけ。',
                                    created_at=self.ago(50))
        PromiseCheck.objects.create(site=site, author=me, promise='要件は確定済み', actual='確定後の取消など業務ルールが未定', is_gap=True)
        PromiseCheck.objects.create(site=site, author=me, promise='帳票は20本', actual='棚卸しで35本見つかった', is_gap=True)
        PromiseCheck.objects.create(site=site, author=me, promise='レビューは週1回', actual='週1回で回っている')
        goal = Goal.objects.create(
            site=site, author=me, skill_item='プロジェクトマネジメント', concrete_skill='担当機能の設計スケジュール管理',
            current_level=3, target_text='3人以上の設計チームのスケジュール・課題・リスクを管理する',
            preparation='マスタースケジュールの読み方と、課題管理表の運用を確認しておく。', created_at=self.ago(71),
            reached_level=4, mentored=True, role='leader', team_size=3, complexity='1,4',
            explanation='3人の設計チームの課題とリスクを週次で棚卸しし、レビュー計画に組み込んだ。仕様書が最新でない課題は、影響と見込みを添えて上げた。',
            next_text='10人以上の開発チームのコスト・スケジュール・リスクを管理し、他を指導する')
        problem = site.questions.get(title='旧システムの仕様書が最新でない')
        GoalEvidence.objects.create(goal=goal, record_type='issue', record_id=problem.pk, title=f'課題：{problem.title}')
        sato_goal = Goal.objects.create(
            site=site, author=sato, skill_item='業務システム構築', concrete_skill='業務フローの把握と可視化',
            current_level=2, target_text='担当機能の業務フローを自分で追い、業務ルールと例外を漏れなく設計する',
            created_at=self.ago(70), reached_level=3, role='member', team_size=3,
            explanation='受注から請求までの業務フローを自分で追って資料にし、受注画面の業務ルールと例外を設計した。',
            next_text='画面・帳票・テーブル・IFを、影響範囲を含めて独力で設計する', submitted_at=self.ago(1))
        flow = site.documents.get(title='受注から請求までの業務フロー')
        GoalEvidence.objects.create(goal=sato_goal, record_type='doc', record_id=flow.pk, title=f'03_業務理解：{flow.title}')
        Goal.objects.create(site=site, author=suzuki, skill_item='デザイン', current_level=2,
                            target_text='画面・帳票・テーブル・IFを、影響範囲を含めて独力で設計する', reached_level=3, role='member',
                            team_size=3, complexity='1', explanation='帳票20本の出力設計を、画面項目との対応を含めて独力で設計した。',
                            **self.approved(3, 'B', confirmed_by=me, days=28))

    # ---- 現場2: 保険金請求の基本設計（自分はメンバー。現場は12週目、自分は入場から6日） ----

    def seed_claims(self, me, tanaka, ito, watanabe, yamamoto):
        started = self.today - timedelta(weeks=12)
        site = Site.objects.create(
            name='保険金請求 基本設計', repo_name=f'{DEMO_PREFIX}claims', industry='保険業',
            description='保険金請求の受付から支払までの業務フローと画面の基本設計。', started_on=started,
        )
        others = (ito, watanabe, yamamoto)
        self.join(site, tanaka, Membership.ROLE_LEADER, duty='PM')
        for user, duty in zip(others, ('交渉・要件調整', '画面設計', '帳票設計')):
            self.join(site, user, duty=duty)
        # 自分だけ後から入場した（危険信号は入場日から数えるので、1週目の途中の筋書きになる）
        joined = self.today - timedelta(days=6)
        self.join(site, me, duty='業務フロー設計', joined_on=joined)
        for user in (tanaka, *others):
            for stage in kit.STAGE_LABELS:
                self.check_items(site, user, stage)
        self.check_items(site, me, 'pre')
        self.check_items(site, me, 'day1')
        self.check_items(site, me, 'week1', sections=['全体像'], items=2)
        self.check_items(site, me, 'week1', sections=['読めない'], items=1)
        Goal.objects.create(
            site=site, author=me, skill_item='デザイン', concrete_skill='画面・入力チェック設計', current_level=3,
            target_text='画面標準・データモデル・IF方針をチームに示し、レビューで整合させる',
            preparation='保険金請求の業務と画面標準（入力チェックの共通化）を入場前に読む。', created_at=self.ago(8))

        Question.objects.create(site=site, author=me, title='査定の二段階承認は全件か', ask_to='査定部', created_at=self.ago(2))
        Question.objects.create(site=site, author=me, unreadable=True, title='支払業務説明書の「免責判定」の前提が分からない',
                                source='支払業務説明書', location='3章 免責判定フロー', reason='premise', ask_to='査定部 担当',
                                created_at=self.ago(3))
        Question.objects.create(site=site, author=me, unreadable=True, title='事故受付画面の遷移図がない',
                                source='画面一覧', location='事故受付〜請求受付', reason='figure', created_at=self.ago(1))
        Term.objects.create(site=site, author=me, term='事故受付', meaning='事故の連絡を受けた時点。請求書の受付とは別。')
        Term.objects.create(site=site, author=me, term='免責', meaning='')
        Term.objects.create(site=site, author=me, term='締め', meaning='支払の締切。毎週金曜。')
        Term.objects.create(site=site, author=yamamoto, term='支払査定', meaning='請求内容と約款を照らして、支払額を決めること。',
                            **self.published(tanaka, '支払査定', '請求内容と約款を照らして、支払額を決めること。'))
        Document.objects.create(site=site, author=tanaka, kind=Document.KIND_STRUCTURE, title='体制と会議体',
                                body='定例: 毎週火曜 10:00\n顧客側窓口: 査定部\n決裁者: 査定部長')
        Document.objects.create(site=site, author=me, kind=Document.KIND_PRE, title='参画前面談メモ',
                                body='契約形態：準委任。指示は元請PMから。\n担当工程：基本設計（業務フロー）\n期待値：1週目で現状把握')
        # 入場した週の週報（曜日によらず「前週の週報がない」にならないよう、入場週に出す）
        joined_week = (week_start(self.today) - week_start(joined)).days // 7
        self.weekly(site, me, joined_week, done='入場。体制と資料の置き場所を確認した。',
                    learned='決裁者は査定部長。日々の指示は元請PMから。',
                    stuck='支払業務説明書の免責判定が読めていない。', next_step='まとめを5日目に出す。')
        Summary.objects.create(site=site, author=me, overview='保険金の請求受付から支払まで。利用者は査定部と支払担当。')
        # 前から入っているメンバーの週報（チームの週次共有に並ぶ。demo_yamamoto は先週出していない）
        for user in others:
            for n in range(0, 4):
                if user == yamamoto and n == 1:
                    continue
                self.weekly(site, user, n, done='担当範囲の設計を進めた。', next_step='レビュー指摘を反映する。')
        # リーダーの懸念：メンバーにはフィードバックとして見える
        Question.objects.create(site=site, author=tanaka, kind=Question.KIND_PROBLEM, title='査定部の窓口が一人に集中している',
                                created_at=self.ago(2), fact='確認事項の回答がすべて査定部の担当者1人に集まっている。',
                                interpretation='担当者の都合で回答が遅れやすい。',
                                unconfirmed='査定部に代わりの窓口を立てられるか。',
                                impact='業務フローの確認が止まると、基本設計の初版が遅れる。')
        # スコープ外・約束と実際・体制のズレ（マネジメントの運営指標に出る）
        for title, days in (('旧システムのデータ移行', 40), ('操作マニュアル作成', 10)):
            Question.objects.create(site=site, author=ito, kind=Question.KIND_TASK, title=title, scope_out=True,
                                    created_at=self.ago(days))
        PromiseCheck.objects.create(site=site, author=ito, promise='画面と業務フローの設計のみ', actual='移行・マニュアルまで依頼',
                                    is_gap=True)
        StructureGap.objects.create(site=site, author=ito, role_kind='approver', on_chart='査定部長',
                                    actual='元請のPM', note='仕様の決裁を元請PMが代わりに出している。', created_at=self.ago(60))
        # 一般化して公開した振り返り（会社のナレッジ資産）と、そこから足した質問リスト
        retro = Document.objects.create(site=site, author=ito, kind=Document.KIND_RETRO, title='振り返り：スコープ外作業が増えた理由',
                                        body='約束: 画面と業務フローの設計のみ\n実際: 移行・マニュアルまで依頼された\n'
                                             '次に面談で確認すること: 周辺作業の範囲',
                                        skill_item='ネゴシエーション：方針と責任範囲の合意', created_at=self.ago(5),
                                        **self.published(tanaka, 'スコープ外の作業が増えた理由（保険系の基本設計）',
                                                         '約束: 画面と業務フローの設計のみ\n実際: データ移行・操作マニュアルまで依頼された\n'
                                                         '次に面談で確かめること: 周辺作業（移行・マニュアル）の範囲と、誰が決めるか'))
        InterviewQuestion.objects.create(question='データ移行・マニュアル作成は作業範囲に入るか。入らないなら誰が担うか',
                                         industry='保険業', source=retro, author=ito)
        InterviewQuestion.objects.create(question='仕様の決裁者は誰か。日々の指示は誰から来るか（体制図と同じか）',
                                         author=tanaka)
        KitProposal.objects.create(target='checklist', title='入場前に「周辺作業の範囲」を確かめる項目を足す',
                                   proposal='「契約・商流」に「データ移行・マニュアル作成が作業範囲に入るか」を足す',
                                   reason='周辺作業がスコープ外のまま依頼され続けた', author=ito, created_at=self.ago(4))
        KitProposal.objects.create(target='phrases', title='直接の依頼を元請に戻す一言',
                                   proposal='「ご依頼は承りました。範囲の確認のため、元請のPMを通していただけますか。」',
                                   reason='顧客の担当者から直接作業を頼まれることが続いた', author=ito, status='adopted',
                                   decided_by=me, created_at=self.ago(20))

        # 到達：承認済み（台帳に載る）・承認待ち（マネジメントの画面に届く）
        # リーダー本人の到達は、確認できる他のリーダーがいないので、マネジメントが確認も行った
        Goal.objects.create(site=site, author=tanaka, skill_item='プロジェクトマネジメント', current_level=4,
                            target_text='10人以上の開発チームのコスト・スケジュール・リスクを管理し、他を指導する', reached_level=4,
                            role='leader', team_size=6, complexity='1,4,5',
                            explanation='6人の設計チームのスケジュールと課題を管理した（レベル5には規模が届かない）。',
                            **self.approved(4, 'A', confirmed_by=me, days=38))
        Goal.objects.create(site=site, author=ito, skill_item='ネゴシエーション', current_level=3,
                            target_text='チームを代表して仕様変更・責任範囲を交渉する', reached_level=4, role='leader',
                            team_size=6, complexity='3,8', explanation='スコープ外の依頼について、元請と範囲を交渉した。',
                            next_text='複数社にまたがる仕様変更と責任範囲の交渉を主導する',
                            **self.approved(4, 'A', confirmed_by=tanaka, days=9,
                                            comment='範囲の交渉を根拠を示して進めた。'))
        Goal.objects.create(site=site, author=yamamoto, skill_item='デザイン', current_level=2,
                            target_text='画面・帳票・テーブル・IFを、影響範囲を含めて独力で設計する', reached_level=3,
                            role='member', team_size=6, complexity='1',
                            explanation='支払通知書など帳票12本を、画面項目との対応を含めて独力で設計した。',
                            next_text='画面標準・データモデル・IF方針をチームに示し、レビューで整合させる',
                            **self.approved(3, 'B', confirmed_by=tanaka, days=14))
        Goal.objects.create(site=site, author=watanabe, skill_item='業務分析', current_level=2,
                            target_text='顧客ヒアリングや現行システム解析で、担当範囲の要件を自分で確定させる', reached_level=3,
                            role='member', team_size=6,
                            explanation='査定部へのヒアリングで、事故受付画面の要件を確定した。',
                            submitted_at=self.ago(3), confirmed_at=self.ago(2), confirmed_by=tanaka,
                            confirm_comment='ヒアリングの記録と画面要件の対応を確かめた。')

    # ---- ひとり現場1: 物流の出荷バッチ改修（demo_takahashi。チーム1の一人） ----

    def seed_logistics(self, takahashi):
        started = self.today - timedelta(weeks=8)
        site = Site.objects.create(
            name='出荷バッチ改修', repo_name=f'{DEMO_PREFIX}logistics', industry='物流業',
            description='倉庫の出荷指示バッチを、締め時刻の変更に合わせて改修する。', started_on=started,
        )
        # ひとり現場：本人だけがメンバーとして入る（自社のリーダーはいない）
        self.join(site, takahashi, duty='バッチ設計')
        for stage in kit.STAGE_LABELS:
            self.check_items(site, takahashi, stage)
        for n in range(0, 6):
            self.weekly(site, takahashi, n, done='出荷指示バッチの処理順を整理した。', next_step='締め時刻の変更の影響を確かめる。')
        Question.objects.create(site=site, author=takahashi, title='夜間バッチと締め処理の実行順', ask_to='元請PM',
                                created_at=self.ago(5))
        Document.objects.create(site=site, author=takahashi, kind=Document.KIND_DESIGN, title='締め時刻の変更は設定値で持つ',
                                body='案: プログラムに埋め込む／設定値で持つ\n決定: 設定値\n理由: 倉庫ごとに締め時刻が違うため。',
                                created_at=self.ago(20))
        # ひとり現場の到達：マネジメントが元請・顧客の声を聞き取って、確認と承認を行った
        Goal.objects.create(site=site, author=takahashi, skill_item='ソフトウェアエンジニアリング', current_level=2,
                            target_text='担当機能の影響範囲を自分で分析し、設計に反映する', reached_level=3,
                            role='member', team_size=1, complexity='2',
                            explanation='締め時刻の変更が出荷指示・在庫引当・帳票に及ぶ範囲を洗い出し、元請PMに説明して改修範囲を合意した。',
                            next_text='方式の選択肢を比べ、チームに設計方針として示す',
                            **self.approved(3, 'B', confirmed_by=self.me, days=12,
                                            customer_voice='元請PM：影響範囲の説明が具体的で、改修範囲をすぐ決められた。'))

    # ---- ひとり現場2: 店舗在庫の可視化（demo_nakamura。チーム2の一人） ----

    def seed_stock(self, nakamura):
        started = self.today - timedelta(weeks=20)
        site = Site.objects.create(
            name='店舗在庫の可視化', repo_name=f'{DEMO_PREFIX}stock', industry='小売業', started_on=started,
        )
        # ひとり現場：本人だけがメンバーとして入る（自社のリーダーはいない）
        self.join(site, nakamura, duty='可視化画面の設計')
        for stage in kit.STAGE_LABELS:
            self.check_items(site, nakamura, stage)
        for title, days in (('旧POSのデータ移行', 40), ('店舗端末の手配', 30)):
            Question.objects.create(site=site, author=nakamura, kind=Question.KIND_TASK, title=title, scope_out=True,
                                    created_at=self.ago(days))
        StructureGap.objects.create(site=site, author=nakamura, role_kind='instructor', on_chart='元請のPM',
                                    actual='顧客の店舗担当者', note='店舗担当者から直接作業を頼まれる。', created_at=self.ago(30))
        self.weekly(site, nakamura, 0, done='可視化画面のレビュー。', stuck='移行の範囲が決まらない。')
        Term.objects.create(site=site, author=nakamura, term='理論在庫', meaning='入出荷の記録から計算した在庫。棚卸しの実在庫と区別する。')
        # 確認を依頼したところ。確認するリーダーがいないので、マネジメントの承認待ちに「確認して承認する」で届く
        Goal.objects.create(site=site, author=nakamura, skill_item='業務分析', current_level=2,
                            target_text='顧客ヒアリングや現行システム解析で、担当範囲の要件を自分で確定させる', reached_level=3,
                            role='member', team_size=1,
                            explanation='店舗担当者へのヒアリングで在庫の要件を確定した。元請PMに要件一覧を説明し、範囲を合意した。',
                            submitted_at=self.ago(3))
