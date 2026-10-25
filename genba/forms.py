from django import forms

from .models import Document, Membership, PromiseCheck, Question, Site, Summary, Term


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

    WEEKLY_FIELDS = ['title', 'week_of', 'done', 'learned', 'stuck', 'next_step']

    class Meta:
        model = Document
        fields = ['title', 'body', 'week_of', 'done', 'learned', 'stuck', 'next_step', 'skill_item']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 10}),
            'week_of': forms.DateInput(attrs={'type': 'date'}),
            'done': forms.Textarea(attrs={'rows': 3}),
            'learned': forms.Textarea(attrs={'rows': 3}),
            'stuck': forms.Textarea(attrs={'rows': 3}),
            'next_step': forms.Textarea(attrs={'rows': 3}),
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


class SummaryReviewForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Summary
        fields = ['review_note']
        widgets = {'review_note': forms.Textarea(attrs={'rows': 3})}


class PromiseCheckForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PromiseCheck
        fields = ['promise', 'actual', 'is_gap']


class SearchForm(forms.Form):
    q = forms.CharField(label='キーワード', required=False, widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': '用語・設計判断・振り返りを検索'}))
