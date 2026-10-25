from django import forms

from .models import Document, PromiseCheck, Question, Term


class BootstrapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs['class'] = 'form-check-input'
            elif isinstance(f.widget, forms.Select):
                f.widget.attrs['class'] = 'form-select'
            else:
                f.widget.attrs['class'] = 'form-control'


class QuestionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Question
        fields = ['kind', 'title', 'body', 'ask_to', 'scope_out']
        widgets = {'body': forms.Textarea(attrs={'rows': 4})}


class TermForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Term
        fields = ['term', 'meaning', 'general_difference']
        widgets = {
            'meaning': forms.Textarea(attrs={'rows': 3}),
            'general_difference': forms.Textarea(attrs={'rows': 3}),
        }


class DocumentForm(BootstrapMixin, forms.ModelForm):
    """種類ごとに要る項目だけ出す"""

    class Meta:
        model = Document
        fields = ['title', 'body', 'week_of', 'risks', 'skill_item']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 10}),
            'risks': forms.Textarea(attrs={'rows': 4}),
            'week_of': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        if kind == Document.KIND_WEEKLY:
            self.fields['week_of'].required = True
        else:
            del self.fields['week_of']
            del self.fields['risks']
        if kind != Document.KIND_RETRO:
            del self.fields['skill_item']


class PromiseCheckForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PromiseCheck
        fields = ['promise', 'actual', 'is_gap']


class SearchForm(forms.Form):
    q = forms.CharField(label='キーワード', required=False, widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': '用語・設計判断・振り返りを検索'}))
