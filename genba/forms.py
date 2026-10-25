from django import forms
from django.utils import timezone

from . import growth
from .models import (
    Document, Goal, InterviewQuestion, KitProposal, Membership, PromiseCheck, Question, Site, StructureGap, Summary, Term,
)


class BootstrapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs['class'] = 'form-check-input'
            elif isinstance(f.widget, (forms.Select, forms.RadioSelect)):
                f.widget.attrs['class'] = 'form-select'
            else:
                f.widget.attrs['class'] = 'form-control'


class SiteForm(BootstrapMixin, forms.ModelForm):
    """テンプレートから現場を作る。作った人はメンバーかリーダーとして入る。"""

    my_role = forms.ChoiceField(label='自分の立場', choices=Membership.ROLE_CHOICES, initial=Membership.ROLE_MEMBER)

    class Meta:
        model = Site
        fields = ['name', 'repo_name', 'industry', 'started_on', 'description']
        widgets = {
            'started_on': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_repo_name(self):
        name = self.cleaned_data['repo_name']
        if not name.startswith('genba-'):
            raise forms.ValidationError('リポジトリ名は genba- で始めてください。')
        return name


class IssueForm(BootstrapMixin, forms.ModelForm):
    """Issue の作成。種類（確認事項・読めない・課題・作業・スコープ外）で欄を変える。"""

    FIELDS = {
        'question': ['title', 'body', 'ask_to'],
        'unreadable': ['title', 'source', 'location', 'reason', 'ask_to'],
        'problem': ['title', 'fact', 'interpretation', 'unconfirmed', 'impact'],
        'task': ['title', 'body', 'due_on'],
        'scope_out': ['title', 'body', 'ask_to'],
    }

    class Meta:
        model = Question
        fields = ['title', 'body', 'ask_to', 'source', 'location', 'reason',
                  'fact', 'interpretation', 'unconfirmed', 'impact', 'due_on']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 4}),
            'fact': forms.Textarea(attrs={'rows': 3}),
            'interpretation': forms.Textarea(attrs={'rows': 3}),
            'unconfirmed': forms.Textarea(attrs={'rows': 3}),
            'impact': forms.Textarea(attrs={'rows': 2}),
            'due_on': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, issue_kind, **kwargs):
        super().__init__(*args, **kwargs)
        for name in list(self.fields):
            if name not in self.FIELDS[issue_kind]:
                del self.fields[name]
        if issue_kind == 'unreadable':
            self.fields['title'].help_text = '例：「受注仕様書 3.2 の引当ルールが読めない」'
            self.fields['reason'].required = True
            self.fields['source'].required = True
            self.fields['ask_to'].label = '説明を頼む相手'
        if issue_kind == 'problem':
            self.fields['fact'].required = True
        if issue_kind == 'scope_out':
            self.fields['body'].label = '頼まれたこと'
            self.fields['ask_to'].label = '相談する自社の相手'
            self.fields['ask_to'].initial = '課長'


class TaskProgressForm(BootstrapMixin, forms.ModelForm):
    """作業 Issue の予定と実績"""

    class Meta:
        model = Question
        fields = ['due_on', 'done_on', 'delay_note']
        widgets = {
            'due_on': forms.DateInput(attrs={'type': 'date'}),
            'done_on': forms.DateInput(attrs={'type': 'date'}),
            'delay_note': forms.Textarea(attrs={'rows': 2}),
        }


class TermForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Term
        fields = ['term', 'meaning', 'general_difference']
        widgets = {
            'meaning': forms.Textarea(attrs={'rows': 3}),
            'general_difference': forms.Textarea(attrs={'rows': 3}),
        }


class DocumentForm(BootstrapMixin, forms.ModelForm):
    """区分ごとに要る項目だけ出す"""

    WEEKLY_FIELDS = ['title', 'week_of', 'done', 'learned', 'stuck', 'next_step', 'goal_action']

    class Meta:
        model = Document
        fields = ['title', 'body', 'week_of', 'done', 'learned', 'stuck', 'next_step', 'goal_action', 'skill_item']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 10}),
            'week_of': forms.DateInput(attrs={'type': 'date'}),
            'done': forms.Textarea(attrs={'rows': 3}),
            'learned': forms.Textarea(attrs={'rows': 3}),
            'stuck': forms.Textarea(attrs={'rows': 3}),
            'next_step': forms.Textarea(attrs={'rows': 3}),
            'goal_action': forms.TextInput(),
        }

    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        if kind == Document.KIND_WEEKLY:
            keep = self.WEEKLY_FIELDS
            self.fields['week_of'].required = True
        else:
            keep = ['title', 'body'] + (['skill_item'] if kind == Document.KIND_RETRO else [])
        for name in list(self.fields):
            if name not in keep:
                del self.fields[name]


class SummaryForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Summary
        fields = ['overview', 'functions']
        widgets = {
            'overview': forms.Textarea(attrs={'rows': 5}),
            'functions': forms.Textarea(attrs={'rows': 5}),
        }


