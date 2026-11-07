"""countdown の API で受け渡すデータの形。

React の画面は、ここで決めた形の JSON だけを見て描く。
日数の計算（残り・経過・週・周年）はモデルのプロパティをそのまま使い、
サーバーで済ませてから渡す。ブラウザの時計やタイムゾーンに左右されないようにするため。
"""

from rest_framework import serializers

from .models import CountEvent


class CountEventSerializer(serializers.ModelSerializer):
    # メモは空でもよい。DB では null を許しているが、API では null を返さず空文字にそろえる
    memo = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')
    # 画面の日付欄（<input type="date">）は形式を崩せないので、形式のまちがいが起きるのは
    # 空のまま送ったときだけ。DRF の「日付の形式が違います」ではなく、選ぶよう促す
    date = serializers.DateField(error_messages={'invalid': '日付を選んでください。'})

    # ---- 読むだけの欄（モデルのプロパティから作る） ----
    # 今日から見た日数差。未来なら正、過去なら負、当日は 0
    days_delta = serializers.IntegerField(read_only=True)
    # 画面に大きく出す日数（常に 0 以上）
    count_days = serializers.IntegerField(read_only=True)
    # 「残り」「経過」「当日」
    count_label = serializers.CharField(read_only=True)
    # 残り日数の補足（○週と○日）
    weeks = serializers.SerializerMethodField()
    extra_days = serializers.SerializerMethodField()
    # 過ぎた出来事の周年（迎えていなければ 0）
    years_passed = serializers.IntegerField(read_only=True)

    class Meta:
        model = CountEvent
        fields = (
            'id', 'name', 'date', 'memo', 'notify', 'created_at',
            'days_delta', 'count_days', 'count_label', 'weeks', 'extra_days', 'years_passed',
        )
        # created_at は、画面の「登録した日から当日までの進み具合」のリングと、
        # 「登録が新しい順」の並べ替えに使う
        read_only_fields = ('id', 'created_at')

    def get_weeks(self, event):
        return event.weeks_and_days[0]

    def get_extra_days(self, event):
        return event.weeks_and_days[1]

    def to_representation(self, event):
        data = super().to_representation(event)
        if data['memo'] is None:
            data['memo'] = ''
        return data
