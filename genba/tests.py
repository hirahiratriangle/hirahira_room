from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser

from . import forms, grades, growth, kit, kit_data, kpi, money, skillset_data
from .metrics import site_metrics, weekly_rate
from .models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_NONE, KNOWLEDGE_PUBLISHED, VISIBILITY_COMPANY, VISIBILITY_TEAM,
    Assignment, ChecklistItem, CompanyMonth, Document, FocusSkill, Goal, GoalEvidence, Grade, GradeAssignment, GradeReview, InterviewQuestion, KitProposal, KnowledgeUse, MemberRepository,
    Membership, MonthlyWork, PromiseCheck, Question, Site, StructureGap, Summary, Term, week_start,
)
from .roles import GROUP_COMPANY, GROUP_MANAGEMENT
from .signals import signals_for, weeks_missing_in_row


class GenbaTestBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.member = CustomUser.objects.create_user('member', 'm@example.com', 'pass12345')
        self.leader = CustomUser.objects.create_user('leader', 'l@example.com', 'pass12345')
        self.outsider = CustomUser.objects.create_user('outsider', 'o@example.com', 'pass12345')
        self.site = self.make_site('現場A', 'genba-a', weeks=4)

    def make_site(self, name, repo, weeks=4, industry='製造業'):
        started = self.today - timedelta(weeks=weeks)
        site = Site.objects.create(name=name, team_slug=repo, industry=industry, started_on=started)
        Membership.objects.create(site=site, user=self.member, joined_on=started)
        Membership.objects.create(site=site, user=self.leader, role=Membership.ROLE_LEADER, joined_on=started)
        return site

    def assertNoUsername(self, res, name):
        """画面に人の名前が出ていないこと（切り替えボタンの URL /demo/as/member/ は名前ではないので数えない）"""
        self.assertNotRegex(res.content.decode(), rf'(?<![/\w]){name}(?![/\w])')

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
            'name': '新現場', 'team_slug': 'genba-new', 'industry': '物流業',
            'started_on': self.today.isoformat(), 'description': '', 'my_role': 'member', 'job_type': 'se',
        })
        site = Site.objects.get(team_slug='genba-new')
        self.assertRedirects(res, reverse('genba:site_detail', args=[site.pk]) + '?tab=checklist')
        self.assertTrue(Membership.objects.filter(site=site, user=self.outsider, joined_on=self.today).exists())
        tasks = site.questions.exclude(stage='')
        self.assertEqual(tasks.count(), 12)
        self.assertTrue(all(q.kind == Question.KIND_TASK for q in tasks))
        self.assertEqual(ChecklistItem.objects.filter(question__site=site).count(), 45)
        self.assertEqual(site.memberships.get().user.genba_repository.template_version, kit.TEMPLATE_VERSION)
        page = self.client.get(reverse('genba:site_detail', args=[site.pk]))
        self.assertContains(page, '［入場前］契約・商流')

    def test_team_slug_must_start_with_genba(self):
        self.client.force_login(self.member)
        res = self.client.post(reverse('genba:site_create'), {
            'name': 'x', 'team_slug': 'other', 'industry': 'x', 'started_on': self.today.isoformat(), 'my_role': 'member', 'job_type': 'se',
        })
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Site.objects.filter(team_slug='other').exists())

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

    def test_anonymous_default_is_member_persona(self):
        # 立場を選ばずに開いた未ログインの人は、ログイン画面へ送らずメンバーとして見せる
        for name in ('portal', 'kit', 'phrases', 'skillset', 'member_home'):
            self.assertEqual(self.client.get(reverse(f'genba:{name}')).status_code, 200, name)
        self.assertEqual(self.client.get(reverse('genba:management')).status_code, 403)
        self.assertEqual(self.client.get(reverse('genba:company')).status_code, 403)
        res = self.client.post(reverse('genba:site_create'), {
            'name': 'デモの現場', 'team_slug': 'genba-persona', 'industry': '物流業',
            'started_on': self.today.isoformat(), 'description': '', 'my_role': 'leader', 'job_type': 'se',
        })
        site = Site.objects.get(team_slug='genba-persona')
        self.assertRedirects(res, reverse('genba:site_detail', args=[site.pk]) + '?tab=checklist')
        self.assertTrue(Membership.objects.filter(site=site, user__username='demo_kimura',
                                                  role=Membership.ROLE_LEADER).exists())
        # 入っていない現場の原文は、これまでどおり読めない
        self.assertEqual(self.client.get(reverse('genba:site_detail', args=[self.site.pk])).status_code, 403)

    def test_team_document_hidden_from_outsider_public_one_visible(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='顧客名入りの設計',
                                      body='原文の本文')
        self.client.force_login(self.outsider)
        url = reverse('genba:document_detail', args=[doc.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        doc.visibility, doc.knowledge_status = VISIBILITY_COMPANY, KNOWLEDGE_PUBLISHED
        doc.public_title, doc.public_body = '一般化した設計', '一般化した本文'
        doc.save()
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        # 会社公開では一般化した版と業界だけを出す（原文・現場名・書いた人は出さない）
        self.assertContains(res, '製造業')
        self.assertContains(res, '一般化した本文')
        self.assertNotContains(res, '現場A')
        self.assertNotContains(res, '原文の本文')
        self.assertNotContains(res, '顧客名入り')

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
        self.assertNoUsername(res, 'member')

    def test_company_sees_only_published(self):
        Term.objects.create(site=self.site, author=self.member, term='公開語', meaning='x', visibility=VISIBILITY_COMPANY,
                            knowledge_status=KNOWLEDGE_PUBLISHED, public_title='公開語', public_body='一般化した意味')
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
        hidden.visibility, hidden.knowledge_status = VISIBILITY_COMPANY, KNOWLEDGE_PUBLISHED
        hidden.public_title, hidden.public_body = '締め', '支払の締切（一般化）'
        hidden.save()
        res = self.client.get(url, {'tab': 'terms'})
        self.assertContains(res, '保険業の現場では：支払の締切（一般化）')
        self.assertNotContains(res, '非公開の意味')

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
        # 入場期（4週）を過ぎたら出さない
        m.joined_on = self.today - timedelta(days=29)
        m.save()
        self.assertFalse(self.signal(self.member, self.site, sign).detected)
        m.joined_on = self.today - timedelta(days=5)
        m.save()
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

    def publish_url(self, obj, model='doc'):
        return reverse('genba:knowledge_publish', args=[model, obj.pk])

    def test_full_flow(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='原文の題', body='原文')
        # 現場チームの誰でも候補にできる（書いた本人でなくてもよい）
        self.assertEqual(self.act(self.leader, doc, 'candidate').status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.knowledge_status, KNOWLEDGE_CANDIDATE)
        # 本人は公開できない（公開範囲を選ばせない）
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.publish_url(doc)).status_code, 403)
        self.assertEqual(self.act(self.member, doc, 'publish').status_code, 403)
        # リーダーが一般化した版を書いて公開する
        self.client.force_login(self.leader)
        res = self.client.get(self.publish_url(doc))
        self.assertContains(res, '原文の題')
        self.client.post(self.publish_url(doc), {'public_title': '一般化した題', 'public_body': '一般化した本文'})
        doc.refresh_from_db()
        self.assertEqual((doc.knowledge_status, doc.visibility, doc.published_by), (KNOWLEDGE_PUBLISHED, VISIBILITY_COMPANY, self.leader))
        self.add_group(self.outsider, GROUP_COMPANY)
        self.client.force_login(self.outsider)
        res = self.client.get(reverse('genba:company'))
        self.assertContains(res, '一般化した題')
        self.assertNotContains(res, '原文の題')
        # 公開をやめられるのはリーダー
        self.assertEqual(self.act(self.member, doc, 'unpublish').status_code, 403)
        self.act(self.leader, doc, 'unpublish')
        doc.refresh_from_db()
        self.assertEqual((doc.knowledge_status, doc.visibility), (KNOWLEDGE_CANDIDATE, VISIBILITY_TEAM))

    def test_cannot_publish_without_candidate(self):
        term = Term.objects.create(site=self.site, author=self.member, term='x', meaning='y')
        self.client.force_login(self.leader)
        self.assertEqual(self.client.get(self.publish_url(term, 'term')).status_code, 403)
        self.act(self.member, term, 'candidate', 'term')
        self.act(self.member, term, 'withdraw', 'term')
        term.refresh_from_db()
        self.assertEqual(term.knowledge_status, KNOWLEDGE_NONE)

    def test_outsider_cannot_mark_candidate(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='設計')
        self.assertEqual(self.act(self.outsider, doc, 'candidate').status_code, 403)

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
    def test_old_guest_leaves_role_groups(self):
        guest = CustomUser.objects.create_user('genba_guest', 'g@example.com', 'pass12345')
        guest.groups.add(Group.objects.get_or_create(name=GROUP_MANAGEMENT)[0], Group.objects.get_or_create(name=GROUP_COMPANY)[0])
        call_command('seed_genba', stdout=StringIO())
        self.assertFalse(guest.groups.exists())

    def test_seed_twice(self):
        for _ in range(2):
            call_command('seed_genba', stdout=StringIO())
        self.assertEqual(Site.objects.filter(is_archived=False).count(), 4)
        self.assertEqual(Site.objects.filter(is_archived=True).count(), 2)
        counts = {s.team_slug: (s.memberships.filter(role=Membership.ROLE_LEADER).count(),
                                s.memberships.filter(role=Membership.ROLE_MEMBER).count())
                  for s in Site.objects.filter(is_archived=False)}
        self.assertEqual(counts, {'genba-demo-order': (1, 2), 'genba-demo-logistics': (0, 1),
                                  'genba-demo-claims': (1, 4), 'genba-demo-stock': (0, 1)})
        solo = Goal.objects.filter(site__team_slug__in=['genba-demo-logistics', 'genba-demo-stock'])
        self.assertTrue(all(g.is_solo for g in solo))
        # 立場ごとに別の人。マネジメント・会社は1人ずつ
        self.assertEqual([u.username for u in CustomUser.objects.filter(groups__name=GROUP_MANAGEMENT)], ['demo_kacho'])
        self.assertEqual([u.username for u in CustomUser.objects.filter(groups__name=GROUP_COMPANY)], ['demo_honbu'])
        member, leader = CustomUser.objects.get(username='demo_kimura'), CustomUser.objects.get(username='demo_kobayashi')
        self.assertEqual([m.site.team_slug for m in member.genba_memberships.all()], ['genba-demo-claims'])
        self.assertEqual([m.site.team_slug for m in leader.genba_memberships.all()], ['genba-demo-order'])
        self.assertFalse(member.has_usable_password())
        claims = Site.objects.get(team_slug='genba-demo-claims')
        detected = {s.sign for s in signals_for(member, claims) if s.detected}
        self.assertEqual(detected, {'3日経っても理解が進んでいない', '理解したことが形になっていない'})
        for key, name in (('member', 'member_home'), ('leader', 'leader_home'), ('management', 'management'), ('company', 'company')):
            self.client.post(reverse('genba:demo_switch', args=[key]))
            self.assertEqual(self.client.get(reverse(f'genba:{name}')).status_code, 200, name)
        for site in Site.objects.all():
            client = Client()
            client.force_login(site.memberships.first().user)
            self.assertEqual(client.get(reverse('genba:site_detail', args=[site.pk])).status_code, 200)
            self.assertEqual(client.get(reverse('genba:growth', args=[site.pk])).status_code, 200)
        self.client.post(reverse('genba:demo_switch', args=['member']))
        for name in ('weekly_board', 'lines', 'summary'):
            self.assertEqual(self.client.get(reverse(f'genba:{name}', args=[claims.pk])).status_code, 200, name)
        for goal in Goal.objects.filter(author=member):
            self.assertEqual(self.client.get(reverse('genba:goal_reach', args=[goal.pk])).status_code, 200)


