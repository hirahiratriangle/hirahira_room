"""問題 JSON を取り込むためのフォーム。

問題は AI に作らせて JSON ファイルで受け取り、アプリは一括で差し替えるだけ。
画面に入力欄も貼り付け欄も置かないので、更新経路がファイル1本に定まる。
手で書いたり直したりすることは想定していない。
"""

from django import forms

from .importer import ImportError_, parse, validate

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


class QuestionUploadForm(forms.Form):
    """JSON ファイルのアップロード。"""

    upload = forms.FileField(
        label='JSONファイル',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.json,application/json'}),
        help_text='自分の AI に書き出させた .json ファイルを選ぶ。',
    )

    def clean_upload(self):
        """中身まで読んで、取り込める内容かをここで確かめる。

        検証を通ってからでないと 1 問も書き込まないので、
        壊れたファイルを選んでも、いまの問題集はそのまま残る。
        """
        upload = self.cleaned_data['upload']
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                'ファイルが大きすぎます（上限 {} MB）。'.format(MAX_UPLOAD_BYTES // 1024 // 1024)
            )
        try:
            payload = upload.read().decode('utf-8')
        except UnicodeDecodeError:
            raise forms.ValidationError('UTF-8 のテキストとして読めませんでした。')

        try:
            self.records = validate(parse(payload))
        except ImportError_ as exc:
            raise forms.ValidationError(str(exc))
        return upload
