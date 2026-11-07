"""問題 JSON を取り込むためのフォーム。

問題は AI に作らせて JSON ファイルで受け取り、アプリは一括で差し替えるだけ。
画面に入力欄も貼り付け欄も置かないので、更新経路がファイル1本に定まる。
手で書いたり直したりすることは想定していない。
"""

from django import forms
from django.conf import settings
from django.utils import timezone

from .importer import ImportError_, parse, validate
from .models import Category, Learner, Question

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

    問題と解説は更新の頻度が違い、科目Aと科目Bも別々に作るので、手元の
    ファイルは 問題・解説 × 科目A・科目B の4本になる。欄もその4つに分けてあり、
    どれか1つだけ選んでも、まとめて選んでも取り込める。選ばなかった欄の
    中身（その側のその科目）には触らない。

    欄ごとに、入っている側と科目が欄と合っているかを確かめる。科目Aの欄に
    科目Bのファイルを選ぶといった取り違えを、取り込む前に止めるため。
    """

    # (欄の名前, 側, 科目, 見出し)
    SLOTS = (
        ('questions_a_file', 'questions', Question.SUBJECT_A, '問題（科目A）'),
        ('questions_b_file', 'questions', Question.SUBJECT_B, '問題（科目B）'),
        ('notes_a_file', 'notes', Question.SUBJECT_A, '技術解説（科目A）'),
        ('notes_b_file', 'notes', Question.SUBJECT_B, '技術解説（科目B）'),
    )

    questions_a_file = _file_field('問題（科目A）のJSON', '例：fe_questions_a.json')
    questions_b_file = _file_field('問題（科目B）のJSON', '例：fe_questions_b.json')
    notes_a_file = _file_field('技術解説（科目A）のJSON', '例：fe_notes_a.json')
    notes_b_file = _file_field('技術解説（科目B）のJSON', '例：fe_notes_b.json')

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

    @staticmethod
    def _check_slot(name, payload, side, subject, label, subjects):
        """欄と中身の側・科目が合っているか。合わなければ理由を添えて止める。"""
        other, other_label = (
            ('notes', '技術解説') if side == 'questions' else ('questions', '問題')
        )
        if payload[side] is None or payload[other] is not None:
            raise forms.ValidationError(
                '{}：「{}」の欄に{}が入っています。{}だけのファイルを選んでください。'.format(
                    name, label, other_label, label,
                )
            )
        subject_label = '科目A' if subject == Question.SUBJECT_A else '科目B'
        for i, r in enumerate(payload[side], start=1):
            if not isinstance(r, dict):
                continue  # 形の検査は validate に任せる
            if side == 'questions':
                found = r.get('subject', Question.SUBJECT_A)
            else:
                found = subjects.get(r.get('category'))
            if found is not None and found != subject:
                raise forms.ValidationError(
                    '{}：{}件目が{}ではありません。「{}」の欄には{}だけを入れてください。'.format(
                        name, i, subject_label, label, subject_label,
                    )
                )

    def clean(self):
        """4つの欄をまとめて1つの取り込み内容にする。

        検証を通ってからでないと1件も書き込まないので、壊れたファイルを
        選んでも、いまの問題集と解説はそのまま残る。
        """
        cleaned = super().clean()
        subjects = dict(Category.objects.values_list('code', 'subject'))
        merged = {'questions': None, 'notes': None}
        for field, side, subject, label in self.SLOTS:
            payload = self._read(field)
            if payload is None:
                continue
            self._check_slot(
                self.cleaned_data[field].name, payload, side, subject, label, subjects,
            )
            merged[side] = (merged[side] or []) + payload[side]

        if merged['questions'] is None and merged['notes'] is None:
            raise forms.ValidationError('取り込むファイルを1つ以上選んでください。')

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


# 画面の名前と同じ ID は紛らわしいので作れないようにしておく
# （ID は URL に出さないので、ぶつかりはしない）。
RESERVED_CODES = {'quiz', 'learn', 'stats', 'history', 'pass', 'manage', 'enter', 'guide', 'console', 'mypage'}


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