class SkillsetTests(TestCase):
    def test_skillset_data_matches_source(self):
        self.assertEqual(len(skillset_data.SKILL_ITEMS), 11)
        self.assertEqual(len(skillset_data.CONCRETE_SKILLS), 23)
        self.assertEqual(len(skillset_data.COMPLEXITY), 8)
        self.assertEqual(len(skillset_data.ITSS_LEVELS), 6)
        covered = {i for row in skillset_data.LEVEL_DESCRIPTIONS for i in row['items']}
        self.assertEqual(covered, set(growth.SKILL_ITEMS))
        self.assertEqual({s['item'] for s in skillset_data.CONCRETE_SKILLS}, set(growth.SKILL_ITEMS))

    def test_no_pay_grade_in_skillset(self):
        text = repr({k: v for k, v in vars(skillset_data).items() if k.isupper()})
        self.assertNotIn('職級', text)
        self.assertNotIn('賃金', text)

    def test_auto_skills_exist_in_skillset(self):
        pairs = {(s['item'], s['skill']) for s in skillset_data.CONCRETE_SKILLS}
        for pair in growth.AUTO_SKILLS.values():
            self.assertIn(pair, pairs)

    def test_level_description(self):
        self.assertEqual(growth.level_description('デザイン', 4), '画面標準・データモデル・IF方針をチームに示し、レビューで整合させる')
        self.assertEqual(growth.level_description('ネゴシエーション', 3), growth.level_description('コミュニケーション', 3))
        self.assertEqual(growth.level_description('リーダーシップ', 2), '')

    def test_grade_and_itss(self):
        self.assertEqual(growth.ae_grade(2, False), 'C')
        self.assertEqual(growth.ae_grade(3, False), 'B')
        self.assertEqual(growth.ae_grade(3, True), 'A')
        self.assertEqual(growth.ae_grade(4, False), 'A')
        self.assertEqual(growth.itss_equivalent('member', 5, 0), 3)
        self.assertEqual(growth.itss_equivalent('leader', 2, 0), 3)
        self.assertEqual(growth.itss_equivalent('leader', 3, 0), 4)
        self.assertEqual(growth.itss_equivalent('owner', 10, 0), 5)
        self.assertEqual(growth.itss_equivalent('owner', 3, 4), 5)
        self.assertIsNone(growth.itss_equivalent('', 3, 0))


class GoalTests(GenbaTestBase):
    def test_pick_shows_one_level_up(self):
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:goal_create', args=[self.site.pk]), {'item': 'デザイン', 'level': 3})
        self.assertContains(res, '画面標準・データモデル・IF方針をチームに示し、レビューで整合させる')
        self.assertContains(res, '画面・入力チェック設計')

    def test_create_goal(self):
        self.client.force_login(self.member)
        res = self.client.post(reverse('genba:goal_create', args=[self.site.pk]), {
            'item': 'デザイン', 'level': 3, 'concrete_skill': '帳票・出力設計', 'target_text': '目標の文', 'preparation': ''})
        self.assertRedirects(res, reverse('genba:growth', args=[self.site.pk]))
        g = Goal.objects.get()
        self.assertEqual((g.author, g.skill_item, g.current_level, g.target_level), (self.member, 'デザイン', 3, 4))

    def test_concrete_skill_must_belong_to_item(self):
        self.client.force_login(self.member)
        self.client.post(reverse('genba:goal_create', args=[self.site.pk]), {
            'item': 'デザイン', 'level': 3, 'concrete_skill': '後進の指導', 'target_text': 'x'})
        self.assertFalse(Goal.objects.exists())

    def test_goal_and_reach_visible_to_team_only(self):
        g = Goal.objects.create(site=self.site, author=self.member, skill_item='デザイン', current_level=3,
                                target_text='チームに見える目標', explanation='チームに見える説明')
        self.client.force_login(self.leader)
        res = self.client.get(reverse('genba:site_detail', args=[self.site.pk]), {'tab': 'members'})
        self.assertContains(res, 'チームに見える目標')
        self.assertContains(self.client.get(reverse('genba:goal_reach', args=[g.pk])), 'チームに見える説明')
        self.assertEqual(self.client.post(reverse('genba:goal_reach', args=[g.pk]), {'reached_level': 3}).status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('genba:goal_reach', args=[g.pk])).status_code, 403)

    def test_candidates_are_own_records_with_matching_first(self):
        other = self.make_site('現場B', 'genba-b')
        g = Goal.objects.create(site=self.site, author=self.member, skill_item='コミュニケーション', current_level=2,
                                target_text='x')
        mine = Question.objects.create(site=self.site, author=self.member, title='読めないA', unreadable=True)
        Term.objects.create(site=self.site, author=self.member, term='用語A', meaning='m')
        Question.objects.create(site=self.site, author=self.leader, title='他人の読めない', unreadable=True)
        Question.objects.create(site=other, author=self.member, title='他現場の読めない', unreadable=True)
        kit.create_checklist(self.site, self.member)
        rows = growth.candidates(g)
        titles = [r['title'] for r in rows]
        self.assertEqual(rows[0]['key'], f'issue:{mine.pk}')
        self.assertTrue(rows[0]['matches'])
        self.assertIn('用語集：用語A', titles)
        self.assertFalse(any('他人' in t or '他現場' in t or '［' in t for t in titles))

    def test_reach_saves_evidence_and_paste_text(self):
        g = Goal.objects.create(site=self.site, author=self.member, skill_item='コミュニケーション', current_level=2,
                                target_text='説明して取り決める')
        q = Question.objects.create(site=self.site, author=self.member, title='読めないA', unreadable=True)
        other = Question.objects.create(site=self.site, author=self.leader, title='他人の', unreadable=True)
        self.client.force_login(self.member)
        self.client.post(reverse('genba:goal_reach', args=[g.pk]), {
            'reached_level': 3, 'explanation': 'やった', 'role': 'member', 'team_size': 5,
            'complexity': ['1', '4'], 'action': 'save', 'evidence': [f'issue:{q.pk}', f'issue:{other.pk}']})
        g.refresh_from_db()
        self.assertEqual((g.reached_level, g.complexity, g.stage), (3, '1,4', Goal.STAGE_DRAFT))
        self.assertEqual(list(g.evidence.values_list('record_id', flat=True)), [q.pk])
        res = self.client.get(reverse('genba:goal_reach', args=[g.pk]))
        self.assertContains(res, 'スキルシート（基本設計）の評価の目安：B')
        self.assertContains(res, 'ITSSレベル3相当')

    def test_weekly_goal_action_on_growth_page(self):
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='w',
                                week_of=self.today, goal_action='設計方針をチームに示した')
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse('genba:growth', args=[self.site.pk])), '設計方針をチームに示した')

    def test_checklist_is_per_person(self):
        newcomer = CustomUser.objects.create_user('newcomer', 'n@example.com', 'pass12345')
        kit.join_site(self.site, newcomer, Membership.ROLE_MEMBER, self.today)
        self.assertEqual(self.site.questions.filter(author=newcomer).exclude(stage='').count(), 12)
        self.assertFalse(self.site.questions.filter(author=self.member).exclude(stage='').exists())
        q = self.site.questions.filter(author=newcomer, title__contains='役割と期待値').get()
        self.assertIn(('目標（スキルセット）', reverse('genba:goal_create', args=[self.site.pk])), q.record_targets())

    def test_skillset_page(self):
        self.client.force_login(self.outsider)
        res = self.client.get(reverse('genba:skillset'))
        self.assertContains(res, '業務アプリケーション開発 基本設計スキルセット')
        self.assertContains(res, '読めない資料の早期の共有')


