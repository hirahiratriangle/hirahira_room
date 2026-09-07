"""問題 JSON を取り込むためのフォーム。

問題の作成・修正は JSON 側で行い、アプリは一括で差し替えるだけにしている。
画面に入力フォームを置かないので、更新経路が JSON の一本に定まる。
"""

from django import forms

from .importer import ImportError_, parse, validate

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


class QuestionUploadForm(forms.Form):
    """JSON ファイルのアップロード、または本文の貼り付け。"""

    upload = forms.FileField(
        label='JSONファイル',
        required=False,
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.json,application/json'}),
        help_text='fe/data/questions_*.json のような形式のファイルを選ぶ。',
    )
    payload = forms.CharField(
        label='貼り付け',
        required=False,
        widget=forms.Textarea(attrs={'rows': 12, 'class': 'form-control', 'spellcheck': 'false'}),
        help_text='ファイルを使わずに、JSON を直接貼り付けてもよい。',
    )
    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get('upload')
        payload = (cleaned.get('payload') or '').strip()

        if upload and payload:
            raise forms.ValidationError(
                'ファイルと貼り付けの両方が指定されています。どちらか一方にしてください。'
            )
        if not upload and not payload:
            raise forms.ValidationError('JSONファイルを選ぶか、本文を貼り付けてください。')

        if upload:
            if upload.size > MAX_UPLOAD_BYTES:
                raise forms.ValidationError(
                    'ファイルが大きすぎます（上限 {} MB）。'.format(MAX_UPLOAD_BYTES // 1024 // 1024)
                )
            try:
                payload = upload.read().decode('utf-8')
            except UnicodeDecodeError:
                raise forms.ValidationError('UTF-8 のテキストとして読めませんでした。')

        try:
            cleaned['records'] = validate(parse(payload))
        except ImportError_ as exc:
            raise forms.ValidationError(str(exc))
        return cleaned