class SupportForm(BootstrapMixin, forms.ModelForm):
    """リーダーが「読めない」に説明の時間を割り当てる"""

    class Meta:
        model = Question
        fields = ['support_on', 'support_note']
        widgets = {'support_on': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['support_on'].required = True


class ResponseForm(BootstrapMixin, forms.ModelForm):
    """リーダーが懸念を受け取り、返事を書く"""

    class Meta:
        model = Question
        fields = ['response']
        widgets = {'response': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['response'].required = True


class FeedbackForm(BootstrapMixin, forms.ModelForm):
    """リーダーがまとめに差と次の作業を返す"""

    class Meta:
        model = Summary
        fields = ['feedback']
        widgets = {'feedback': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['feedback'].required = True


class LineForm(BootstrapMixin, forms.ModelForm):
    """裁量の線"""

    class Meta:
        model = Membership
        fields = ['line_self', 'line_consult']
        widgets = {'line_self': forms.Textarea(attrs={'rows': 3}), 'line_consult': forms.Textarea(attrs={'rows': 3})}


class SummaryReviewForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Summary
        fields = ['review_note']
        widgets = {'review_note': forms.Textarea(attrs={'rows': 3})}


class GoalPickForm(forms.Form):
    """目標を立てる1段目：スキル項目と今のレベルを選ぶ"""

    item = forms.ChoiceField(label='スキル項目', choices=[('', '選ぶ')] + [(n, n) for n in growth.SKILL_ITEMS],
                             widget=forms.Select(attrs={'class': 'form-select'}))
    level = forms.TypedChoiceField(label='今のレベル', coerce=int, choices=Goal.LEVEL_CHOICES[:4], initial=3,
                                   widget=forms.Select(attrs={'class': 'form-select'}))


class GoalForm(BootstrapMixin, forms.ModelForm):
    """目標を立てる2段目：一つ上の具体記述を目標の文にする"""

    class Meta:
        model = Goal
        fields = ['concrete_skill', 'target_text', 'preparation']
        widgets = {
            'target_text': forms.Textarea(attrs={'rows': 3}),
            'preparation': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, item, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['concrete_skill'] = forms.ChoiceField(
            label='基本設計の具体スキル', required=False,
            choices=[('', '（選ばない）')] + [(s['skill'], s['skill']) for s in growth.concrete_skills(item)],
            widget=forms.Select(attrs={'class': 'form-select'}),
        )


class ReachForm(BootstrapMixin, forms.ModelForm):
    """振り返りで到達を説明する"""

    complexity = forms.MultipleChoiceField(
        label='複雑性（当てはまるもの）', required=False, widget=forms.CheckboxSelectMultiple,
        choices=[(str(i), f'{i}. {text}') for i, text in enumerate(growth.data.COMPLEXITY, 1)],
    )
    request_agreement = forms.BooleanField(label='この到達について、マネジメント（課長）に合意を依頼する', required=False)

    class Meta:
        model = Goal
        fields = ['reached_level', 'explanation', 'mentored', 'role', 'team_size', 'next_text', 'share_next']
        widgets = {'explanation': forms.Textarea(attrs={'rows': 4}), 'next_text': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['complexity'].initial = [str(n) for n in self.instance.complexity_numbers]
        self.fields['complexity'].widget.attrs = {}
        self.fields['request_agreement'].initial = bool(self.instance.agreement_requested_at)

    def save(self, commit=True):
        goal = super().save(commit=False)
        goal.complexity = ','.join(sorted(self.cleaned_data['complexity'], key=int))
        # 合意したあとで到達の中身を変えたら、合意はやり直し
        if goal.agreed_on and set(self.changed_data) - {'request_agreement'}:
            goal.agreed_on, goal.agreed_by = None, None
        if not self.cleaned_data['request_agreement']:
            goal.agreement_requested_at, goal.agreed_on, goal.agreed_by = None, None, None
        elif not goal.agreement_requested_at:
            goal.agreement_requested_at = timezone.now()
        if not goal.share_next or not goal.next_text.strip():
            goal.share_next, goal.shared_at = False, None
        elif not goal.shared_at:
            goal.shared_at = timezone.now()
        if commit:
            goal.save()
        return goal


class StructureGapForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = StructureGap
        fields = ['role_kind', 'on_chart', 'actual', 'note']
        widgets = {'note': forms.Textarea(attrs={'rows': 3})}


class PromiseCheckForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PromiseCheck
        fields = ['promise', 'actual', 'is_gap']


def publish_form_for(model):
    """一般化した版を書くフォーム（文書・用語のどちらにも使う）"""
    target_model = model

    class PublishForm(BootstrapMixin, forms.ModelForm):
        class Meta:
            model = target_model
            fields = ['public_title', 'public_body']
            widgets = {'public_body': forms.Textarea(attrs={'rows': 8})}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields['public_title'].required = True
            self.fields['public_body'].required = True
            if model is Term:
                self.fields['public_title'].label = '用語（一般化）'
                self.fields['public_body'].label = '意味（一般化）'

    return PublishForm


class KitProposalForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = KitProposal
        fields = ['target', 'title', 'proposal', 'reason']
        widgets = {'proposal': forms.Textarea(attrs={'rows': 4}), 'reason': forms.Textarea(attrs={'rows': 3})}


class InterviewQuestionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = InterviewQuestion
        fields = ['question', 'industry', 'source']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['source'].queryset = Document.objects.filter(kind=Document.KIND_RETRO, visibility='company')
        self.fields['source'].label_from_instance = lambda d: f'{d.public_title}（{d.site.industry}）'
        self.fields['source'].empty_label = '元になった振り返り（任意）'
        self.fields['question'].widget.attrs['placeholder'] = '入場前に確かめる質問'
        self.fields['industry'].widget.attrs['placeholder'] = '業界（任意）'


class SearchForm(forms.Form):
    q = forms.CharField(label='キーワード', required=False, widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': '用語・設計判断・振り返りを検索'}))