class ReachStageTests(GenbaTestBase):
    def setUp(self):
        super().setUp()
        self.manager = CustomUser.objects.create_user('manager', 'k@example.com', 'pass12345')
        self.add_group(self.manager, GROUP_MANAGEMENT)
        self.goal = Goal.objects.create(site=self.site, author=self.member, skill_item='デザイン', current_level=3,
                                        target_text='目標')
        self.reach_url = reverse('genba:goal_reach', args=[self.goal.pk])
        self.approval_url = reverse('genba:goal_approval', args=[self.goal.pk])

    def submit(self):
        self.client.force_login(self.member)
        self.client.post(self.reach_url, {'reached_level': 4, 'explanation': '説明', 'action': 'submit'})
        self.goal.refresh_from_db()

    def test_enter_confirm_approve(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.approval_url).status_code, 403)
        self.submit()
        self.assertEqual(self.goal.stage, Goal.STAGE_SUBMITTED)
        self.client.post(self.reach_url, {'reached_level': 2, 'action': 'save'})
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.reached_level, 4)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.approval_url).status_code, 403)
        self.client.force_login(self.leader)
        self.assertContains(self.client.get(reverse('genba:leader_home')), '確認する')
        self.client.post(self.reach_url, {'action': 'confirm', 'comment': '事実を確かめた'})
        self.goal.refresh_from_db()
        self.assertEqual((self.goal.stage, self.goal.confirmed_by), (Goal.STAGE_CONFIRMED, self.leader))
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('genba:management')), '承認する')
        self.client.post(self.approval_url, {'approved_level': 4, 'approved_grade': 'A', 'next_text': '次',
                                             'approval_comment': '', 'action': 'approve'})
        self.goal.refresh_from_db()
        self.assertEqual((self.goal.stage, self.goal.approved_level, self.goal.approved_grade, self.goal.approved_by),
                         (Goal.STAGE_APPROVED, 4, 'A', self.manager))
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self.reach_url, {'action': 'withdraw'}).status_code, 403)

    def test_return_and_withdraw(self):
        self.submit()
        self.client.force_login(self.leader)
        self.client.post(self.reach_url, {'action': 'return', 'comment': '証拠を足して'})
        self.goal.refresh_from_db()
        self.assertEqual((self.goal.stage, self.goal.confirm_comment), (Goal.STAGE_DRAFT, '証拠を足して'))
        self.submit()
        self.client.post(self.reach_url, {'action': 'withdraw'})
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.stage, Goal.STAGE_DRAFT)

    def test_only_other_leader_confirms_and_not_own(self):
        self.submit()
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self.reach_url, {'action': 'confirm'}).status_code, 403)
        self.add_group(self.member, GROUP_MANAGEMENT)
        self.assertEqual(self.client.get(self.approval_url).status_code, 403)

    def test_solo_site_management_confirms_and_approves(self):
        solo = Site.objects.create(name='ひとり', team_slug='genba-solo', industry='小売業', started_on=self.today)
        Membership.objects.create(site=solo, user=self.outsider, joined_on=self.today)
        g = Goal.objects.create(site=solo, author=self.outsider, skill_item='業務分析', current_level=2, target_text='t',
                                reached_level=3, submitted_at=timezone.now())
        self.assertTrue(g.is_solo)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('genba:management')), '確認して承認する')
        self.client.post(reverse('genba:goal_approval', args=[g.pk]), {
            'approved_level': 3, 'approved_grade': 'B', 'customer_voice': '元請：よく説明してくれた', 'action': 'approve'})
        g.refresh_from_db()
        self.assertEqual((g.stage, g.confirmed_by, g.customer_voice), (Goal.STAGE_APPROVED, self.manager, '元請：よく説明してくれた'))

    def test_weekly_with_goal_action_is_candidate(self):
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='週報A',
                                week_of=self.today, goal_action='方針を示した')
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='週報B',
                                week_of=self.today - timedelta(weeks=1))
        rows = growth.candidates(self.goal)
        titles = [r['title'] for r in rows]
        self.assertTrue(any('週報A' in t and '方針を示した' in t for t in titles))
        self.assertFalse(any('週報B' in t for t in titles))
        self.assertTrue(rows[0]['matches'])


class LeaderTests(GenbaTestBase):
    def test_leader_home_sections_and_weekly_gap(self):
        Question.objects.create(site=self.site, author=self.member, title='止まっている確認事項',
                                created_at=timezone.now() - timedelta(days=9))
        self.client.force_login(self.leader)
        res = self.client.get(reverse('genba:leader_home'))
        for text in ('詰まりの一覧', '「読めない」の受け取り', '1週目のまとめ確認', '週次共有の一覧', '裁量の線', '懸念の受け取り'):
            self.assertContains(res, text)
        self.assertContains(res, '止まっている確認事項')
        self.assertContains(res, '週報が途切れている')
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='w',
                                week_of=week_start(self.today) - timedelta(weeks=1))
        self.assertNotContains(self.client.get(reverse('genba:leader_home')), '週報が途切れている')

    def test_leader_home_shows_no_workload_counts(self):
        kit.create_checklist(self.site, self.member)
        Term.objects.create(site=self.site, author=self.member, term='t', meaning='m')
        self.client.force_login(self.leader)
        res = self.client.get(reverse('genba:leader_home'))
        self.assertNotContains(res, '/45')
        self.assertNotContains(res, '入場時チェックリスト')

    def test_support_time_for_unreadable(self):
        q = Question.objects.create(site=self.site, author=self.member, title='読めない', unreadable=True)
        url = reverse('genba:issue_detail', args=[q.pk])
        data = {'form': 'support', 'support_on': self.today.isoformat(), 'support_note': '15分'}
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.leader)
        self.client.post(url, data)
        q.refresh_from_db()
        self.assertEqual(q.support_on, self.today)
        self.client.force_login(self.member)
        self.assertContains(self.client.get(url), '15分')

    def test_response_to_concern(self):
        q = Question.objects.create(site=self.site, author=self.member, kind=Question.KIND_PROBLEM, title='懸念', fact='f')
        url = reverse('genba:issue_detail', args=[q.pk])
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(url, {'form': 'response', 'response': 'x'}).status_code, 403)
        self.client.force_login(self.leader)
        self.client.post(url, {'form': 'response', 'response': '受け取りました。業務部に確認します'})
        q.refresh_from_db()
        self.assertIsNotNone(q.received_at)
        self.client.force_login(self.member)
        self.assertContains(self.client.get(url), '業務部に確認します')

    def test_feedback_on_summary(self):
        Summary.objects.create(site=self.site, author=self.member, submitted_at=timezone.now(), overview='理解')
        Goal.objects.create(site=self.site, author=self.member, skill_item='デザイン', current_level=3, target_text='目標の文')
        url = reverse('genba:summary', args=[self.site.pk])
        self.client.force_login(self.leader)
        res = self.client.get(url, {'user': self.member.pk})
        self.assertContains(res, '目標の文')
        self.assertContains(res, '画面標準・データモデル・IF方針をチームに示し、レビューで整合させる')
        self.client.post(url, {'action': 'feedback', 'user': self.member.pk, 'feedback': '差は影響範囲'})
        s = Summary.objects.get(author=self.member)
        self.assertEqual((s.feedback, s.feedback_by), ('差は影響範囲', self.leader))
        self.client.force_login(self.member)
        self.assertContains(self.client.get(url), '差は影響範囲')
        self.assertEqual(self.client.post(url, {'action': 'feedback', 'user': self.member.pk, 'feedback': 'x'}).status_code, 403)

    def test_feedback_needs_submitted_summary(self):
        Summary.objects.create(site=self.site, author=self.member, overview='書きかけ')
        self.client.force_login(self.leader)
        res = self.client.post(reverse('genba:summary', args=[self.site.pk]),
                               {'action': 'feedback', 'user': self.member.pk, 'feedback': 'x'})
        self.assertEqual(res.status_code, 404)

    def test_weekly_board(self):
        other = self.make_site('現場B', 'genba-b')
        week = week_start(self.today) - timedelta(weeks=1)
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='w', week_of=week,
                                done='この現場でやったこと', stuck='この現場の詰まり')
        Document.objects.create(site=other, author=self.member, kind=Document.KIND_WEEKLY, title='w', week_of=week,
                                done='他の現場でやったこと')
        self.client.force_login(self.member)
        url = reverse('genba:weekly_board', args=[self.site.pk])
        res = self.client.get(url)
        self.assertContains(res, 'この現場でやったこと')
        self.assertNotContains(res, '他の現場でやったこと')
        res = self.client.get(url, {'week': (week - timedelta(weeks=1)).isoformat()})
        self.assertNotContains(res, 'この現場でやったこと')
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_lines_editable_by_self_and_leader_only(self):
        third = CustomUser.objects.create_user('third', 't@example.com', 'pass12345')
        Membership.objects.create(site=self.site, user=third, joined_on=self.today)
        m = Membership.objects.get(site=self.site, user=self.member)
        url = reverse('genba:lines', args=[self.site.pk])
        data = {'membership': m.pk, 'line_self': '画面項目', 'line_consult': '業務ルール'}
        self.client.force_login(third)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        for user in (self.member, self.leader):
            self.client.force_login(user)
            self.assertEqual(self.client.post(url, data).status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.line_self, '画面項目')
        self.assertIsNotNone(m.line_updated_at)



class MetricsV2Tests(GenbaTestBase):
    def test_structure_gap_record_and_sign_link(self):
        self.client.force_login(self.member)
        url = reverse('genba:structure_gap_create', args=[self.site.pk])
        sign = self.signal(self.member, self.site, '日々の指示者が体制図と違う')
        self.assertEqual(sign.link[1], url)
        self.client.post(url, {'role_kind': 'instructor', 'on_chart': 'PM', 'actual': '業務部課長', 'note': ''})
        self.assertEqual(site_metrics(self.site).structure_gaps, 1)
        self.assertIn('契約形態と指示系統が一致していない', site_metrics(self.site).alerts)

    def test_weeks_missing_in_row(self):
        m = Membership.objects.get(site=self.site, user=self.member)  # 入場は4週前
        self.assertEqual(weeks_missing_in_row(m), 4)
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='w',
                                week_of=week_start(self.today) - timedelta(weeks=2))
        self.assertEqual(weeks_missing_in_row(m), 1)
        m.joined_on = self.today
        self.assertEqual(weeks_missing_in_row(m), 0)

    def test_weekly_gap_reaches_management_only_when_continued(self):
        last = week_start(self.today) - timedelta(weeks=1)
        Document.objects.create(site=self.site, author=self.leader, kind=Document.KIND_WEEKLY, title='w', week_of=last)
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_WEEKLY, title='w',
                                week_of=last - timedelta(weeks=1))
        self.assertEqual(site_metrics(self.site).weekly_gap_people, 0)  # 1週だけ途切れている
        Document.objects.filter(author=self.member).update(week_of=last - timedelta(weeks=2))
        self.assertEqual(site_metrics(self.site).weekly_gap_people, 1)
        self.add_group(self.outsider, GROUP_MANAGEMENT)
        self.client.force_login(self.outsider)
        res = self.client.get(reverse('genba:management'))
        self.assertContains(res, '1人')
        self.assertNoUsername(res, 'member')

    def test_leader_concern_metric_and_feedback(self):
        q = Question.objects.create(site=self.site, author=self.leader, kind=Question.KIND_PROBLEM, title='リーダーの懸念',
                                    fact='f', impact='影響')
        Question.objects.create(site=self.site, author=self.member, kind=Question.KIND_PROBLEM, title='メンバーの懸念')
        m = site_metrics(self.site)
        self.assertEqual((m.leader_concerns, m.leader_concerns_unreceived), (1, 1))
        # メンバーにはフィードバックとして見え、受け取れる
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse('genba:member_home')), 'リーダーからの懸念')
        url = reverse('genba:issue_detail', args=[q.pk])
        self.client.post(url, {'form': 'response', 'response': '受け取りました'})
        q.refresh_from_db()
        self.assertEqual(q.received_by, self.member)
        self.assertEqual(site_metrics(self.site).leader_concerns_unreceived, 0)
        # リーダーは自分の懸念を受け取れない
        self.client.force_login(self.leader)
        self.assertEqual(self.client.post(url, {'form': 'response', 'response': 'x'}).status_code, 403)

    def test_member_cannot_receive_member_concern(self):
        third = CustomUser.objects.create_user('third', 't@example.com', 'pass12345')
        Membership.objects.create(site=self.site, user=third, joined_on=self.today)
        q = Question.objects.create(site=self.site, author=self.member, kind=Question.KIND_PROBLEM, title='懸念')
        self.client.force_login(third)
        url = reverse('genba:issue_detail', args=[q.pk])
        self.assertEqual(self.client.post(url, {'form': 'response', 'response': 'x'}).status_code, 403)

    def test_management_shows_definitions_and_no_text(self):
        Question.objects.create(site=self.site, author=self.leader, kind=Question.KIND_PROBLEM, title='秘密の懸念')
        StructureGap.objects.create(site=self.site, author=self.member, role_kind='approver', on_chart='秘密の部長', actual='x')
        self.add_group(self.outsider, GROUP_MANAGEMENT)
        self.client.force_login(self.outsider)
        res = self.client.get(reverse('genba:management'))
        self.assertContains(res, '指標の定義（案）')
        self.assertContains(res, '契約形態と指示系統の不一致')
        self.assertNotContains(res, '秘密の懸念')
        self.assertNotContains(res, '秘密の部長')



