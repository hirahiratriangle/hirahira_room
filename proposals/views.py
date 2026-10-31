from django.views import generic

from . import concept_data


class IndexView(generic.TemplateView):
    """社内活動の3つの構想を、1枚ずつ送って読むページ。「社内活動 3つの構想.pptx」の全スライドを載せる。

    内容は concept_data.SLIDES に持つ（文言はプレゼンのまま）。スライド番号はプレゼンと同じになるよう、並び順から振る。
    発表はせず各自に読んでもらうので、発表者ノートの説明もスライドの下に出す。
    このページは入り口で、詳しい中身は各構想のページ（genba の案内ページ、基本情報の対策アプリ、fun IT club）にある。
    """
    template_name = 'proposals/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['slides'] = concept_data.SLIDES
        context['total'] = len(concept_data.SLIDES)
        context['concepts'] = concept_data.CONCEPTS
        return context
