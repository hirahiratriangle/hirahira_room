from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser

from . import kit, kit_data
from .metrics import site_metrics, weekly_rate
from .models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_NONE, KNOWLEDGE_REVIEWED, VISIBILITY_COMPANY, VISIBILITY_TEAM,
    ChecklistItem, Document, Membership, PromiseCheck, Question, Site, Summary, Term, week_start,
)
from .roles import GROUP_COMPANY, GROUP_MANAGEMENT
from .signals import signals_for


class GenbaTestBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.member = CustomUser.objects.create_user('member', 'm@example.com', 'pass12345')
        self.leader = CustomUser.objects.create_user('leader', 'l@example.com', 'pass12345')
        self.outsider = CustomUser.objects.create_user('outsider', 'o@example.com', 'pass12345')
        self.site = self.make_site('現場A', 'genba-a', weeks=4)

    def make_site(self, name, repo, weeks=4, industry='製造業'):
        started = self.today - timedelta(weeks=weeks)
        site = Site.objects.create(name=name, repo_name=repo, industry=industry, started_on=started)
        Membership.objects.create(site=site, user=self.member, joined_on=started)
        Membership.objects.create(site=site, user=self.leader, role=Membership.ROLE_LEADER, joined_on=started)
        return site

    def add_group(self, user, name):
        user.groups.add(Group.objects.get_or_create(name=name)[0])

    def signal(self, user, site, sign):
        return next(s for s in signals_for(user, site) if s.sign == sign)


class KitTests(TestCase):
    def test_kit_data_matches_sources(self):
        stages = kit.checklist()
        self.assertEqual([s['key'] for s in stages], ['pre', 'day1', 'week1', 'week2'])
        self.assertEqual(sum(len(s['sections']) for s in stages), 12)
        self.assertEqual(sum(len(sec['items']) for s in stages for sec in s['sections']), 45)  # 44＋目標
        self.assertEqual(len(kit_data.DANGER_SIGNS), 7)
        self.assertEqual(len(kit_data.PHRASES), 8)
        self.assertEqual(sum(len(c['words']) for c in kit_data.WORD_CATEGORIES), 30)
        self.assertEqual(len(kit.DOC_SECTIONS), 8)

    def test_no_customer_names_in_template(self):
        text = repr([kit_data.CHECKLIST_INTRO, kit_data.CHECKLIST_STAGES])
        self.assertNotIn('SMBC', text)
        self.assertIn('前回の現場の教訓', text)

    def test_every_detected_sign_and_target_exists(self):
        signs = {s['sign'] for s in kit_data.DANGER_SIGNS}
        self.assertTrue(set(kit.DETECTED_SIGNS) <= signs)
        self.assertTrue(set(kit.SIGN_TARGETS) <= signs)


class SiteCreateTests(GenbaTestBase):
    def test_create_site_from_template(self):
        self.client.force_login(self.outsider)
        res = self.client.post(reverse('genba:site_create'), {
            'name': '新現場', 'repo_name': 'genba-new', 'industry': '物流業',
            'started_on': self.today.isoformat(), 'description': '', 'my_role': 'member',
        })
        site = Site.objects.get(repo_name='genba-new')
        self.assertRedirects(res, reverse('genba:site_detail', args=[site.pk]) + '?tab=checklist')
        self.assertTrue(Membership.objects.filter(site=site, user=self.outsider, joined_on=self.today).exists())
        tasks = site.questions.exclude(stage='')
        self.assertEqual(tasks.count(), 12)
        self.assertTrue(all(q.kind == Question.KIND_TASK for q in tasks))
        self.assertEqual(ChecklistItem.objects.filter(question__site=site).count(), 45)
        self.assertEqual(site.template_version, kit.TEMPLATE_VERSION)
        page = self.client.get(reverse('genba:site_detail', args=[site.pk]))
        self.assertContains(page, '［入場前］契約・商流')

    def test_repo_name_must_start_with_genba(self):
        self.client.force_login(self.member)
        res = self.client.post(reverse('genba:site_create'), {
            'name': 'x', 'repo_name': 'other', 'industry': 'x', 'started_on': self.today.isoformat(), 'my_role': 'member',
        })
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Site.objects.filter(repo_name='other').exists())

    def test_checklist_item_closes_issue_when_all_done(self):
        kit.create_checklist(self.site, self.member)
        q = self.site.questions.get(title='［入場前］自社側の支援')
        self.client.force_login(self.member)
        for item in q.items.all():
            self.client.post(reverse('genba:checklist_item_toggle', args=[item.pk]))
        q.refresh_from_db()
        self.assertFalse(q.is_open)
        self.assertEqual(q.done_on, self.today)
        self.client.post(reverse('genba:checklist_item_toggle', args=[q.items.first().pk]))
        q.refresh_from_db()
        self.assertTrue(q.is_open)

    def test_archived_site_is_read_only(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(reverse('genba:site_archive', args=[self.site.pk])).status_code, 403)
        self.client.force_login(self.leader)
        self.client.post(reverse('genba:site_archive', args=[self.site.pk]))
        self.site.refresh_from_db()
        self.assertTrue(self.site.is_archived)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('genba:site_detail', args=[self.site.pk])).status_code, 200)
        res = self.client.post(reverse('genba:term_create', args=[self.site.pk]), {'term': 'x', 'meaning': ''})
        self.assertEqual(res.status_code, 403)


