from django.views import generic

from proposals import concept_data


class IndexView(generic.TemplateView):
    """会社（PVSYS）に関わるページをまとめた入り口。genba、3つの構想、基本情報、福祉従事者の活動ページへ案内する。

    4つのページの URL はこの下（/pvsys/genba/ など）にある。移す前の URL からは config/urls.py で転送する。
    見出しのビジョンは文言を言い換えないよう、3つの構想の2枚目から取る。
    """
    template_name = 'pvsys/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['vision'] = ''.join(concept_data.SLIDES[2]['lines'])
        return context
