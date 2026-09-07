"""このアプリが対象とする試験の定義。

試験ごとにアプリを作る方針のため、対象の試験と、問題を作るのに必要な資料を
ここ1か所で定義する。画面に出す案内も、利用者が自分の AI に渡すプロンプトも、
すべてこの定義から組み立てるので、書き換えれば全体が追随する。

別の試験に流用するときに触るのは、このファイルと data/categories.py の2つ。
"""

from .models import Category

EXAM = {
    'name': '基本情報技術者試験',
    'short_name': 'FE',
    'authority': 'IPA（情報処理推進機構）',
    'url': 'https://www.ipa.go.jp/shiken/kubun/fe.html',
    'note': 'いずれも無償で公開されている。ダウンロードして、自分の AI から読める場所に置く。',
    # 問題を作るのに必要な資料。required は「無いと質が大きく落ちるもの」。
    'materials': [
        {
            'name': '試験要綱',
            'purpose': '出題範囲の階層と知識項目例。どの分野に何が含まれるかの根拠になる。',
            'required': True,
        },
        {
            'name': 'シラバス',
            'purpose': '小分類ごとの学習目標と用語例。作問の種になる。',
            'required': True,
        },
        {
            'name': '公開問題（過去問・サンプル問題）',
            'purpose': '実際の設問形式と難易度。転記すれば正解が公式に裏付けられる。',
            'required': True,
        },
    ],
}


def material_lines():
    """プロンプトに差し込む資料の一覧。"""
    return [
        '- {}：{}'.format(m['name'], m['purpose'])
        for m in EXAM['materials']
    ]


def category_lines():
    """プロンプトに差し込む中分類の一覧。データベースの内容から作る。"""
    parts = [
        '{} {}({})'.format(c.code, c.name, c.exam_weight)
        for c in Category.objects.all()
    ]
    lines, current = [], []
    for part in parts:
        current.append(part)
        if len(current) == 3:
            lines.append(' / '.join(current))
            current = []
    if current:
        lines.append(' / '.join(current))
    return lines


def build_prompt():
    """利用者が自分の AI に渡すプロンプトを組み立てる。

    資料の一覧と中分類は定義とデータベースから引くので、
    試験を差し替えても、分野を増やしても、内容がずれない。
    """
    return '\n'.join([
        '{}の対策アプリ用に、問題をJSONで作ってください。'.format(EXAM['name']),
        '出力はJSON配列だけ。説明文は不要です。',
        '',
        '【手元に用意する資料】',
        '{}が公開しているものを参照してください。推測で作らないでください。'.format(EXAM['authority']),
        *material_lines(),
        '',
        '[',
        '  {',
        '    "category": 11,',
        '    "subject": "A",',
        '    "topic": "情報セキュリティ",',
        '    "difficulty": 2,',
        '    "stem": "問題文",',
        '    "choices": ["ア の本文", "イ の本文", "ウ の本文", "エ の本文"],',
        '    "answer": 0,',
        '    "explanation": "正解の理由と、他の選択肢がなぜ違うか",',
        '    "source": "出典（過去問の問番号、またはシラバスの小分類）"',
        '  }',
        ']',
        '',
        '【規則】',
        '- category は中分類の番号（下表）。必須',
        '- subject は "A"（知識）か "B"（技能・擬似言語）。既定は "A"',
        '- choices にア／イ／ウ／エの記号は入れない（表示側で付ける）',
        '- answer は 0 から数える',
        '- 科目Aは必ず4択。科目Bは本番同様6〜10択にする（4択だと消去法が効きすぎる）',
        '- 擬似言語は stem に改行と全角スペースの字下げでそのまま書く',
        '- キーは書かない',
        '- source には参照した資料の箇所を必ず書く。'
        '過去問から転記したものと自分で作ったものを、あとで区別するため',
        '',
        '【中分類（括弧内は本番60問での想定出題数）】',
        *category_lines(),
        '',
        '【誤答の作り方】',
        '混同しやすい近い概念を誤答に置く。'
        'でたらめな誤答にすると消去法で解けてしまい学習にならない。',
        '計算問題なら「式を取り違えたときに出る値」を並べる。',
        '',
        '【依頼】',
        'セキュリティ（category 11）を20問、科目Aで作ってください。',
    ])