class AccessTests(GenbaTestBase):
    def test_member_can_read_site(self):
        self.client.force_login(self.member)
        for tab in ('checklist', 'issues', 'unreadable', 'progress', 'docs', 'terms', 'promises', 'members'):
            res = self.client.get(reverse('genba:site_detail', args=[self.site.pk]), {'tab': tab})
            self.assertEqual(res.status_code, 200, tab)

    def test_outsider_cannot_read_site(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('genba:site_detail', args=[self.site.pk])).status_code, 403)
        q = Question.objects.create(site=self.site, author=self.member, title='秘密')
        self.assertEqual(self.client.get(reverse('genba:issue_detail', args=[q.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('genba:summary', args=[self.site.pk])).status_code, 403)

    def test_kit_pages_open_to_logged_in_users(self):
        self.client.force_login(self.outsider)
        self.assertContains(self.client.get(reverse('genba:kit')), '入場時チェックリスト上流編')
        res = self.client.get(reverse('genba:phrases'))
        self.assertContains(res, 'SI業界ワード確認表')
        self.assertContains(res, '清濁併せ呑む')

    def test_team_document_hidden_from_outsider_public_one_visible(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='設計')
        self.client.force_login(self.outsider)
        url = reverse('genba:document_detail', args=[doc.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        doc.visibility = VISIBILITY_COMPANY
        doc.save()
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        # 会社公開では顧客（現場）名ではなく業界を出す
        self.assertContains(res, '製造業')
        self.assertNotContains(res, '現場A')

    def test_role_homes_require_role(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('genba:leader_home')).status_code, 403)
        self.assertEqual(self.client.get(reverse('genba:management')).status_code, 403)
        self.assertEqual(self.client.get(reverse('genba:company')).status_code, 403)
        self.client.force_login(self.leader)
        self.assertEqual(self.client.get(reverse('genba:leader_home')).status_code, 200)

    def test_management_sees_aggregates_only(self):
        Question.objects.create(site=self.site, author=self.member, title='秘密の確認事項')
        self.add_group(self.outsider, GROUP_MANAGEMENT)
        self.client.force_login(self.outsider)
        res = self.client.get(reverse('genba:management'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '現場A')
        self.assertNotContains(res, '秘密の確認事項')
        self.assertNotContains(res, 'member')

    def test_company_sees_only_published(self):
        Term.objects.create(site=self.site, author=self.member, term='公開語', meaning='x', visibility=VISIBILITY_COMPANY)
        Term.objects.create(site=self.site, author=self.member, term='非公開語', meaning='y')
        self.add_group(self.outsider, GROUP_COMPANY)
        self.client.force_login(self.outsider)
        res = self.client.get(reverse('genba:company'))
        self.assertContains(res, '公開語')
        self.assertNotContains(res, '非公開語')


class IssueTests(GenbaTestBase):
    def create(self, kind, data):
        self.client.force_login(self.member)
        return self.client.post(reverse('genba:issue_create', args=[self.site.pk, kind]), data)

    def test_question_create_and_close(self):
        self.create('question', {'title': '締め日は？', 'ask_to': '経理'})
        q = Question.objects.get(title='締め日は？')
        self.assertEqual((q.kind, q.site, q.author, q.is_open), (Question.KIND_QUESTION, self.site, self.member, True))
        self.client.post(reverse('genba:question_toggle', args=[q.pk]))
        q.refresh_from_db()
        self.assertFalse(q.is_open)
        self.assertIsNotNone(q.closed_at)

    def test_unreadable_requires_source_and_reason(self):
        res = self.create('unreadable', {'title': '読めない'})
        self.assertEqual(res.status_code, 200)
        self.create('unreadable', {'title': '読めない', 'source': '仕様書', 'location': '3章', 'reason': 'premise'})
        q = Question.objects.get(title='読めない')
        self.assertTrue(q.unreadable)
        self.assertEqual(q.get_reason_display(), '前提')

    def test_problem_has_concern_fields(self):
        self.create('problem', {'title': '懸念', 'fact': '事実', 'interpretation': '解釈',
                                'unconfirmed': '未確認', 'impact': '影響'})
        q = Question.objects.get(title='懸念')
        self.assertEqual((q.kind, q.fact, q.impact), (Question.KIND_PROBLEM, '事実', '影響'))
        res = self.client.get(reverse('genba:issue_detail', args=[q.pk]))
        self.assertContains(res, '自分の解釈')

    def test_scope_out_is_task_with_label(self):
        self.create('scope_out', {'title': '追加の棚卸し', 'body': '', 'ask_to': '課長'})
        q = Question.objects.get(title='追加の棚卸し')
        self.assertEqual((q.kind, q.scope_out), (Question.KIND_TASK, True))
        self.assertEqual(site_metrics(self.site).scope_out, 1)

    def test_task_progress_and_lateness(self):
        self.create('task', {'title': '画面設計', 'due_on': (self.today - timedelta(days=2)).isoformat()})
        q = Question.objects.get(title='画面設計')
        self.assertTrue(q.is_late)
        self.client.post(reverse('genba:issue_detail', args=[q.pk]), {
            'due_on': q.due_on.isoformat(), 'done_on': q.due_on.isoformat(), 'delay_note': ''})
        q.refresh_from_db()
        self.assertFalse(q.is_late)
        res = self.client.get(reverse('genba:site_detail', args=[self.site.pk]), {'tab': 'progress'})
        self.assertContains(res, '画面設計')

    def test_outsider_cannot_add(self):
        self.client.force_login(self.outsider)
        res = self.client.post(reverse('genba:term_create', args=[self.site.pk]), {'term': 'x', 'meaning': 'y'})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Term.objects.exists())


class RecordTests(GenbaTestBase):
    def test_term_without_meaning(self):
        self.client.force_login(self.member)
        self.client.post(reverse('genba:term_create', args=[self.site.pk]), {'term': '免責', 'meaning': ''})
        self.assertTrue(Term.objects.filter(term='免責', meaning='').exists())
        res = self.client.get(reverse('genba:site_detail', args=[self.site.pk]), {'tab': 'terms'})
        self.assertContains(res, '意味を確認中')

    def test_same_term_in_other_sites_only_if_published(self):
        other = self.make_site('現場B', 'genba-b', industry='保険業')
        Term.objects.create(site=self.site, author=self.member, term='締め', meaning='請求締め')
        hidden = Term.objects.create(site=other, author=self.member, term='締め', meaning='非公開の意味')
        self.client.force_login(self.member)
        url = reverse('genba:site_detail', args=[self.site.pk])
        self.assertNotContains(self.client.get(url, {'tab': 'terms'}), '非公開の意味')
        hidden.visibility = VISIBILITY_COMPANY
        hidden.save()
        self.assertContains(self.client.get(url, {'tab': 'terms'}), '保険業の現場では：非公開の意味')

    def test_weekly_report_has_four_fields_and_monday(self):
        self.client.force_login(self.member)
        day = week_start(self.today) + timedelta(days=3)
        self.client.post(reverse('genba:document_create', args=[self.site.pk, 'weekly']), {
            'title': '週報', 'week_of': day.isoformat(), 'done': 'やった', 'learned': '分かった',
            'stuck': '詰まった', 'next_step': '次'})
        doc = Document.objects.get(kind=Document.KIND_WEEKLY)
        self.assertEqual(doc.week_of, week_start(self.today))
        self.assertEqual((doc.done, doc.stuck), ('やった', '詰まった'))
        self.client.force_login(self.leader)
        self.assertContains(self.client.get(reverse('genba:leader_home')), '詰まった')

    def test_only_author_can_edit_document(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_MINUTES, title='議事録')
        self.client.force_login(self.leader)
        self.assertEqual(self.client.get(reverse('genba:document_update', args=[doc.pk])).status_code, 403)


class SummaryTests(GenbaTestBase):
    def test_summary_collects_site_records(self):
        other = self.make_site('現場B', 'genba-b')
        Term.objects.create(site=self.site, author=self.leader, term='出荷', meaning='受領時点')
        Question.objects.create(site=self.site, author=self.leader, title='未解決の質問A')
        Question.objects.create(site=self.site, author=self.member, title='読めないA', unreadable=True, source='仕様書A')
        Term.objects.create(site=other, author=self.member, term='他現場の語', meaning='x')
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:summary', args=[self.site.pk]))
        self.assertContains(res, '出荷')
        self.assertContains(res, '未解決の質問A')
        self.assertContains(res, '仕様書A')
        self.assertNotContains(res, '他現場の語')

    def test_submit_and_review(self):
        self.client.force_login(self.member)
        url = reverse('genba:summary', args=[self.site.pk])
        self.client.post(url, {'action': 'submit', 'overview': '全体像', 'functions': '担当'})
        s = Summary.objects.get(site=self.site, author=self.member)
        self.assertIsNotNone(s.submitted_at)
        self.client.post(url, {'action': 'review', 'review_note': '差は帳票'})
        s.refresh_from_db()
        self.assertEqual((s.overview, s.review_note), ('全体像', '差は帳票'))

    def test_others_summary_is_read_only(self):
        Summary.objects.create(site=self.site, author=self.leader, overview='リーダーの理解')
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:summary', args=[self.site.pk]), {'user': self.leader.pk})
        self.assertContains(res, 'リーダーの理解')
        self.assertNotContains(res, 'まとめを出す')


class SignalTests(GenbaTestBase):
    def test_unreadable_stuck_after_three_days(self):
        q = Question.objects.create(site=self.site, author=self.member, title='読めない', unreadable=True,
                                    created_at=timezone.now() - timedelta(days=2, hours=1))
        self.assertFalse(self.signal(self.member, self.site, '3日経っても理解が進んでいない').detected)
        q.created_at = timezone.now() - timedelta(days=3, hours=1)
        q.save()
        self.assertTrue(self.signal(self.member, self.site, '3日経っても理解が進んでいない').detected)
        # 他人の記録では、自分に危険信号は出ない
        self.assertFalse(self.signal(self.leader, self.site, '3日経っても理解が進んでいない').detected)

    def test_no_weekly_last_week(self):
        sign = '指摘も指示も来ない'
        self.assertTrue(self.signal(self.member, self.site, sign).detected)
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='w',
                                week_of=week_start(self.today) - timedelta(weeks=1))
        self.assertFalse(self.signal(self.member, self.site, sign).detected)

    def test_no_summary_after_five_days(self):
        sign = '理解したことが形になっていない'
        m = Membership.objects.get(site=self.site, user=self.member)
        m.joined_on = self.today - timedelta(days=4)
        m.save()
        self.assertFalse(self.signal(self.member, self.site, sign).detected)
        m.joined_on = self.today - timedelta(days=5)
        m.save()
        self.assertTrue(self.signal(self.member, self.site, sign).detected)
        Summary.objects.create(site=self.site, author=self.member, submitted_at=timezone.now())
        self.assertFalse(self.signal(self.member, self.site, sign).detected)

    def test_undetected_signs_are_guidance(self):
        signals = signals_for(self.member, self.site)
        self.assertEqual(len(signals), 7)
        self.assertEqual(sum(1 for s in signals if s.detected is None), 4)


class KnowledgeFlowTests(GenbaTestBase):
    def act(self, user, obj, action, model='doc'):
        self.client.force_login(user)
        return self.client.post(reverse('genba:knowledge_action', args=[model, obj.pk, action]))

    def test_full_flow(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='設計')
        self.assertEqual(self.act(self.member, doc, 'candidate').status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.knowledge_status, KNOWLEDGE_CANDIDATE)
        self.assertEqual(self.act(self.member, doc, 'review').status_code, 403)
        self.act(self.leader, doc, 'review')
        doc.refresh_from_db()
        self.assertEqual(doc.knowledge_status, KNOWLEDGE_REVIEWED)
        self.assertEqual(self.act(self.leader, doc, 'publish').status_code, 403)
        self.act(self.member, doc, 'publish')
        doc.refresh_from_db()
        self.assertEqual(doc.visibility, VISIBILITY_COMPANY)
        self.act(self.member, doc, 'unpublish')
        doc.refresh_from_db()
        self.assertEqual(doc.visibility, VISIBILITY_TEAM)

    def test_cannot_publish_before_review(self):
        term = Term.objects.create(site=self.site, author=self.member, term='x', meaning='y',
                                   knowledge_status=KNOWLEDGE_CANDIDATE)
        self.assertEqual(self.act(self.member, term, 'publish', 'term').status_code, 403)
        self.act(self.member, term, 'withdraw', 'term')
        term.refresh_from_db()
        self.assertEqual(term.knowledge_status, KNOWLEDGE_NONE)

    def test_minutes_cannot_be_published(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_MINUTES, title='議事録')
        self.assertEqual(self.act(self.member, doc, 'candidate').status_code, 403)


class MetricsTests(GenbaTestBase):
    def test_site_metrics(self):
        now = timezone.now()
        Question.objects.create(site=self.site, author=self.member, title='a', created_at=now - timedelta(days=10))
        Question.objects.create(site=self.site, author=self.member, title='b', created_at=now - timedelta(days=4))
        Question.objects.create(site=self.site, author=self.member, title='c', is_open=False, created_at=now - timedelta(days=30))
        Question.objects.create(site=self.site, author=self.member, kind=Question.KIND_TASK, title='d', scope_out=True)
        PromiseCheck.objects.create(site=self.site, author=self.leader, promise='p', actual='a', is_gap=True)
        PromiseCheck.objects.create(site=self.site, author=self.leader, promise='p', actual='p')
        Term.objects.create(site=self.site, author=self.member, term='t', meaning='m', general_difference='違う')
        Term.objects.create(site=self.site, author=self.member, term='u', meaning='m')
        kit.create_checklist(self.site, self.member)  # チェックリストの作業は滞留日数に入らない
        m = site_metrics(self.site)
        self.assertEqual(m.stall_days, 7.0)
        self.assertEqual(m.open_questions, 2)
        self.assertEqual((m.promise_gaps, m.scope_out, m.term_gaps), (1, 1, 1))
        self.assertIn('意思決定が止まっている可能性', m.alerts)

    def test_weekly_rate(self):
        # 開始は4週前 → 対象は5週。そのうち2週分を出している
        for n in (0, 2):
            Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='w',
                                    week_of=week_start(self.today) - timedelta(weeks=n))
        self.assertEqual(weekly_rate(self.site), (40, 5))


class SeedCommandTests(TestCase):
    def test_seed_twice(self):
        user = CustomUser.objects.create_user('me', 'me@example.com', 'pass12345')
        for _ in range(2):
            call_command('seed_genba', '--user', 'me', stdout=StringIO())
        self.assertEqual(Site.objects.count(), 3)
        self.client.force_login(user)
        for name in ('portal', 'kit', 'phrases', 'member_home', 'leader_home', 'management', 'company'):
            self.assertEqual(self.client.get(reverse(f'genba:{name}')).status_code, 200, name)
        claims = Site.objects.get(repo_name='genba-demo-claims')
        detected = {s.sign for s in signals_for(user, claims) if s.detected}
        self.assertEqual(detected, {'3日経っても理解が進んでいない', '理解したことが形になっていない'})
        for site in Site.objects.all():
            self.client.force_login(site.memberships.first().user)
            self.assertEqual(self.client.get(reverse('genba:site_detail', args=[site.pk])).status_code, 200)