class CompanyTests(GenbaTestBase):
    def setUp(self):
        super().setUp()
        self.company = CustomUser.objects.create_user('company', 'c@example.com', 'pass12345')
        self.add_group(self.company, GROUP_COMPANY)

    def goal(self, author, item, level, approved=True, **kw):
        if approved:
            kw.update(approved_on=self.today, approved_level=level, approved_grade='B')
        return Goal.objects.create(site=self.site, author=author, skill_item=item, current_level=level - 1,
                                   target_text='t', reached_level=level, **kw)

    def test_distribution_counts_only_approved_latest(self):
        self.goal(self.member, 'デザイン', 3)
        g = self.goal(self.member, 'デザイン', 4)
        g.approved_on = self.today + timedelta(days=1)
        g.save()
        self.goal(self.leader, 'デザイン', 3, approved=False)
        self.goal(self.leader, '業務分析', 4, role='leader', team_size=3, complexity='1,2')
        rows = {r['item']: r['counts'] for r in growth.skill_distribution()}
        self.assertEqual(rows['デザイン'], [0, 0, 1, 0])
        self.assertEqual(rows['業務分析'], [0, 0, 1, 0])
        self.assertEqual(dict(growth.itss_distribution())[4], 1)
        self.assertEqual({i: n for i, _, n in growth.complexity_experience()}[1], 1)
        self.assertIn('テクノロジ', growth.thin_items(growth.skill_distribution()))

    def test_ledger_shows_only_approved(self):
        self.goal(self.member, 'デザイン', 3, explanation='承認した行動', next_text='次はチームに方針を示す')
        self.goal(self.leader, '業務分析', 3, approved=False, explanation='承認していない行動')
        self.client.force_login(self.company)
        res = self.client.get(reverse('genba:company'))
        self.assertContains(res, '個人のスキル台帳')
        self.assertContains(res, '承認した行動')
        self.assertContains(res, '次はチームに方針を示す')
        self.assertNotContains(res, '承認していない行動')
        self.assertNotContains(res, '現場A')

    def test_kit_proposal_by_anyone_decided_by_company(self):
        self.client.force_login(self.member)
        self.client.post(reverse('genba:kit_proposal_create'), {
            'target': 'checklist', 'title': '項目を足す', 'proposal': '周辺作業の範囲', 'reason': ''})
        p = KitProposal.objects.get()
        self.assertEqual(self.client.post(reverse('genba:company'), {'action': 'adopted', 'proposal': p.pk}).status_code, 403)
        self.client.force_login(self.company)
        self.client.post(reverse('genba:company'), {'action': 'adopted', 'proposal': p.pk})
        p.refresh_from_db()
        self.assertEqual((p.status, p.decided_by), ('adopted', self.company))
        self.assertContains(self.client.get(reverse('genba:kit')), '項目を足す')

    def test_interview_question_by_company(self):
        self.client.force_login(self.company)
        self.client.post(reverse('genba:company'), {'action': 'question', 'question': '周辺作業は範囲に入るか', 'industry': ''})
        self.assertTrue(InterviewQuestion.objects.filter(question='周辺作業は範囲に入るか').exists())

    def test_leader_feedback_is_evidence(self):
        Summary.objects.create(site=self.site, author=self.member, submitted_at=timezone.now(), feedback='差',
                               feedback_by=self.leader, feedback_at=timezone.now())
        g = Goal.objects.create(site=self.site, author=self.leader, skill_item='リーダーシップ', current_level=3,
                                target_text='t')
        rows = growth.candidates(g)
        self.assertEqual(rows[0]['type'], 'feedback')
        self.assertTrue(rows[0]['matches'])


class CompanyKpiTests(GenbaTestBase):
    """会社の職掌と経営KPI（spec 007）：公開範囲と、数え方"""

    def setUp(self):
        super().setUp()
        self.company = CustomUser.objects.create_user('company', 'c@example.com', 'pass12345')
        self.add_group(self.company, GROUP_COMPANY)
        self.manager = CustomUser.objects.create_user('manager', 'k@example.com', 'pass12345')
        self.add_group(self.manager, GROUP_MANAGEMENT)

    def goal(self, author, level, approved_on, item='デザイン', **kw):
        return Goal.objects.create(site=self.site, author=author, skill_item=item, current_level=level - 1, target_text='t',
                                   reached_level=level, approved_on=approved_on, approved_level=level if approved_on else None,
                                   approved_grade='B' if approved_on else '', **kw)

    def tile(self, name):
        for function in kpi.company_functions():
            for tile in function['tiles']:
                if tile.name == name:
                    return tile
        raise AssertionError(name)

    def test_only_company_can_read(self):
        url = reverse('genba:company_kpi')
        for user in (self.member, self.leader, self.manager):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.company)
        self.assertContains(self.client.get(url), '職掌と経営KPI')

    def test_page_shows_no_site_or_person(self):
        self.goal(self.member, 3, self.today, explanation='承認した行動')
        self.client.force_login(self.company)
        res = self.client.get(reverse('genba:company_kpi'))
        self.assertNotContains(res, '現場A')
        self.assertNotContains(res, '承認した行動')
        self.assertNoUsername(res, self.member.username)

    def test_periods_are_three_months_ending_this_month(self):
        spans = kpi.periods(date(2026, 1, 15))
        self.assertEqual(len(spans), 6)
        self.assertEqual((spans[-1].start, spans[-1].end), (date(2025, 11, 1), date(2026, 1, 31)))
        self.assertEqual((spans[-2].start, spans[-2].end), (date(2025, 8, 1), date(2025, 10, 31)))
        self.assertEqual(spans[-1].label, '25/11–1')

    def test_itss_history_uses_ledger_at_that_time(self):
        old = self.today - timedelta(days=200)
        self.goal(self.member, 3, old, role='member', team_size=3)
        self.goal(self.member, 4, self.today, role='leader', team_size=3)
        series = {s['label']: s['data'] for s in kpi.company_functions()[1]['chart']['series']}
        self.assertEqual((series['レベル3相当'][-3], series['レベル4相当'][-3]), (1, 0))
        self.assertEqual((series['レベル3相当'][-1], series['レベル4相当'][-1]), (0, 1))

    def test_rates_count_people_and_only_approved(self):
        self.goal(self.member, 3, self.today)
        self.goal(self.member, 3, self.today, item='業務分析')
        self.goal(self.leader, 3, None)
        people = kpi.headcount()
        self.assertEqual(self.tile('成長した人（直近3か月）').detail, '1人 ／ 前の3か月 0人')
        self.assertEqual(self.tile('成長した人（直近3か月）').value, f'{round(100 / people)}%')

    def test_archived_site_leaves_headcount(self):
        people = kpi.headcount()
        self.site.is_archived = True
        self.site.save()
        self.assertLess(kpi.headcount(), people)

    def test_rate_without_data_is_not_judged(self):
        tile = self.tile('改訂提案の決定率')
        self.assertEqual((tile.value, tile.met), ('―', None))

    def test_unpublished_is_not_counted(self):
        term = Term.objects.create(site=self.site, author=self.member, term='出荷', public_title='出荷',
                                   visibility=VISIBILITY_COMPANY, published_at=timezone.now())
        self.assertEqual(self.tile('公開したナレッジ（直近3か月）').value, '1件')
        term.visibility = VISIBILITY_TEAM
        term.save()
        self.assertEqual(self.tile('公開したナレッジ（直近3か月）').value, '0件')

    def test_proposal_rates(self):
        for status in ('adopted', 'declined', 'open', 'open'):
            KitProposal.objects.create(target='checklist', title='t', proposal='p', author=self.member, status=status)
        self.assertEqual(self.tile('改訂提案の決定率').value, '50%')
        self.assertEqual(self.tile('改訂提案の採用率').value, '50%')

    def test_management_lists_only_active_sites(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('genba:management')), '現場A')
        self.site.is_archived = True
        self.site.save()
        self.assertNotContains(self.client.get(reverse('genba:management')), '現場A')

    def test_seeded_page_renders(self):
        call_command('seed_genba', stdout=StringIO())
        self.client.post(reverse('genba:demo_switch', args=['company']))
        res = self.client.get(reverse('genba:company_kpi'))
        self.assertEqual(res.status_code, 200)
        for name in ('保険金請求 基本設計', '受発注システム刷新', 'demo_tanaka'):
            self.assertNotContains(res, name)
        # 履歴があるので、グラフが右肩上がりになっている
        knowledge = kpi.company_functions()[2]['chart']['series']
        self.assertGreater(sum(s['data'][-1] for s in knowledge), sum(s['data'][0] for s in knowledge))


