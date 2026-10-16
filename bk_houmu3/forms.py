"""問題 JSON を取り込むためのフォーム。

問題は AI に作らせて JSON ファイルで受け取り、アプリは一括で差し替えるだけ。
画面に入力欄も貼り付け欄も置かないので、更新経路がファイル1本に定まる。
手で書いたり直したりすることは想定していない。
"""

from django import forms
from django.conf import settings
from django.utils import timezone

from .importer import ImportError_, parse, validate
from .models import Learner

# 上限は設定に一本化してある。Django 側の上限とずれないようにするため。
MAX_UPLOAD_BYTES = settings.BK_HOUMU3_MAX_UPLOAD_BYTES


def _file_field(label, help_text):
    return forms.FileField(
        label=label, required=False, help_text=help_text,
        widget=forms.ClearableFileInput(
            attrs={'class': 'form-control', 'accept': '.json,application/json'}
        ),
    )


class QuestionUploadForm(forms.Form):
    """問題 JSON ファイルのアップロード。

    法務3級は学習モードを使わず、技術解説（notes）を持たないので、取り込むのは問題だけ。
    基本情報版にある解説の欄は置かない。ファイルに "notes" が入っていたら、
    取り違えとみなして弾く（黙って解説を入れると、使わない解説が溜まる）。
    """

    questions_file = _file_field('問題のJSON', '')

    def _read(self, field):
        """1本ぶんを読んで、取り込める内容かを確かめる。"""
        upload = self.cleaned_data.get(field)
        if not upload:
            return None
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                '{}：ファイルが大きすぎます（{:.1f} MB。上限 {} MB）。'.format(
                    upload.name, upload.size / 1024 / 1024,
                    MAX_UPLOAD_BYTES // 1024 // 1024,
                )
            )
        try:
            text = upload.read().decode('utf-8')
        except UnicodeDecodeError:
            raise forms.ValidationError(
                '{}：UTF-8 のテキストとして読めませんでした。'.format(upload.name)
            )
        try:
            return parse(text)
        except ImportError_ as exc:
            raise forms.ValidationError('{}：{}'.format(upload.name, exc))

    def clean(self):
        """取り込む内容を確かめる。

        検証を通ってからでないと1件も書き込まないので、壊れたファイルを
        選んでも、いまの問題集はそのまま残る。
        """
        cleaned = super().clean()
        if self.errors:
            return cleaned
        payload = self._read('questions_file')
        if payload is None:
            raise forms.ValidationError('問題の JSON ファイルを選んでください。')
        if payload['notes'] is not None:
            raise forms.ValidationError(
                'このファイルには技術解説（"notes"）が入っています。'
                'このアプリは技術解説を使わないので取り込めません。問題（"questions"）だけのファイルにしてください。'
            )
        if payload['questions'] is None:
            raise forms.ValidationError('このファイルには問題（"questions"）が入っていません。')

        try:
            self.records = validate({'questions': payload['questions'], 'notes': None})
        except ImportError_ as exc:
            raise forms.ValidationError(str(exc))
        return cleaned


class PassReportForm(forms.Form):
    """本番に合格したことの申告。受験日だけを聞く。"""

    passed_on = forms.DateField(
        label='合格した試験の受験日',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
    )

    def clean_passed_on(self):
        passed_on = self.cleaned_data['passed_on']
        if passed_on > timezone.localdate():
            raise forms.ValidationError('受験日が未来になっています。')
        return passed_on


# /bk_houmu3/ 直下のほかの画面と同じ名前は、ダッシュボードの URL（/bk_houmu3/<ID>/）と
# ぶつかるので ID にできない。
RESERVED_CODES = {'quiz', 'learn', 'stats', 'history', 'pass', 'manage', 'enter'}


class LearnerForm(forms.Form):
    """ID の入力。入るときは既にある ID、作るときはまだ無い ID を受け付ける。

    打ち間違えた ID で新しく作ってしまわないよう、「入る」と「作る」を分ける。
    """

    code = forms.CharField(
        label='ID', max_length=30,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'autocomplete': 'username',
            'autocapitalize': 'none', 'spellcheck': 'false',
        }),
        help_text='半角の英小文字・数字・「_」「-」で、3〜30文字。',
    )

    def __init__(self, *args, creating=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.creating = creating
        self.learner = None

    def clean_code(self):
        code = self.cleaned_data['code'].strip().lower()
        for validator in Learner._meta.get_field('code').validators:
            validator(code)
        exists = Learner.objects.filter(code=code).first()
        if self.creating:
            if code in RESERVED_CODES:
                raise forms.ValidationError('この ID は使えません。別の ID にしてください。')
            if exists:
                raise forms.ValidationError('その ID は既に使われています。')
        else:
            if exists is None:
                raise forms.ValidationError(
                    'その ID はまだありません。初めてなら「新しく作る」を押してください。'
                )
            self.learner = exists
        return code
