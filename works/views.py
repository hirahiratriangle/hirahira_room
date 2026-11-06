from django.views import generic

from proposals import concept_data


class IndexView(generic.TemplateView):
    """取り組みをまとめた入り口。3つの構想と SE／PGの賃金へ案内する。

    genba・基本情報・福祉従事者の活動ページは、ホームのアプリ一覧に直接置いている。

    入り口は /works/。公開ページには社名を出さないので、見出しもタブ名も「取り組み」とする。
    見出しのビジョンは文言を言い換えないよう、3つの構想の2枚目から取る。
    """
    template_name = 'works/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['vision'] = ''.join(concept_data.SLIDES[1]['lines'])
        return context