class KnowledgeAssetTests(GenbaTestBase):
    """資産ベースの KPI（spec 015）：6つの資産の残高・増えた種類・別の現場での活用"""

    def setUp(self):
        super().setUp()
        self.company = CustomUser.objects.create_user('company', 'c@example.com', 'pass12345')
        self.add_group(self.company, GROUP_COMPANY)
        self.other_site = self.make_site('現場B', 'genba-b')
        kit.join_site(self.other_site, self.outsider, Membership.ROLE_MEMBER, self.today)

    def publish(self, **kw):
        return Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='t',
                                       visibility=VISIBILITY_COMPANY, public_title='t', published_at=timezone.now(), **kw)

    def tile(self, name):
        function = next(f for f in kpi.company_functions() if f['name'] == 'ナレッジ資産')
        return next(t for t in function['tiles'] if t.name == name)

    def test_balance_counts_six_assets_and_only_live_ones(self):
        self.publish()
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='非公開')
        Term.objects.create(site=self.site, author=self.member, term='a', visibility=VISIBILITY_COMPANY, published_at=timezone.now())
        InterviewQuestion.objects.create(question='q', author=self.member)
        for status, target in (('adopted', 'checklist'), ('adopted', 'phrases'), ('open', 'skillset'), ('declined', 'skillset')):
            KitProposal.objects.create(target=target, title='t', proposal='p', status=status, author=self.member)
        tile = self.tile('ナレッジ資産の残高')
        self.assertEqual((tile.value, tile.detail), ('5件', '直近3か月 +5件'))
        self.assertTrue(tile.met)
        self.assertEqual(self.tile('育っている資産').value, '5種類')

    def test_opened_from_other_site_counts_once_a_day(self):
        doc = self.publish()
        self.publish()
        self.client.force_login(self.outsider)
        url = reverse('genba:document_detail', args=[doc.pk])
        self.client.get(url)
        self.client.get(url)
        self.assertEqual(KnowledgeUse.objects.filter(document=doc).count(), 1)
        tile = self.tile('別の現場で開かれた文書')
        self.assertEqual((tile.value, tile.detail), ('50%', '1件 ／ 公開 2件'))

    def test_own_site_and_company_do_not_count(self):
        doc = self.publish()
        url = reverse('genba:document_detail', args=[doc.pk])
        for user in (self.member, self.company):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(KnowledgeUse.objects.exists())

    def test_no_public_documents_is_not_judged(self):
        self.assertIsNone(self.tile('別の現場で開かれた文書').met)

    def test_use_does_not_keep_who_opened(self):
        """人ごとに数えられる形にしない（憲章 II）"""
        names = {f.name for f in KnowledgeUse._meta.get_fields()}
        self.assertEqual(names, {'id', 'document', 'used_on'})

    def test_shown_to_company_but_not_to_member(self):
        self.client.force_login(self.company)
        self.assertContains(self.client.get(reverse('genba:company_kpi')), 'ナレッジ資産の残高')
        self.assertNotIn('ナレッジ資産', [f['name'] for f in kpi.for_role('member')])

    def test_demo_has_assets_growing_and_used(self):
        call_command('seed_genba', stdout=StringIO())
        function = next(f for f in kpi.company_functions() if f['name'] == 'ナレッジ資産')
        self.assertEqual(len(function['chart']['series']), 6)
        totals = [sum(s['data'][i] for s in function['chart']['series']) for i in (0, -1)]
        self.assertGreater(totals[1], totals[0])
        self.assertTrue(KnowledgeUse.objects.exists())


class MoneyTests(GenbaTestBase):
    """階級 × 人月の積み上げ（憲章 VI）と、立場ごとの見え方（spec 008）"""

    def setUp(self):
        super().setUp()
        self.company = CustomUser.objects.create_user('company', 'c@example.com', 'pass12345')
        self.add_group(self.company, GROUP_COMPANY)
        self.manager = CustomUser.objects.create_user('manager', 'k@example.com', 'pass12345')
        self.add_group(self.manager, GROUP_MANAGEMENT)
        self.other = self.make_site('現場B', 'genba-b', industry='保険業')
        self.g1 = Grade.objects.create(name='G1', standard_price=800000, standard_cost=500000)
        self.g2 = Grade.objects.create(name='G2', standard_price=930000, standard_cost=700000)
        GradeAssignment.objects.create(user=self.member, grade=self.g1)
        GradeAssignment.objects.create(user=self.leader, grade=self.g2)
        self.month = self.today.replace(day=1)
        self.work(self.site, self.member, self.g1)
        self.work(self.site, self.leader, self.g2, '0.5')
        self.work(self.other, self.member, self.g1)

    def work(self, site, user, grade, man_month='1'):
        return MonthlyWork.objects.create(membership=site.memberships.get(user=user), month=self.month, grade=grade,
                                          man_month=Decimal(man_month))

    def test_every_level_adds_up(self):
        r = money.rollup(self.month, self.today)
        self.assertEqual(r['sites']['現場A'].revenue, 800000 + 465000)
        self.assertEqual(r['sites']['現場A'].cost, 500000 + 350000)
        self.assertEqual(r['total'].revenue, sum(a.revenue for a in r['people'].values()))
        self.assertEqual(r['total'].revenue, sum(a.revenue for a in r['sites'].values()))
        self.assertEqual(r['total'].gross, sum(a.gross for a in r['industries'].values()))

    def test_operating_rate_counts_people_once(self):
        CompanyMonth.objects.create(month=self.month, headcount=4)
        self.assertEqual(money.operating(self.today), (2, 4, 50))

    def test_operating_rate_without_headcount(self):
        self.assertEqual(money.operating(self.today), (2, None, None))

    def test_team_reads_all_rows_but_no_cost(self):
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:work', args=[self.site.pk]))
        self.assertContains(res, '800,000')
        self.assertContains(res, '465,000')
        self.assertContains(res, '現場の標準売上')
        self.assertNotContains(res, '原価')
        self.assertNotContains(res, '粗利')

    def test_member_enters_own_man_month_only(self):
        MonthlyWork.objects.all().delete()
        self.client.force_login(self.member)
        self.client.post(reverse('genba:work', args=[self.site.pk]), {'man_month': '0.75'})
        work = MonthlyWork.objects.get()
        self.assertEqual((work.membership.user, work.grade, work.man_month), (self.member, self.g1, Decimal('0.75')))

    def test_grade_change_keeps_past_month(self):
        GradeAssignment.objects.filter(user=self.member).update(grade=self.g2)
        self.client.force_login(self.member)
        self.client.post(reverse('genba:work', args=[self.site.pk]), {'man_month': '0.5'})
        work = self.site.memberships.get(user=self.member).works.get()
        self.assertEqual((work.grade, work.man_month), (self.g1, Decimal('0.5')))

    def test_no_grade_cannot_enter(self):
        GradeAssignment.objects.filter(user=self.member).delete()
        self.client.force_login(self.member)
        url = reverse('genba:work', args=[self.site.pk])
        self.assertEqual(self.client.post(url, {'man_month': '1'}).status_code, 403)
        self.assertNotContains(self.client.get(url), 'name="man_month"')

    def test_outsiders_cannot_read(self):
        for user in (self.outsider, self.manager):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('genba:work', args=[self.site.pk])).status_code, 403)

    def test_archived_site_is_read_only(self):
        self.site.is_archived = True
        self.site.save()
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(reverse('genba:work', args=[self.site.pk]), {'man_month': '1'}).status_code, 403)

    def test_no_real_price_field(self):
        names = {f.name for model in (MonthlyWork, Membership) for f in model._meta.get_fields()}
        self.assertFalse(names & {'unit_price', 'contract_amount', 'salary'})

    def test_member_kpi_has_no_money_or_people(self):
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:role_kpi'))
        self.assertContains(res, '層の薄い項目')
        for text in ('売上', '粗利', '稼働率', '成長した人', '在籍'):
            self.assertNotContains(res, text)

    def test_leader_kpi_adds_growth_and_own_site(self):
        self.client.force_login(self.leader)
        res = self.client.get(reverse('genba:role_kpi'))
        self.assertContains(res, '成長した人')
        self.assertContains(res, '自分の現場の運営指標')
        self.assertNotContains(res, '粗利')

    def test_management_sees_each_site(self):
        self.client.force_login(self.manager)
        result = next(f for f in kpi.for_role('management') if f['name'] == '経営の結果')
        names = [s['label'] for s in result['chart']['series']]
        self.assertEqual(names, ['現場A', '現場B'])
        self.assertContains(self.client.get(reverse('genba:role_kpi')), '標準粗利率')
        self.assertContains(self.client.get(reverse('genba:management')), '現場ごとの標準売上と標準粗利')

    def test_company_sees_industries_not_sites(self):
        self.client.force_login(self.company)
        res = self.client.get(reverse('genba:company_kpi'))
        self.assertContains(res, '保険業')
        self.assertNotContains(res, '現場A')
        self.assertRedirects(self.client.get(reverse('genba:role_kpi')), reverse('genba:company_kpi'))

    def test_seed_rolls_up(self):
        call_command('seed_genba', stdout=StringIO())
        call_command('seed_genba', stdout=StringIO())
        r = money.rollup(date(2000, 1, 1), self.today)
        self.assertGreater(r['total'].revenue, 0)
        self.assertEqual(r['total'].revenue, sum(w.revenue for w in MonthlyWork.objects.select_related('grade')))


