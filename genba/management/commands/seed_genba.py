"""genba のデモデータを入れる。

    python manage.py seed_genba --user <ユーザー名>

指定したユーザーを「受発注」現場のリーダー、「保険金請求」現場のメンバーにし、
マネジメント・会社のグループにも入れて、4つの立場の画面をひとりで見られるようにする。
デモの現場（リポジトリ名が genba-demo- で始まるもの）は毎回作り直す。

筋書き：
- 受発注（リーダー）：入場から10週。チェックリストはほぼ済み、まとめはリーダーと確認済み。
  目標（プロジェクトマネジメント レベル3→4）の到達を説明し、課長（demo_kacho）と合意済み。
  demo_sato からの合意の依頼が、マネジメントの画面に届いている
- 保険金請求（メンバー）：入場から6日。1週目の途中で、3日止まっている「読めない」があり、
  まとめはまだ出していない → 危険信号が2つ出る。目標（デザイン レベル3→4）を立てている
- 店舗在庫（入っていない）：マネジメント・会社の画面でだけ見える
"""
from datetime import timedelta

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import CustomUser
from genba import kit
from genba.models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_REVIEWED, VISIBILITY_COMPANY,
    Document, Goal, GoalEvidence, Membership, PromiseCheck, Question, Site, Summary, Term, week_start,
)
from genba.roles import GROUP_COMPANY, GROUP_MANAGEMENT

DEMO_PREFIX = 'genba-demo-'
DEMO_USERS = ['demo_sato', 'demo_suzuki', 'demo_tanaka', 'demo_ito', 'demo_kacho']

# チェックリストの段階ごとの予定日（入場日からの日数）
STAGE_DUE = {'pre': -1, 'day1': 0, 'week1': 4, 'week2': 14}


