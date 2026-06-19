# -*- coding: utf-8 -*-
"""ツール模擬実行のオフライン用サンプル。

GitHub API を叩かずに動作を体験できるよう、search/issues API の items と
同じ形（number / title / body / html_url）のダミー Issue を用意する。
ケース A/B/C/D が一通り出るように作ってある。
"""

SAMPLE_REPO = "jami-fhir-jp-wg/jp-core-v1x"
SAMPLE_LABEL = "（サンプル）検査診療WG"

SAMPLE_ITEMS = [
    {
        "number": 925,
        "title": "[Low] short 記述のタイポ修正（用語の統一）",
        "body": "input/fsh/JP_Observation.fsh の short にタイポ。用語を統一したい。",
        "html_url": "https://github.com/jami-fhir-jp-wg/jp-core-v1x/issues/925",
    },
    {
        "number": 931,
        "title": "[High] slicing の discriminator 未定義で cardinality が崩れる",
        "body": "input/fsh/JP_DiagnosticReport.fsh の slicing に discriminator が無く、"
                "cardinality の解釈が曖昧。構造の見直しが必要。",
        "html_url": "https://github.com/jami-fhir-jp-wg/jp-core-v1x/issues/931",
    },
    {
        "number": 944,
        "title": "[High] マイナ保険証の資格確認に伴う保険者番号の扱い",
        "body": "資格確認のユースケースで保険者番号の桁・体系をどう持つか制度設計が必要。"
                "input/fsh/JP_Coverage.fsh に影響。",
        "html_url": "https://github.com/jami-fhir-jp-wg/jp-core-v1x/issues/944",
    },
    {
        "number": 950,
        "title": "[Med] 本文と表で記述が矛盾（食い違い）している",
        "body": "input/pagecontent/observation.md と JP_Observation の short で記述が不一致。"
                "どちらを正とするか決める必要がある。",
        "html_url": "https://github.com/jami-fhir-jp-wg/jp-core-v1x/issues/950",
    },
    {
        "number": 958,
        "title": "[Low] コメント行の重複を削除",
        "body": "input/fsh/JP_Patient.fsh にコメントの重複。掃除のみ。",
        "html_url": "https://github.com/jami-fhir-jp-wg/jp-core-v1x/issues/958",
    },
]