class ManagementKpiTests(GenbaTestBase):
    """マネジメントの職掌とKPI（spec 009）"""

    def setUp(self):
        super().setUp()
        self.manager = CustomUser.objects.create_user('manager', 'k@example.com', 'pass12345')
        self.add_group(self.manager, GROUP_MANAGEMENT)

    def tile(self, name):
        return next(t for f in kpi.management_functions() for t in f['tiles'] if t.name == name)

    def test_four_functions_before_company_reference(self):
        self.client.force_login(self.manager)
        res = self.client.get(reverse('genba:role_kpi'))
        body = res.content.decode()
        self.assertEqual([f['name'] for f in res.context['functions']], ['現場の運営', '人の成長の確定', 'アサイン', '約束の調整'])
        self.assertLess(body.index('現場の運営'), body.index('会社のKPI（参考）'))
        self.assertEqual([f['name'] for f in res.context['reference'] if f['chart']], ['成長の寄与', '経営の結果'])

    def test_others_do_not_get_management_functions(self):
        self.client.force_login(self.leader)
        res = self.client.get(reverse('genba:role_kpi'))
        self.assertNotContains(res, '承認待ちの平均日数')

    def test_alert_sites_ignore_archived(self):
        self.make_site('現場B', 'genba-b')
        PromiseCheck.objects.bulk_create([PromiseCheck(site=self.site, author=self.member, promise='p', actual='a', is_gap=True)] * 2)
        before = self.tile('約束と実際のズレ').value
        self.assertEqual(before, '2件')
        self.site.is_archived = True
        self.site.save()
        self.assertEqual(self.tile('約束と実際のズレ').value, '0件')

    def test_waiting_days_use_confirmation_date(self):
        Goal.objects.create(site=self.site, author=self.member, skill_item='デザイン', current_level=2, target_text='t',
                            submitted_at=timezone.now() - timedelta(days=9), confirmed_at=timezone.now() - timedelta(days=4))
        self.assertEqual(self.tile('承認待ちの平均日数').value, '4.0日')

    def test_unconfirmed_goal_with_leader_is_not_waiting(self):
        Goal.objects.create(site=self.site, author=self.member, skill_item='デザイン', current_level=2, target_text='t',
                            submitted_at=timezone.now())
        tile = self.tile('承認待ちの平均日数')
        self.assertEqual((tile.value, tile.met), ('―', None))

    def test_next_goal_counts_people(self):
        for item, text in (('デザイン', '次の目標'), ('業務分析', '')):
            Goal.objects.create(site=self.site, author=self.member, skill_item=item, current_level=2, target_text='t', next_text=text,
                                approved_on=self.today, approved_level=3, approved_grade='B')
        Goal.objects.create(site=self.site, author=self.leader, skill_item='デザイン', current_level=2, target_text='t',
                            approved_on=self.today, approved_level=3, approved_grade='B')
        self.assertEqual(self.tile('次の目標を持つ人').detail, '1人 ／ 2人')

    def test_seeded_page_renders(self):
        call_command('seed_genba', stdout=StringIO())
        self.client.post(reverse('genba:demo_switch', args=['management']))
        self.assertContains(self.client.get(reverse('genba:role_kpi')), 'マネジメントの職掌とKPI')


class LeaderKpiTests(GenbaTestBase):
    """リーダーの職掌とKPI（spec 010）"""

    def setUp(self):
        super().setUp()
        self.manager = CustomUser.objects.create_user('manager', 'k@example.com', 'pass12345')
        self.add_group(self.manager, GROUP_MANAGEMENT)

    def tile(self, name):
        return next(t for f in kpi.leader_functions(self.site, self.leader) for t in f['tiles'] if t.name == name)

    def test_panels_for_each_active_site(self):
        other = self.make_site('現場B', 'genba-b')
        archived = self.make_site('現場D', 'genba-d')
        archived.is_archived = True
        archived.save()
        self.client.force_login(self.leader)
        res = self.client.get(reverse('genba:role_kpi'))
        self.assertEqual({p['site'] for p in res.context['panels']}, {self.site, other})
        self.assertEqual([f['name'] for f in res.context['panels'][0]['functions']],
                         ['詰まりの解消', 'メンバーの育成', 'チームの運営', '知見の資産化'])
        ids = [c['id'] for c in res.context['charts']]
        self.assertEqual(len(ids), len(set(ids)))
        body = res.content.decode()
        self.assertLess(body.index('確認事項の平均滞留日数'), body.index('会社のKPI（参考）'))
        self.assertNotContains(res, '使わない指標')

    def test_not_shown_to_member_or_management(self):
        for user in (self.member, self.manager):
            self.client.force_login(user)
            self.assertNotContains(self.client.get(reverse('genba:role_kpi')), 'まとめに返した割合')

    def test_no_member_names_in_kpi(self):
        self.client.force_login(self.leader)
        self.assertNoUsername(self.client.get(reverse('genba:role_kpi')), self.member.username)

    def test_support_rate(self):
        for on in (self.today, None):
            Question.objects.create(site=self.site, author=self.member, title='t', unreadable=True, support_on=on)
        self.assertEqual(self.tile('説明の時間を割り当てた割合').value, '50%')

    def test_rates_exclude_leader(self):
        self.site.memberships.filter(user=self.leader).update(line_updated_at=timezone.now())
        self.assertEqual(self.tile('裁量の線を書いた人').detail, '0人 ／ 1人')
        Summary.objects.create(site=self.site, author=self.leader, submitted_at=timezone.now())
        self.assertEqual(self.tile('まとめに返した割合').value, '―')
        Summary.objects.create(site=self.site, author=self.member, submitted_at=timezone.now(), feedback_at=timezone.now())
        self.assertEqual(self.tile('まとめに返した割合').value, '100%')

    def test_reach_waiting_counts_unconfirmed_only(self):
        for confirmed in (None, timezone.now()):
            Goal.objects.create(site=self.site, author=self.member, skill_item='デザイン', current_level=2, target_text='t',
                                submitted_at=timezone.now(), confirmed_at=confirmed)
        self.assertEqual(self.tile('成長の確認待ち').value, '1件')

    def test_candidates_counts_docs_and_terms(self):
        Term.objects.create(site=self.site, author=self.member, term='a', knowledge_status=KNOWLEDGE_CANDIDATE)
        Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='d',
                                knowledge_status=KNOWLEDGE_CANDIDATE)
        self.assertEqual(self.tile('公開待ちの候補').value, '2件')

    def test_seeded_page_renders(self):
        call_command('seed_genba', stdout=StringIO())
        self.client.post(reverse('genba:demo_switch', args=['leader']))
        self.assertContains(self.client.get(reverse('genba:role_kpi')), 'リーダーの職掌とKPI')


class MemberFunctionTests(GenbaTestBase):
    """メンバーの職掌（spec 011）"""

    def setUp(self):
        super().setUp()
        self.other = CustomUser.objects.create_user('other', 'x@example.com', 'pass12345')
        Membership.objects.create(site=self.site, user=self.other, joined_on=self.today)

    def tile(self, name, user=None):
        return next(t for f in kpi.member_functions(self.site, user or self.member) for t in f['tiles'] if t.name == name)

    def test_page_shows_own_panel_first(self):
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:role_kpi'))
        self.assertEqual([f['name'] for f in res.context['panels'][0]['functions']],
                         ['現場の理解', '担当の遂行', '状況の共有', '自分の成長'])
        body = res.content.decode()
        self.assertLess(body.index('入場時チェックリスト'), body.index('会社のKPI（参考）'))

    def test_no_unmet_label_or_target(self):
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:role_kpi'))
        panel = res.context['panels'][0]['functions']
        for function in panel:
            for tile in function['tiles']:
                self.assertEqual(tile.target, '')
                self.assertIsNot(tile.met, False)

    def test_only_own_records(self):
        Question.objects.create(site=self.site, author=self.other, kind=Question.KIND_TASK, title='t',
                                due_on=self.today - timedelta(days=3))
        self.assertEqual(self.tile('予定日を過ぎた作業').value, '0件')
        self.assertEqual(self.tile('予定日を過ぎた作業', self.other).value, '1件')

    def test_not_shown_to_others(self):
        self.client.force_login(self.leader)
        res = self.client.get(reverse('genba:role_kpi'))
        self.assertNotContains(res, '今出ている危険信号')
        self.assertNotIn('member-weekly', res.content.decode())

    def test_on_time_rate(self):
        for done in (self.today, self.today + timedelta(days=2)):
            Question.objects.create(site=self.site, author=self.member, kind=Question.KIND_TASK, title='t', due_on=self.today,
                                    done_on=done, is_open=False)
        self.assertEqual(self.tile('予定どおり終えた作業').value, '50%')

    def test_monthly_work_state(self):
        self.assertEqual(self.tile('今月の人月').value, 'まだ')
        grade = Grade.objects.create(name='G1', standard_price=1, standard_cost=1)
        MonthlyWork.objects.create(membership=self.site.memberships.get(user=self.member), month=self.today, grade=grade)
        self.assertEqual(self.tile('今月の人月').value, '入れた')

    def test_goal_stage_uses_latest(self):
        self.assertEqual(self.tile('目標の段階').value, '目標がまだ')
        Goal.objects.create(site=self.site, author=self.member, skill_item='デザイン', current_level=2, target_text='t',
                            submitted_at=timezone.now(), next_text='次')
        self.assertEqual(self.tile('目標の段階').value, '確認待ち')
        self.assertEqual(self.tile('次の目標').value, 'ある')

    def test_archived_site_has_no_panel(self):
        self.site.is_archived = True
        self.site.save()
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('genba:role_kpi')).context['panels'], [])

    def test_seeded_page_renders(self):
        call_command('seed_genba', stdout=StringIO())
        self.client.post(reverse('genba:demo_switch', args=['member']))
        self.assertContains(self.client.get(reverse('genba:role_kpi')), 'メンバーの職掌')


