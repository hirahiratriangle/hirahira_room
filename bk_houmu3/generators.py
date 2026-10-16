"""計算問題のテンプレート。

基本情報版では、数値を変えれば何度でも演習できる型（稼働率、基数変換など）を
ここで作っていた。法務3級は法令と実務の知識を問う試験で、数値を振り直して
作れる型が無いので、テンプレートは1つも持たない。

仕組み（レジストリ・生成・同期）だけを基本情報版から引き継いでいる。
型を足すときは、生成関数を書いて TEMPLATE_SPECS に並べれば、出題と集計がそのまま追随する。
"""

import random

from .models import Question, QuestionTemplate

# ---------------------------------------------------------------- レジストリ

# (テンプレートキー, 題材, 中分類番号, 小分類, 生成関数)
TEMPLATE_SPECS = []

REGISTRY = {spec[0]: spec for spec in TEMPLATE_SPECS}


def generators_for_category(category):
    """その中分類で使える有効なテンプレートを返す。"""
    return [
        template
        for template in QuestionTemplate.objects.filter(
            category=category, is_active=True
        )
        if template.key in REGISTRY
    ]


def generate_question(template, rng=None):
    """テンプレートから Question を1件作る。

    問題は全 ID で共有する。同じ数値を引いたときは、同じ問題を使い回す。
    """
    spec = REGISTRY.get(template.key)
    if spec is None:
        return None

    rng = rng or random
    built = spec[4](rng)

    distractors = built['distractors']
    if len(distractors) < 3:
        return None

    options = [built['correct']] + distractors[:3]
    rng.shuffle(options)
    answer_index = options.index(built['correct'])

    question, created = Question.objects.get_or_create(
        template=template, params=built['params'],
        defaults={
            'subject': Question.SUBJECT_A,
            'category': template.category,
            'topic': template.topic,
            'stem': built['stem'],
            'choices': options,
            'answer_index': answer_index,
            'explanation': built['explanation'],
            'difficulty': 2,
            'source': 'テンプレート「{}」による自動生成'.format(template.title),
        },
    )
    return question


def sync_templates():
    """レジストリの内容を QuestionTemplate に反映する。"""
    from .models import Category

    created = 0
    for key, title, category_code, topic, _ in TEMPLATE_SPECS:
        category = Category.objects.get(code=category_code)
        _, made = QuestionTemplate.objects.update_or_create(
            key=key,
            defaults={
                'title': title,
                'category': category,
                'topic': topic,
                'is_active': True,
            },
        )
        created += 1 if made else 0
    return created