class Command(BaseCommand):
    help = 'genba のデモデータ（3現場）を作り直す'

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='4つの立場を割り当てるユーザー名')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            me = CustomUser.objects.get(username=options['user'])
        except CustomUser.DoesNotExist:
            raise CommandError(f'ユーザー {options["user"]} がいません')

        Site.objects.filter(repo_name__startswith=DEMO_PREFIX).delete()
        users = {}
        for name in DEMO_USERS:
            u, created = CustomUser.objects.get_or_create(username=name, defaults={'email': f'{name}@example.com'})
            if created:
                u.set_unusable_password()
                u.save()
            users[name] = u
        sato, suzuki, tanaka, ito, self.kacho = (users[n] for n in DEMO_USERS)
        for g in (GROUP_MANAGEMENT, GROUP_COMPANY):
            me.groups.add(Group.objects.get_or_create(name=g)[0])
        self.kacho.groups.add(Group.objects.get(name=GROUP_MANAGEMENT))

        self.now = timezone.now()
        self.today = timezone.localdate()
        self.seed_order(me, sato, suzuki)
        self.seed_claims(me, tanaka)
        self.seed_stock(ito)
        self.stdout.write(self.style.SUCCESS(
            f'デモの現場を3つ作りました。{me.username} でログインして /genba/ を開いてください。'))

    # ---- 共通 ----

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
                            general_difference='一般には倉庫から出した時点。', visibility=VISIBILITY_COMPANY,
                            knowledge_status=KNOWLEDGE_REVIEWED)
        Term.objects.create(site=site, author=suzuki, term='赤黒', meaning='確定済み伝票を、マイナス伝票と正しい伝票の2枚で訂正すること。',
                            knowledge_status=KNOWLEDGE_CANDIDATE)
        Term.objects.create(site=site, author=me, term='締め', meaning='得意先ごとの請求締め日。月末とは限らない。',
                            general_difference='社内では「月次締め」を指すことが多い。', visibility=VISIBILITY_COMPANY,
                            knowledge_status=KNOWLEDGE_REVIEWED)
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
                 visibility=VISIBILITY_COMPANY, knowledge_status=KNOWLEDGE_REVIEWED, created_at=self.ago(25))
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
        Summary.objects.create(site=site, author=sato, submitted_at=self.ago(65),
                               overview='受注〜請求の基幹業務を刷新。利用者は業務部と倉庫。業務フローは 03_業務理解 を参照。',
                               functions='担当：受注画面。入力＝受注明細、処理＝引当、出力＝出荷指示。つながる機能：在庫引当・請求。',
                               review_note='担当範囲は受注・訂正まで。期待値との差は帳票の理解。2週目は帳票一覧を読む。')
        Summary.objects.create(site=site, author=me, submitted_at=self.ago(65),
                               overview='受注〜請求の基幹業務を刷新。帳票とバッチの全体像は 03_業務理解 を参照。',
                               functions='担当：設計全体のリード。受注・帳票・バッチのつながりを整理。',
                               review_note='PMと確認。期待値との差は帳票本数の把握。2週目は帳票の棚卸し範囲を契約と照らす。')
        PromiseCheck.objects.create(site=site, author=me, promise='要件は確定済み', actual='確定後の取消など業務ルールが未定', is_gap=True)
        PromiseCheck.objects.create(site=site, author=me, promise='帳票は20本', actual='棚卸しで35本見つかった', is_gap=True)
        PromiseCheck.objects.create(site=site, author=me, promise='レビューは週1回', actual='週1回で回っている')
        goal = Goal.objects.create(
            site=site, author=me, skill_item='プロジェクトマネジメント', concrete_skill='担当機能の設計スケジュール管理',
            current_level=3, target_text='3人以上の設計チームのスケジュール・課題・リスクを管理する',
            preparation='マスタースケジュールの読み方と、課題管理表の運用を確認しておく。', created_at=self.ago(71),
            reached_level=4, mentored=True, role='leader', team_size=3, complexity='1,4',
            explanation='3人の設計チームの課題とリスクを週次で棚卸しし、レビュー計画に組み込んだ。仕様書が最新でない課題は、影響と見込みを添えて上げた。',
            agreement_requested_at=self.ago(4), agreed_on=self.today - timedelta(days=2), agreed_by=self.kacho,
            agreement_comment='チームの課題管理が回っていた。次はレベル4の「デザイン」も視野に。')
        problem = site.questions.get(title='旧システムの仕様書が最新でない')
        GoalEvidence.objects.create(goal=goal, record_type='issue', record_id=problem.pk, title=f'課題：{problem.title}')
        sato_goal = Goal.objects.create(
            site=site, author=sato, skill_item='業務システム構築', concrete_skill='業務フローの把握と可視化',
            current_level=2, target_text='担当機能の業務フローを自分で追い、業務ルールと例外を漏れなく設計する',
            created_at=self.ago(70), reached_level=3, role='member', team_size=3,
            explanation='受注から請求までの業務フローを自分で追って資料にし、受注画面の業務ルールと例外を設計した。',
            agreement_requested_at=self.ago(1))
        flow = site.documents.get(title='受注から請求までの業務フロー')
        GoalEvidence.objects.create(goal=sato_goal, record_type='doc', record_id=flow.pk, title=f'03_業務理解：{flow.title}')

    # ---- 現場2: 保険金請求の基本設計（自分はメンバー。入場から6日） ----

    def seed_claims(self, me, tanaka):
        started = self.today - timedelta(days=6)
        site = Site.objects.create(
            name='保険金請求 基本設計', repo_name=f'{DEMO_PREFIX}claims', industry='保険業',
            description='保険金請求の受付から支払までの業務フローと画面の基本設計。', started_on=started,
        )
        self.join(site, tanaka, Membership.ROLE_LEADER, duty='PM')
        self.join(site, me, duty='業務フロー設計')
        for stage in kit.STAGE_LABELS:
            self.check_items(site, tanaka, stage)
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
        Document.objects.create(site=site, author=tanaka, kind=Document.KIND_STRUCTURE, title='体制と会議体',
                                body='定例: 毎週火曜 10:00\n顧客側窓口: 査定部\n決裁者: 査定部長')
        Document.objects.create(site=site, author=me, kind=Document.KIND_PRE, title='参画前面談メモ',
                                body='契約形態：準委任。指示は元請PMから。\n担当工程：基本設計（業務フロー）\n期待値：1週目で現状把握')
        # 入場した週の週報（曜日によらず「前週の週報がない」にならないよう、入場週に出す）
        joined_week = (week_start(self.today) - week_start(started)).days // 7
        self.weekly(site, me, joined_week, done='入場。体制と資料の置き場所を確認した。',
                    learned='決裁者は査定部長。日々の指示は元請PMから。',
                    stuck='支払業務説明書の免責判定が読めていない。', next_step='まとめを5日目に出す。')
        Summary.objects.create(site=site, author=me, overview='保険金の請求受付から支払まで。利用者は査定部と支払担当。')

    # ---- 現場3: 店舗在庫の可視化（自分は入っていない） ----

    def seed_stock(self, ito):
        started = self.today - timedelta(weeks=20)
        site = Site.objects.create(
            name='店舗在庫の可視化', repo_name=f'{DEMO_PREFIX}stock', industry='小売業', started_on=started,
        )
        self.join(site, ito, Membership.ROLE_LEADER)
        for stage in kit.STAGE_LABELS:
            self.check_items(site, ito, stage)
        for title, days in (('旧POSのデータ移行', 40), ('店舗端末の手配', 30), ('操作マニュアル作成', 10)):
            Question.objects.create(site=site, author=ito, kind=Question.KIND_TASK, title=title, scope_out=True,
                                    created_at=self.ago(days))
        Term.objects.create(site=site, author=ito, term='理論在庫', meaning='入出荷の記録から計算した在庫。棚卸しの実在庫と区別する。',
                            visibility=VISIBILITY_COMPANY, knowledge_status=KNOWLEDGE_REVIEWED)
        Document.objects.create(site=site, author=ito, kind=Document.KIND_RETRO, title='振り返り：スコープ外作業が増えた理由',
                                body='約束: 可視化画面のみ\n実際: 移行・端末手配・マニュアルまで依頼された\n次に面談で確認すること: 周辺作業の範囲',
                                skill_item='ネゴシエーション：方針と責任範囲の合意', visibility=VISIBILITY_COMPANY,
                                knowledge_status=KNOWLEDGE_REVIEWED, created_at=self.ago(5))
        self.weekly(site, ito, 7, done='可視化画面のレビュー。')
        PromiseCheck.objects.create(site=site, author=ito, promise='可視化画面の設計のみ', actual='移行・端末手配まで依頼', is_gap=True)