class GrowthChainTests(GenbaTestBase):
    """成長の連鎖（憲章 VII・spec 012）：タスクの消化 → 成長 → 階級 → 標準売上 → 会社の成長"""

    def setUp(self):
        super().setUp()
        self.company = CustomUser.objects.create_user('company', 'c@example.com', 'pass12345')
        self.add_group(self.company, GROUP_COMPANY)
        self.manager = CustomUser.objects.create_user('manager', 'k@example.com', 'pass12345')
        self.add_group(self.manager, GROUP_MANAGEMENT)
        self.g1 = Grade.objects.create(name='G1', order=0, standard_price=700000, standard_cost=500000, required_level=2)
        self.g2 = Grade.objects.create(name='G2', order=1, standard_price=800000, standard_cost=550000, required_level=3)
        GradeAssignment.objects.create(user=self.member, grade=self.g1)
        self.goal = Goal.objects.create(site=self.site, author=self.member, skill_item='デザイン', current_level=2, target_text='t',
                                        approved_on=self.today, approved_level=3, approved_grade='B')

    def test_chain_from_growth_to_sales(self):
        """承認した成長が、提案と採用を通って、次に入れる月の標準売上まで届く"""
        membership = self.site.memberships.get(user=self.member)
        month = self.today.replace(day=1)
        MonthlyWork.objects.create(membership=membership, month=month, grade=self.g1)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('genba:management')), '会社に提案する')
        self.client.post(reverse('genba:grade_propose', args=[self.goal.pk]))
        review = GradeReview.objects.get()
        self.assertEqual((review.current_grade, review.proposed_grade), (self.g1, self.g2))
        self.client.force_login(self.company)
        self.assertContains(self.client.get(reverse('genba:company_kpi')), '階級の見直し')
        self.client.post(reverse('genba:grade_decide', args=[review.pk, 'adopt']))
        self.assertEqual(GradeAssignment.objects.get(user=self.member).grade, self.g2)
        # 過去の行は動かず、次の月から新しい階級の標準単価になる
        self.assertEqual(membership.works.get(month=month).grade, self.g1)
        following = (month + timedelta(days=32)).replace(day=1)
        self.client.force_login(self.member)
        self.client.post(reverse('genba:work', args=[self.site.pk]) + f'?month={following:%Y-%m}', {'man_month': '1'})
        self.assertEqual(membership.works.get(month=following).revenue, 800000)
        self.assertEqual(money.growth_share(following, following), (800000, 100000))

    def test_only_management_proposes_and_company_decides(self):
        url = reverse('genba:grade_propose', args=[self.goal.pk])
        self.client.force_login(self.company)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.manager)
        self.client.post(url)
        decide = reverse('genba:grade_decide', args=[GradeReview.objects.get().pk, 'adopt'])
        self.assertEqual(self.client.post(decide).status_code, 403)
        self.assertEqual(GradeAssignment.objects.get(user=self.member).grade, self.g1)

    def test_grade_is_not_raised_automatically(self):
        self.assertEqual(GradeAssignment.objects.get(user=self.member).grade, self.g1)

    def test_below_required_level_is_not_a_candidate(self):
        self.goal.approved_level = 2
        self.goal.save()
        self.assertEqual(grades.candidates(), [])

    def test_declined_returns_with_next_growth_only(self):
        review = grades.propose(self.goal, self.manager)
        grades.decide(review, self.company, adopted=False)
        self.assertEqual(GradeAssignment.objects.get(user=self.member).grade, self.g1)
        self.assertEqual(grades.candidates(), [])
        Goal.objects.create(site=self.site, author=self.member, skill_item='業務分析', current_level=3, target_text='t',
                            approved_on=self.today, approved_level=4, approved_grade='A')
        self.assertEqual(len(grades.candidates()), 1)

    def test_candidate_not_shown_to_self(self):
        self.assertEqual(grades.candidates(self.member), [])

    def test_finished_task_becomes_evidence_candidate(self):
        site = self.site
        done = Question.objects.create(site=site, author=self.member, kind=Question.KIND_TASK, title='終えた作業',
                                       due_on=self.today, is_open=False, done_on=self.today)
        check = Question.objects.create(site=site, author=self.member, kind=Question.KIND_TASK, title='チェック', stage='day1',
                                        due_on=self.today, is_open=False, done_on=self.today)
        Question.objects.create(site=site, author=self.member, kind=Question.KIND_TASK, title='途中の作業', due_on=self.today)
        ids = {r['id'] for r in growth.candidates(self.goal) if r['type'] == 'issue'}
        self.assertEqual(ids, {done.pk, check.pk})

    def test_used_task_shows_for_member_and_upward(self):
        task = Question.objects.create(site=self.site, author=self.member, kind=Question.KIND_TASK, title='作業',
                                       due_on=self.today, is_open=False, done_on=self.today)
        GoalEvidence.objects.create(goal=self.goal, record_type='issue', record_id=task.pk, title='作業')
        member = next(t for f in kpi.member_functions(self.site, self.member) for t in f['tiles'] if t.name == '成長の証拠にした作業')
        leader = next(t for f in kpi.leader_functions(self.site, self.leader) for t in f['tiles'] if t.name == '成長の証拠になった作業')
        manager = next(t for f in kpi.management_functions() for t in f['tiles'] if t.name == '作業を証拠にした成長')
        self.assertEqual((member.value, leader.value, manager.value), ('1件', '100%', '100%'))

    def test_member_sees_next_grade(self):
        tile = next(t for f in kpi.member_functions(self.site, self.member) for t in f['tiles'] if t.name == '今の階級')
        self.assertEqual((tile.value, tile.detail), ('G1', '次のG2はレベル3で見直し'))

    def test_company_order_starts_with_growth_and_ends_with_result(self):
        names = [f['name'] for f in kpi.company_functions()]
        self.assertEqual(names[0], '人材開発')
        self.assertEqual(names[-2:], ['成長の寄与', '経営の結果'])

    def test_seed_shows_chain(self):
        call_command('seed_genba', stdout=StringIO())
        call_command('seed_genba', stdout=StringIO())
        self.assertEqual([c['user'].username for c in grades.candidates() if c['user'].username.startswith('demo_')], ['demo_watanabe'])
        self.assertEqual(GradeReview.objects.filter(status='open').count(), 1)
        span = kpi.periods()[-1]
        self.assertGreater(money.growth_share(span.start, span.end)[1], 0)


class VisionTests(GenbaTestBase):
    """ビジョンの統合（憲章 VII・spec 013）：1メンバー1リポジトリ、上から下への流れ、アサインの輪"""

    def setUp(self):
        super().setUp()
        self.company = CustomUser.objects.create_user('company', 'c@example.com', 'pass12345')
        self.add_group(self.company, GROUP_COMPANY)
        self.manager = CustomUser.objects.create_user('manager', 'k@example.com', 'pass12345')
        self.add_group(self.manager, GROUP_MANAGEMENT)

    def test_one_repository_per_member_across_sites(self):
        first = kit.ensure_repository(self.outsider)
        site = self.make_site('現場B', 'genba-b')
        kit.join_site(site, self.outsider, Membership.ROLE_MEMBER, self.today)
        other = self.make_site('現場C', 'genba-c')
        kit.join_site(other, self.outsider, Membership.ROLE_MEMBER, self.today)
        self.assertEqual(MemberRepository.objects.filter(user=self.outsider).count(), 1)
        self.assertEqual(self.outsider.genba_repository, first)

    def test_programmer_gets_programmer_checklist(self):
        site = self.make_site('現場B', 'genba-b')
        kit.join_site(site, self.outsider, Membership.ROLE_MEMBER, self.today, job_type=kit.JOB_PROGRAMMER)
        items = list(ChecklistItem.objects.filter(question__site=site, question__author=self.outsider).values_list('text', flat=True))
        self.assertIn('担当工程（詳細設計／製造／単体テスト／結合テスト）と担当機能の範囲', items)
        self.assertIn('開発環境で既存のコードを動かし、担当機能の処理を追う', items)
        self.assertNotIn('担当工程（要件定義／基本設計／詳細設計）と担当機能の範囲', items)

    def test_programmer_replacements_match_source_items(self):
        """元資料の文言が変わったら、差し替えが黙って効かなくなるので気づけるようにする"""
        items = {i for stage in kit_data.CHECKLIST_STAGES for section in stage['sections'] for i in section['items']}
        titles = {section['title'] for stage in kit_data.CHECKLIST_STAGES for section in stage['sections']}
        self.assertLessEqual(set(kit.PROGRAMMER_REPLACED_ITEMS), items)
        self.assertLessEqual(set(kit.PROGRAMMER_ADDED_ITEMS), titles)

    def test_programmer_checklist_keeps_stages_and_targets(self):
        def shape(job):
            return [(sec['title'], sec['targets']) for stage in kit.checklist(job) for sec in stage['sections']]
        self.assertEqual(shape(kit.JOB_PROGRAMMER), shape(kit.JOB_SE))

    def test_changing_job_keeps_one_repository_and_existing_checklist(self):
        """プログラマーが SE になっても、リポジトリは増えず、入っている現場のチェックリストも変わらない"""
        site = self.make_site('現場B', 'genba-b')
        kit.join_site(site, self.outsider, Membership.ROLE_MEMBER, self.today, job_type=kit.JOB_PROGRAMMER)
        before = ChecklistItem.objects.filter(question__author=self.outsider).count()
        self.client.force_login(self.outsider)
        res = self.client.post(reverse('genba:repository_job'), {'job_type': kit.JOB_SE})
        self.assertRedirects(res, reverse('genba:member_home'))
        self.outsider.genba_repository.refresh_from_db()
        self.assertEqual(self.outsider.genba_repository.job_type, kit.JOB_SE)
        self.assertEqual(MemberRepository.objects.filter(user=self.outsider).count(), 1)
        self.assertEqual(ChecklistItem.objects.filter(question__author=self.outsider).count(), before)
        other = self.make_site('現場C', 'genba-c')
        kit.join_site(other, self.outsider, Membership.ROLE_MEMBER, self.today)
        self.assertTrue(ChecklistItem.objects.filter(question__site=other, text__startswith='担当工程（要件定義').exists())

    def test_invalid_job_is_ignored(self):
        repository = kit.ensure_repository(self.member, kit.JOB_PROGRAMMER)
        self.client.force_login(self.member)
        self.client.post(reverse('genba:repository_job'), {'job_type': 'manager'})
        repository.refresh_from_db()
        self.assertEqual(repository.job_type, kit.JOB_PROGRAMMER)

    def test_site_create_sets_job_and_starts_from_current_job(self):
        kit.ensure_repository(self.outsider, kit.JOB_PROGRAMMER)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('genba:site_create')).context['form'].initial['job_type'], kit.JOB_PROGRAMMER)
        self.client.post(reverse('genba:site_create'), {
            'name': '現場D', 'team_slug': 'genba-d', 'industry': '製造業', 'started_on': self.today,
            'my_role': Membership.ROLE_MEMBER, 'job_type': kit.JOB_SE})
        self.outsider.genba_repository.refresh_from_db()
        self.assertEqual(self.outsider.genba_repository.job_type, kit.JOB_SE)

    def test_kit_page_shows_own_job_and_switches(self):
        kit.ensure_repository(self.member, kit.JOB_PROGRAMMER)
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse('genba:kit')), kit.PROGRAMMER_CHECKLIST_TITLE)
        self.assertContains(self.client.get(reverse('genba:kit'), {'job': kit.JOB_SE}), kit_data.CHECKLIST_TITLE)

    def test_members_tab_shows_job(self):
        kit.ensure_repository(self.member, kit.JOB_PROGRAMMER)
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:site_detail', args=[self.site.pk]), {'tab': 'members'})
        self.assertContains(res, '<td>プログラマー</td>')

    def test_ledger_separates_jobs(self):
        """スキル台帳は職種で分けて見られる。集計も同じ絞り込みになる"""
        kit.ensure_repository(self.member, kit.JOB_PROGRAMMER)
        kit.ensure_repository(self.leader, kit.JOB_SE)
        for author, item in ((self.member, 'デザイン'), (self.leader, 'テクノロジ')):
            Goal.objects.create(site=self.site, author=author, skill_item=item, current_level=2, target_text='t',
                                approved_on=self.today, approved_by=self.manager, approved_level=3, approved_grade='B')
        self.client.force_login(self.company)
        def people(**params):
            return [row['author'] for row in self.client.get(reverse('genba:company'), params).context['ledger']]
        self.assertCountEqual(people(), [self.member, self.leader])
        self.assertEqual(people(job=kit.JOB_PROGRAMMER), [self.member])
        self.assertEqual(people(job=kit.JOB_SE), [self.leader])

    def test_members_tab_shows_template_version_of_repository(self):
        """テンプレートの版は現場ではなくメンバーのリポジトリが持つ"""
        repository = kit.ensure_repository(self.member)
        repository.template_version = '2099-01'
        repository.save()
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse('genba:site_detail', args=[self.site.pk])), 'テンプレート ')
        res = self.client.get(reverse('genba:site_detail', args=[self.site.pk]), {'tab': 'members'})
        self.assertContains(res, '<td class="text-muted">2099-01</td>')

    def test_records_stay_with_person_across_sites(self):
        """現場が変わっても、本人の記録は本人の分として数えられる"""
        kit.ensure_repository(self.member)
        other = self.make_site('現場B', 'genba-b')
        Term.objects.create(site=self.site, author=self.member, term='a')
        Term.objects.create(site=other, author=self.member, term='b')
        self.assertEqual(self.member.genba_terms.count(), 2)

    def test_vision_on_every_role_page(self):
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse('genba:role_kpi')), '会社の成長は、人から始まる。')
        self.client.force_login(self.leader)
        self.assertContains(self.client.get(reverse('genba:role_kpi')), '人の心を豊かにする')
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('genba:role_kpi')), '社会全体を豊かにする')
        self.client.force_login(self.company)
        self.assertContains(self.client.get(reverse('genba:company_kpi')), '会社の成長は、人から始まる。')

    def test_company_sets_focus_and_it_reaches_goal_form(self):
        url = reverse('genba:focus_update')
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(url, {'items': ['デザイン']}).status_code, 403)
        self.client.force_login(self.company)
        self.client.post(url, {'items': ['デザイン', '存在しない項目']})
        self.assertEqual(growth.focus_items(), ['デザイン'])
        self.client.force_login(self.member)
        res = self.client.get(reverse('genba:goal_create', args=[self.site.pk]))
        self.assertContains(res, '★ デザイン')

    def test_focus_does_not_force_choice(self):
        FocusSkill.objects.create(skill_item='デザイン')
        form = forms.GoalPickForm({'item': '業務分析', 'level': 2})
        self.assertTrue(form.is_valid())

    def test_focus_rate_counts_open_goals(self):
        FocusSkill.objects.create(skill_item='デザイン')
        for item in ('デザイン', '業務分析'):
            Goal.objects.create(site=self.site, author=self.member, skill_item=item, current_level=2, target_text='t')
        tile = next(t for f in kpi.company_functions() for t in f['tiles'] if t.name == '重点に向かう目標')
        self.assertEqual(tile.value, '50%')

    def goal(self, author):
        return Goal.objects.create(site=self.site, author=author, skill_item='デザイン', current_level=2, target_text='t',
                                   next_text='次', approved_on=self.today, approved_level=3, approved_grade='B')

    def test_assignment_closes_loop(self):
        goal = self.goal(self.outsider)
        other = self.make_site('現場B', 'genba-b')
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('genba:management')), '次の目標に合う現場へアサイン')
        self.client.post(reverse('genba:assignment_create', args=[goal.pk]), {'site': other.pk, 'matched': 'on'})
        record = Assignment.objects.get()
        self.assertEqual((record.site, record.matched), (other, True))
        self.assertTrue(other.memberships.filter(user=self.outsider).exists())
        self.assertTrue(self.outsider.genba_repository)
        self.assertEqual(kpi.assignment_candidates(self.manager), [])
        tile = next(t for f in kpi.management_functions() for t in f['tiles'] if t.name == '目標に合うアサイン')
        self.assertEqual(tile.value, '100%')

    def test_only_management_records_assignment(self):
        goal = self.goal(self.member)
        self.client.force_login(self.company)
        url = reverse('genba:assignment_create', args=[goal.pk])
        self.assertEqual(self.client.post(url, {'site': self.site.pk}).status_code, 403)

    def test_archived_site_cannot_be_assigned(self):
        goal = self.goal(self.member)
        other = self.make_site('現場B', 'genba-b')
        other.is_archived = True
        other.save()
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(reverse('genba:assignment_create', args=[goal.pk]), {'site': other.pk}).status_code, 404)

    def test_seed_has_vision_chain(self):
        call_command('seed_genba', stdout=StringIO())
        call_command('seed_genba', stdout=StringIO())
        self.assertTrue(MemberRepository.objects.filter(user__username='demo_kimura').exists())
        self.assertEqual(Assignment.objects.count(), 3)
        self.assertTrue(growth.focus_items())


