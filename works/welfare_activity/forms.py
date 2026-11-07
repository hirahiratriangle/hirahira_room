from django import forms

from .tickets import TRACKERS


def _choices(values):
    return [('', '（すべて）')] + [(v, v) for v in values]


class IssueFilterForm(forms.Form):
    """一覧の絞り込み。すべて任意。選択肢は GitHub から読んだもの（board）から作る。"""

    OPEN_CHOICES = [('open', '未完了'), ('closed', '完了'), ('all', 'すべて')]

    state = forms.ChoiceField(label='状態', choices=OPEN_CHOICES, required=False)
    project = forms.ChoiceField(label='プロジェクト', required=False)
    tracker = forms.ChoiceField(
        label='トラッカー', choices=[('', '（すべて）')] + [(v, label) for v, label, _ in TRACKERS],
        required=False,
    )
    status = forms.ChoiceField(label='ステータス', required=False)
    priority = forms.ChoiceField(label='優先度', required=False)

    def __init__(self, *args, board, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = _choices(board.status_order)
        if board.has_project:
            self.fields['project'].choices = [('', '（すべて）')] + [(g.id, g.name) for g in board.groups]
            self.fields['priority'].choices = _choices(board.priority_order)
        else:
            # Project が無いと、プロジェクトも優先度も無い
            del self.fields['project']
            del self.fields['priority']
