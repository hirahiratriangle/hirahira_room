from django.views import generic

from . import deck_data


class IndexView(generic.TemplateView):
    """SE／PGの賃金の議論を、1枚ずつ送って読むページ。「SE_PG賃金_第1弾.pptx」をもとにしている。

    内容は deck_data.SLIDES に持つ。スライド番号は並び順から振る。
    話し手がいないので、発表者ノートをスライドの下に出す。
    """
    template_name = 'wages/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['slides'] = deck_data.SLIDES
        context['total'] = len(deck_data.SLIDES)
        context['footer'] = deck_data.FOOTER
        return context
