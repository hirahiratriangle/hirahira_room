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
MAX_UPLOAD_BYTES = settings.FE_MAX_UPLOAD_BYTES


def _file_field(label, help_text):
    return forms.FileField(
        label=label, required=False, help_text=help_text,
        widget=forms.ClearableFileInput(
            attrs={'class': 'form-control', 'accept': '.json,application/json'}
        ),
    )


class QuestionUploadForm(forms.Form):
    """JSON ファイルのアップロード。

    問題と解説は更新の頻度が違うので別のファイルで渡せる。欄も分けてあり、
    片方だけ選んでも、両方選んでも取り込める。選ばなかったほうには触らない。
    両方に同じ側（問題なら問題）が入っていたら、どちらを使うか決められないので弾く。
    """

    questions_file = _file_field(
        '問題のJSON', '問題だけを選んだときは、いまの技術解説はそのまま残ります。',
    )
    notes_file = _file_field(
        '技術解説のJSON', '解説だけを選んだときは、いまの問題はそのまま残ります。',
    )

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
        """2つの欄をまとめて1つの取り込み内容にする。

        検証を通ってからでないと1件も書き込まないので、壊れたファイルを
        選んでも、いまの問題集と解説はそのまま残る。
        """
        cleaned = super().clean()
        parts = [self._read('questions_file'), self._read('notes_file')]
        parts = [p for p in parts if p is not None]
        if not parts:
            raise forms.ValidationError(
                '問題と技術解説のどちらか一方は選んでください。'
            )

        merged = {'questions': None, 'notes': None}
        for side, label in (('questions', '問題'), ('notes', '技術解説')):
            found = [p[side] for p in parts if p[side] is not None]
            if len(found) > 1:
                raise forms.ValidationError(
                    '選んだ2つのファイルの両方に{}が入っています。'
                    'どちらを使うか決められないので、片方にまとめてください。'.format(label)
                )
            if found:
                merged[side] = found[0]

        try:
            self.records = validate(merged)
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


# /fe/ 直下のほかの画面と同じ名前は、ダッシュボードの URL（/fe/<ID>/）と
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
