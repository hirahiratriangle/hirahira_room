"""genba のデモデータを入れる。

    python manage.py seed_genba --user <ユーザー名>

指定したユーザーを「受発注」現場のリーダー、「保険金請求」現場のメンバーにし、
マネジメント・会社のグループにも入れて、4つの立場の画面をひとりで見られるようにする。
デモの現場（リポジトリ名が genba-demo- で始まるもの）は毎回作り直す。
"""
from datetime import timedelta

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import CustomUser
from genba.models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_REVIEWED, VISIBILITY_COMPANY,
    Document, Membership, PromiseCheck, Question, Site, Term, week_start,
)
from genba.roles import GROUP_COMPANY, GROUP_MANAGEMENT

DEMO_PREFIX = 'genba-demo-'
DEMO_USERS = ['demo_sato', 'demo_suzuki', 'demo_tanaka', 'demo_ito']


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
        sato, suzuki, tanaka, ito = (users[n] for n in DEMO_USERS)
        for g in (GROUP_MANAGEMENT, GROUP_COMPANY):
            me.groups.add(Group.objects.get_or_create(name=g)[0])

        now = timezone.now()
        today = timezone.localdate()

        def ago(days):
            return now - timedelta(days=days)

        def week(n):
            return week_start(today) - timedelta(weeks=n)

        # ---- 現場1: 受発注システム刷新（自分はリーダー） ----
        order = Site.objects.create(
            name='受発注システム刷新', repo_name=f'{DEMO_PREFIX}order', industry='製造業',
            description='既存の受発注システムを刷新する基本設計フェーズ。', started_on=today - timedelta(weeks=10),
        )
        Membership.objects.create(site=order, user=me, role=Membership.ROLE_LEADER, duty='基本設計リード')
        Membership.objects.create(site=order, user=sato, duty='画面設計')
        Membership.objects.create(site=order, user=suzuki, duty='帳票・バッチ設計')

        Question.objects.create(site=order, author=sato, title='「確定」ボタン押下後の取消可否', ask_to='業務部 課長',
                                body='確定後に取り消せるのか、赤黒で訂正するのか未定。画面遷移に影響する。', created_at=ago(12))
        Question.objects.create(site=order, author=sato, title='得意先マスタの更新権限は誰が持つか', ask_to='情シス', created_at=ago(4))
        Question.objects.create(site=order, author=suzuki, title='月末締めバッチの実行時刻', ask_to='運用チーム', created_at=ago(9))
        Question.objects.create(site=order, author=suzuki, kind=Question.KIND_TASK, title='既存帳票の棚卸し（追加依頼）',
                                scope_out=True, created_at=ago(6))
        Question.objects.create(site=order, author=me, kind=Question.KIND_PROBLEM, title='旧システムの仕様書が最新でない', created_at=ago(15))
        Question.objects.create(site=order, author=sato, title='消費税の端数処理', ask_to='経理', created_at=ago(20),
                                is_open=False, closed_at=ago(17))

        Term.objects.create(site=order, author=sato, term='出荷', meaning='倉庫から出た時点ではなく、得意先が受領した時点を指す。',
                            general_difference='一般には倉庫から出した時点。', visibility=VISIBILITY_COMPANY,
                            knowledge_status=KNOWLEDGE_REVIEWED)
        Term.objects.create(site=order, author=suzuki, term='赤黒', meaning='確定済み伝票を、マイナス伝票と正しい伝票の2枚で訂正すること。',
                            knowledge_status=KNOWLEDGE_CANDIDATE)
        Term.objects.create(site=order, author=me, term='締め', meaning='得意先ごとの請求締め日。月末とは限らない。',
                            general_difference='社内では「月次締め」を指すことが多い。')

        Document.objects.create(site=order, author=me, kind=Document.KIND_SCOPE, title='作業範囲と約束事項',
                                body='基本設計（画面・帳票・バッチ）まで。詳細設計以降は別契約。\nレビューは週1回、木曜。')
        Document.objects.create(site=order, author=sato, kind=Document.KIND_MINUTES, title='第8回 設計レビュー',
                                body='・受注画面の項目並びを合意\n・確定後の取消は次回までに業務部が回答', created_at=ago(3))
        Document.objects.create(site=order, author=suzuki, kind=Document.KIND_MINUTES, title='第7回 設計レビュー',
                                body='・帳票の出力形式はPDFで統一', created_at=ago(10))
        Document.objects.create(site=order, author=me, kind=Document.KIND_DESIGN, title='伝票訂正は赤黒方式にする',
                                body='案: 直接更新／赤黒\n決定: 赤黒\n理由: 監査で訂正履歴を追えることが求められたため。',
                                visibility=VISIBILITY_COMPANY, knowledge_status=KNOWLEDGE_REVIEWED, created_at=ago(25))
        Document.objects.create(site=order, author=sato, kind=Document.KIND_INTERVIEW, title='「出荷」の意味の確認',
                                body='業務部は「出荷」を得意先受領の意味で使っている。画面ラベルを「納品」に寄せる案を相談中。',
                                knowledge_status=KNOWLEDGE_CANDIDATE, created_at=ago(30))
        for n in range(0, 10):
            if n in (2, 5):
                continue  # 出していない週
            Document.objects.create(
                site=order, author=me, kind=Document.KIND_WEEKLY, title=f'週報 {week(n):%m/%d}', week_of=week(n),
                body='今週の進捗は予定どおり。',
                risks='確定後の取消可否の回答待ちで、受注画面の遷移が決められない。' if n <= 1 else '',
            )
        PromiseCheck.objects.create(site=order, author=me, promise='要件は確定済み', actual='確定後の取消など業務ルールが未定', is_gap=True)
        PromiseCheck.objects.create(site=order, author=me, promise='帳票は20本', actual='棚卸しで35本見つかった', is_gap=True)
        PromiseCheck.objects.create(site=order, author=me, promise='レビューは週1回', actual='週1回で回っている')

        # ---- 現場2: 保険金請求の基本設計（自分はメンバー） ----
        claims = Site.objects.create(
            name='保険金請求 基本設計', repo_name=f'{DEMO_PREFIX}claims', industry='保険業',
            description='保険金請求の受付から支払までの業務フローと画面の基本設計。', started_on=today - timedelta(weeks=6),
        )
        Membership.objects.create(site=claims, user=tanaka, role=Membership.ROLE_LEADER, duty='PM')
        Membership.objects.create(site=claims, user=me, duty='業務フロー設計')
        Question.objects.create(site=claims, author=me, title='査定の二段階承認は全件か', ask_to='査定部', created_at=ago(2))
        Term.objects.create(site=claims, author=me, term='事故受付', meaning='事故の連絡を受けた時点。請求書の受付とは別。')
        Document.objects.create(site=claims, author=tanaka, kind=Document.KIND_STRUCTURE, title='体制と会議体',
                                body='定例: 毎週火曜 10:00\n顧客側窓口: 査定部')
        for n in range(0, 6):
            Document.objects.create(site=claims, author=tanaka, kind=Document.KIND_WEEKLY,
                                    title=f'週報 {week(n):%m/%d}', week_of=week(n), body='予定どおり。')

        # ---- 現場3: 店舗在庫の可視化（自分は入っていない。マネジメント・会社の画面でだけ見える） ----
        stock = Site.objects.create(
            name='店舗在庫の可視化', repo_name=f'{DEMO_PREFIX}stock', industry='小売業',
            started_on=today - timedelta(weeks=20),
        )
        Membership.objects.create(site=stock, user=ito, role=Membership.ROLE_LEADER)
        Question.objects.create(site=stock, author=ito, kind=Question.KIND_TASK, title='旧POSのデータ移行', scope_out=True, created_at=ago(40))
        Question.objects.create(site=stock, author=ito, kind=Question.KIND_TASK, title='店舗端末の手配', scope_out=True, created_at=ago(30))
        Question.objects.create(site=stock, author=ito, kind=Question.KIND_TASK, title='操作マニュアル作成', scope_out=True, created_at=ago(10))
        Term.objects.create(site=stock, author=ito, term='理論在庫', meaning='入出荷の記録から計算した在庫。棚卸しの実在庫と区別する。',
                            visibility=VISIBILITY_COMPANY, knowledge_status=KNOWLEDGE_REVIEWED)
        Document.objects.create(site=stock, author=ito, kind=Document.KIND_RETRO, title='振り返り：スコープ外作業が増えた理由',
                                body='約束: 可視化画面のみ\n実際: 移行・端末手配・マニュアルまで依頼された\n次に面談で確認すること: 周辺作業の範囲',
                                skill_item='要件定義：作業範囲の合意', visibility=VISIBILITY_COMPANY,
                                knowledge_status=KNOWLEDGE_REVIEWED, created_at=ago(5))
        Document.objects.create(site=stock, author=ito, kind=Document.KIND_WEEKLY, title='週報', week_of=week(7))
        PromiseCheck.objects.create(site=stock, author=ito, promise='可視化画面の設計のみ', actual='移行・端末手配まで依頼', is_gap=True)

        self.stdout.write(self.style.SUCCESS(
            f'デモの現場を3つ作りました。{me.username} でログインして /genba/ を開いてください。'))
