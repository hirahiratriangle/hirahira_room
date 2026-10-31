# 活動ページに出す WG の情報。funITclub/wg-welfare の活動ページから hirahira_room へ移したもの。
#
# 個人の名前・連絡先は書かない（メンバーは Classroom と Chat で分かる）。

# WG 一覧のカードと同じ識別子と名前
CODE = 'WG-03'
NAME = '福祉従事者アセスメント&プランニング支援kit開発研究'

# 何をしている WG かを 1 文で。ページの見出しの下に出る
LEAD = '福祉従事者の勘を支える伴奏者となるプラットフォームを構想している。'

# WG の紹介。空行で段落を分ける
INTRO = '''
福祉従事者（まずはサービス管理責任者＝サビ管）のアセスメント・計画の仕事を LLM に担わせ、
LOOP から生まれる知見を学習し続けることで、従事者の勘を支える伴奏者となる
プラットフォーム（Assessment & Planning Support Kit）を構想している。

ITの目的を「効率化」から「創発」へ移し、仕事と知見が循環する基盤をつくることを目指す。
いまは構想を共有し、論点について意見を集めている段階。
'''

# 構想資料（templates/welfare_activity/slides.html）。まだ無いうちは None のままにする。
# 入れると、トップに案内が出て /slides/ が開く
SLIDES_TITLE = '福祉従事者支援プラットフォーム構想'
SLIDES_SUMMARY = '構想共有・ディスカッション資料（スライド 14 枚）。仕事と知見が循環する基盤をつくる。'

# トップの「関連リンク」。（名前, URL）を並べる。WG が作っている Web アプリなど
LINKS = [
    # ('Webアプリ', 'https://funitclub.org/welfare/'),
]

# チケットを読む GitHub のリポジトリ（funITclub/ の下）。チケットは会員がここに直接書く。
# 題名と本文は活動ページで一般公開される（issue テンプレートにも書いてある）
GITHUB_REPO = 'wg-welfare'

# ステータス・優先度・期日などを持つ org の Project の番号（https://github.com/orgs/funITclub/projects/<番号>）。
# org のテンプレートから複製して入れる（wg-activity スキルの手順）。None のあいだは Issue の
# Open/Closed とラベルだけで動き、優先度・期日・プロジェクトは出ない
GITHUB_PROJECT = 3

# 「参加するには」のリンク先。WG 一覧の「WG に参加」から入ってもらう
JOIN_URL = 'https://funitclub.org/wg/'


def as_dict():
    """テンプレートに渡す形。"""
    return {
        'code': CODE,
        'name': NAME,
        'lead': LEAD,
        'intro': INTRO.strip(),
        'slides_title': SLIDES_TITLE,
        'slides_summary': SLIDES_SUMMARY,
        'links': LINKS,
        'join_url': JOIN_URL,
    }
