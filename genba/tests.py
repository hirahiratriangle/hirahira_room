from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser

from .metrics import site_metrics, weekly_rate
from .models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_NONE, KNOWLEDGE_REVIEWED, VISIBILITY_COMPANY, VISIBILITY_TEAM,
    Document, Membership, PromiseCheck, Question, Site, Term, week_start,
)
from .roles import GROUP_COMPANY, GROUP_MANAGEMENT


class GenbaTestBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.member = CustomUser.objects.create_user('member', 'm@example.com', 'pass12345')
        self.leader = CustomUser.objects.create_user('leader', 'l@example.com', 'pass12345')
        self.outsider = CustomUser.objects.create_user('outsider', 'o@example.com', 'pass12345')
        self.site = Site.objects.create(name='現場A', repo_name='genba-a', industry='製造業',
                                        started_on=self.today - timedelta(weeks=4))
        Membership.objects.create(site=self.site, user=self.member)
        Membership.objects.create(site=self.site, user=self.leader, role=Membership.ROLE_LEADER)

    def add_group(self, user, name):
        user.groups.add(Group.objects.get_or_create(name=name)[0])


class AccessTests(GenbaTestBase):
    def test_member_can_read_site(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('genba:site_detail', args=[self.site.pk])).status_code, 200)

    def test_outsider_cannot_read_site(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('genba:site_detail', args=[self.site.pk])).status_code, 403)

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


class RecordTests(GenbaTestBase):
    def test_member_adds_question_and_closes_it(self):
        self.client.force_login(self.member)
        res = self.client.post(reverse('genba:question_create', args=[self.site.pk]),
                               {'kind': 'question', 'title': '締め日は？', 'ask_to': '経理'})
        self.assertEqual(res.status_code, 302)
        q = Question.objects.get(title='締め日は？')
        self.assertEqual((q.site, q.author, q.is_open), (self.site, self.member, True))
        self.client.post(reverse('genba:question_toggle', args=[q.pk]))
        q.refresh_from_db()
        self.assertFalse(q.is_open)
        self.assertIsNotNone(q.closed_at)

    def test_outsider_cannot_add(self):
        self.client.force_login(self.outsider)
        res = self.client.post(reverse('genba:term_create', args=[self.site.pk]), {'term': 'x', 'meaning': 'y'})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Term.objects.exists())

    def test_weekly_report_week_is_monday(self):
        self.client.force_login(self.member)
        day = week_start(self.today) + timedelta(days=3)
        self.client.post(reverse('genba:document_create', args=[self.site.pk, 'weekly']),
                         {'title': '週報', 'body': '', 'week_of': day.isoformat(), 'risks': '遅れ'})
        doc = Document.objects.get(kind=Document.KIND_WEEKLY)
        self.assertEqual(doc.week_of, week_start(self.today))

    def test_only_author_can_edit_document(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_MINUTES, title='議事録')
        self.client.force_login(self.leader)
        self.assertEqual(self.client.get(reverse('genba:document_update', args=[doc.pk])).status_code, 403)


class KnowledgeFlowTests(GenbaTestBase):
    def act(self, user, obj, action, model='doc'):
        self.client.force_login(user)
        return self.client.post(reverse('genba:knowledge_action', args=[model, obj.pk, action]))

    def test_full_flow(self):
        doc = Document.objects.create(site=self.site, author=self.member, kind=Document.KIND_DESIGN, title='設計')
        self.assertEqual(self.act(self.member, doc, 'candidate').status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.knowledge_status, KNOWLEDGE_CANDIDATE)
        # 本人は確認済みにできない。リーダーだけ
        self.assertEqual(self.act(self.member, doc, 'review').status_code, 403)
        self.act(self.leader, doc, 'review')
        doc.refresh_from_db()
        self.assertEqual(doc.knowledge_status, KNOWLEDGE_REVIEWED)
        # 公開の最終判断は本人。リーダーは公開できない
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
        for name in ('portal', 'member_home', 'leader_home', 'management', 'company'):
            self.assertEqual(self.client.get(reverse(f'genba:{name}')).status_code, 200, name)