class AboutTests(TestCase):
    def test_about_opens_without_login_and_links_to_app(self):
        res = self.client.get(reverse('genba:about'))
        self.assertContains(res, '会社の成長は、人から始まる。')
        self.assertContains(res, f'href="{reverse("genba:demo")}"')
        self.assertContains(res, 'デモ（genba）')
        self.assertContains(res, f'href="{reverse("genba:concept")}"')

    def test_no_site_header(self):
        for name in ('about', 'concept', 'demo', 'portal'):
            res = self.client.get(reverse(f'genba:{name}'))
            self.assertEqual(res.status_code, 200)
            self.assertNotContains(res, 'HIRAHIRAROOM')
            self.assertNotContains(res, 'Log In')

    def test_pvsys_opens_about_first(self):
        res = self.client.get(reverse('pvsys:index'))
        self.assertContains(res, f'href="{reverse("genba:about")}"')
        self.assertNotContains(res, f'href="{reverse("genba:portal")}"')


class DemoGuideTests(TestCase):
    def test_shows_organization_sites_and_members_without_login(self):
        call_command('seed_genba', stdout=StringIO())
        res = self.client.get(reverse('genba:demo'))
        for name in ('受発注システム刷新', '出荷バッチ改修', '保険金請求 基本設計', '店舗在庫の可視化', 'demo_sato', 'demo_tanaka',
                     'demo_kobayashi', 'ひとり現場', '立場を選ぶ'):
            self.assertContains(res, name)
        for key in ('member', 'leader', 'management', 'company'):
            self.assertContains(res, reverse('genba:demo_switch', args=[key]))
        self.assertEqual(sum(len(t['sites']) for t in res.context['teams']), 4)
        solo = {s['site'].name for t in res.context['teams'] for s in t['sites'] if s['is_solo']}
        self.assertEqual(solo, {'出荷バッチ改修', '店舗在庫の可視化'})

    def test_opens_without_demo_data(self):
        self.assertEqual(self.client.get(reverse('genba:demo')).status_code, 200)


class PersonaTests(TestCase):
    def setUp(self):
        call_command('seed_genba', stdout=StringIO())
        self.leader_site = Site.objects.get(team_slug='genba-demo-order')

    def switch(self, key):
        return self.client.post(reverse('genba:demo_switch', args=[key]))

    def test_switch_opens_home_of_that_role(self):
        for key, name in (('member', 'member_home'), ('leader', 'leader_home'), ('management', 'management'), ('company', 'company')):
            self.assertRedirects(self.switch(key), reverse(f'genba:{name}'))

    def test_each_persona_has_only_its_own_role(self):
        self.switch('member')
        for name in ('leader_home', 'management', 'company'):
            self.assertEqual(self.client.get(reverse(f'genba:{name}')).status_code, 403, name)
        self.switch('company')
        self.assertEqual(self.client.get(reverse('genba:management')).status_code, 403)
        self.switch('management')
        self.assertEqual(self.client.get(reverse('genba:company')).status_code, 403)

    def test_switch_is_per_visitor(self):
        other = Client()
        self.switch('leader')
        self.assertEqual(self.client.get(reverse('genba:leader_home')).status_code, 200)
        self.assertEqual(other.get(reverse('genba:leader_home')).status_code, 403)

    def test_switch_bar_marks_current_persona(self):
        self.switch('leader')
        res = self.client.get(reverse('genba:leader_home'))
        self.assertContains(res, f'btn btn-sm btn-primary" formaction="{reverse("genba:demo_switch", args=["leader"])}"')

    def test_logged_in_user_stays_self_until_choosing(self):
        user = CustomUser.objects.create_user('me', 'me@example.com', 'pass12345')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('genba:member_home')).context['user'], user)
        self.switch('leader')
        self.assertEqual(self.client.get(reverse('genba:leader_home')).status_code, 200)

    def test_unknown_persona_and_get_are_rejected(self):
        self.assertEqual(self.switch('nobody').status_code, 404)
        self.assertEqual(self.client.get(reverse('genba:demo_switch', args=['leader'])).status_code, 405)

    def test_management_approves_leaders_goal(self):
        goal = Goal.objects.get(site=self.leader_site, author__username='demo_sato')
        self.switch('leader')
        self.client.post(reverse('genba:goal_reach', args=[goal.pk]), {'action': 'confirm', 'comment': '確かめた'})
        self.switch('management')
        self.client.post(reverse('genba:goal_approval', args=[goal.pk]), {
            'approved_level': 3, 'approved_grade': 'B', 'action': 'approve'})
        goal.refresh_from_db()
        self.assertEqual((goal.stage, goal.confirmed_by.username, goal.approved_by.username),
                         (Goal.STAGE_APPROVED, 'demo_kobayashi', 'demo_kacho'))


class ConceptTests(TestCase):
    def test_all_slides_open_without_login(self):
        res = self.client.get(reverse('genba:concept'))
        self.assertEqual(res.context['total'], 23)
        for text in ('genbaは、GitHubを土台として実現する', '現場の経験が、個人の中に閉じている', '立場ごとの成長のharmony',
                     '始める前に、6つを決める', '多様な人こそ会社の礎。\n行けるところまで、皆で行こう。', 'Pull Request のレビュー'):
            self.assertContains(res, text)
        self.assertContains(res, 'id="slide-23"')
        self.assertContains(res, 'id="deck-next"')
        self.assertContains(res, '#2F6B5E')
        self.assertContains(res, 'genba 構想 · 立場ごとの機能')
        self.assertContains(res, 'id="slide-22" class="slide slide-dark"')
        self.assertContains(res, f'href="{reverse("genba:about")}"')
